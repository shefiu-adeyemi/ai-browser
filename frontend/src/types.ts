export type Status = 'idle' | 'running' | 'paused' | 'done' | 'error'

export type MessageRole = 'user' | 'agent' | 'system'

// The independent check of a finished run: 'pass' = judged complete, 'fail' = judged NOT complete
// (the reason says why), 'unknown' = no check was possible.
export type Verdict = 'pass' | 'fail' | 'unknown'

export interface Turn {
  id: string
  role: MessageRole
  content: string
  ts: string
  logs?: string[]
  verdict?: Verdict
  reason?: string
  retries?: number
  duration?: number
  pending?: boolean
  stepCount?: number
  error?: boolean   // the run ended in an error (the bubble offers Retry / Settings)
}

// The browser toolbar's view of the agent's browser (GET /browser/state)
export interface BrowserTab {
  id: string
  title: string
  url: string
}

export interface BrowserState {
  ready: boolean
  tabs: BrowserTab[]
  active: string | null
  can_back: boolean
  can_forward: boolean
  url: string
  title: string
  locked: boolean
}

export interface SessionInfo {
  id: string
  title: string
  updated_at: string
  turns: number
  last_verdict?: Verdict
  last_score?: number   // sessions saved before the judge existed
  last_duration?: number
}

// One AI provider as the server describes it (server/providers.py is the single source of truth)
export interface ProviderInfo {
  id: string
  label: string
  key_env: string
  default_model: string
  models: string[]
}

export interface Settings {
  LLM_PROVIDER: string
  LLM_MODEL: string
  ANTHROPIC_API_KEY: string
  OPENAI_API_KEY: string
  GEMINI_API_KEY: string
  DEEPSEEK_API_KEY: string
  QWEN_API_KEY: string
  LINKEDIN_EMAIL: string
  LINKEDIN_PASSWORD: string
  MAX_STEPS: number
  CONTEXT_TURNS: number
  APPROVAL_MODE: string   // 'ask' | 'auto' (the server also allows 'off', hand-edited only)
  UNREADABLE?: string[]   // read-only: stored secrets this Windows account cannot decrypt
  PROVIDERS: ProviderInfo[]   // read-only: sent by the server, never saved back
  LLM_FAST_PROVIDER: string   // '' = the Standard provider
  LLM_FAST_MODEL: string
  LLM_STRONG_PROVIDER: string
  LLM_STRONG_MODEL: string
  LLM_ROUTER: string          // 'auto' | 'off'
  TIERS?: TiersInfo           // read-only: what each slot really resolves to
}

export type ModelTier = 'fast' | 'standard' | 'strong'

export interface TierSlot {
  provider: string
  model: string
  label: string
  explicit: boolean
  usable: boolean
  collapsed: boolean
  reason: string
}

export interface TiersInfo {
  router: 'auto' | 'off'
  slots: Record<'fast' | 'standard' | 'strong', TierSlot>
  suggestions: Record<string, { fast: string; strong: string }>
  purposes: { purpose: string; tier: 'fast' | 'standard' | 'strong'; label: string }[]
}

export interface ModelCost {
  per_tier: Record<'fast' | 'standard' | 'strong', number>
  total: number
  unpriced_tokens: number
}

export type ModelPrices = Record<string, { in: number; out: number }>

export interface ModelUsage {
  range_days: number
  totals: Record<'fast' | 'standard' | 'strong', { calls: number; prompt: number; completion: number }>
}

export type FormatOption = 'auto' | 'json' | 'bullets' | 'table'

// The agent is waiting for a person (approve/deny an action, or finish a manual step)
export interface ApprovalRequest {
  id: string
  kind: 'approve' | 'handoff' | 'input'
  title: string
  detail: string
  url: string
  timeout: number
}

// WebSocket message types
export type WsMessage =
  | { type: 'screenshot'; data: string; w?: number; h?: number }
  | { type: 'url'; data: string }
  | { type: 'action'; text: string; step?: number }
  | { type: 'result'; text: string; verdict: Verdict; reason?: string; retries: number; duration: number }
  | { type: 'verdict'; verdict: Verdict; retry: number; reason: string }
  | { type: 'status'; text: Status; max_steps?: number }
  | ({ type: 'approval' } & ApprovalRequest)
  | { type: 'approval_done'; id: string }
  | { type: 'session_changed'; id: string; history: Array<{ role: 'user' | 'agent'; content: string; ts: string }> }
  | ({ type: 'notification'; unread: number } & Omit<NotificationItem, 'read'> & { read?: boolean })

// ── Playbooks (server: playbook.py, the single source of truth for the format) ──
export interface PlaybookFingerprint {
  node_name: string
  attributes: Record<string, string>
  ax_name?: string | null
}

export interface PlaybookStep {
  id: string
  kind: 'open' | 'search' | 'back' | 'click' | 'type' | 'select' | 'key' | 'scroll' | 'wait' | 'tab' | 'handoff' | 'approve' | 'input' | 'scrape' | 'foreach' | 'extract' | 'ai'
  tag: 'deterministic' | 'ai'
  note: string
  url?: string | null
  text?: string | null
  keys?: string | null
  seconds?: number | null
  query?: string | null
  engine?: string | null
  message?: string | null
  tab_index?: number | null
  close_tab?: boolean
  timeout_minutes?: number | null
  on_timeout?: 'cancel' | 'skip' | null
  row_selector?: string | null
  max_rows?: number | null
  fields?: ScrapeField[] | null
  source?: string | null
  column?: string | null
  body?: number | null
  on_failure?: 'stop' | 'skip' | null
  variable?: string | null
  tier?: ModelTier | null         // AI steps: which model tier reads the page (null = automatic)
  ask: boolean
  target?: PlaybookFingerprint | null
  [more: string]: unknown        // fields the screen doesn't show are passed back to the server untouched
}

export interface ScrapeField {
  name: string
  selector: string
  type: 'text' | 'number' | 'link' | 'attr'
  attr?: string | null
}

/** What a table-reading step read: every cell is text. */
export interface ScrapedTable { columns: string[]; rows: string[][]; truncated: boolean; total: number }

export interface PlaybookVariable {
  name: string
  type: 'text' | 'number' | 'url' | 'linkedin_url'
  label: string
  default?: string | null
  required: boolean
  runtime?: boolean
  loop?: boolean
  allowed_hosts: string[]
  [more: string]: unknown
}

export interface Playbook {
  id: string
  name: string
  description: string
  version: number
  variables: PlaybookVariable[]
  steps: PlaybookStep[]
  domains: string[]
  [more: string]: unknown
}

export interface VariableSuggestion {
  step_id: string
  field: 'url' | 'text' | 'query'
  value: string
  name: string
  type: PlaybookVariable['type']
  allowed_hosts: string[]
  reason: string
}

export interface PlaybookSummary {
  id: string
  name: string
  description: string
  version: number
  updated_at: string
  steps: number
  ai_steps: number
  variables: string[]
  domains: string[]
  approved: boolean
}

// Input events sent from frontend → backend → CDP
export type InputMessage =
  | { type: 'input'; kind: 'mouse'; event: 'mousePressed' | 'mouseReleased' | 'mouseMoved' | 'mouseWheel'; x: number; y: number; button?: string; clickCount?: number; deltaX?: number; deltaY?: number; modifiers?: number }
  | { type: 'input'; kind: 'key'; event: 'keyDown' | 'keyUp' | 'char' | 'insertText'; key?: string; code?: string; text?: string; keyCode?: number; modifiers?: number }

/** What a schedule watches for (server: monitor.py). */
export interface WatchSettings {
  enabled: boolean
  key_columns: string[]
  ignore_columns: string[]
  ignore_patterns: string[]
  min_change_percent: number
  report_added: boolean
  report_removed: boolean
  report_changed: boolean
}

export interface WatchChange { table: string; key: string; columns: { column: string; before: string; after: string }[] }
export interface WatchRow { table: string; key: string; cells: Record<string, string> }

/** How one watched run compared with the last one. */
export interface WatchResult {
  status: 'baseline' | 'unchanged' | 'changed' | 'reset' | 'skipped'
  summary?: string
  reason?: string
  added?: number
  removed?: number
  changed?: number
  diff?: { cut: boolean; added_rows: WatchRow[]; removed_rows: WatchRow[]; changed_rows: WatchChange[] }
}

export interface PlaybookRunSummary {
  run_id: string
  status: 'done' | 'failed' | 'needs_you' | 'stopped' | 'waiting' | 'declined' | 'expired' | 'cancelled'
  started_at: number
  finished_at: number
  seconds: number
  attended: boolean
  error: string
  playbook_version: number
  steps: number
  ok_steps: number
  watch?: string
}

export interface PlaybookStepResult {
  id: string
  kind: string
  note: string
  status: 'ok' | 'failed' | 'needs_you'
  detail: string
  seconds: number
  matched?: string | null
  approval?: string | null
  output?: string | null
  model?: string | null          // which model read the page, e.g. "claude-haiku-4-5 (Fast)"
  tokens?: number | null         // what that used (prompt + answer), when the model reported it
}

export interface PlaybookRunRecord extends Omit<PlaybookRunSummary, 'steps' | 'ok_steps' | 'watch'> {
  screenshot?: boolean
  inputs: Record<string, string>
  steps: PlaybookStepResult[]
  outputs: { step_id: string; query: string; text: string; table?: ScrapedTable }[]
  watch?: WatchResult
}

export interface DamagedPlaybook {
  id: string
  problem: string
}

// ── Schedules and the inbox (server: schedules.py, scheduler.py, notifications.py) ──
export type CadenceKind = 'once' | 'interval' | 'daily' | 'weekly'

export interface Cadence {
  kind: CadenceKind
  every_minutes?: number | null
  at?: string | null
  weekdays?: number[]
  run_at?: string | null
}

export interface ScheduleView {
  id: string
  name: string
  playbook_id: string
  playbook_name: string
  enabled: boolean
  cadence: Cadence
  cadence_text: string
  items: Record<string, string>[]
  on_failure: 'stop' | 'skip'
  allow_sensitive: boolean
  status: 'scheduled' | 'running' | 'waiting' | 'asking' | 'paused' | 'off' | 'finished'
  next_run: string | null
  last_run_at: string | null
  last_status: string
  last_detail: string
  paused_reason: string
  paused_detail: string
  missed: number
  runs_total: number
  remaining: number
  resume_after: string | null
  watch: WatchSettings
  last_change: string
  last_change_at: string | null
  pending: PendingQuestion[]
}

export interface PendingQuestion {
  run_id: string
  row: number
  message: string
  detail: string
  url: string
  parked_at: string
  expires_at: string
  kind: 'approve' | 'input'
  variable: string
  on_timeout: 'cancel' | 'skip'
}

export interface Allowance { start: number; ceiling: number; ramp_per_week: number }

export interface QuietHours { enabled: boolean; start: string; end: string }

export interface LimitsInfo {
  allowances: Record<string, Allowance>
  gap: { min: number; jitter: number }
  usage: Record<string, { used: number; cap: number }>
}

export interface NotificationItem {
  id: string
  ts: string
  level: 'info' | 'warning' | 'error'
  title: string
  text: string
  schedule_id: string
  playbook_id: string
  run_id: string
  read: boolean
  action?: { type: 'checkpoint'; schedule_id: string; run_id: string; kind?: 'approve' | 'input' } | null
}
