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
  LINKEDIN_EMAIL: string
  LINKEDIN_PASSWORD: string
  MAX_STEPS: number
  CONTEXT_TURNS: number
  APPROVAL_MODE: string   // 'ask' | 'auto' (the server also allows 'off', hand-edited only)
  UNREADABLE?: string[]   // read-only: stored secrets this Windows account cannot decrypt
  PROVIDERS: ProviderInfo[]   // read-only: sent by the server, never saved back
}

export type FormatOption = 'auto' | 'json' | 'bullets' | 'table'

// The agent is waiting for a person (approve/deny an action, or finish a manual step)
export interface ApprovalRequest {
  id: string
  kind: 'approve' | 'handoff'
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

// ── Playbooks (server: playbook.py, the single source of truth for the format) ──
export interface PlaybookFingerprint {
  node_name: string
  attributes: Record<string, string>
  ax_name?: string | null
}

export interface PlaybookStep {
  id: string
  kind: 'open' | 'search' | 'back' | 'click' | 'type' | 'select' | 'key' | 'scroll' | 'wait' | 'tab' | 'handoff' | 'extract' | 'ai'
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
  ask: boolean
  target?: PlaybookFingerprint | null
  [more: string]: unknown        // fields the screen doesn't show are passed back to the server untouched
}

export interface PlaybookVariable {
  name: string
  type: 'text' | 'number' | 'url' | 'linkedin_url'
  label: string
  default?: string | null
  required: boolean
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

export interface PlaybookRunSummary {
  run_id: string
  status: 'done' | 'failed' | 'needs_you' | 'stopped'
  started_at: number
  finished_at: number
  seconds: number
  attended: boolean
  error: string
  playbook_version: number
  steps: number
  ok_steps: number
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
}

export interface PlaybookRunRecord extends Omit<PlaybookRunSummary, 'steps' | 'ok_steps'> {
  inputs: Record<string, string>
  steps: PlaybookStepResult[]
  outputs: { step_id: string; query: string; text: string }[]
}

export interface DamagedPlaybook {
  id: string
  problem: string
}
