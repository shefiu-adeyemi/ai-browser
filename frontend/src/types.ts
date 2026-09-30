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

// Input events sent from frontend → backend → CDP
export type InputMessage =
  | { type: 'input'; kind: 'mouse'; event: 'mousePressed' | 'mouseReleased' | 'mouseMoved' | 'mouseWheel'; x: number; y: number; button?: string; clickCount?: number; deltaX?: number; deltaY?: number; modifiers?: number }
  | { type: 'input'; kind: 'key'; event: 'keyDown' | 'keyUp' | 'char' | 'insertText'; key?: string; code?: string; text?: string; keyCode?: number; modifiers?: number }
