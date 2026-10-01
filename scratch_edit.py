def edit(path, pairs):
    s = open(path, encoding="utf-8").read()
    for old, new in pairs:
        assert old in s, (path, old[:70])
        s = s.replace(old, new, 1)
    open(path, "w", encoding="utf-8").write(s)


edit("frontend/src/types.ts", [
 ("'wait' | 'tab' | 'handoff' | 'extract' | 'ai'\n", "'wait' | 'tab' | 'handoff' | 'approve' | 'extract' | 'ai'\n"),
 ("  close_tab?: boolean\n  ask: boolean\n", "  close_tab?: boolean\n  timeout_minutes?: number | null\n  ask: boolean\n"),
 ("  status: 'scheduled' | 'running' | 'waiting' | 'paused' | 'off' | 'finished'\n", "  status: 'scheduled' | 'running' | 'waiting' | 'asking' | 'paused' | 'off' | 'finished'\n"),
 ("  remaining: number\n  resume_after: string | null\n}", "  remaining: number\n  resume_after: string | null\n  pending: PendingQuestion[]\n}\n\nexport interface PendingQuestion {\n  run_id: string\n  row: number\n  message: string\n  detail: string\n  url: string\n  parked_at: string\n  expires_at: string\n}"),
 ("  run_id: string\n  read: boolean\n}", "  run_id: string\n  read: boolean\n  action?: { type: 'checkpoint'; schedule_id: string; run_id: string } | null\n}"),
])

edit("frontend/src/components/playbook/shared.ts", [
 ("  key: 'Press keys', scroll: 'Scroll', wait: 'Wait', tab: 'Tab', handoff: 'Your turn',\n",
  "  key: 'Press keys', scroll: 'Scroll', wait: 'Wait', tab: 'Tab', handoff: 'Your turn', approve: 'Ask you first',\n"),
 ("    case 'handoff': return s.message ?? ''\n", "    case 'handoff': return s.message ?? ''\n    case 'approve': return s.message ?? ''\n"),
 ("export function formatWhen(", """/** A short reason a person can read for what an answer would do: "in 3 hours", "in 40 minutes". */
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

export function formatWhen("""),
])

edit("frontend/src/components/playbook/Editor.tsx", [
 ("import { KIND_LABEL, errText, readJson, reasonOf, stepDetail, stepTarget, usedVariables } from './shared'",
  "import { KIND_LABEL, errText, isGuarded, readJson, reasonOf, stepDetail, stepTarget, usedVariables } from './shared'"),
 ("""  const editVariable = (name: string, patch: Record<string, unknown>) => {""", """  // "Ask me each time": put an approve step right before a risky step (or take it away again).
  const toggleGuard = (index: number) => {
    const step = pb.steps[index]
    if (isGuarded(pb.steps, index)) {
      stepsChanged({ ...pb, steps: pb.steps.filter((_, i) => i !== index - 1) })
      return
    }
    const nums = pb.steps.map((s) => Number(s.id.slice(1))).filter((n) => !Number.isNaN(n))
    const id = `s${Math.max(0, ...nums) + 1}`
    const label = stepTarget(step) || step.note || 'this step'
    const guard: PlaybookStep = { id, kind: 'approve', tag: 'deterministic', note: '', message: `Run "${label}"?`, ask: false }
    stepsChanged({ ...pb, steps: [...pb.steps.slice(0, index), guard, ...pb.steps.slice(index)] })
  }

  const editStep = (id: string, patch: Partial<PlaybookStep>) => {
    stepsChanged({ ...pb, steps: pb.steps.map((s) => (s.id === id ? { ...s, ...patch } : s)) })
  }

  const editVariable = (name: string, patch: Record<string, unknown>) => {"""),
 ("""                {stepDetail(s) && <p className="mt-0.5 break-all text-xs text-gray-500">{stepDetail(s)}</p>}
              </div>""", """                {s.kind === 'approve' ? (
                  <div className="mt-1 space-y-1">
                    <input aria-label={`Question for step ${i + 1}`} value={s.message ?? ''} maxLength={500}
                      onChange={(e) => editStep(s.id, { message: e.target.value })}
                      className="w-full rounded-md border border-gray-300 px-2 py-1 text-xs focus:border-brand focus:outline-none" />
                    <label className="flex items-center gap-1.5 text-[11px] text-gray-500">
                      If nobody answers within
                      <select aria-label={`Wait for step ${i + 1}`} value={s.timeout_minutes ?? 1440}
                        onChange={(e) => editStep(s.id, { timeout_minutes: Number(e.target.value) })}
                        className="rounded border border-gray-300 px-1 py-0.5 text-[11px]">
                        <option value={60}>1 hour</option><option value={360}>6 hours</option>
                        <option value={1440}>1 day</option><option value={4320}>3 days</option><option value={10080}>7 days</option>
                      </select>
                      it is cancelled, never sent.
                    </label>
                  </div>
                ) : (
                  stepDetail(s) && <p className="mt-0.5 break-all text-xs text-gray-500">{stepDetail(s)}</p>
                )}
                {s.ask && (
                  <label className="mt-1 flex items-center gap-1.5 text-[11px] text-gray-600">
                    <input type="checkbox" checked={isGuarded(pb.steps, i)} onChange={() => toggleGuard(i)} />
                    Ask me each time this runs
                  </label>
                )}
              </div>"""),
 ("""                  {s.tag === 'ai' ? (""", """                  {s.kind === 'approve' ? (
                    <span className="rounded-full bg-amber-50 px-1.5 py-0.5 text-[10px] font-medium text-amber-700">you answer</span>
                  ) : s.tag === 'ai' ? ("""),
])
