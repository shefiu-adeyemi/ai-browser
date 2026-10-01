import { useEffect, useState } from 'react'
import { Plus, Trash2 } from 'lucide-react'
import type { Cadence, CadenceKind, PlaybookSummary, Playbook, ScheduleView } from '@/types'
import { errText, readJson, reasonOf } from './shared'

const DAYS = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun']

type When = 'daily' | 'weekly' | 'interval' | 'once'

interface Props {
  /** The schedule being changed, or null for a new one. */
  existing: ScheduleView | null
  onCancel: () => void
  onSaved: (name: string) => void
}

function toLocalInput(iso: string | null | undefined): string {
  return iso ? iso.slice(0, 16) : ''
}

/** Create or change a schedule: which playbook, when, with what inputs, and what to do when a row can't run. */
export default function ScheduleForm({ existing, onCancel, onSaved }: Props) {
  const [playbooks, setPlaybooks] = useState<PlaybookSummary[] | null>(null)
  const [pb, setPb] = useState<Playbook | null>(null)
  const [loadError, setLoadError] = useState('')
  const [problems, setProblems] = useState<string[]>([])
  const [busy, setBusy] = useState(false)

  const [name, setName] = useState(existing?.name ?? '')
  const [playbookId, setPlaybookId] = useState(existing?.playbook_id ?? '')
  const [when, setWhen] = useState<When>((existing?.cadence.kind as When) ?? 'daily')
  const [at, setAt] = useState(existing?.cadence.at ?? '09:00')
  const [weekdays, setWeekdays] = useState<number[]>(existing?.cadence.weekdays ?? [0, 1, 2, 3, 4])
  const [everyValue, setEveryValue] = useState(() => {
    const m = existing?.cadence.every_minutes ?? 60
    return m % 60 === 0 ? m / 60 : m
  })
  const [everyUnit, setEveryUnit] = useState<'minutes' | 'hours'>(() => {
    const m = existing?.cadence.every_minutes ?? 60
    return m % 60 === 0 ? 'hours' : 'minutes'
  })
  const [runAt, setRunAt] = useState(toLocalInput(existing?.cadence.run_at))
  const [rows, setRows] = useState<Record<string, string>[]>(existing?.items ?? [{}])
  const [onFailure, setOnFailure] = useState<'stop' | 'skip'>(existing?.on_failure ?? 'stop')
  const [allowSensitive, setAllowSensitive] = useState(existing?.allow_sensitive ?? false)
  const [enabled, setEnabled] = useState(existing?.enabled ?? true)
  const [pasted, setPasted] = useState('')

  useEffect(() => {
    let alive = true
    ;(async () => {
      try {
        const res = await fetch('/playbooks')
        const data = await readJson(res)
        if (!alive) return
        if (!res.ok || !data.ok) { setLoadError(reasonOf(res, data)); return }
        setPlaybooks(data.playbooks)
      } catch (e) {
        if (alive) setLoadError(`Couldn't load your playbooks (${errText(e)}).`)
      }
    })()
    return () => { alive = false }
  }, [])

  // The chosen playbook decides which inputs there are, and whether it has steps that need a second yes.
  useEffect(() => {
    if (!playbookId) { setPb(null); return }
    let alive = true
    ;(async () => {
      try {
        const res = await fetch(`/playbooks/${playbookId}`)
        const data = await readJson(res)
        if (alive && res.ok && data.ok) setPb(data.playbook)
      } catch { /* the playbook list already told the user if the server is unreachable */ }
    })()
    return () => { alive = false }
  }, [playbookId])

  if (loadError) {
    return (
      <div className="p-5">
        <p role="alert" className="rounded-lg bg-red-50 px-3 py-2 text-sm text-red-700">{loadError}</p>
        <div className="mt-4 flex justify-end"><button onClick={onCancel} className="rounded-lg border border-gray-300 px-3 py-1.5 text-xs text-gray-700">Back</button></div>
      </div>
    )
  }
  if (!playbooks) return <p className="p-8 text-center text-xs text-gray-400">Opening…</p>

  const chosen = playbooks.find((p) => p.id === playbookId)
  const variables = pb?.variables ?? []
  const askCount = pb ? pb.steps.filter((s) => s.ask).length : 0
  const handoffs = pb ? pb.steps.filter((s) => s.kind === 'handoff').length : 0

  const setCell = (i: number, key: string, value: string) =>
    setRows(rows.map((r, n) => (n === i ? { ...r, [key]: value } : r)))

  const addPasted = () => {
    const first = variables[0]
    if (!first) return
    const lines = pasted.split(/\r?\n/).map((l) => l.trim()).filter(Boolean)
    if (lines.length === 0) return
    const keep = rows.filter((r) => Object.values(r).some((v) => v.trim() !== ''))
    setRows([...keep, ...lines.map((l) => ({ [first.name]: l }))])
    setPasted('')
  }

  const buildCadence = (): Cadence => {
    const kind = when as CadenceKind
    if (kind === 'daily') return { kind, at }
    if (kind === 'weekly') return { kind, at, weekdays }
    if (kind === 'interval') return { kind, every_minutes: Math.round(everyValue * (everyUnit === 'hours' ? 60 : 1)) }
    return { kind, run_at: runAt.length === 16 ? `${runAt}:00` : runAt }
  }

  const save = async () => {
    setBusy(true)
    setProblems([])
    try {
      const res = await fetch('/schedules', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          schedule: {
            id: existing?.id, name, playbook_id: playbookId, enabled, cadence: buildCadence(),
            items: variables.length === 0 ? [{}] : rows, on_failure: onFailure, allow_sensitive: allowSensitive,
          },
        }),
      })
      const data = await readJson(res)
      if (!res.ok || !data.ok) { setProblems(data.problems?.length ? data.problems : [reasonOf(res, data)]); return }
      onSaved(data.schedule.name)
    } catch (e) {
      setProblems([`Couldn't save (${errText(e)}).`])
    } finally { setBusy(false) }
  }

  const field = 'mt-1 w-full rounded-lg border border-gray-300 px-3 py-2 text-sm focus:border-brand focus:outline-none'

  return (
    <>
      <div className="min-h-0 flex-1 space-y-4 overflow-y-auto px-5 py-4">
        <label className="block">
          <span className="text-xs font-medium text-gray-700">Name</span>
          <input value={name} maxLength={120} onChange={(e) => setName(e.target.value)} className={field} placeholder="For example: Morning profile check" />
        </label>

        <label className="block">
          <span className="text-xs font-medium text-gray-700">Playbook</span>
          <select value={playbookId} onChange={(e) => { setPlaybookId(e.target.value); setRows([{}]) }} className={field}>
            <option value="">Choose a playbook…</option>
            {playbooks.map((p) => (
              <option key={p.id} value={p.id} disabled={!p.approved}>{p.name}{p.approved ? '' : ' (not approved)'}</option>
            ))}
          </select>
          <span className="mt-0.5 block text-[11px] text-gray-400">Only approved playbooks can run while you're away. Approve one with Edit.</span>
        </label>
        {chosen && handoffs > 0 && (
          <p className="rounded-lg bg-red-50 px-3 py-2 text-xs text-red-700">
            This playbook has {handoffs === 1 ? 'a step' : `${handoffs} steps`} that needs you by hand, so it can't run while you're away.
          </p>
        )}

        <fieldset>
          <legend className="text-xs font-medium text-gray-700">When</legend>
          <div className="mt-1 flex flex-wrap gap-1.5">
            {([['daily', 'Every day'], ['weekly', 'Certain days'], ['interval', 'Every so often'], ['once', 'Once']] as const).map(([k, label]) => (
              <button key={k} type="button" onClick={() => setWhen(k)} aria-pressed={when === k}
                className={`rounded-full border px-3 py-1 text-xs ${when === k ? 'border-brand bg-brand/10 font-medium text-brand' : 'border-gray-300 text-gray-600 hover:bg-gray-50'}`}>
                {label}
              </button>
            ))}
          </div>
          <div className="mt-2 flex flex-wrap items-center gap-2 text-xs text-gray-600">
            {(when === 'daily' || when === 'weekly') && (
              <label className="flex items-center gap-1.5">at <input type="time" value={at} onChange={(e) => setAt(e.target.value)} className="rounded-md border border-gray-300 px-2 py-1" /></label>
            )}
            {when === 'weekly' && (
              <div className="flex flex-wrap gap-1">
                {DAYS.map((d, i) => (
                  <label key={d} className={`cursor-pointer rounded-md border px-2 py-1 ${weekdays.includes(i) ? 'border-brand bg-brand/10 text-brand' : 'border-gray-300'}`}>
                    <input type="checkbox" className="sr-only" checked={weekdays.includes(i)}
                      onChange={(e) => setWeekdays(e.target.checked ? [...weekdays, i].sort() : weekdays.filter((x) => x !== i))} />
                    {d}
                  </label>
                ))}
              </div>
            )}
            {when === 'interval' && (
              <label className="flex items-center gap-1.5">every
                <input type="number" min={1} value={everyValue} onChange={(e) => setEveryValue(Number(e.target.value))} className="w-20 rounded-md border border-gray-300 px-2 py-1" />
                <select value={everyUnit} onChange={(e) => setEveryUnit(e.target.value as 'minutes' | 'hours')} className="rounded-md border border-gray-300 px-2 py-1">
                  <option value="minutes">minutes</option><option value="hours">hours</option>
                </select>
              </label>
            )}
            {when === 'once' && (
              <label className="flex items-center gap-1.5">on <input type="datetime-local" value={runAt} onChange={(e) => setRunAt(e.target.value)} className="rounded-md border border-gray-300 px-2 py-1" /></label>
            )}
          </div>
          <p className="mt-1 text-[11px] text-gray-400">It only runs while this app is open. A run missed while it was closed is skipped, not run late.</p>
        </fieldset>

        {variables.length > 0 && (
          <div>
            <p className="text-xs font-medium text-gray-700">Inputs: one row per run</p>
            <div className="mt-1 space-y-1.5">
              <div className="grid gap-1.5 text-[11px] text-gray-500" style={{ gridTemplateColumns: `repeat(${variables.length}, minmax(0, 1fr)) 28px` }}>
                {variables.map((v) => <span key={v.name}>{v.label || v.name}</span>)}<span />
              </div>
              {rows.map((r, i) => (
                <div key={i} className="grid items-center gap-1.5" style={{ gridTemplateColumns: `repeat(${variables.length}, minmax(0, 1fr)) 28px` }}>
                  {variables.map((v) => (
                    <input key={v.name} value={r[v.name] ?? ''} onChange={(e) => setCell(i, v.name, e.target.value)}
                      aria-label={`${v.label || v.name}, row ${i + 1}`} className="rounded-md border border-gray-300 px-2 py-1 text-xs focus:border-brand focus:outline-none" />
                  ))}
                  <button type="button" aria-label={`Remove row ${i + 1}`} disabled={rows.length === 1} onClick={() => setRows(rows.filter((_, n) => n !== i))}
                    className="rounded p-1 text-gray-300 hover:text-red-600 disabled:opacity-30"><Trash2 size={13} /></button>
                </div>
              ))}
              <button type="button" onClick={() => setRows([...rows, {}])} className="inline-flex items-center gap-1 text-xs text-brand"><Plus size={12} /> Add a row</button>
            </div>
            <details className="mt-2">
              <summary className="cursor-pointer text-xs text-gray-500">Paste a list</summary>
              <textarea value={pasted} onChange={(e) => setPasted(e.target.value)} rows={4} placeholder={`One ${variables[0].label || variables[0].name} per line`}
                className="mt-1 w-full rounded-lg border border-gray-300 px-2 py-1.5 text-xs focus:border-brand focus:outline-none" />
              <button type="button" onClick={addPasted} className="mt-1 rounded-md border border-gray-300 px-2 py-1 text-xs text-gray-700 hover:bg-gray-50">Add these as rows</button>
              {variables.length > 1 && <span className="ml-2 text-[11px] text-gray-400">Fills "{variables[0].label || variables[0].name}"; fill the others in the table.</span>}
            </details>
          </div>
        )}

        <fieldset>
          <legend className="text-xs font-medium text-gray-700">If a run can't finish (for example it needs you to sign in)</legend>
          <label className="mt-1 flex items-start gap-2 text-xs text-gray-700">
            <input type="radio" name="onfail" checked={onFailure === 'stop'} onChange={() => setOnFailure('stop')} className="mt-0.5" />
            <span><span className="font-medium">Stop and tell me.</span> The schedule pauses until you resume it.</span>
          </label>
          <label className="mt-1 flex items-start gap-2 text-xs text-gray-700">
            <input type="radio" name="onfail" checked={onFailure === 'skip'} onChange={() => setOnFailure('skip')} className="mt-0.5" />
            <span><span className="font-medium">Tell me and carry on</span> with the next row.</span>
          </label>
        </fieldset>

        {askCount > 0 && (
          <label className="flex items-start gap-2 rounded-lg border border-amber-200 bg-amber-50 px-3 py-2 text-xs text-amber-900">
            <input type="checkbox" checked={allowSensitive} onChange={(e) => setAllowSensitive(e.target.checked)} className="mt-0.5" />
            <span><span className="font-medium">Let these steps run while I'm away.</span> {askCount} step{askCount === 1 ? '' : 's'} send something, post, or can't be undone.</span>
          </label>
        )}

        <label className="flex items-center gap-2 text-xs text-gray-700">
          <input type="checkbox" checked={enabled} onChange={(e) => setEnabled(e.target.checked)} /> Switched on
        </label>

        {problems.length > 0 && (
          <ul role="alert" className="space-y-1 rounded-lg bg-red-50 px-3 py-2 text-xs text-red-700">
            {problems.map((p, i) => <li key={i}>• {p}</li>)}
          </ul>
        )}
      </div>
      <div className="flex justify-end gap-2 border-t border-gray-100 px-5 py-3">
        <button onClick={onCancel} className="rounded-lg border border-gray-300 px-3 py-1.5 text-xs font-medium text-gray-700 hover:bg-gray-50">Back</button>
        <button disabled={busy || !name.trim() || !playbookId || !pb} onClick={save} className="rounded-lg bg-brand px-4 py-1.5 text-xs font-medium text-white disabled:opacity-40">
          {existing ? 'Save changes' : 'Create schedule'}
        </button>
      </div>
    </>
  )
}
