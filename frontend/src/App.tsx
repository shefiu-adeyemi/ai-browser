import { useState, useEffect, useRef, useCallback } from 'react'
import BrowserPane from '@/components/BrowserPane'
import ChatPanel from '@/components/ChatPanel'
import ApprovalPrompt from '@/components/ApprovalPrompt'
import { generateId } from '@/lib/utils'
import type { Turn, Status, SessionInfo, FormatOption, WsMessage, ApprovalRequest } from '@/types'

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
  const [maxSteps, setMaxSteps] = useState<number | undefined>(undefined)
  const [lastDuration, setLastDuration] = useState<number | undefined>(undefined)
  const [currentAction, setCurrentAction] = useState<string | undefined>(undefined)
  const [currentUrl, setCurrentUrl] = useState<string>('google.com')

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
        retryDelay = 1000
      }

      ws.onmessage = (event) => {
        try {
          const msg = JSON.parse(event.data) as WsMessage

          if (msg.type === 'url') {
            try {
              setCurrentUrl(new URL(msg.data).hostname + new URL(msg.data).pathname.replace(/\/$/, ''))
            } catch { setCurrentUrl(msg.data) }
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
                    return { ...m, pending: false, content: m.content || shown }
                  })
                )
              }
              pendingTurnIdRef.current = null
              stepCountRef.current = 0
              setCurrentAction(undefined)
            }
          } else if (msg.type === 'action') {
            stepCountRef.current += 1
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

  const sendTask = useCallback(async (task: string, fmt: FormatOption) => {
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

    try {
      await fetch('/task', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ task, fmt }),
      })
    } catch {
      // Update agent turn to show error
      setMessages((prev) =>
        prev.map((m) =>
          m.id === agentTurnId
            ? { ...m, pending: false, content: 'Failed to start task. Check the server.' }
            : m
        )
      )
      pendingTurnIdRef.current = null
    }
  }, [])

  const handleStop = useCallback(async () => {
    try {
      await fetch('/stop', { method: 'POST' })
    } catch {
      // ignore
    }
  }, [])

  const handleContinue = useCallback(async (extraSteps: number) => {
    try {
      await fetch('/continue', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ extra_steps: extraSteps }),
      })
    } catch {
      // ignore
    }
  }, [])

  // Changing chats while a task is in progress ends that task (the server stops it first, so its
  // result can't land in the wrong chat). Ask before doing something the user can't undo.
  const okToEndTask = useCallback(() => {
    if (status !== 'running' && status !== 'paused') return true
    return window.confirm('A task is in progress in this chat. Continuing will stop it. Go ahead?')
  }, [status])

  const handleNewSession = useCallback(async () => {
    if (!okToEndTask()) return
    try {
      await fetch('/sessions/new', { method: 'POST' })
      setMessages([])
      setScreenshot(null)
      setLastDuration(undefined)
    } catch {
      // ignore
    }
  }, [okToEndTask])

  const handleSwitchSession = useCallback(async (id: string) => {
    if (id !== currentSessionId && !okToEndTask()) return
    try {
      await fetch(`/sessions/${id}/switch`, { method: 'POST' })
    } catch {
      // ignore
    }
  }, [okToEndTask, currentSessionId])

  const handleDeleteSession = useCallback(async (id: string) => {
    if (id === currentSessionId && !okToEndTask()) return
    try {
      await fetch(`/sessions/${id}`, { method: 'DELETE' })
      setSessions((prev) => prev.filter((s) => s.id !== id))
      if (id === currentSessionId) {
        setMessages([])
        setScreenshot(null)
      }
    } catch {
      // ignore
    }
  }, [currentSessionId, okToEndTask])

  // Returns true once the server has the answer. On failure the prompt stays up, because the
  // agent is still blocked waiting for it (it denies on its own only after the timeout).
  const answerApproval = useCallback(async (id: string, allow: boolean): Promise<boolean> => {
    try {
      const res = await fetch(`/approvals/${id}`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ allow }),
      })
      if (!res.ok) return false
      setApproval((a) => (a && a.id === id ? null : a))
      return true
    } catch {
      return false
    }
  }, [])

  // The user may be in another tab while the agent waits: flag it in the tab title
  useEffect(() => {
    if (!approval) return
    const original = document.title
    document.title = '⚠ Approval needed — ' + original
    return () => {
      document.title = original
    }
  }, [approval])

  // Find current step count from pending agent turn
  const pendingTurn = messages.find((m) => m.pending)
  const currentStep = pendingTurn?.stepCount ?? 0

  return (
    <div className="flex h-screen w-screen overflow-hidden bg-gray-100">
      {approval && <ApprovalPrompt request={approval} onAnswer={answerApproval} />}
      {/* Left: browser pane */}
      <div className="flex-1 min-w-0 overflow-hidden">
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
        />
      </div>

      {/* Right: chat panel */}
      <div className="w-[420px] flex-shrink-0 flex flex-col overflow-hidden">
        <ChatPanel
          messages={messages}
          status={status}
          currentSessionId={currentSessionId}
          sessions={sessions}
          settingsOpen={settingsOpen}
          onSettingsOpen={setSettingsOpen}
          onSendTask={sendTask}
          onStop={handleStop}
          onContinue={handleContinue}
          onNewSession={handleNewSession}
          onSwitchSession={handleSwitchSession}
          onDeleteSession={handleDeleteSession}
        />
      </div>
    </div>
  )
}
