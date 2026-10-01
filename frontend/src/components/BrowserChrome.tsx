import { useEffect, useRef, useState } from 'react'
import { ArrowLeft, ArrowRight, RotateCw, Plus, X, Maximize2, Minimize2, Lock, Expand, Shrink } from 'lucide-react'
import { cn } from '@/lib/utils'
import type { BrowserState } from '@/types'

export interface BrowserActions {
  go: (text: string) => void
  back: () => void
  forward: () => void
  reload: () => void
  newTab: () => void
  switchTab: (id: string) => void
  closeTab: (id: string) => void
}

interface Props {
  state: BrowserState | null
  currentUrl: string          // live address pushed by the server (fresher than the polled state)
  locked: boolean             // the agent is working: look, but don't touch
  controlling: boolean
  fullscreen: boolean
  onFullscreen: () => void
  fill: boolean               // the page fills the pane (cropping the edges) instead of fitting inside it
  onToggleFill: () => void
  actions: BrowserActions
}

const LOCKED_TIP = 'The agent is working. Stop it to browse yourself.'

function tabLabel(title: string, url: string): string {
  if (url === 'about:blank' || url === '') return 'New tab'
  if (title && title !== url) return title
  try {
    const u = new URL(url)
    return u.protocol.startsWith('http') ? u.host : url || 'New tab'
  } catch {
    return url || 'New tab'
  }
}

function IconButton(props: {
  label: string
  disabled?: boolean
  onClick: () => void
  children: React.ReactNode
}) {
  return (
    <button
      type="button"
      aria-label={props.label}
      title={props.label}
      disabled={props.disabled}
      onClick={props.onClick}
      className="flex h-7 w-7 flex-shrink-0 items-center justify-center rounded-lg text-gray-600 hover:bg-black/5 disabled:cursor-not-allowed disabled:text-gray-300 disabled:hover:bg-transparent"
    >
      {props.children}
    </button>
  )
}

/** The browser's tab strip and toolbar: tabs, back / forward / reload and an editable address bar. */
export default function BrowserChrome({ state, currentUrl, locked, controlling, fullscreen, onFullscreen, fill, onToggleFill, actions }: Props) {
  // The polled state knows the ACTIVE tab (and about:blank on a new one); the pushed address only
  // covers web pages, so it is the fallback while the state isn't available yet.
  const raw = state?.ready ? state.url : currentUrl
  const url = raw === 'about:blank' ? '' : raw
  const [draft, setDraft] = useState('')
  const [editing, setEditing] = useState(false)
  const inputRef = useRef<HTMLInputElement>(null)
  const justFocused = useRef(false)
  const tabs = state?.tabs ?? []
  const ready = state?.ready ?? false

  // Follow the real address while the user isn't typing in the box.
  useEffect(() => {
    if (!editing) setDraft(url)
  }, [url, editing])

  const shown = editing ? draft : url.replace(/^https:\/\//, '').replace(/\/$/, '')
  const insecure = url.startsWith('http://')
  const secure = url.startsWith('https://')

  const submit = () => {
    const text = draft.trim()
    inputRef.current?.blur()
    if (text && text !== url) actions.go(text)
  }

  return (
    <div className="flex-shrink-0" style={{ background: 'rgba(255,255,255,0.88)', backdropFilter: 'blur(12px)', borderBottom: '1px solid rgba(0,0,0,0.07)' }}>
      {/* Tabs */}
      <div role="tablist" aria-label="Browser tabs" className="flex items-end gap-0.5 overflow-x-auto bg-gray-200/80 px-3 pt-2 scrollbar-thin">
        {tabs.map((t) => {
          const active = t.id === state?.active
          return (
            <div
              key={t.id}
              role="tab"
              aria-selected={active}
              tabIndex={0}
              title={t.title || t.url}
              onClick={() => !active && !locked && actions.switchTab(t.id)}
              onKeyDown={(e) => {
                if ((e.key === 'Enter' || e.key === ' ') && !active && !locked) {
                  e.preventDefault()
                  actions.switchTab(t.id)
                }
              }}
              className={cn(
                'group flex h-8 min-w-[110px] max-w-[200px] items-center gap-1.5 rounded-t-lg border-t-2 px-3 text-[12px]',
                active
                  ? 'border-transparent bg-white font-semibold text-gray-900 shadow-[0_-2px_6px_rgba(0,0,0,0.08)]'
                  : 'cursor-pointer border-transparent bg-gray-300/50 text-gray-600 hover:bg-gray-100',
                locked && !active && 'cursor-not-allowed opacity-70',
              )}
            >
              <span className="truncate">{tabLabel(t.title, t.url)}</span>
              {tabs.length > 1 && (
                <button
                  type="button"
                  aria-label={`Close tab ${tabLabel(t.title, t.url)}`}
                  disabled={locked}
                  onClick={(e) => {
                    e.stopPropagation()
                    actions.closeTab(t.id)
                  }}
                  className="ml-auto flex h-4 w-4 flex-shrink-0 items-center justify-center rounded text-gray-400 hover:bg-black/10 hover:text-gray-700 disabled:hidden"
                >
                  <X size={11} />
                </button>
              )}
            </div>
          )
        })}
        <IconButton label={locked ? LOCKED_TIP : 'New tab'} disabled={locked || !ready} onClick={actions.newTab}>
          <Plus size={14} />
        </IconButton>
      </div>

      {/* Toolbar */}
      <div className="flex h-10 items-center gap-1.5 bg-white/70 px-3">
        <IconButton label="Back" disabled={locked || !state?.can_back} onClick={actions.back}>
          <ArrowLeft size={15} />
        </IconButton>
        <IconButton label="Forward" disabled={locked || !state?.can_forward} onClick={actions.forward}>
          <ArrowRight size={15} />
        </IconButton>
        <IconButton label="Reload" disabled={locked || !ready} onClick={actions.reload}>
          <RotateCw size={14} />
        </IconButton>

        <form
          className="mx-1 flex h-7 min-w-0 flex-1 items-center gap-2 rounded-lg bg-black/5 px-3 focus-within:bg-white focus-within:ring-2 focus-within:ring-brand/30"
          onSubmit={(e) => {
            e.preventDefault()
            submit()
          }}
        >
          {secure && <Lock size={11} className="flex-shrink-0 text-gray-400" aria-label="Secure connection" />}
          <input
            ref={inputRef}
            value={shown}
            disabled={locked || !ready}
            placeholder={ready ? 'Search or enter an address' : 'Waiting for the browser…'}
            aria-label="Address bar"
            title={locked ? LOCKED_TIP : url}
            spellCheck={false}
            autoComplete="off"
            onFocus={() => {
              setEditing(true)
              setDraft(url)
              justFocused.current = true
              // After the re-render that swaps in the full address: select it all, so typing replaces it.
              requestAnimationFrame(() => inputRef.current?.select())
            }}
            onMouseUp={(e) => {
              if (justFocused.current) { e.preventDefault(); justFocused.current = false }   // keep the selection after a click
            }}
            onBlur={() => setEditing(false)}
            onChange={(e) => setDraft(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === 'Escape') {
                setDraft(url)
                e.currentTarget.blur()
              }
            }}
            className="min-w-0 flex-1 bg-transparent text-[12px] text-gray-700 outline-none placeholder:text-gray-400 disabled:cursor-not-allowed"
          />
          {insecure && !editing && (
            <span className="flex-shrink-0 rounded bg-amber-100 px-1.5 py-px text-[10px] font-medium text-amber-700">Not secure</span>
          )}
        </form>

        {controlling && (
          <span className="flex-shrink-0 rounded-full bg-blue-100 px-2.5 py-0.5 text-[11px] font-semibold text-blue-700">
            You're in control
          </span>
        )}
        <IconButton label={fill ? 'Fit whole page' : 'Fill the pane (zoomed in)'} onClick={onToggleFill}>
          {fill ? <Shrink size={14} /> : <Expand size={14} />}
        </IconButton>
        <IconButton label={fullscreen ? 'Exit full screen' : 'Full screen'} onClick={onFullscreen}>
          {fullscreen ? <Minimize2 size={14} /> : <Maximize2 size={14} />}
        </IconButton>
      </div>
    </div>
  )
}
