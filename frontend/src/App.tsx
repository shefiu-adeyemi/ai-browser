import { useState, useEffect, useRef, useCallback } from 'react'
import BrowserPane from '@/components/BrowserPane'
import ChatPanel from '@/components/ChatPanel'
import ApprovalPrompt from '@/components/ApprovalPrompt'
import PlaybookDialog from '@/components/PlaybookDialog'
import NotificationsDialog from '@/components/NotificationsDialog'
import Toast, { type ToastMessage } from '@/components/Toast'
import { generateId } from '@/lib/utils'
import type { Turn, Status, SessionInfo, FormatOption, WsMessage, ApprovalRequest, BrowserState } from '@/types'
import type { BrowserActions } from '@/components/BrowserChrome'

export default function App() {
  const [status, setStatus] = useState<Status>('idle')
  const [messages, setMessages] = useState<Turn[]>([])
  const [screenshot, setScreenshot] = useState<string | null>(null)
  const [approval, setApproval] = useState<ApprovalRequest | null>(null)
  // Browser viewport size (CSS px): the coordinate space for clicks sent back to the page
  const [viewport, setViewport] = useState<{ w: number; h: number } | undefined>(undefined)
  const [currentSessionId, setCurrentSessionId] = useState<string | null>(null)
  const [sessions, setSessions] = useState<SessionInfo[]>([])
  const [settingsOpen, setSettingsOpen] = useState(false)
  const [playbooksOpen, setPlaybooksOpen] = useState(false)
  const [inboxOpen, setInboxOpen] = useState(false)
  const [unread, setUnread] = useState(0)
  const [maxSteps, setMaxSteps] = useState<number | undefined>(undefined)
  const [lastDuration, setLastDuration] = useState<number | undefined>(undefined)
  const [currentAction, setCurrentAction] = useState<string | undefined>(undefined)
  const [currentUrl, setCurrentUrl] = useState<string>('')
  const [connected, setConnected] = useState(true)
  const [toast, setToast] = useState<ToastMessage | null>(null)
  // Narrow windows show one pane at a time instead of squeezing the browser out of view.
  const [narrow, setNarrow] = useState(() => window.matchMedia('(max-width: 899px)').matches)
  const [pane, setPane] = useState<'chat' | 'browser'>('chat')

  // Chat panel width (drag the divider) and collapse; remembered between visits.
  const [chatWidth, setChatWidth] = useState(() => {
    try { return Math.min(720, Math.max(320, Number(localStorage.getItem('aiba_chat_width')) || 420)) } catch { return 420 }
  })
  const [chatHidden, setChatHidden] = useState(() => {
    try { return localStorage.getItem('aiba_chat_hidden') === '1' } catch { return false }
  })
  const saveLayout = useCallback((w: number, hidden: boolean) => {
    try {
      localStorage.setItem('aiba_chat_width', String(w))
      localStorage.setItem('aiba_chat_hidden', hidden ? '1' : '0')
    } catch { /* private window: layout just isn't remembered */ }
  }, [])
  const setHidden = useCallback((hidden: boolean) => {
    setChatHidden(hidden)
    saveLayout(chatWidth, hidden)
  }, [chatWidth, saveLayout])
  const startDrag = useCallback((e: React.PointerEvent<HTMLDivElement>) => {
    e.preventDefault()
    const el = e.currentTarget
    el.setPointerCapture(e.pointerId)
    let last = chatWidth
    const move = (ev: PointerEvent) => {
      last = Math.round(Math.min(Math.min(720, window.innerWidth - 360), Math.max(320, window.innerWidth - ev.clientX)))
      setChatWidth(last)
    }
    const up = () => {
      el.removeEventListener('pointermove', move)
      el.removeEventListener('pointerup', up)
      saveLayout(last, false)
    }
    el.addEventListener('pointermove', move)
    el.addEventListener('pointerup', up)
  }, [chatWidth, saveLayout])
  const dividerKey = useCallback((e: React.KeyboardEvent<HTMLDivElement>) => {
    const step = e.key === 'ArrowLeft' ? 24 : e.key === 'ArrowRight' ? -24 : 0
    if (e.key === 'Home') { setChatWidth(420); saveLayout(420, false) }
    else if (step) {
      const w = Math.min(720, Math.max(320, chatWidth + step))
      setChatWidth(w)
      saveLayout(w, false)
    }
  }, [chatWidth, saveLayout])

  const notify = useCallback((text: string) => setToast({ id: Date.now(), text }), [])

  // How many inbox entries are unread (after that the server tells us as they arrive).
  useEffect(() => {
    fetch('/notifications?limit=1')
      .then((r) => (r.ok ? r.json() : null))
      .then((d) => { if (d && typeof d.unread === 'number') setUnread(d.unread) })
      .catch(() => { /* the bell just shows no count */ })
  }, [])

  // The agent's browser as the toolbar needs it: tabs, back / forward, current page. Polled while the
  // window is visible; refreshed straight away after every toolbar action.
  const [browser, setBrowser] = useState<BrowserState | null>(null)
  const browserSeq = useRef(0)
  const refreshBrowser = useCallback(async () => {
    const mine = ++browserSeq.current
    try {
      const res = await fetch('/browser/state')
      if (!res.ok) return
      const data = await res.json()
      if (mine === browserSeq.current) setBrowser(data)   // a slower, older answer must not win
    } catch { /* offline: keep the last state */ }
  }, [])
  useEffect(() => {
    refreshBrowser()
    const t = setInterval(() => { if (!document.hidden) refreshBrowser() }, 2000)
    return () => clearInterval(t)
  }, [refreshBrowser])

  const browse = useCallback(async (path: string, body?: unknown) => {
    try {
      const res = await fetch(path, {
        method: 'POST',
        headers: body === undefined ? undefined : { 'Content-Type': 'application/json' },
        body: body === undefined ? undefined : JSON.stringify(body),
      })
      if (!res.ok) {
        const data = await res.json().catch(() => ({}))
        notify(data.reason ?? `The browser couldn't do that (error ${res.status}).`)
      }
    } catch {
      notify("Couldn't reach the server.")
    }
    refreshBrowser()
  }, [notify, refreshBrowser])

  const browserActions: BrowserActions = {
    go: (text) => browse('/browser/go', { text }),
    back: () => browse('/browser/back'),
    forward: () => browse('/browser/forward'),
    reload: () => browse('/browser/reload'),
    newTab: () => browse('/browser/tabs/new'),
    switchTab: (id) => browse('/browser/tabs/switch', { id }),
    closeTab: (id) => browse('/browser/tabs/close', { id }),
  }
  const closeToast = useCallback(() => setToast(null), [])

  useEffect(() => {
    const mq = window.matchMedia('(max-width: 899px)')
    const on = () => setNarrow(mq.matches)
    mq.addEventListener('change', on)
    return () => mq.removeEventListener('change', on)
  }, [])

  const wsRef = useRef<WebSocket | null>(null)
  const pendingTurnIdRef = useRef<string | null>(null)
  const stepCountRef = useRef(0)

  // Fetch sessions list
  const fetchSessions = useCallback(async () => {
    try {
      const res = await fetch('/sessions')
      if (res.ok) {
        const data: SessionInfo[] = await res.json()
        setSessions(data)
      }
    } catch {
      // ignore
    }
  }, [])

  useEffect(() => {
    fetchSessions()
  }, [fetchSessions])

  // WebSocket connection with exponential-backoff reconnect
  useEffect(() => {
    let retryDelay = 1000
    let alive = true
    let ws: WebSocket
    let retryTimer: ReturnType<typeof setTimeout> | null = null

    function connect() {
      if (!alive) return
      const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:'
      ws = new WebSocket(`${protocol}//${window.location.host}/ws`)
      wsRef.current = ws

      let opened = false
      ws.onopen = () => {
        opened = true
        setConnected(true)
        retryDelay = 1000
      }

      ws.onmessage = (event) => {
        try {
          const msg = JSON.parse(event.data) as WsMessage

          if (msg.type === 'url') {
            setCurrentUrl(msg.data)
          } else if (msg.type === 'screenshot') {
            setScreenshot(msg.data)
            if (msg.w && msg.h) setViewport((v) => (v && v.w === msg.w && v.h === msg.h ? v : { w: msg.w!, h: msg.h! }))
          } else if (msg.type === 'status') {
            setStatus(msg.text)
            if (msg.max_steps !== undefined) setMaxSteps(msg.max_steps)
            if (msg.text === 'idle' || msg.text === 'done' || msg.text === 'error') {
              // A run that ends in an error or is stopped sends no result, so without this its
              // bubble would keep its "working" spinner and step counter for ever.
              const pendingId = pendingTurnIdRef.current
              if (pendingId && msg.text !== 'done') {
                const fallback =
                  msg.text === 'error' ? 'The task stopped because of an error.' : 'Stopped.'
                setMessages((prev) =>
                  prev.map((m) => {
                    if (m.id !== pendingId || !m.pending) return m
                    // Show the actual error the server reported, not just "an error happened"
                    const err = [...(m.logs ?? [])].reverse().find((l) => l.startsWith('[Error]'))
                    const shown = msg.text === 'error' && err ? err.replace(/^\[Error\]\s*/, '') : fallback
                    return { ...m, pending: false, content: m.content || shown, error: msg.text === 'error' }
                  })
                )
              }
              pendingTurnIdRef.current = null
              stepCountRef.current = 0
              setCurrentAction(undefined)
              fetchSessions()   // a failed or stopped run is saved too: keep History current
            }
          } else if (msg.type === 'action') {
            // The server tags real step lines ("Step 3"); other lines don't advance the counter.
            if (typeof msg.step === 'number') stepCountRef.current = msg.step
            setCurrentAction(msg.text)
            // Read the refs NOW. The updater below runs later (after this handler has finished and
            // possibly cleared them), so reading them inside it would attach logs to nothing.
            const pendingId = pendingTurnIdRef.current
            const step = stepCountRef.current
            setMessages((prev) => {
              if (!pendingId) return prev
              return prev.map((m) =>
                m.id === pendingId
                  ? { ...m, logs: [...(m.logs ?? []), msg.text], stepCount: step }
                  : m
              )
            })
          } else if (msg.type === 'notification') {
            // A scheduled run wants attention (or finished). The desktop toast comes from the server.
            setUnread(msg.unread)
            if (msg.level !== 'info') notify(msg.title)
          } else if (msg.type === 'approval') {
            setApproval(msg)
          } else if (msg.type === 'approval_done') {
            setApproval((a) => (a && a.id === msg.id ? null : a))
          } else if (msg.type === 'verdict') {
            // Only worth a line in the chat when the check found a problem (a passing run
            // shows its "Checked" badge on the answer itself).
            if (msg.verdict === 'fail') {
              const sysMsg: Turn = {
                id: generateId(),
                role: 'system',
                content: `Checked: not complete${msg.reason ? ` — ${msg.reason}` : ''}`,
                ts: new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }),
              }
              // Show it ABOVE the answer bubble that is still being worked on, so the chat reads
              // in the order things happened: reviewer's note, then the corrected answer.
              const pendingId = pendingTurnIdRef.current
              setMessages((prev) => {
                const at = pendingId ? prev.findIndex((m) => m.id === pendingId) : -1
                return at < 0 ? [...prev, sysMsg] : [...prev.slice(0, at), sysMsg, ...prev.slice(at)]
              })
            }
          } else if (msg.type === 'result') {
            setLastDuration(msg.duration)
            // Capture the bubble id BEFORE the updater: it runs after this handler has cleared the
            // ref, so reading it inside made every answer look like it had no bubble to finish and
            // left the "Working..." bubble spinning above a duplicate answer bubble.
            const pendingId = pendingTurnIdRef.current
            setMessages((prev) => {
              if (!pendingId) {
                const turn: Turn = {
                  id: generateId(),
                  role: 'agent',
                  content: msg.text,
                  ts: new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }),
                  verdict: msg.verdict,
                  reason: msg.reason,
                  retries: msg.retries,
                  duration: msg.duration,
                }
                return [...prev, turn]
              }
              return prev.map((m) =>
                m.id === pendingId
                  ? { ...m, pending: false, content: msg.text, verdict: msg.verdict, reason: msg.reason, retries: msg.retries, duration: msg.duration }
                  : m
              )
            })
            pendingTurnIdRef.current = null
            fetchSessions()
          } else if (msg.type === 'session_changed') {
            setCurrentSessionId(msg.id)
            const rebuilt: Turn[] = msg.history.map((h) => ({
              id: generateId(),
              role: h.role,
              content: h.content,
              ts: h.ts,
            }))
            setMessages(rebuilt)
            fetchSessions()
          }
        } catch {
          // ignore parse errors
        }
      }

      ws.onerror = () => {
        // onclose fires after onerror; reconnect happens there
      }

      ws.onclose = () => {
        if (!alive) return
        setConnected(false)
        // Exponential backoff: 1s → 2s → 4s → 8s → cap at 30s
        retryTimer = setTimeout(async () => {
          retryDelay = Math.min(retryDelay * 2, 30_000)
          // Rejected before opening usually means the server restarted and our
          // auth cookie is stale — loading "/" issues a fresh one.
          if (!opened) {
            try { await fetch('/', { cache: 'no-store' }) } catch {}
          }
          connect()
        }, retryDelay)
      }
    }

    connect()

    return () => {
      alive = false
      if (retryTimer) clearTimeout(retryTimer)
      ws?.close()
    }
  }, [fetchSessions])

  const lastTierRef = useRef('auto')      // a Retry keeps the tier the failed task used
  const sendTask = useCallback(async (task: string, fmt: FormatOption, tier: string = 'auto') => {
    lastTierRef.current = tier
    const ts = new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })

    // Add user bubble
    const userTurn: Turn = {
      id: generateId(),
      role: 'user',
      content: task,
      ts,
    }

    // Add pending agent bubble
    const agentTurnId = generateId()
    const agentTurn: Turn = {
      id: agentTurnId,
      role: 'agent',
      content: '',
      ts,
      pending: true,
      logs: [],
      stepCount: 0,
    }

    pendingTurnIdRef.current = agentTurnId
    stepCountRef.current = 0

    setMessages((prev) => [...prev, userTurn, agentTurn])

    const failBubble = (why: string) => {
      setMessages((prev) =>
        prev.map((m) => (m.id === agentTurnId ? { ...m, pending: false, error: true, content: why } : m))
      )
      pendingTurnIdRef.current = null
    }
    try {
      const res = await fetch('/task', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ task, fmt, tier }),
      })
      const data = await res.json().catch(() => ({}))
      if (!res.ok || data.ok === false) {
        failBubble(data.reason ? `Couldn't start the task: ${data.reason}` : `Couldn't start the task (server error ${res.status}).`)
      }
    } catch {
      failBubble("Couldn't reach the server. Check that the app is still running, then try again.")
    }
  }, [])

  // POST to the server; tell the user when it didn't work instead of silently doing nothing.
  const post = useCallback(async (path: string, what: string, body?: unknown) => {
    try {
      const res = await fetch(path, {
        method: 'POST',
        headers: body === undefined ? undefined : { 'Content-Type': 'application/json' },
        body: body === undefined ? undefined : JSON.stringify(body),
      })
      if (!res.ok) {
        notify(`${what} failed (server error ${res.status}).`)
        return null
      }
      return (await res.json().catch(() => ({}))) as { ok?: boolean; reason?: string }
    } catch {
      notify(`${what}: couldn't reach the server.`)
      return null
    }
  }, [notify])

  const handleStop = useCallback(async () => {
    const r = await post('/stop', 'Stop')
    if (r && r.ok === false && r.reason !== 'not running') notify(`Stop: ${r.reason ?? 'refused'}`)
  }, [post, notify])

  const handleContinue = useCallback(async (extraSteps: number) => {
    const r = await post('/continue', 'Continue', { extra_steps: extraSteps })
    if (r && r.ok === false) notify(`Continue: ${r.reason ?? 'refused'}`)
  }, [post, notify])

  // Changing chats while a task is in progress ends that task (the server stops it first, so its
  // result can't land in the wrong chat). Ask before doing something the user can't undo.
  const okToEndTask = useCallback(() => {
    if (status !== 'running' && status !== 'paused') return true
    return window.confirm('A task is in progress in this chat. Continuing will stop it. Go ahead?')
  }, [status])

  const handleNewSession = useCallback(async () => {
    if (!okToEndTask()) return
    const r = await post('/sessions/new', 'New chat')
    if (r) {
      setMessages([])
      setScreenshot(null)
      setLastDuration(undefined)
    }
  }, [okToEndTask, post])

  const handleSwitchSession = useCallback(async (id: string) => {
    if (id !== currentSessionId && !okToEndTask()) return
    const r = await post(`/sessions/${id}/switch`, 'Opening that chat')
    if (r && r.ok === false) notify(`Opening that chat: ${r.reason ?? 'refused'}`)
  }, [okToEndTask, currentSessionId, post, notify])

  const handleDeleteSession = useCallback(async (id: string) => {
    if (id === currentSessionId && !okToEndTask()) return
    try {
      const res = await fetch(`/sessions/${id}`, { method: 'DELETE' })
      if (!res.ok) {
        notify(`Delete failed (server error ${res.status}).`)
        return
      }
      setSessions((prev) => prev.filter((s) => s.id !== id))
      if (id === currentSessionId) {
        setMessages([])
        setScreenshot(null)
      }
    } catch {
      notify("Delete: couldn't reach the server.")
    }
  }, [currentSessionId, okToEndTask, notify])

  const handleRenameSession = useCallback(async (id: string, title: string) => {
    const r = await post(`/sessions/${id}/rename`, 'Rename', { title })
    if (r && r.ok) fetchSessions()
    else if (r) notify('Rename: the name can’t be empty.')
  }, [post, fetchSessions, notify])

  // Returns true once the server has the answer. On failure the prompt stays up, because the
  // agent is still blocked waiting for it (it denies on its own only after the timeout).
  const answerApproval = useCallback(async (id: string, allow: boolean, text?: string, remember?: boolean): Promise<boolean> => {
    try {
      const res = await fetch(`/approvals/${id}`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(text === undefined ? { allow } : { allow, text, remember: remember === true }),
      })
      if (!res.ok) return false
      setApproval((a) => (a && a.id === id ? null : a))
      return true
    } catch {
      return false
    }
  }, [])

  const handleRetry = useCallback(() => {
    const lastUser = [...messages].reverse().find((m) => m.role === 'user')
    if (lastUser) sendTask(lastUser.content, 'auto', lastTierRef.current)
  }, [messages, sendTask])

  useEffect(() => {
    if (!approval || approval.kind === 'handoff') return
    setChatHidden(false)
    saveLayout(chatWidth, false)
    if (document.fullscreenElement) document.exitFullscreen?.()
  }, [approval, chatWidth, saveLayout])

  // A hand-off needs the user in the browser view: bring it forward on a narrow window.
  useEffect(() => {
    if (approval?.kind === 'handoff') setPane('browser')
  }, [approval])

  // The user may be in another tab while the agent waits: flag it in the tab title
  useEffect(() => {
    if (!approval) return
    const original = document.title
    document.title = '⚠ Approval needed — ' + original
    return () => {
      document.title = original
    }
  }, [approval])

  // A run that used all its steps waits for the user's Continue or Stop: say so in the tab title.
  useEffect(() => {
    if (status !== 'paused') return
    const original = document.title
    document.title = '⏸ Paused — ' + original
    return () => {
      document.title = original
    }
  }, [status])

  // Find current step count from pending agent turn
  const pendingTurn = messages.find((m) => m.pending)
  const currentStep = pendingTurn?.stepCount ?? 0

  const showBrowser = !narrow || pane === 'browser'
  const showChat = !narrow || pane === 'chat'
  const approvalCard =
    approval && approval.kind !== 'handoff' ? (
      <ApprovalPrompt request={approval} onAnswer={answerApproval} step={stepCountRef.current} />
    ) : null
  const handoffBanner =
    approval && approval.kind === 'handoff' ? (
      <ApprovalPrompt request={approval} onAnswer={answerApproval} step={stepCountRef.current} />
    ) : null
  // On a narrow window the chat is hidden while the browser is showing: the card then sits above the tab bar.
  const cardBelow = narrow && pane === 'browser'

  return (
    <div className="flex h-screen w-screen flex-col overflow-hidden bg-gray-100">
      <Toast toast={toast} onClose={closeToast} />
      {inboxOpen && <NotificationsDialog onClose={() => setInboxOpen(false)} onUnread={setUnread} />}
      {playbooksOpen && (
        <PlaybookDialog
          onClose={() => setPlaybooksOpen(false)}
          notify={notify}
          canSave={messages.some((m) => m.role === 'agent' && !m.pending)}
          busy={status === 'running' || status === 'paused'}
        />
      )}
      <div className="flex min-h-0 flex-1">
        {/* Left: browser pane (kept mounted when hidden so the live frame isn't lost) */}
        <div className={showBrowser ? 'flex-1 min-w-0 overflow-hidden' : 'hidden'}>
          <BrowserPane
            screenshot={screenshot}
            viewport={viewport}
            status={status}
            duration={lastDuration}
            maxSteps={maxSteps}
            stepCount={currentStep}
            currentAction={currentAction}
            currentUrl={currentUrl}
            wsRef={wsRef}
            banner={handoffBanner}
            browser={browser}
            locked={(status === 'running' || status === 'paused') && approval?.kind !== 'handoff' || !!browser?.locked}
            actions={browserActions}
          />
        </div>

        {/* Divider: drag to resize, arrow keys too, Home resets (wide windows only) */}
        {!narrow && !chatHidden && (
          <div
            role="separator"
            aria-orientation="vertical"
            aria-label="Resize chat panel"
            aria-valuenow={chatWidth}
            aria-valuemin={320}
            aria-valuemax={720}
            tabIndex={0}
            onPointerDown={startDrag}
            onKeyDown={dividerKey}
            onDoubleClick={() => { setChatWidth(420); saveLayout(420, false) }}
            className="w-1 flex-shrink-0 cursor-col-resize bg-gray-200 transition-colors hover:bg-brand/40 focus:bg-brand/50 focus:outline-none"
          />
        )}

        {/* Right: chat panel, or a slim rail when it is hidden */}
        {!narrow && chatHidden ? (
          <div className="flex w-12 flex-shrink-0 flex-col items-center gap-3 border-l border-gray-200 bg-white py-3">
            <button
              aria-label="Show chat panel"
              title="Show chat panel"
              onClick={() => setHidden(false)}
              className="flex h-8 w-8 items-center justify-center rounded-xl text-gray-500 hover:bg-gray-100"
            >
              <span aria-hidden="true">&laquo;</span>
            </button>
            <span
              title={status}
              className={`h-2 w-2 rounded-full ${status === 'running' ? 'animate-pulse bg-green-500' : status === 'error' ? 'bg-red-400' : status === 'paused' ? 'bg-amber-400' : 'bg-gray-300'}`}
            />
            {status === 'running' && (
              <button
                aria-label="Stop the task"
                title="Stop the task"
                onClick={handleStop}
                className="flex h-8 w-8 items-center justify-center rounded-xl bg-gray-100 text-gray-700 hover:bg-red-100 hover:text-red-600"
              >
                <span aria-hidden="true" className="block h-2.5 w-2.5 bg-current" />
              </button>
            )}
          </div>
        ) : (
          <div
            className={showChat ? (narrow ? 'w-full flex flex-col overflow-hidden' : 'flex-shrink-0 flex flex-col overflow-hidden') : 'hidden'}
            style={narrow ? undefined : { width: chatWidth, maxWidth: 'calc(100vw - 360px)' }}
          >
            <ChatPanel
              messages={messages}
              status={status}
              connected={connected}
              currentSessionId={currentSessionId}
              sessions={sessions}
              settingsOpen={settingsOpen}
              onSettingsOpen={setSettingsOpen}
              onOpenPlaybooks={() => setPlaybooksOpen(true)}
              onOpenInbox={() => setInboxOpen(true)}
              unread={unread}
              onSendTask={sendTask}
              onStop={handleStop}
              onContinue={handleContinue}
              onRetry={handleRetry}
              onNewSession={handleNewSession}
              onSwitchSession={handleSwitchSession}
              onDeleteSession={handleDeleteSession}
              onRenameSession={handleRenameSession}
              approvalSlot={cardBelow ? null : approvalCard}
              onCollapse={narrow ? undefined : () => setHidden(true)}
              page={browser?.ready && browser.url ? { url: browser.url, title: browser.title } : null}
              wide={chatWidth >= 600}
              onToggleWide={narrow ? undefined : () => {
                const w = chatWidth >= 600 ? 420 : 640
                setChatWidth(w)
                saveLayout(w, false)
              }}
            />
          </div>
        )}
      </div>

      {cardBelow && approvalCard && <div className="flex flex-col bg-white pt-2">{approvalCard}</div>}

      {narrow && (
        <div role="tablist" aria-label="View" className="flex flex-shrink-0 border-t border-gray-200 bg-white">
          {(['chat', 'browser'] as const).map((p) => (
            <button
              key={p}
              role="tab"
              aria-selected={pane === p}
              onClick={() => setPane(p)}
              className={`flex-1 py-3 text-sm font-medium ${pane === p ? 'text-brand border-t-2 border-brand -mt-px' : 'text-gray-500'}`}
            >
              {p === 'chat' ? 'Chat' : 'Browser'}
            </button>
          ))}
        </div>
      )}
    </div>
  )
}
