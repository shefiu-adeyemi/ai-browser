import type { PlaybookStep } from '@/types'

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
  key: 'Press keys', scroll: 'Scroll', wait: 'Wait', tab: 'Tab', handoff: 'Your turn', approve: 'Ask you first',
  extract: 'Read page', ai: 'AI step',
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
    case 'approve': return s.message ?? ''
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
  const used = (name: string) => steps.some((s) =>
    [s.url, s.text, s.keys, s.query].some((v) => typeof v === 'string' && v.includes('{{' + name + '}}')))
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
