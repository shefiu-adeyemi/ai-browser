import type { ModelTier, Playbook, PlaybookStep, TiersInfo } from '@/types'

export async function readJson(res: Response): Promise<any> {
  try { return await res.json() } catch { return {} }
}

/** Why a request failed, in words a person can act on. */
export function reasonOf(res: Response, data: { reason?: string }): string {
  if (res.status === 401 || res.status === 403) {
    return 'The app no longer recognises this page (it was restarted or opened elsewhere). Reload the page and try again.'
  }
  return data.reason || `The app answered with an error (HTTP ${res.status}).`
}

export function errText(e: unknown): string {
  return e instanceof Error ? e.message : String(e)
}

export const KIND_LABEL: Record<string, string> = {
  open: 'Open page', search: 'Search', back: 'Go back', click: 'Click', type: 'Type', select: 'Choose option',
  key: 'Press keys', scroll: 'Scroll', wait: 'Wait', tab: 'Tab', handoff: 'Your turn', approve: 'Ask you first', input: 'Ask you for text',
  extract: 'Read page', ai: 'AI step',
}

/** How the models behind the tiers are set up right now (no secrets in it), or null if the app can't say. */
export async function fetchTiers(): Promise<TiersInfo | null> {
  try {
    const res = await fetch('/models/tiers')
    const data = await readJson(res)
    return res.ok && data.ok ? (data.tiers as TiersInfo) : null
  } catch {
    return null
  }
}

export const TIER_LABEL: Record<ModelTier, string> = { fast: 'Fast', standard: 'Standard', strong: 'Strong' }

/** What a tier will really use, in words: "claude-haiku-4-5", or "Standard model (Fast isn't set up)". */
export function tierModelText(tiers: TiersInfo | null, tier: ModelTier): string {
  const slot = tiers?.slots?.[tier]
  if (!slot) return ''
  if (tiers?.router === 'off') return `${tiers.slots.standard.model} (tiers are off in Settings)`
  return slot.collapsed ? `${slot.model} (${TIER_LABEL[tier]} isn't set up: ${slot.reason})` : slot.model
}

/** The tier the router picks by itself for a job ('playbook_extract' for a step that reads a page). */
export function autoTier(tiers: TiersInfo | null, purpose: string): ModelTier {
  return tiers?.purposes?.find((p) => p.purpose === purpose)?.tier ?? 'standard'
}

export function stepDetail(s: PlaybookStep): string {
  switch (s.kind) {
    case 'open': return s.url ?? ''
    case 'search': return `${s.engine ?? ''}: ${s.query ?? ''}`
    case 'type':
    case 'select': return s.text ? `"${s.text}"` : ''
    case 'key': return s.keys ?? ''
    case 'wait': return `${s.seconds ?? 0} s`
    case 'tab': return `${s.close_tab ? 'close' : 'switch to'} tab ${(s.tab_index ?? 0) + 1}`
    case 'handoff': return s.message ?? ''
    case 'approve':
    case 'input': return s.message ?? ''
    case 'extract':
    case 'ai': return s.query ?? ''
    default: return ''
  }
}

export function stepTarget(s: PlaybookStep): string {
  const t = s.target
  if (!t) return ''
  return t.ax_name || t.attributes['aria-label'] || t.attributes.placeholder || t.attributes.name || `<${t.node_name}>`
}

/** Variables that no remaining step uses (an input nobody reads would be asked for on every run). */
export function usedVariables<V extends { name: string }>(steps: PlaybookStep[], variables: V[]): V[] {
  const used = (name: string) => steps.some((s) => s.variable === name ||
    [s.url, s.text, s.keys, s.query, s.message].some((v) => typeof v === 'string' && v.includes('{{' + name + '}}')))
  return variables.filter((v) => used(v.name))
}

/** A short reason a person can read for what an answer would do: "in 3 hours", "in 40 minutes". */
export function timeLeft(iso: string | null | undefined): string {
  if (!iso) return ''
  const ms = new Date(iso).getTime() - Date.now()
  if (ms <= 0) return 'now'
  const min = Math.round(ms / 60000)
  if (min < 60) return `${min} minute${min === 1 ? '' : 's'}`
  const h = Math.round(min / 60)
  return h < 48 ? `${h} hour${h === 1 ? '' : 's'}` : `${Math.round(h / 24)} days`
}

/** Say yes or no to a run waiting at an "ask you first" step. Returns why it failed, or '' if it was taken. */
export async function answerCheckpoint(scheduleId: string, runId: string, approve: boolean, value?: string, remember?: boolean): Promise<string> {
  try {
    const res = await fetch(`/schedules/${scheduleId}/checkpoints/${runId}/answer`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(value === undefined ? { approve } : { approve, value, remember: remember === true }),
    })
    const data = await readJson(res)
    return res.ok && data.ok ? '' : reasonOf(res, data)
  } catch (e) {
    return errText(e)
  }
}

/** Is the step at this position directly preceded by an "ask you first" step? */
export function isGuarded(steps: PlaybookStep[], index: number): boolean {
  return index > 0 && steps[index - 1].kind === 'approve'
}

export function formatWhen(epochSeconds: number): string {
  if (!epochSeconds) return ''
  return new Date(epochSeconds * 1000).toLocaleString(undefined, {
    month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit',
  })
}

const ASKED = /^\{\{(answer_\d+)\}\}$/

/** Does this "type" step take its text from a question asked during the run? Returns the variable's name. */
export function askedVariable(s: PlaybookStep): string | null {
  const m = ASKED.exec(s.text ?? '')
  return s.kind === 'type' && m ? m[1] : null
}

function nextNumber(values: string[], prefix: string): number {
  const nums = values.filter((v) => v.startsWith(prefix)).map((v) => Number(v.slice(prefix.length))).filter((n) => !Number.isNaN(n))
  return Math.max(0, ...nums) + 1
}

/**
 * "Ask me for this text each time": the text of the step at `index` becomes a question asked when the run gets
 * there. The question goes right before the step, or before the "ask you first" step that guards it, so that
 * the guard still sits directly in front of the step it guards.
 */
export function askForText(pb: Playbook, index: number): { playbook: Playbook; original: string } {
  const step = pb.steps[index]
  const name = `answer_${nextNumber(pb.variables.map((v) => v.name), 'answer_')}`
  const id = `s${nextNumber(pb.steps.map((x) => x.id), 's')}`
  const label = stepTarget(step) || step.note || 'this field'
  const ask: PlaybookStep = {
    id, kind: 'input', tag: 'deterministic', note: '', message: `What should be typed into "${label}"?`,
    variable: name, timeout_minutes: 1440, ask: false,
  }
  const at = isGuarded(pb.steps, index) ? index - 1 : index
  const steps = pb.steps.map((x) => (x.id === step.id ? { ...x, text: `{{${name}}}` } : x))
  return {
    original: step.text ?? '',
    playbook: {
      ...pb, steps: [...steps.slice(0, at), ask, ...steps.slice(at)],
      variables: [...pb.variables, { name, type: 'text', label: 'Answer', required: true, runtime: true, allowed_hosts: [] }],
    },
  }
}

/** Undo askForText for one variable: the question and the variable go, and the step types `original` again. */
export function stopAskingForText(pb: Playbook, name: string, original: string): Playbook {
  const steps = pb.steps
    .filter((s) => !(s.kind === 'input' && s.variable === name))
    .map((s) => (s.kind === 'type' && s.text === `{{${name}}}` ? { ...s, text: original } : s))
  return { ...pb, steps, variables: pb.variables.filter((v) => v.name !== name) }
}
