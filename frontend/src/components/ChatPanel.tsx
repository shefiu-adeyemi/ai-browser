import {
  useState, useRef, useEffect, useCallback, KeyboardEvent, ChangeEvent,
} from 'react'
import * as DropdownMenu from '@radix-ui/react-dropdown-menu'
import { Tooltip, TooltipContent, TooltipTrigger, TooltipProvider } from '@/components/ui/tooltip'
import { Button } from '@/components/ui/button'
import { Slider } from '@/components/ui/slider'
import { cn, formatDuration, formatDate } from '@/lib/utils'
import {
  Plus, Settings, ChevronDown, Square, Trash2, ChevronRight,
  Briefcase, Newspaper, ShoppingBag, MapPin, ArrowUp, X,
  MessageSquare, History, Eye, EyeOff,
} from 'lucide-react'
import type { Turn, Status, SessionInfo, Settings as SettingsType, FormatOption, Verdict } from '@/types'

// 'unknown' (no independent check was possible) shows nothing rather than a made-up rating
const hasVerdict = (v: Verdict | undefined): v is 'pass' | 'fail' => v === 'pass' || v === 'fail'

// ─── Props ────────────────────────────────────────────────────────────────────

interface ChatPanelProps {
  messages: Turn[]
  status: Status
  currentSessionId: string | null
  sessions: SessionInfo[]
  settingsOpen: boolean
  onSettingsOpen: (v: boolean) => void
  onSendTask: (task: string, fmt: FormatOption) => void
  onStop: () => void
  onContinue: (extraSteps: number) => void
  onNewSession: () => void
  onSwitchSession: (id: string) => void
  onDeleteSession: (id: string) => void
}

// ─── Brand avatar for agent messages ─────────────────────────────────────────

function AgentAvatar() {
  return (
    <img src="/favicon.svg" width="28" height="28" alt="" className="flex-shrink-0" />
  )
}

// ─── Thinking dots ─────────────────────────────────────────────────────────────

function ThinkingDots({ stepCount, logs }: { stepCount?: number; logs?: string[] }) {
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
        <p className="text-xs text-gray-400 truncate max-w-[260px] pl-[22px]">
          {logs[logs.length - 1]}
        </p>
      )}
    </div>
  )
}

// ─── Message bubble ────────────────────────────────────────────────────────────

function MessageBubble({ turn }: { turn: Turn }) {
  const [logsOpen, setLogsOpen] = useState(false)

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
            <p className="text-[14.5px] text-gray-800 leading-relaxed whitespace-pre-wrap">
              {turn.content || '(no result)'}
            </p>

            {/* Independent check + meta row */}
            {(hasVerdict(turn.verdict) || turn.duration !== undefined) && (
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
                  {turn.logs.length} steps
                </button>
                {logsOpen && (
                  <div className="mt-2 rounded-xl bg-gray-50 border border-gray-100 p-3 max-h-44 overflow-y-auto scrollbar-thin space-y-0.5">
                    {turn.logs.map((log, i) => (
                      <p key={i} className="text-[11px] text-gray-500 font-mono leading-relaxed">
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
    <div className="flex flex-col items-center justify-center h-full px-5 py-8 select-none">
      <div className="mb-7 text-center">
        <div className="flex justify-center mb-4">
          <img src="/favicon.svg" width="48" height="48" alt="" />
        </div>
        <h2 className="text-lg font-semibold text-gray-900">What should I do?</h2>
        <p className="text-sm text-gray-500 mt-1.5 leading-snug">
          Give me any task — I'll handle it in the browser
        </p>
      </div>

      <div className="grid grid-cols-2 gap-2.5 w-full">
        {SUGGESTIONS.map((s) => (
          <button
            key={s.prompt}
            onClick={() => onSelect(s.prompt)}
            className="text-left p-3.5 rounded-xl bg-white border border-gray-100 hover:border-brand/25 hover:bg-brand-tint/50 transition-all duration-150 shadow-subtle group"
          >
            <div className="w-7 h-7 rounded-lg bg-surface-subtle group-hover:bg-brand/10 flex items-center justify-center mb-2.5 transition-colors">
              <s.icon size={14} className="text-gray-500 group-hover:text-brand transition-colors" />
            </div>
            <p className="text-xs font-semibold text-gray-700 leading-snug">{s.title}</p>
            <p className="text-xs text-gray-400 mt-0.5 leading-snug">{s.desc}</p>
          </button>
        ))}
      </div>
    </div>
  )
}

// ─── Format dropdown ───────────────────────────────────────────────────────────

const FORMAT_OPTIONS: { value: FormatOption; label: string }[] = [
  { value: 'auto',    label: 'Auto' },
  { value: 'json',    label: 'JSON' },
  { value: 'bullets', label: 'Bullets' },
  { value: 'table',   label: 'Table' },
]

function FormatDropdown({
  value,
  onChange,
}: {
  value: FormatOption
  onChange: (v: FormatOption) => void
}) {
  return (
    <DropdownMenu.Root>
      <DropdownMenu.Trigger asChild>
        <button className="flex items-center gap-1 text-xs text-gray-400 hover:text-gray-600 transition-colors px-1 py-0.5 rounded">
          {FORMAT_OPTIONS.find((o) => o.value === value)?.label}
          <ChevronDown size={11} />
        </button>
      </DropdownMenu.Trigger>
      <DropdownMenu.Portal>
        <DropdownMenu.Content
          side="top"
          align="start"
          sideOffset={6}
          className="z-50 min-w-[100px] bg-white rounded-xl shadow-card border border-gray-100 p-1 animate-fade-in"
        >
          {FORMAT_OPTIONS.map((opt) => (
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
  ANTHROPIC_API_KEY: 'Anthropic API key',
  OPENAI_API_KEY: 'OpenAI API key',
  GEMINI_API_KEY: 'Gemini API key',
  DEEPSEEK_API_KEY: 'DeepSeek API key',
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
            }}
          />
        </FieldRow>
        <FieldRow label="Model" hint="Leave on Default unless you want a specific model">
          <ProviderDropdown
            value={settings.LLM_MODEL}
            options={modelOptions}
            onChange={(v) => update('LLM_MODEL', v)}
          />
        </FieldRow>
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
}: {
  sessions: SessionInfo[]
  currentSessionId: string | null
  onSwitch: (id: string) => void
  onDelete: (id: string) => void
}) {
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
        {sessions.map((s) => {
          const active = s.id === currentSessionId
          return (
            <div
              key={s.id}
              onClick={() => onSwitch(s.id)}
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
                <p
                  className={cn(
                    'text-xs font-semibold truncate',
                    active ? 'text-brand' : 'text-gray-700',
                  )}
                >
                  {s.title || 'Untitled session'}
                </p>
                <div className="flex items-center gap-1.5 mt-0.5">
                  <span className="text-[11px] text-gray-400">{formatDate(s.updated_at)}</span>
                  {s.turns > 0 && (
                    <>
                      <span className="text-[11px] text-gray-300">·</span>
                      <span className="text-[11px] text-gray-400">{s.turns} turns</span>
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
                    onClick={(e) => {
                      e.stopPropagation()
                      onDelete(s.id)
                    }}
                    className="opacity-0 group-hover:opacity-100 w-6 h-6 rounded-lg hover:bg-red-100 flex items-center justify-center text-gray-400 hover:text-red-500 transition-all flex-shrink-0 mt-0.5"
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

// ─── Input card ────────────────────────────────────────────────────────────────

function InputCard({
  status,
  onSend,
  onStop,
  hasMessages,
  prefill,
  onPrefillConsumed,
}: {
  status: Status
  onSend: (task: string, fmt: FormatOption) => void
  onStop: () => void
  hasMessages: boolean
  prefill: string
  onPrefillConsumed: () => void
}) {
  const DRAFT_KEY = 'agent_draft_text'
  const [value, setValue] = useState(() => {
    try { return localStorage.getItem(DRAFT_KEY) ?? '' } catch { return '' }
  })
  const [fmt, setFmt] = useState<FormatOption>('auto')
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
    onSend(task, fmt)
  }, [value, status, onSend, fmt, setValuePersisted])

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
          placeholder="Describe a task for the agent…"
          rows={1}
          className="w-full px-4 pt-3.5 pb-1 text-[14px] text-gray-800 placeholder:text-gray-400 resize-none border-none outline-none bg-transparent leading-relaxed disabled:opacity-60"
          style={{ minHeight: '52px', maxHeight: '150px' }}
        />

        <div className="flex items-center gap-2 px-3.5 pb-3 pt-1">
          <FormatDropdown value={fmt} onChange={setFmt} />
          <div className="flex-1" />
          {running ? (
            <button
              onClick={onStop}
              className="flex items-center gap-1.5 h-8 px-3 rounded-xl bg-gray-100 hover:bg-red-100 text-gray-700 hover:text-red-600 text-xs font-medium transition-all"
            >
              <Square size={11} className="fill-current" />
              Stop
            </button>
          ) : (
            <button
              onClick={handleSend}
              disabled={!canSend}
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
  currentSessionId,
  sessions,
  settingsOpen,
  onSettingsOpen,
  onSendTask,
  onStop,
  onContinue,
  onNewSession,
  onSwitchSession,
  onDeleteSession,
}: ChatPanelProps) {
  const [tab, setTab] = useState('chat')
  const [prefill, setPrefill] = useState('')
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
                        status === 'running'
                          ? 'bg-green-500 animate-pulse'
                          : status === 'error'
                          ? 'bg-red-400'
                          : status === 'paused'
                          ? 'bg-amber-400'
                          : 'bg-gray-300',
                      )}
                    />
                    <span className="text-[11px] text-gray-400 capitalize">{status}</span>
                  </div>
                </div>
              </div>

              {/* Actions */}
              <div className="flex items-center gap-1">
                <TooltipProvider>
                  <Tooltip>
                    <TooltipTrigger asChild>
                      <button
                        onClick={onNewSession}
                        className="w-8 h-8 rounded-xl hover:bg-gray-100 flex items-center justify-center text-gray-500 hover:text-gray-700 transition-colors"
                      >
                        <Plus size={16} />
                      </button>
                    </TooltipTrigger>
                    <TooltipContent side="bottom">New chat</TooltipContent>
                  </Tooltip>
                  <Tooltip>
                    <TooltipTrigger asChild>
                      <button
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
            <div className="flex gap-1 border-b border-gray-100 -mx-4 px-4">
              {[
                { value: 'chat',    label: 'Chat',    icon: MessageSquare },
                { value: 'history', label: 'History', icon: History       },
              ].map((t) => (
                <button
                  key={t.value}
                  onClick={() => setTab(t.value)}
                  className={cn(
                    'flex items-center gap-1.5 px-3 py-2.5 text-xs font-medium border-b-2 -mb-px transition-all',
                    tab === t.value
                      ? 'border-brand text-brand'
                      : 'border-transparent text-gray-500 hover:text-gray-700',
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
                className="flex-1 overflow-y-auto scrollbar-thin"
              >
                {messages.length === 0 ? (
                  <WelcomeState onSelect={(p) => setPrefill(p)} />
                ) : (
                  <div className="px-5 py-5 space-y-5">
                    {messages.map((turn) => (
                      <MessageBubble key={turn.id} turn={turn} />
                    ))}
                    <div ref={bottomRef} />
                  </div>
                )}
              </div>

              {/* Input card */}
              <InputCard
                status={status}
                onSend={onSendTask}
                onStop={onStop}
                hasMessages={messages.length > 0}
                prefill={prefill}
                onPrefillConsumed={clearPrefill}
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
              />
            </div>
          )}
        </>
      )}
    </div>
  )
}
