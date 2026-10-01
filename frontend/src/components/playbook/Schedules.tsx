import { useCallback, useEffect, useState } from 'react'
import { Pause, Pencil, Play, PlayCircle, Trash2 } from 'lucide-react'
import type { LimitsInfo, ScheduleView } from '@/types'
import { errText, formatWhen, readJson, reasonOf } from './shared'

const STATUS: Record<string, { label: string; cls: string }> = {
  scheduled: { label: 'Scheduled', cls: 'bg-green-50 text-green-700' },
  running: { label: 'Running', cls: 'bg-blue-50 text-blue-700' },
  waiting: { label: 'Waiting', cls: 'bg-blue-50 text-blue-700' },
  paused: { label: 'Paused', cls: 'bg-amber-50 text-amber-700' },
  off: { label: 'Off', cls: 'bg-gray-100 text-gray-600' },
  finished: { label: 'Finished', cls: 'bg-gray-100 text-gray-600' },
}

const LIMIT_LABEL: Record<string, string> = {
  linkedin_profile_view: 'LinkedIn profile views',
  linkedin_sensitive: 'LinkedIn messages and requests',
}

function nextText(s: ScheduleView): string {
  if (s.status === 'paused') return s.paused_detail || 'Paused'
  if (s.status === 'waiting') return `${s.remaining} waiting for tomorrow's allowance`
  if (s.status === 'running') return `${s.remaining} row${s.remaining === 1 ? '' : 's'} left`
  if (s.status === 'off') return 'Switched off'
  if (!s.next_run) return 'No more runs'
  return `Next: ${new Date(s.next_run).toLocaleString(undefined, { weekday: 'short', month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' })}`
}

interface Props {
  onNew: () => void
  onEdit: (s: ScheduleView) => void
  notify: (t: string) => void
}

/** The schedules list: when each runs next, what happened last, and the switches (pause everything, limits). */
export default function Schedules({ onNew, onEdit, notify }: Props) {
  const [rows, setRows] = useState<ScheduleView[] | null>(null)
  const [pausedAll, setPausedAll] = useState(false)
  const [limits, setLimits] = useState<LimitsInfo | null>(null)
  const [problem, setProblem] = useState('')
  const [error, setError] = useState('')
  const [confirming, setConfirming] = useState<string | null>(null)
  const [editingLimits, setEditingLimits] = useState(false)

  const load = useCallback(async () => {
    try {
      const res = await fetch('/schedules')
      const data = await readJson(res)
      if (!res.ok || !data.ok) throw new Error(reasonOf(res, data))
      setRows(data.schedules)
      setPausedAll(data.paused_all)
      setLimits(data.limits)
      setProblem(data.problem || '')
      setError('')
    } catch (e) {
      setError(`Couldn't load your schedules (${errText(e)}).`)
    }
  }, [])
  useEffect(() => {
    load()
    const t = setInterval(load, 10000)            // status changes by itself while the dialog is open
    return () => clearInterval(t)
  }, [load])

  const call = async (url: string, init: RequestInit, done?: string) => {
    try {
      const res = await fetch(url, init)
      const data = await readJson(res)
      if (!res.ok || !data.ok) { setError(data.problems?.[0] || reasonOf(res, data)); return false }
      if (done) notify(done)
      await load()
      return true
    } catch (e) {
      setError(errText(e))
      return false
    }
  }
  const post = (url: string, body: unknown = {}, done?: string) =>
    call(url, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) }, done)

  const toggle = (s: ScheduleView) =>
    post('/schedules', { schedule: { ...s, enabled: !s.enabled } }, s.enabled ? `Switched off "${s.name}".` : `Switched on "${s.name}".`)

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <div className="flex items-center justify-between gap-3 px-5 py-3">
        <label className="flex items-center gap-2 text-xs text-gray-700">
          <input type="checkbox" checked={pausedAll} onChange={(e) => post('/schedules/pause-all', { paused: e.target.checked })} />
          Pause all schedules
        </label>
        <button onClick={onNew} className="flex-shrink-0 rounded-lg bg-brand px-3 py-1.5 text-xs font-medium text-white">New schedule</button>
      </div>
      {pausedAll && <p className="mx-5 mb-2 rounded-lg bg-amber-50 px-3 py-2 text-xs text-amber-800">Nothing runs by itself while this is ticked.</p>}
      {problem && <p role="alert" className="mx-5 mb-2 rounded-lg bg-red-50 px-3 py-2 text-xs text-red-700">{problem}</p>}
      {error && <p role="alert" className="mx-5 mb-2 rounded-lg bg-red-50 px-3 py-2 text-xs text-red-700">{error}</p>}

      <div className="min-h-0 flex-1 overflow-y-auto px-5 pb-5">
        {rows === null && !error && <p className="py-8 text-center text-xs text-gray-400">Loading…</p>}
        {rows !== null && rows.length === 0 && (
          <p className="py-8 text-center text-xs text-gray-400">No schedules yet. Create one to run a playbook by itself.</p>
        )}
        <ul className="space-y-2">
          {rows?.map((s) => {
            const st = STATUS[s.status] ?? STATUS.off
            return (
              <li key={s.id} className="rounded-xl border border-gray-200 p-3">
                <div className="flex items-start justify-between gap-3">
                  <div className="min-w-0">
                    <p className="truncate text-sm font-medium text-gray-900">{s.name}</p>
                    <p className="mt-0.5 truncate text-xs text-gray-500">{s.playbook_name || 'Playbook missing'} · {s.cadence_text} · {s.items.length} row{s.items.length === 1 ? '' : 's'}</p>
                    <p className={`mt-0.5 text-xs ${s.status === 'paused' ? 'text-amber-700' : 'text-gray-500'}`}>{nextText(s)}</p>
                    {s.last_run_at && (
                      <p className="mt-0.5 truncate text-[11px] text-gray-400">
                        Last: {formatWhen(new Date(s.last_run_at).getTime() / 1000)} · {s.last_status}{s.last_detail ? ` · ${s.last_detail}` : ''}
                      </p>
                    )}
                    {s.missed > 0 && <p className="text-[11px] text-gray-400">{s.missed} missed while the app was closed</p>}
                  </div>
                  <span className={`flex-shrink-0 rounded-full px-2 py-0.5 text-[11px] font-medium ${st.cls}`}>{st.label}</span>
                </div>
                <div className="mt-2 flex items-center justify-end gap-1.5">
                  {confirming === s.id ? (
                    <>
                      <button onClick={async () => { if (await call(`/schedules/${s.id}`, { method: 'DELETE' }, `Deleted "${s.name}".`)) setConfirming(null) }}
                        className="rounded-md bg-red-600 px-2 py-1 text-xs font-medium text-white">Delete</button>
                      <button onClick={() => setConfirming(null)} className="rounded-md border border-gray-300 px-2 py-1 text-xs text-gray-700">Keep</button>
                    </>
                  ) : (
                    <>
                      <button aria-label={`Delete ${s.name}`} title="Delete" onClick={() => setConfirming(s.id)} className="rounded-md p-1 text-gray-400 hover:bg-gray-100 hover:text-red-600"><Trash2 size={14} /></button>
                      <button aria-label={`Edit ${s.name}`} title="Edit" onClick={() => onEdit(s)} className="rounded-md p-1 text-gray-400 hover:bg-gray-100 hover:text-gray-700"><Pencil size={14} /></button>
                      <button aria-label={s.enabled ? `Switch off ${s.name}` : `Switch on ${s.name}`} title={s.enabled ? 'Switch off' : 'Switch on'} onClick={() => toggle(s)}
                        className="rounded-md p-1 text-gray-400 hover:bg-gray-100 hover:text-gray-700">{s.enabled ? <Pause size={14} /> : <Play size={14} />}</button>
                      {s.status === 'paused' && (
                        <button onClick={() => post(`/schedules/${s.id}/resume`, {}, `Resumed "${s.name}".`)} className="rounded-md border border-amber-300 bg-amber-50 px-2 py-1 text-xs font-medium text-amber-800">Resume</button>
                      )}
                      <button onClick={() => post(`/schedules/${s.id}/run-now`, {}, `Started "${s.name}" now.`)} disabled={s.status === 'running' || s.status === 'waiting' || s.status === 'paused'}
                        className="inline-flex items-center gap-1 rounded-md bg-brand px-2.5 py-1 text-xs font-medium text-white disabled:cursor-not-allowed disabled:opacity-40"><PlayCircle size={12} /> Run now</button>
                    </>
                  )}
                </div>
              </li>
            )
          })}
        </ul>

        {limits && (
          <div className="mt-5 rounded-xl bg-gray-50 p-3">
            <div className="flex items-center justify-between gap-2">
              <p className="text-xs font-medium text-gray-700">Daily limits (protect your account)</p>
              <button onClick={() => setEditingLimits(!editingLimits)} className="text-xs text-brand">{editingLimits ? 'Close' : 'Change'}</button>
            </div>
            <ul className="mt-1 space-y-0.5 text-xs text-gray-600">
              {Object.entries(limits.usage).map(([k, u]) => (
                <li key={k}>{LIMIT_LABEL[k] ?? k}: <span className="font-medium">{u.used}</span> of {u.cap} today</li>
              ))}
              <li className="text-gray-400">At least {limits.gap.min} s (plus up to {limits.gap.jitter} s) between two runs on the same site.</li>
            </ul>
            {editingLimits && <LimitsEditor limits={limits} onSaved={(l) => { setLimits(l); setEditingLimits(false); notify('Saved the limits.') }} />}
          </div>
        )}
      </div>
    </div>
  )
}

function LimitsEditor({ limits, onSaved }: { limits: LimitsInfo; onSaved: (l: LimitsInfo) => void }) {
  const [vals, setVals] = useState(() => JSON.parse(JSON.stringify(limits)) as LimitsInfo)
  const [problems, setProblems] = useState<string[]>([])
  const num = (v: string) => (v === '' ? 0 : Number(v))

  const save = async () => {
    try {
      const res = await fetch('/scheduler/limits', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ allowances: vals.allowances, gap: vals.gap }),
      })
      const data = await readJson(res)
      if (!res.ok || !data.ok) { setProblems(data.problems?.length ? data.problems : [reasonOf(res, data)]); return }
      onSaved(data.limits)
    } catch (e) {
      setProblems([errText(e)])
    }
  }

  const box = 'w-16 rounded-md border border-gray-300 px-1.5 py-1 text-xs'
  return (
    <div className="mt-2 space-y-2 border-t border-gray-200 pt-2 text-xs text-gray-600">
      {Object.entries(vals.allowances).map(([k, a]) => (
        <div key={k} className="flex flex-wrap items-center gap-2">
          <span className="w-44">{LIMIT_LABEL[k] ?? k}</span>
          start <input aria-label={`${k} start`} className={box} value={a.start} onChange={(e) => setVals({ ...vals, allowances: { ...vals.allowances, [k]: { ...a, start: num(e.target.value) } } })} />
          up to <input aria-label={`${k} ceiling`} className={box} value={a.ceiling} onChange={(e) => setVals({ ...vals, allowances: { ...vals.allowances, [k]: { ...a, ceiling: num(e.target.value) } } })} />
          +<input aria-label={`${k} ramp`} className={box} value={a.ramp_per_week} onChange={(e) => setVals({ ...vals, allowances: { ...vals.allowances, [k]: { ...a, ramp_per_week: num(e.target.value) } } })} /> a week
        </div>
      ))}
      <div className="flex flex-wrap items-center gap-2">
        <span className="w-44">Gap between runs (seconds)</span>
        at least <input aria-label="gap minimum" className={box} value={vals.gap.min} onChange={(e) => setVals({ ...vals, gap: { ...vals.gap, min: num(e.target.value) } })} />
        plus up to <input aria-label="gap jitter" className={box} value={vals.gap.jitter} onChange={(e) => setVals({ ...vals, gap: { ...vals.gap, jitter: num(e.target.value) } })} />
      </div>
      <p className="text-[11px] text-gray-400">Published safe ranges for an established LinkedIn account are about 150-300 profile views a day. Starting low and rising each week is gentler on new accounts.</p>
      {problems.length > 0 && <ul role="alert" className="space-y-0.5 text-red-700">{problems.map((p, i) => <li key={i}>• {p}</li>)}</ul>}
      <button onClick={save} className="rounded-md bg-brand px-3 py-1 text-xs font-medium text-white">Save limits</button>
    </div>
  )
}
