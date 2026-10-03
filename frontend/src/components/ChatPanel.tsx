import {
  useState, useRef, useEffect, useCallback, lazy, Suspense, KeyboardEvent, ChangeEvent, type ReactNode,
} from 'react'
import * as DropdownMenu from '@radix-ui/react-dropdown-menu'
import { Tooltip, TooltipContent, TooltipTrigger, TooltipProvider } from '@/components/ui/tooltip'
import { Button } from '@/components/ui/button'
import { Slider } from '@/components/ui/slider'
import CopyButton from '@/components/CopyButton'
import ModelTiers from '@/components/ModelTiers'
import VisionToggle from '@/components/VisionToggle'
import SpeedSetting from '@/components/SpeedSetting'
import TierPicker, { type TierChoice } from '@/components/TierPicker'
import { cn, formatDuration, formatDate, plural } from '@/lib/utils'
import {
  Plus, Settings, ChevronDown, Square, Trash2, ChevronRight,
  Briefcase, Newspaper, ShoppingBag, MapPin, ArrowUp, X,
  MessageSquare, History, Eye, EyeOff, Pencil, Search, Copy, ChevronsRight, Maximize2, Minimize2, Globe, Sparkles, List, Table2, BookMarked, Bell,
} from 'lucide-react'
import type { Turn, Status, SessionInfo, Settings as SettingsType, FormatOption, Verdict } from '@/types'

// The markdown renderer is the biggest single dependency and only matters once there is an answer to show.
const Markdown = lazy(() => import('@/components/Markdown'))

// 'unknown' (no independent check was possible) shows nothing rather than a made-up rating
const hasVerdict = (v: Verdict | undefined): v is 'pass' | 'fail' => v === 'pass' || v === 'fail'

// ─── Props ────────────────────────────────────────────────────────────────────

interface ChatPanelProps {
  messages: Turn[]
  status: Status
  connected: boolean
  currentSessionId: string | null
  sessions: SessionInfo[]
  settingsOpen: boolean
  onSettingsOpen: (v: boolean) => void
  onOpenPlaybooks: () => void
  onOpenInbox: () => void
  unread: number
  onSendTask: (task: string, fmt: FormatOption, tier: TierChoice) => void
  onStop: () => void
  onContinue: (extraSteps: number) => void
  onRetry: () => void
  onNewSession: () => void
  onSwitchSession: (id: string) => void
  onDeleteSession: (id: string) => void
  onRenameSession: (id: string, title: string) => void
  approvalSlot?: ReactNode        // a pending approval card, shown just above the task box
  page?: { url: string; title: string } | null   // the page open in the browser, for the quick actions
  wide?: boolean
  onToggleWide?: () => void
  onCollapse?: () => void         // hide this panel (wide windows only)
}

// ─── Brand avatar for agent messages ─────────────────────────────────────────

function AgentAvatar() {
  return (
    <img src="/favicon.svg" width="28" height="28" alt="" className="flex-shrink-0" />
  )
}

// ─── Thinking dots ─────────────────────────────────────────────────────────────

function ThinkingDots({ stepCount, logs }: { stepCount?: number; logs?: string[] }) {
  const [open, setOpen] = useState(false)
  const listRef = useRef<HTMLDivElement>(null)
  useEffect(() => {
    if (open && listRef.current) listRef.current.scrollTop = listRef.current.scrollHeight
  }, [open, logs?.length])
  return (
    <div className="space-y-2 py-1">
      <div className="flex items-center gap-2.5">
        <div className="flex items-center gap-1">
          {[0, 160, 320].map((delay) => (
            <span
              key={delay}
              className="w-1.5 h-1.5 rounded-full bg-brand inline-block animate-dot-bounce"
              style={{ animationDelay: `${delay}ms` }}
            />
          ))}
        </div>
        <span className="text-sm text-gray-500">Working…</span>
        {(stepCount ?? 0) > 0 && (
          <span className="text-xs text-gray-400 bg-gray-100 px-1.5 py-0.5 rounded-full">
            step {stepCount}
          </span>
        )}
      </div>
      {logs && logs.length > 0 && (
        <div className="pl-[22px]">
          <button
            type="button"
            onClick={() => setOpen(!open)}
            aria-expanded={open}
            className="flex max-w-full items-center gap-1 text-xs text-gray-400 hover:text-gray-600"
          >
            <ChevronRight size={12} className={cn('flex-shrink-0 transition-transform duration-150', open && 'rotate-90')} />
            <span aria-hidden="true" title={logs[logs.length - 1]} className="truncate max-w-[240px]">
              {open ? `Details · ${plural(logs.length, 'line')}` : logs[logs.length - 1]}
            </span>
          </button>
          {open && (
            <div
              ref={listRef}
              aria-hidden="true"
              className="mt-1.5 max-h-40 space-y-0.5 overflow-y-auto rounded-xl border border-gray-100 bg-gray-50 p-2.5 scrollbar-thin"
            >
              {logs.slice(-200).map((log, i) => (
                <p key={i} title={log} className="truncate font-mono text-[11px] leading-relaxed text-gray-500">
                  {log}
                </p>
              ))}
            </div>
          )}
        </div>
      )}
    </div>
  )
}

// ─── Message bubble ────────────────────────────────────────────────────────────

function MessageBubble({
  turn, onRetry, onOpenSettings,
}: {
  turn: Turn
  onRetry?: () => void           // only given for the latest failed run
  onOpenSettings: () => void
}) {
  const [logsOpen, setLogsOpen] = useState(false)
  const [openLines, setOpenLines] = useState<Set<number>>(new Set())
  const [copied, setCopied] = useState(false)
  const toggleLine = (i: number) =>
    setOpenLines((prev) => {
      const next = new Set(prev)
      if (!next.delete(i)) next.add(i)
      return next
    })
  const copyLogs = async () => {
    try {
      await navigator.clipboard.writeText((turn.logs ?? []).join(String.fromCharCode(10)))
      setCopied(true)
      setTimeout(() => setCopied(false), 1500)
    } catch { /* clipboard not available: nothing to do */ }
  }

  if (turn.role === 'system') {
    return (
      <div className="flex justify-center my-3 animate-fade-in">
        <span className="px-3 py-1 rounded-full bg-gray-100 text-gray-500 text-xs font-medium">
          {turn.content}
        </span>
      </div>
    )
  }

  if (turn.role === 'user') {
    return (
      <div className="flex justify-end gap-2 animate-msg-in">
        <div className="max-w-[78%] flex flex-col items-end">
          <div className="bg-brand-muted border border-brand-subtle px-4 py-2.5 rounded-[18px] rounded-br-[4px]">
            <p className="text-[14.5px] text-gray-900 leading-relaxed whitespace-pre-wrap">
              {turn.content}
            </p>
          </div>
          <span className="text-[11px] text-gray-400 mt-1 pr-0.5">{turn.ts}</span>
        </div>
      </div>
    )
  }

  // Agent message
  return (
    <div className="flex gap-3 animate-msg-in">
      <div className="pt-0.5">
        <AgentAvatar />
      </div>
      <div className="flex-1 min-w-0">
        {turn.pending ? (
          <ThinkingDots stepCount={turn.stepCount} logs={turn.logs} />
        ) : (
          <div className="space-y-2">
            {turn.error ? (
              <div className="rounded-xl border border-red-200 bg-red-50 px-3.5 py-3">
                <p className="text-[14px] leading-relaxed text-red-800 whitespace-pre-wrap">{turn.content}</p>
                <div className="mt-2.5 flex flex-wrap gap-2">
                  {/api key|settings|provider|credential/i.test(turn.content) && (
                    <button
                      onClick={onOpenSettings}
                      className="rounded-lg bg-brand px-3 py-1.5 text-xs font-semibold text-white hover:bg-brand-hover"
                    >
                      Open Settings
                    </button>
                  )}
                  {onRetry && (
                    <button
                      onClick={onRetry}
                      className="rounded-lg border border-red-200 bg-white px-3 py-1.5 text-xs font-semibold text-red-700 hover:bg-red-100"
                    >
                      Try again
                    </button>
                  )}
                </div>
              </div>
            ) : (
              turn.content ? (
                <Suspense fallback={<p className="text-[14.5px] text-gray-800 whitespace-pre-wrap">{turn.content}</p>}>
                  <Markdown text={turn.content} />
                </Suspense>
              ) : (
                <p className="text-[14.5px] text-gray-500">(no result)</p>
              )
            )}

            {/* Independent check + meta row */}
            {(hasVerdict(turn.verdict) || turn.duration !== undefined || (!turn.error && !!turn.content)) && (
              <div className="flex items-center gap-2 flex-wrap">
                {hasVerdict(turn.verdict) && (
                  <span
                    title={turn.reason || undefined}
                    className={cn(
                      'inline-flex items-center px-2 py-0.5 rounded-md text-xs font-semibold',
                      turn.verdict === 'pass'
                        ? 'text-green-700 bg-green-100'
                        : 'text-amber-700 bg-amber-100',
                    )}
                  >
                    {turn.verdict === 'pass' ? 'Checked ✓' : 'Needs review'}
                  </span>
                )}
                {turn.duration !== undefined && (
                  <span className="text-xs text-gray-400">{formatDuration(turn.duration)}</span>
                )}
                {(turn.retries ?? 0) > 0 && (
                  <span className="text-xs text-gray-400">
                    {turn.retries} retr{turn.retries === 1 ? 'y' : 'ies'}
                  </span>
                )}
                {!turn.error && !!turn.content && <CopyButton text={turn.content} label="Copy answer" className="ml-auto" />}
              </div>
            )}
            {turn.verdict === 'fail' && turn.reason && (
              <p className="text-xs leading-snug text-amber-700">{turn.reason}</p>
            )}

            {/* Log toggle */}
            {turn.logs && turn.logs.length > 0 && (
              <div>
                <button
                  onClick={() => setLogsOpen(!logsOpen)}
                  className="flex items-center gap-1 text-xs text-gray-400 hover:text-gray-600 transition-colors"
                >
                  <ChevronRight
                    size={12}
                    className={cn('transition-transform duration-150', logsOpen && 'rotate-90')}
                  />
                  {turn.stepCount ? `${plural(turn.stepCount, 'step')} · ` : ''}details
                </button>
                {logsOpen && (
                  <div className="mt-2 rounded-xl bg-gray-50 border border-gray-100 p-3 max-h-44 overflow-y-auto scrollbar-thin space-y-0.5">
                    <button
                      onClick={copyLogs}
                      className="mb-1 flex items-center gap-1 text-[11px] text-gray-400 hover:text-gray-600"
                    >
                      <Copy size={11} /> {copied ? 'Copied' : 'Copy all'}
                    </button>
                    {turn.logs.map((log, i) => (
                      <p
                        key={i}
                        onClick={() => toggleLine(i)}
                        title={openLines.has(i) ? 'Click to shorten' : 'Click to read the whole line'}
                        className={cn(
                          'cursor-pointer text-[11px] text-gray-500 font-mono leading-relaxed',
                          openLines.has(i) ? 'whitespace-pre-wrap break-words' : 'truncate',
                        )}
                      >
                        {log}
                      </p>
                    ))}
                  </div>
                )}
              </div>
            )}

            <span className="text-[11px] text-gray-400 block">{turn.ts}</span>
          </div>
        )}
      </div>
    </div>
  )
}

// ─── Welcome / empty state ─────────────────────────────────────────────────────

const SUGGESTIONS = [
  {
    icon: Briefcase,
    title: 'Job Search',
    desc: 'Find developer jobs on LinkedIn',
    prompt: 'Go to LinkedIn and find Python developer jobs in Lagos, Nigeria',
  },
  {
    icon: Newspaper,
    title: 'News Summary',
    desc: 'Summarize the latest AI stories',
    prompt: 'Search for the latest AI news and summarize the top 5 stories today',
  },
  {
    icon: ShoppingBag,
    title: 'Product Research',
    desc: 'Compare products and find deals',
    prompt: 'Find the best-rated wireless headphones under $100 on Amazon',
  },
  {
    icon: MapPin,
    title: 'Local Discovery',
    desc: 'Find places and businesses nearby',
    prompt: 'Find highly-rated coffee shops near Victoria Island, Lagos on Google Maps',
  },
]

function WelcomeState({ onSelect }: { onSelect: (p: string) => void }) {
  return (
    <div className="flex flex-col items-center justify-center min-h-full px-5 py-8 select-none">
      <div className="mb-7 text-center">
        <div className="flex justify-center mb-4">
          <img src="/favicon.svg" width="48" height="48" alt="" />
        </div>
        <h2 className="text-lg font-semibold text-gray-900">What should I do?</h2>
        <p className="text-sm text-gray-500 mt-1.5 leading-snug">
          Give me any task — I'll handle it in the browser
        </p>
      </div>

      <div className="flex w-full flex-col gap-1.5">
        {SUGGESTIONS.map((s) => (
          <button
            key={s.prompt}
            onClick={() => onSelect(s.prompt)}
            className="group flex items-center gap-3 rounded-xl border border-gray-100 bg-white px-3 py-2.5 text-left shadow-subtle transition-all duration-150 hover:border-brand/25 hover:bg-brand-tint/50"
          >
            <span className="flex h-7 w-7 flex-shrink-0 items-center justify-center rounded-lg bg-surface-subtle transition-colors group-hover:bg-brand/10">
              <s.icon size={14} className="text-gray-500 transition-colors group-hover:text-brand" />
            </span>
            <span className="min-w-0">
              <span className="block text-xs font-semibold leading-snug text-gray-700">{s.title}</span>
              <span className="block truncate text-xs leading-snug text-gray-400">{s.desc}</span>
            </span>
          </button>
        ))}
      </div>
    </div>
  )
}

// ─── Continue bar (shown when paused) ─────────────────────────────────────────

function ContinueBar({
  onContinue,
  onStop,
}: {
  onContinue: (n: number) => void
  onStop: () => void
}) {
  const [extra, setExtra] = useState(10)
  return (
    <div className="mx-4 mb-2 rounded-xl bg-amber-50 border border-amber-200 p-3.5 flex-shrink-0">
      <div className="flex items-center gap-1.5 mb-3">
        <div className="w-1.5 h-1.5 rounded-full bg-amber-500" />
        <span className="text-xs font-semibold text-amber-700">Step limit reached</span>
      </div>
      <div className="flex items-center gap-3 mb-3">
        <span className="text-xs text-amber-700 w-28 flex-shrink-0">
          Continue for {extra} more steps
        </span>
        <Slider
          value={[extra]}
          onValueChange={([v]) => setExtra(v)}
          min={10}
          max={200}
          step={10}
          className="flex-1"
        />
      </div>
      <div className="flex items-center gap-2">
        <Button size="sm" onClick={() => onContinue(extra)} className="flex-1 text-xs h-7">
          Continue
        </Button>
        <Button size="sm" variant="outline" onClick={onStop} className="text-xs h-7">
          Stop
        </Button>
      </div>
    </div>
  )
}

// ─── Settings panel ────────────────────────────────────────────────────────────

async function loadSettings(): Promise<SettingsType | null> {
  try {
    const res = await fetch('/settings')
    if (res.ok) return res.json()
  } catch {}
  return null
}

interface SaveResult {
  ok: boolean
  errors?: Record<string, string>   // setting name -> why it was refused ('' = not about one field)
}

async function saveSettings(s: SettingsType): Promise<SaveResult> {
  const body: Partial<SettingsType> = { ...s }
  delete body.UNREADABLE
  delete body.PROVIDERS   // the provider table only flows server -> UI
  delete body.TIERS
  try {
    const res = await fetch('/settings', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    })
    if (res.ok) return { ok: true }
    const data = await res.json().catch(() => null)
    return { ok: false, errors: data?.errors ?? { '': 'The server refused the settings.' } }
  } catch {
    return { ok: false, errors: { '': "Couldn't reach the server." } }
  }
}

const SETTING_NAMES: Record<string, string> = {
  LLM_PROVIDER: 'Provider',
  LLM_MODEL: 'Model',
  LLM_VISION: 'Vision',
  AGENT_SPEED: 'Speed',
  LLM_FAST_PROVIDER: 'Fast model provider',
  LLM_FAST_MODEL: 'Fast model',
  LLM_STRONG_PROVIDER: 'Strong model provider',
  LLM_STRONG_MODEL: 'Strong model',
  LLM_ROUTER: 'Router',
  ANTHROPIC_API_KEY: 'Anthropic API key',
  OPENAI_API_KEY: 'OpenAI API key',
  GEMINI_API_KEY: 'Gemini API key',
  DEEPSEEK_API_KEY: 'DeepSeek API key',
  QWEN_API_KEY: 'Qwen API key',
  LINKEDIN_EMAIL: 'LinkedIn email',
  LINKEDIN_PASSWORD: 'LinkedIn password',
  MAX_STEPS: 'Max steps',
  CONTEXT_TURNS: 'Context turns',
  APPROVAL_MODE: 'Approval mode',
}

function SectionLabel({ children }: { children: React.ReactNode }) {
  return (
    <div className="mt-5 mb-2.5 first:mt-0 [&:first-child>hr]:hidden">
      <hr className="border-gray-200 mb-3" />
      <p className="text-[11px] font-semibold text-gray-600">{children}</p>
    </div>
  )
}

function FieldRow({
  label,
  hint,
  children,
}: {
  label: string
  hint?: string
  children: React.ReactNode
}) {
  return (
    <div className="mb-5">
      <label className="block text-xs font-medium text-gray-700 mb-1.5">{label}</label>
      {children}
      {hint && <p className="text-[11px] text-gray-500 mt-1.5">{hint}</p>}
    </div>
  )
}

const INPUT_CLASS =
  'w-full h-8 px-3 text-xs bg-white border border-gray-200 rounded-lg text-gray-800 placeholder:text-gray-400 focus:outline-none focus:ring-2 focus:ring-brand/30 focus:border-brand/40 transition-all'

interface DropdownOption {
  value: string
  label: string
}

function ProviderDropdown({
  value,
  options,
  onChange,
}: {
  value: string
  options: DropdownOption[]
  onChange: (v: string) => void
}) {
  return (
    <DropdownMenu.Root>
      <DropdownMenu.Trigger asChild>
        <button className="w-full h-8 px-3 flex items-center justify-between text-xs bg-white border border-gray-200 rounded-lg text-gray-800 hover:border-gray-300 focus:outline-none focus:ring-2 focus:ring-brand/30 transition-all">
          <span>{options.find((o) => o.value === value)?.label ?? value}</span>
          <ChevronDown size={12} className="text-gray-400" />
        </button>
      </DropdownMenu.Trigger>
      <DropdownMenu.Portal>
        <DropdownMenu.Content
          side="bottom"
          align="start"
          sideOffset={4}
          className="z-50 w-[--radix-dropdown-menu-trigger-width] bg-white rounded-xl shadow-card border border-gray-100 p-1 animate-fade-in"
        >
          {options.map((opt) => (
            <DropdownMenu.Item
              key={opt.value}
              onSelect={() => onChange(opt.value)}
              className={cn(
                'px-3 py-1.5 rounded-lg text-xs cursor-pointer outline-none transition-colors',
                value === opt.value
                  ? 'bg-brand-tint text-brand font-medium'
                  : 'text-gray-700 hover:bg-gray-50',
              )}
            >
              {opt.label}
            </DropdownMenu.Item>
          ))}
        </DropdownMenu.Content>
      </DropdownMenu.Portal>
    </DropdownMenu.Root>
  )
}

const APPROVAL_CHOICES = [
  {
    value: 'ask',
    title: 'Ask me first',
    hint: 'Pauses for your OK before it sends, posts, connects, buys or deletes.',
  },
  {
    value: 'auto',
    title: 'Auto',
    hint:
      "For when you're away. It sends/posts/connects without asking, but only on the site your task " +
      'names, and skips JavaScript and odd links. Everything it does is logged in the chat.',
  },
]

function ApprovalModeField({ value, onChange }: { value: string; onChange: (v: string) => void }) {
  const current = value === 'auto' ? 'auto' : 'ask'
  return (
    <div role="radiogroup" aria-label="When the agent wants to send, post or buy" className="space-y-2">
      {APPROVAL_CHOICES.map((c) => (
        <button
          key={c.value}
          type="button"
          role="radio"
          aria-checked={current === c.value}
          onClick={() => onChange(c.value)}
          className={cn(
            'w-full text-left rounded-lg border px-3 py-2 transition-colors focus:outline-none focus:ring-2 focus:ring-brand/30',
            current === c.value ? 'border-brand bg-brand-tint' : 'border-gray-200 bg-white hover:border-gray-300',
          )}
        >
          <span className={cn('block text-xs font-medium', current === c.value ? 'text-brand' : 'text-gray-800')}>
            {c.title}
          </span>
          <span className="mt-0.5 block text-[11px] leading-snug text-gray-500">{c.hint}</span>
        </button>
      ))}
      {value === 'off' && (
        <p className="text-[11px] text-amber-700">
          Currently set to "off" in the .env file (no prompts at all). Choosing an option here replaces it.
        </p>
      )}
    </div>
  )
}

function SettingsPanel({ onClose }: { onClose: () => void }) {
  const [settings, setSettings] = useState<SettingsType | null>(null)
  const [saving, setSaving] = useState(false)
  const [saved, setSaved] = useState(false)
  const [showLinkedIn, setShowLinkedIn] = useState(false)
  const [showKeys, setShowKeys] = useState<Record<string, boolean>>({})
  const [errors, setErrors] = useState<Record<string, string>>({})

  useEffect(() => {
    loadSettings().then(setSettings)
  }, [])

  const update = <K extends keyof SettingsType>(k: K, v: SettingsType[K]) =>
    setSettings((s) => (s ? { ...s, [k]: v } : s))

  const handleSave = async () => {
    if (!settings) return
    setSaving(true)
    const result = await saveSettings(settings)
    setSaving(false)
    if (result.ok) {
      setErrors({})
      loadSettings().then((fresh) => fresh && setSettings(fresh))   // masks, and clears warnings that no longer apply
      setSaved(true)
      setTimeout(() => setSaved(false), 2000)
    } else {
      setErrors(result.errors ?? { '': 'Could not save.' })
    }
  }

  if (!settings) {
    return (
      <div className="flex items-center justify-center h-40">
        <div className="flex gap-1">
          {[0, 150, 300].map((d) => (
            <span
              key={d}
              className="w-1.5 h-1.5 rounded-full bg-gray-300 animate-dot-bounce"
              style={{ animationDelay: `${d}ms` }}
            />
          ))}
        </div>
      </div>
    )
  }

  const providerList = settings.PROVIDERS ?? []
  const active = providerList.find((p) => p.id === settings.LLM_PROVIDER)
  // Blank model = the provider's default (shown by name); a model typed into .env by hand is kept visible
  const modelOptions: DropdownOption[] = active
    ? [
        { value: '', label: `Default (${active.default_model})` },
        ...active.models.map((m) => ({ value: m, label: m })),
        ...(settings.LLM_MODEL && !active.models.includes(settings.LLM_MODEL)
          ? [{ value: settings.LLM_MODEL, label: settings.LLM_MODEL }]
          : []),
      ]
    : []

  return (
    <div className="flex flex-col h-full">
      {/* Header */}
      <div className="flex items-center justify-between px-5 py-4 border-b border-gray-100 flex-shrink-0">
        <h3 className="text-sm font-semibold text-gray-900">Settings</h3>
        <button
          onClick={onClose}
          aria-label="Close settings"
          className="w-7 h-7 rounded-lg hover:bg-gray-100 flex items-center justify-center text-gray-500 hover:text-gray-700 transition-colors"
        >
          <X size={15} />
        </button>
      </div>

      {/* Body */}
      <div className="flex-1 overflow-y-auto scrollbar-thin px-5 py-4">
        <SectionLabel>AI Model</SectionLabel>
        <FieldRow label="Provider">
          <ProviderDropdown
            value={settings.LLM_PROVIDER}
            options={providerList.map((p) => ({ value: p.id, label: p.label }))}
            onChange={(v) => {
              update('LLM_PROVIDER', v)
              update('LLM_MODEL', '')   // a new provider starts on its own default model
              update('LLM_VISION', '')  // and the vision answer was about the old one
            }}
          />
        </FieldRow>
        <FieldRow label="Model" hint="Leave on Default unless you want a specific model">
          <ProviderDropdown
            value={settings.LLM_MODEL}
            options={modelOptions}
            onChange={(v) => {
              update('LLM_MODEL', v)
              update('LLM_VISION', '')
            }}
          />
        </FieldRow>
        {active && <VisionToggle providerId={active.id} model={settings.LLM_MODEL || active.default_model} value={settings.LLM_VISION ?? ''} onChange={(v) => update('LLM_VISION', v)} />}
        {active && <SpeedSetting providerId={active.id} value={settings.AGENT_SPEED ?? ''} onChange={(v) => update('AGENT_SPEED', v)} />}
        {/* API key for the active provider lives here */}
        {(() => {
          if (!active) return null
          const activeKey = (active.key_env as keyof SettingsType)
          return (
            <FieldRow
              label={`${active.label} API key`}
              hint="Clear the box and save to remove the stored key"
            >
              <div className="relative">
                <input
                  type={showKeys[activeKey] ? 'text' : 'password'}
                  value={settings[activeKey] as string}
                  onChange={(e: ChangeEvent<HTMLInputElement>) => update(activeKey, e.target.value as never)}
                  placeholder="sk-…"
                  className={cn(INPUT_CLASS, 'pr-8')}
                />
                <button
                  type="button"
                  onClick={() => setShowKeys((p) => ({ ...p, [activeKey]: !p[activeKey] }))}
                  className="absolute right-2 top-1/2 -translate-y-1/2 text-gray-400 hover:text-gray-600"
                >
                  {showKeys[activeKey] ? <EyeOff size={12} /> : <Eye size={12} />}
                </button>
              </div>
            </FieldRow>
          )
        })()}

        <ModelTiers settings={settings} update={update} />

        <SectionLabel>LinkedIn</SectionLabel>
        <FieldRow label="Email">
          <input
            type="email"
            value={settings.LINKEDIN_EMAIL}
            onChange={(e: ChangeEvent<HTMLInputElement>) =>
              update('LINKEDIN_EMAIL', e.target.value)
            }
            placeholder="you@example.com"
            className={INPUT_CLASS}
          />
        </FieldRow>
        <FieldRow label="Password">
          <div className="relative">
            <input
              type={showLinkedIn ? 'text' : 'password'}
              value={settings.LINKEDIN_PASSWORD}
              onChange={(e: ChangeEvent<HTMLInputElement>) =>
                update('LINKEDIN_PASSWORD', e.target.value)
              }
              placeholder="••••••••"
              className={cn(INPUT_CLASS, 'pr-8')}
            />
            <button
              type="button"
              onClick={() => setShowLinkedIn(!showLinkedIn)}
              className="absolute right-2 top-1/2 -translate-y-1/2 text-gray-400 hover:text-gray-600"
            >
              {showLinkedIn ? <EyeOff size={12} /> : <Eye size={12} />}
            </button>
          </div>
        </FieldRow>

        <SectionLabel>Task Behavior</SectionLabel>
        <FieldRow
          label="When it wants to send, post or buy"
          hint="Applies from the next task you start"
        >
          <ApprovalModeField
            value={settings.APPROVAL_MODE}
            onChange={(v) => update('APPROVAL_MODE', v)}
          />
        </FieldRow>
        <FieldRow
          label={`Max steps: ${settings.MAX_STEPS}`}
          hint="Maximum browser actions before pausing"
        >
          <Slider
            value={[settings.MAX_STEPS]}
            onValueChange={([v]) => update('MAX_STEPS', v)}
            min={1}
            max={100}
            step={1}
          />
        </FieldRow>
        <FieldRow
          label={`Context turns: ${settings.CONTEXT_TURNS}`}
          hint="How many past turns the AI remembers"
        >
          <Slider
            value={[settings.CONTEXT_TURNS]}
            onValueChange={([v]) => update('CONTEXT_TURNS', v)}
            min={0}
            max={20}
            step={1}
          />
        </FieldRow>
      </div>

      {/* Footer */}
      <div className="px-5 py-4 border-t border-gray-100 flex-shrink-0">
        {(settings.UNREADABLE?.length ?? 0) > 0 && (
          <div role="status" className="mb-3 rounded-lg border border-amber-300 bg-amber-50 px-3 py-2 text-[11px] leading-snug text-amber-800">
            Saved {settings.UNREADABLE!.map((k) => SETTING_NAMES[k] ?? k).join(', ')} can't be read by this Windows
            account (the settings file came from another computer or user). Enter it again and save.
          </div>
        )}
        {Object.keys(errors).length > 0 && (
          <div role="alert" className="mb-3 rounded-lg border border-red-200 bg-red-50 px-3 py-2 text-[11px] leading-snug text-red-700">
            <p className="font-semibold">Not saved. Please fix:</p>
            <ul className="mt-1 space-y-0.5">
              {Object.entries(errors).map(([key, why]) => (
                <li key={key || 'general'}>{key ? `${SETTING_NAMES[key] ?? key}: ${why}` : why}</li>
              ))}
            </ul>
          </div>
        )}
        <Button onClick={handleSave} disabled={saving} className="w-full h-9 text-sm">
          {saving ? 'Saving…' : saved ? '✓ Saved' : 'Save settings'}
        </Button>
      </div>
    </div>
  )
}

// ─── History tab ───────────────────────────────────────────────────────────────

function HistoryTab({
  sessions,
  currentSessionId,
  onSwitch,
  onDelete,
  onRename,
}: {
  sessions: SessionInfo[]
  currentSessionId: string | null
  onSwitch: (id: string) => void
  onDelete: (id: string) => void
  onRename: (id: string, title: string) => void
}) {
  const [query, setQuery] = useState('')
  const [editing, setEditing] = useState<string | null>(null)
  const [draft, setDraft] = useState('')
  // The search box only exists with 4+ chats; a leftover query must not hide the list after deletes.
  const shown = query.trim() && sessions.length >= 4
    ? sessions.filter((s) => (s.title || 'Untitled session').toLowerCase().includes(query.trim().toLowerCase()))
    : sessions
  const commitRename = (id: string, original: string) => {
    const t = draft.trim()
    setEditing(null)
    if (t && t !== original) onRename(id, t)
  }

  if (sessions.length === 0) {
    return (
      <div className="flex flex-col items-center justify-center flex-1 gap-2 text-gray-400 select-none">
        <History size={28} className="opacity-30" />
        <p className="text-sm text-gray-500">No sessions yet</p>
        <p className="text-xs text-gray-400">Your chat history will appear here</p>
      </div>
    )
  }

  return (
    <TooltipProvider>
      <div className="flex-1 overflow-y-auto scrollbar-thin px-3 py-3 space-y-1">
        {sessions.length >= 4 && (
          <div className="relative mb-2">
            <Search size={13} className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-gray-400" />
            <input
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              aria-label="Search chats"
              placeholder="Search chats"
              className="w-full rounded-xl border border-gray-100 bg-gray-50 py-2 pl-8 pr-3 text-xs text-gray-700 outline-none focus:border-brand/30"
            />
          </div>
        )}
        {shown.length === 0 && <p className="px-2 py-6 text-center text-xs text-gray-400">No chats match “{query}”.</p>}
        {shown.map((s) => {
          const active = s.id === currentSessionId
          return (
            <div
              key={s.id}
              onClick={() => editing !== s.id && onSwitch(s.id)}
              className={cn(
                'group flex items-start gap-3 px-3 py-2.5 rounded-xl cursor-pointer transition-all',
                active
                  ? 'bg-brand-tint border border-brand/20'
                  : 'hover:bg-gray-50 border border-transparent',
              )}
            >
              <div
                className={cn(
                  'w-7 h-7 rounded-lg flex items-center justify-center flex-shrink-0 mt-0.5',
                  active ? 'bg-brand/15' : 'bg-gray-100',
                )}
              >
                <MessageSquare size={13} className={active ? 'text-brand' : 'text-gray-400'} />
              </div>
              <div className="flex-1 min-w-0">
                {editing === s.id ? (
                  <input
                    autoFocus
                    value={draft}
                    maxLength={60}
                    aria-label="Chat name"
                    onChange={(e) => setDraft(e.target.value)}
                    onClick={(e) => e.stopPropagation()}
                    onBlur={() => commitRename(s.id, s.title)}
                    onKeyDown={(e) => {
                      if (e.key === 'Enter') commitRename(s.id, s.title)
                      if (e.key === 'Escape') setEditing(null)
                    }}
                    className="w-full rounded-md border border-brand/30 bg-white px-1.5 py-0.5 text-xs font-semibold text-gray-800 outline-none"
                  />
                ) : (
                  <p
                    className={cn(
                      'text-xs font-semibold truncate',
                      active ? 'text-brand' : 'text-gray-700',
                    )}
                  >
                    {s.title || 'Untitled session'}
                  </p>
                )}
                <div className="flex items-center gap-1.5 mt-0.5">
                  <span className="text-[11px] text-gray-400">{formatDate(s.updated_at)}</span>
                  {s.turns > 0 && (
                    <>
                      <span className="text-[11px] text-gray-300">·</span>
                      <span className="text-[11px] text-gray-400">{plural(s.turns, 'turn')}</span>
                    </>
                  )}
                  {s.last_verdict !== undefined && hasVerdict(s.last_verdict) && (
                    <>
                      <span className="text-[11px] text-gray-300">·</span>
                      <span
                        className={cn(
                          'text-[11px] font-medium',
                          s.last_verdict === 'pass' ? 'text-green-600' : 'text-amber-600',
                        )}
                      >
                        {s.last_verdict === 'pass' ? 'Checked ✓' : 'Needs review'}
                      </span>
                    </>
                  )}
                  {s.last_verdict === undefined && s.last_score !== undefined && (
                    <>
                      <span className="text-[11px] text-gray-300">·</span>
                      <span
                        className={cn(
                          'text-[11px] font-medium',
                          s.last_score >= 8
                            ? 'text-green-600'
                            : s.last_score >= 6
                            ? 'text-amber-600'
                            : 'text-red-500',
                        )}
                      >
                        {s.last_score}/10
                      </span>
                    </>
                  )}
                </div>
              </div>
              <Tooltip>
                <TooltipTrigger asChild>
                  <button
                    aria-label="Rename chat"
                    onClick={(e) => {
                      e.stopPropagation()
                      setDraft(s.title || '')
                      setEditing(s.id)
                    }}
                    className="opacity-0 group-hover:opacity-100 focus:opacity-100 w-6 h-6 rounded-lg hover:bg-gray-200 flex items-center justify-center text-gray-400 hover:text-gray-600 transition-all flex-shrink-0 mt-0.5"
                  >
                    <Pencil size={12} />
                  </button>
                </TooltipTrigger>
                <TooltipContent side="left">Rename chat</TooltipContent>
              </Tooltip>
              <Tooltip>
                <TooltipTrigger asChild>
                  <button
                    aria-label="Delete chat"
                    onClick={(e) => {
                      e.stopPropagation()
                      onDelete(s.id)
                    }}
                    className="opacity-0 group-hover:opacity-100 focus:opacity-100 w-6 h-6 rounded-lg hover:bg-red-100 flex items-center justify-center text-gray-400 hover:text-red-500 transition-all flex-shrink-0 mt-0.5"
                  >
                    <Trash2 size={12} />
                  </button>
                </TooltipTrigger>
                <TooltipContent side="left">Delete session</TooltipContent>
              </Tooltip>
            </div>
          )
        })}
      </div>
    </TooltipProvider>
  )
}

// ─── Model picker (in the task box) ───────────────────────────────────────────

function ModelPicker({ disabled, refreshKey, onOpenSettings }: { disabled: boolean; refreshKey: number; onOpenSettings: () => void }) {
  const [settings, setSettings] = useState<SettingsType | null>(null)
  const [failed, setFailed] = useState(false)
  useEffect(() => {
    loadSettings().then(setSettings)
  }, [refreshKey])

  const providers = settings?.PROVIDERS ?? []
  const current = providers.find((p) => p.id === settings?.LLM_PROVIDER) ?? providers[0]
  if (!settings || !current) return null
  const hasKey = (key: string) => !!(settings as unknown as Record<string, string>)[key]
  const model = settings.LLM_MODEL || current.default_model

  const choose = async (providerId: string, chosen: string, isDefault: boolean) => {
    const res = await fetch('/settings', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ LLM_PROVIDER: providerId, LLM_MODEL: isDefault ? '' : chosen }),
    }).catch(() => null)
    if (res && res.ok) {
      setSettings((s) => (s ? { ...s, LLM_PROVIDER: providerId, LLM_MODEL: isDefault ? '' : chosen } : s))
    } else {
      setFailed(true)
      setTimeout(() => setFailed(false), 3000)
    }
  }

  return (
    <DropdownMenu.Root>
      <DropdownMenu.Trigger asChild>
        <button
          disabled={disabled}
          aria-label={`Model: ${current.label} ${model}`}
          title={disabled ? 'Stop the task to change the model' : 'Choose the AI model'}
          className="flex max-w-[170px] items-center gap-1 rounded px-1 py-0.5 text-xs text-gray-400 transition-colors hover:text-gray-600 disabled:cursor-not-allowed disabled:opacity-60"
        >
          <Sparkles size={11} className="flex-shrink-0" />
          <span className="truncate">{failed ? "Couldn't switch" : `${current.label} · ${model}`}</span>
          <ChevronDown size={11} className="flex-shrink-0" />
        </button>
      </DropdownMenu.Trigger>
      <DropdownMenu.Portal>
        <DropdownMenu.Content
          side="top"
          align="start"
          sideOffset={6}
          className="z-50 max-h-80 min-w-[220px] overflow-y-auto rounded-xl border border-gray-100 bg-white p-1 shadow-card animate-fade-in"
        >
          {providers.map((p) => {
            const keyed = hasKey(p.key_env)
            return (
              <div key={p.id}>
                <DropdownMenu.Label className="flex items-center justify-between px-3 pb-0.5 pt-2 text-[10px] font-semibold uppercase tracking-wide text-gray-400">
                  {p.label}
                  {!keyed && <span className="rounded bg-amber-100 px-1.5 text-[10px] normal-case text-amber-700">no key</span>}
                </DropdownMenu.Label>
                {[p.default_model, ...p.models.filter((m) => m !== p.default_model)].map((m) => {
                  const isDefault = m === p.default_model
                  const selected = current.id === p.id && model === m
                  return (
                    <DropdownMenu.Item
                      key={m}
                      onSelect={() => (keyed ? choose(p.id, m, isDefault) : onOpenSettings())}
                      className={cn(
                        'cursor-pointer rounded-lg px-3 py-1.5 text-xs outline-none transition-colors',
                        selected ? 'bg-brand-tint font-medium text-brand' : 'text-gray-700 hover:bg-gray-50 focus:bg-gray-50',
                        !keyed && 'text-gray-400',
                      )}
                    >
                      {m}
                      {isDefault ? ' (default)' : ''}
                    </DropdownMenu.Item>
                  )
                })}
              </div>
            )
          })}
        </DropdownMenu.Content>
      </DropdownMenu.Portal>
    </DropdownMenu.Root>
  )
}

// ─── The page that is open, with one-tap tasks about it ───────────────────────

function PageChip({ page, onPick }: { page: { url: string; title: string }; onPick: (prompt: string) => void }) {
  let host = page.url
  let short = page.url
  try {
    const u = new URL(page.url)
    host = u.host
    short = (u.host + u.pathname).slice(0, 120)   // no query string or fragment: they can carry anything
  } catch { /* keep the raw text */ }
  const where = `the page open in the browser right now (${short})`
  const actions = [
    { icon: Sparkles, label: 'Summarize this page', prompt: `Summarize ${where} in 5 short bullet points.` },
    { icon: Table2, label: 'Extract the key data', prompt: `From ${where}, extract the key data (names, prices, dates, contact details) as a table.` },
    { icon: List, label: 'List the main links', prompt: `List the most important links on ${where}, each with a one-line description.` },
  ]
  return (
    <div className="mx-4 mb-2 flex-shrink-0">
      <div className="mb-1.5 flex items-center gap-1.5 text-[11px] text-gray-500" title={page.url}>
        <Globe size={12} className="flex-shrink-0 text-gray-400" />
        <span className="truncate">
          {page.title && page.title !== page.url && page.title !== host ? `${page.title} — ${host}` : host}
        </span>
      </div>
      <div className="flex flex-wrap gap-1.5">
        {actions.map((a) => (
          <button
            key={a.label}
            onClick={() => onPick(a.prompt)}
            className="flex items-center gap-1.5 rounded-full border border-gray-200 bg-white px-2.5 py-1 text-[11.5px] text-gray-600 transition-colors hover:border-brand/30 hover:bg-brand-tint/50 hover:text-brand"
          >
            <a.icon size={11} />
            {a.label}
          </button>
        ))}
      </div>
    </div>
  )
}

// ─── Input card ────────────────────────────────────────────────────────────────

function InputCard({
  status,
  onSend,
  onStop,
  hasMessages,
  prefill,
  onPrefillConsumed,
  settingsVersion,
  onOpenSettings,
}: {
  status: Status
  onSend: (task: string, fmt: FormatOption, tier: TierChoice) => void
  onStop: () => void
  hasMessages: boolean
  prefill: string
  onPrefillConsumed: () => void
  settingsVersion: number
  onOpenSettings: () => void
}) {
  const DRAFT_KEY = 'agent_draft_text'
  const [value, setValue] = useState(() => {
    try { return localStorage.getItem(DRAFT_KEY) ?? '' } catch { return '' }
  })
  const [tier, setTierState] = useState<TierChoice>(() => {
    try {
      const saved = localStorage.getItem('agent_tier')
      return saved === 'fast' || saved === 'standard' || saved === 'strong' ? saved : 'auto'
    } catch { return 'auto' }
  })
  const setTier = (t: TierChoice) => {
    setTierState(t)
    try { localStorage.setItem('agent_tier', t) } catch {}
  }
  const [focused, setFocused] = useState(false)
  const textareaRef = useRef<HTMLTextAreaElement>(null)

  const setValuePersisted = useCallback((v: string) => {
    setValue(v)
    try { localStorage.setItem(DRAFT_KEY, v) } catch {}
  }, [])

  // Accept prefilled prompt from suggestion card
  useEffect(() => {
    if (prefill) {
      setValuePersisted(prefill)
      onPrefillConsumed()
      textareaRef.current?.focus()
    }
  }, [prefill, onPrefillConsumed, setValuePersisted])

  // Auto-resize textarea
  useEffect(() => {
    const ta = textareaRef.current
    if (!ta) return
    ta.style.height = 'auto'
    ta.style.height = `${Math.min(ta.scrollHeight, 150)}px`
  }, [value])

  const handleSend = useCallback(() => {
    const task = value.trim()
    if (!task || status === 'running' || status === 'paused') return
    setValuePersisted('')
    onSend(task, 'auto', tier)
  }, [value, status, onSend, tier, setValuePersisted])

  const handleKey = (e: KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault()
      handleSend()
    }
  }

  const running = status === 'running'
  const canSend = value.trim().length > 0 && status !== 'running' && status !== 'paused'

  return (
    <div className="flex-shrink-0 px-4 pb-4 pt-2 bg-gradient-to-t from-white via-white to-transparent">
      <div
        className={cn(
          'rounded-2xl bg-white border transition-all duration-150',
          focused
            ? 'shadow-input-focus border-brand/30'
            : 'shadow-input border-gray-100',
        )}
      >
        <textarea
          ref={textareaRef}
          value={value}
          onChange={(e: ChangeEvent<HTMLTextAreaElement>) => setValuePersisted(e.target.value)}
          onKeyDown={handleKey}
          onFocus={() => setFocused(true)}
          onBlur={() => setFocused(false)}
          disabled={running}
          aria-label="Task for the agent"
          placeholder={running ? 'Working… stop the task to give it a different one' : 'Describe a task for the agent…'}
          rows={1}
          className="w-full px-4 pt-3.5 pb-1 text-[14px] text-gray-800 placeholder:text-gray-400 resize-none border-none outline-none bg-transparent leading-relaxed disabled:opacity-60"
          style={{ minHeight: '52px', maxHeight: '150px' }}
        />

        <div className="flex items-center gap-2 px-3.5 pb-3 pt-1">
          <ModelPicker disabled={running} refreshKey={settingsVersion} onOpenSettings={onOpenSettings} />
          <TierPicker value={tier} onChange={setTier} disabled={running} refreshKey={settingsVersion} />
          <div className="flex-1" />
          {running ? (
            <button
              onClick={onStop}
              aria-label="Stop the task"
              title="Stop the task"
              className="w-8 h-8 rounded-xl flex items-center justify-center bg-gray-100 hover:bg-red-100 text-gray-700 hover:text-red-600 transition-all"
            >
              <Square size={11} className="fill-current" />
            </button>
          ) : (
            <button
              onClick={handleSend}
              disabled={!canSend}
              aria-label="Send task"
              className={cn(
                'w-8 h-8 rounded-xl flex items-center justify-center transition-all',
                canSend
                  ? 'bg-brand hover:bg-brand-hover text-white shadow-subtle'
                  : 'bg-gray-100 text-gray-300 cursor-not-allowed',
              )}
            >
              <ArrowUp size={15} strokeWidth={2.5} />
            </button>
          )}
        </div>
      </div>

      {!hasMessages && (
        <p className="text-center text-[11px] text-gray-400 mt-2">
          Enter to run · Shift+Enter for new line
        </p>
      )}
    </div>
  )
}

// ─── Main ChatPanel ────────────────────────────────────────────────────────────

export default function ChatPanel({
  messages,
  status,
  connected,
  currentSessionId,
  sessions,
  settingsOpen,
  onSettingsOpen,
  onOpenPlaybooks,
  onOpenInbox,
  unread,
  onSendTask,
  onStop,
  onContinue,
  onRetry,
  onNewSession,
  onSwitchSession,
  onDeleteSession,
  onRenameSession,
  approvalSlot,
  onCollapse,
  page,
  wide,
  onToggleWide,
}: ChatPanelProps) {
  const [tab, setTab] = useState('chat')
  const [prefill, setPrefill] = useState('')
  // Counts closings of the settings panel, so the model picker in the task box re-reads what was saved.
  const [settingsVersion, setSettingsVersion] = useState(0)
  const settingsSeen = useRef(false)
  useEffect(() => {
    if (!settingsSeen.current) { settingsSeen.current = settingsOpen; return }   // not on first render
    if (!settingsOpen) { settingsSeen.current = false; setSettingsVersion((v) => v + 1) }
  }, [settingsOpen])
  const clearPrefill = useCallback(() => setPrefill(''), [])

  // Auto-switch to chat tab when messages arrive
  useEffect(() => {
    if (messages.length > 0) setTab('chat')
  }, [messages.length])

  // Message list with auto-scroll
  const bottomRef = useRef<HTMLDivElement>(null)
  const scrollRef = useRef<HTMLDivElement>(null)
  const userScrolledRef = useRef(false)
  const lastScrollTopRef = useRef(0)

  const handleScroll = useCallback(() => {
    const el = scrollRef.current
    if (!el) return
    const distFromBottom = el.scrollHeight - el.scrollTop - el.clientHeight
    const scrolledUp = el.scrollTop < lastScrollTopRef.current
    if (scrolledUp && distFromBottom > 60) userScrolledRef.current = true
    if (distFromBottom < 12) userScrolledRef.current = false
    lastScrollTopRef.current = el.scrollTop
  }, [])

  const scrollToBottom = useCallback((smooth = true) => {
    if (!userScrolledRef.current) {
      bottomRef.current?.scrollIntoView({ behavior: smooth ? 'smooth' : 'instant' })
    }
  }, [])

  useEffect(() => { scrollToBottom() }, [messages, scrollToBottom])

  // Scroll when returning to chat tab or closing settings
  useEffect(() => {
    if (tab === 'chat') { userScrolledRef.current = false; scrollToBottom(false) }
  }, [tab, scrollToBottom])

  useEffect(() => {
    if (!settingsOpen) { userScrolledRef.current = false; scrollToBottom(false) }
  }, [settingsOpen, scrollToBottom])

  return (
    <div className="flex flex-col h-full bg-white border-l border-gray-100 overflow-hidden">
      {settingsOpen ? (
        <SettingsPanel onClose={() => onSettingsOpen(false)} />
      ) : (
        <>
          {/* ── Header ── */}
          <div className="flex-shrink-0 px-4 pt-4 pb-0 bg-white">
            <div className="flex items-center justify-between mb-3">
              {/* Brand */}
              <div className="flex items-center gap-2.5">
                <img src="/favicon.svg" width="28" height="28" alt="" />
                <div>
                  <p className="text-[13px] font-semibold text-gray-900 leading-tight">
                    AI Browser Agent
                  </p>
                  <div className="flex items-center gap-1.5">
                    <span
                      className={cn(
                        'w-1.5 h-1.5 rounded-full',
                        !connected
                          ? 'bg-amber-400 animate-pulse'
                          : status === 'running'
                          ? 'bg-green-500 animate-pulse'
                          : status === 'error'
                          ? 'bg-red-400'
                          : status === 'paused'
                          ? 'bg-amber-400'
                          : 'bg-gray-300',
                      )}
                    />
                    <span className={cn('text-[11px] capitalize', connected ? 'text-gray-400' : 'text-amber-600')}>
                      {connected ? status : 'Reconnecting…'}
                    </span>
                  </div>
                </div>
              </div>

              {/* Actions */}
              <div className="flex items-center gap-1">
                <TooltipProvider>
                  <Tooltip>
                    <TooltipTrigger asChild>
                      <button
                        aria-label="New chat"
                        onClick={onNewSession}
                        className="w-8 h-8 rounded-xl hover:bg-gray-100 flex items-center justify-center text-gray-500 hover:text-gray-700 transition-colors"
                      >
                        <Plus size={16} />
                      </button>
                    </TooltipTrigger>
                    <TooltipContent side="bottom">New chat</TooltipContent>
                  </Tooltip>
                  {onToggleWide && (
                    <Tooltip>
                      <TooltipTrigger asChild>
                        <button
                          aria-label={wide ? 'Narrower chat panel' : 'Wider chat panel'}
                          onClick={onToggleWide}
                          className="w-8 h-8 rounded-xl hover:bg-gray-100 flex items-center justify-center text-gray-500 hover:text-gray-700 transition-colors"
                        >
                          {wide ? <Minimize2 size={14} /> : <Maximize2 size={14} />}
                        </button>
                      </TooltipTrigger>
                      <TooltipContent side="bottom">{wide ? 'Narrower' : 'Wider'}</TooltipContent>
                    </Tooltip>
                  )}
                  {onCollapse && (
                    <Tooltip>
                      <TooltipTrigger asChild>
                        <button
                          aria-label="Hide chat panel"
                          onClick={onCollapse}
                          className="w-8 h-8 rounded-xl hover:bg-gray-100 flex items-center justify-center text-gray-500 hover:text-gray-700 transition-colors"
                        >
                          <ChevronsRight size={16} />
                        </button>
                      </TooltipTrigger>
                      <TooltipContent side="bottom">Hide chat panel</TooltipContent>
                    </Tooltip>
                  )}
                  <Tooltip>
                    <TooltipTrigger asChild>
                      <button
                        aria-label={unread > 0 ? `Inbox, ${unread} unread` : 'Inbox'}
                        onClick={onOpenInbox}
                        className="relative w-8 h-8 rounded-xl hover:bg-gray-100 flex items-center justify-center text-gray-500 hover:text-gray-700 transition-colors"
                      >
                        <Bell size={15} />
                        {unread > 0 && (
                          <span className="absolute -right-0.5 -top-0.5 min-w-[16px] rounded-full bg-red-500 px-1 text-center text-[10px] font-semibold leading-4 text-white">
                            {unread > 9 ? '9+' : unread}
                          </span>
                        )}
                      </button>
                    </TooltipTrigger>
                    <TooltipContent side="bottom">{unread > 0 ? `${unread} unread` : 'Inbox'}</TooltipContent>
                  </Tooltip>
                  <Tooltip>
                    <TooltipTrigger asChild>
                      <button
                        aria-label="Playbooks"
                        onClick={onOpenPlaybooks}
                        className="w-8 h-8 rounded-xl hover:bg-gray-100 flex items-center justify-center text-gray-500 hover:text-gray-700 transition-colors"
                      >
                        <BookMarked size={15} />
                      </button>
                    </TooltipTrigger>
                    <TooltipContent side="bottom">Playbooks</TooltipContent>
                  </Tooltip>
                  <Tooltip>
                    <TooltipTrigger asChild>
                      <button
                        aria-label="Settings"
                        onClick={() => onSettingsOpen(true)}
                        className="w-8 h-8 rounded-xl hover:bg-gray-100 flex items-center justify-center text-gray-500 hover:text-gray-700 transition-colors"
                      >
                        <Settings size={15} />
                      </button>
                    </TooltipTrigger>
                    <TooltipContent side="bottom">Settings</TooltipContent>
                  </Tooltip>
                </TooltipProvider>
              </div>
            </div>

            {/* Tabs */}
            <div role="tablist" className="flex gap-1 border-b border-gray-100 -mx-4 px-4">
              {[
                { value: 'chat',    label: 'Chat',    icon: MessageSquare },
                { value: 'history', label: 'History', icon: History       },
              ].map((t) => (
                <button
                  key={t.value}
                  role="tab"
                  aria-selected={tab === t.value}
                  onClick={() => setTab(t.value)}
                  className={cn(
                    'flex items-center gap-1.5 rounded-t-lg px-4 py-2.5 text-[13px] border-b-[3px] -mb-px transition-all',
                    tab === t.value
                      ? 'border-brand bg-brand-tint font-semibold text-brand'
                      : 'border-transparent font-medium text-gray-500 hover:bg-gray-50 hover:text-gray-800',
                  )}
                >
                  <t.icon size={13} />
                  {t.label}
                  {t.value === 'history' && sessions.length > 0 && (
                    <span className="text-[10px] bg-gray-100 text-gray-500 px-1.5 py-0.5 rounded-full font-semibold">
                      {sessions.length}
                    </span>
                  )}
                </button>
              ))}
            </div>
          </div>

          {/* ── Body ── */}
          {tab === 'chat' ? (
            <div className="flex flex-col flex-1 min-h-0">
              {/* Paused banner */}
              {status === 'paused' && (
                <ContinueBar onContinue={onContinue} onStop={onStop} />
              )}

              {/* Message list */}
              <div
                ref={scrollRef}
                onScroll={handleScroll}
                role="log"
                aria-live="polite"
                aria-relevant="additions"
                className="flex-1 overflow-y-auto scrollbar-thin"
              >
                {messages.length === 0 ? (
                  <WelcomeState onSelect={(p) => setPrefill(p)} />
                ) : (
                  <div className="px-5 py-5 space-y-5">
                    {messages.map((turn, i) => (
                      <MessageBubble
                        key={turn.id}
                        turn={turn}
                        onOpenSettings={() => onSettingsOpen(true)}
                        onRetry={
                          turn.error && i === messages.length - 1 && status !== 'running' ? onRetry : undefined
                        }
                      />
                    ))}
                    <div ref={bottomRef} />
                  </div>
                )}
              </div>

              {approvalSlot}

              {page && /^https?:/.test(page.url) && status !== 'running' && status !== 'paused' && !approvalSlot && (
                <PageChip page={page} onPick={(p) => setPrefill(p)} />
              )}

              {/* Input card */}
              <InputCard
                status={status}
                onSend={onSendTask}
                onStop={onStop}
                hasMessages={messages.length > 0}
                prefill={prefill}
                onPrefillConsumed={clearPrefill}
                settingsVersion={settingsVersion}
                onOpenSettings={() => onSettingsOpen(true)}
              />
            </div>
          ) : (
            <div className="flex flex-col flex-1 min-h-0">
              <HistoryTab
                sessions={sessions}
                currentSessionId={currentSessionId}
                onSwitch={(id) => {
                  onSwitchSession(id)
                  setTab('chat')
                }}
                onDelete={onDeleteSession}
                onRename={onRenameSession}
              />
            </div>
          )}
        </>
      )}
      {/* An approval must be seen on every tab, not only in the chat */}
      {(settingsOpen || tab !== 'chat') && approvalSlot}
    </div>
  )
}
