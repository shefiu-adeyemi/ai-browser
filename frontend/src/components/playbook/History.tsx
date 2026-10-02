import { useEffect, useState } from 'react'
import { CheckCircle2, XCircle, AlertTriangle, StopCircle } from 'lucide-react'
import type { PlaybookRunRecord, PlaybookRunSummary } from '@/types'
import { KIND_LABEL, WATCH_LABEL, errText, formatWhen, readJson, reasonOf } from './shared'
import ResultTable from './ResultTable'
import ChangeList from './ChangeList'
import RepairsPanel from './RepairsPanel'

const STATUS: Record<string, { label: string; cls: string }> = {
  done: { label: 'Done', cls: 'bg-green-50 text-green-700' },
  failed: { label: 'Failed', cls: 'bg-red-50 text-red-700' },
  needs_you: { label: 'Needs you', cls: 'bg-amber-50 text-amber-700' },
  stopped: { label: 'Stopped', cls: 'bg-gray-100 text-gray-600' },
  waiting: { label: 'Waiting for you', cls: 'bg-amber-50 text-amber-700' },
  declined: { label: 'You said no', cls: 'bg-gray-100 text-gray-600' },
  expired: { label: 'No answer', cls: 'bg-gray-100 text-gray-600' },
  cancelled: { label: 'Cancelled', cls: 'bg-gray-100 text-gray-600' },
}

function StatusBadge({ status }: { status: string }) {
  const s = STATUS[status] ?? { label: status, cls: 'bg-gray-100 text-gray-600' }
  return <span className={`rounded-full px-2 py-0.5 text-[11px] font-medium ${s.cls}`}>{s.label}</span>
}

function StepIcon({ status }: { status: string }) {
  if (status === 'ok') return <CheckCircle2 size={14} className="mt-0.5 flex-shrink-0 text-green-600" aria-label="done" />
  if (status === 'needs_you') return <AlertTriangle size={14} className="mt-0.5 flex-shrink-0 text-amber-600" aria-label="needs you" />
  if (status === 'stopped') return <StopCircle size={14} className="mt-0.5 flex-shrink-0 text-gray-500" aria-label="stopped" />
  return <XCircle size={14} className="mt-0.5 flex-shrink-0 text-red-600" aria-label="failed" />
}

/** Past runs of one playbook, and the step-by-step account of any one of them. */
export default function History({ id, name, onBack, onEdit }: { id: string; name: string; onBack: () => void; onEdit: () => void }) {
  const [runs, setRuns] = useState<PlaybookRunSummary[] | null>(null)
  const [open, setOpen] = useState<PlaybookRunRecord | null>(null)
  const [error, setError] = useState('')

  useEffect(() => {
    let alive = true
    ;(async () => {
      try {
        const res = await fetch(`/playbooks/${id}/runs`)
        const data = await readJson(res)
        if (!alive) return
        if (!res.ok || !data.ok) { setError(reasonOf(res, data)); return }
        setRuns(data.runs)
      } catch (e) {
        if (alive) setError(`Couldn't load the runs (${errText(e)}).`)
      }
    })()
    return () => { alive = false }
  }, [id])

  const show = async (runId: string) => {
    setError('')
    try {
      const res = await fetch(`/playbooks/${id}/runs/${runId}`)
      const data = await readJson(res)
      if (!res.ok || !data.ok) { setError(reasonOf(res, data)); return }
      setOpen(data.run)
    } catch (e) {
      setError(`Couldn't open that run (${errText(e)}).`)
    }
  }

  if (open) {
    return (
      <>
        <div className="min-h-0 flex-1 space-y-3 overflow-y-auto px-5 py-4">
          <div className="flex flex-wrap items-center gap-2">
            <StatusBadge status={open.status} />
            {!open.attended && <span className="rounded-full bg-blue-50 px-2 py-0.5 text-[11px] font-medium text-blue-700">Scheduled</span>}
            <span className="text-xs text-gray-500">{formatWhen(open.started_at)} · {open.seconds}s · version {open.playbook_version}</span>
          </div>
          {open.error && <p className="rounded-lg bg-gray-50 px-3 py-2 text-xs text-gray-700">{open.error}</p>}
          {open.watch && <ChangeList watch={open.watch} />}
          {(open.repairs?.length ?? 0) > 0 && (
            <RepairsPanel playbookId={id} runId={open.run_id} repairs={open.repairs!} onApplied={() => { /* the editor shows the new version */ }} />
          )}
          {open.screenshot && (
            <div>
              <p className="text-xs font-medium text-gray-700">What the page looked like when it stopped</p>
              <img src={`/playbooks/${id}/runs/${open.run_id}/screenshot`} alt="The page when the run stopped"
                className="mt-1 max-h-72 w-full rounded-lg border border-gray-200 object-contain object-top bg-gray-50" />
            </div>
          )}
          {Object.keys(open.inputs ?? {}).length > 0 && (
            <div>
              <p className="text-xs font-medium text-gray-700">Inputs</p>
              <ul className="mt-1 text-xs text-gray-600">
                {Object.entries(open.inputs).map(([k, v]) => <li key={k} className="break-all"><span className="font-mono">{k}</span>: {v}</li>)}
              </ul>
            </div>
          )}
          <ol className="space-y-1.5">
            {open.steps.map((s, i) => (
              <li key={`${s.id}-${i}`} className="flex gap-2 rounded-lg border border-gray-200 px-3 py-2">
                <StepIcon status={s.status} />
                <div className="min-w-0 flex-1 text-xs">
                  <p className="font-medium text-gray-900">
                    {KIND_LABEL[s.kind] ?? s.kind}
                    <span className="ml-1.5 font-normal text-gray-500">{s.note}</span>
                  </p>
                  {s.detail && <p className="mt-0.5 text-gray-700">{s.detail}</p>}
                  <p className="mt-0.5 text-[11px] text-gray-400">
                    {s.seconds}s
                    {s.matched ? ` · found by ${s.matched}` : ''}
                    {s.model ? ` · read by ${s.model}` : ''}
                    {s.tokens ? ` · ${s.tokens >= 1000 ? `${(s.tokens / 1000).toFixed(1)}k` : s.tokens} tokens` : ''}
                    {s.approval === 'preapproved' ? ' · pre-approved' : s.approval === 'asked' ? ' · you approved it' : ''}
                  </p>
                </div>
              </li>
            ))}
          </ol>
          {open.outputs?.map((o, i) => (
            <div key={`${o.step_id}-${i}`} className="rounded-lg bg-gray-50 px-3 py-2">
              <p className="text-xs font-medium text-gray-700">{o.query || 'Result'}</p>
              {o.table ? <div className="mt-1"><ResultTable table={o.table} /></div>
                : <p className="mt-1 whitespace-pre-wrap break-words text-xs text-gray-600">{o.text}</p>}
            </div>
          ))}
          {error && <p role="alert" className="rounded-lg bg-red-50 px-3 py-2 text-xs text-red-700">{error}</p>}
        </div>
        <div className="flex justify-end gap-2 border-t border-gray-100 px-5 py-3">
          {open.status !== 'done' && (
            <button onClick={onEdit} className="rounded-lg border border-gray-300 px-3 py-1.5 text-xs font-medium text-gray-700 hover:bg-gray-50">Edit the playbook</button>
          )}
          <button onClick={() => setOpen(null)} className="rounded-lg border border-gray-300 px-3 py-1.5 text-xs font-medium text-gray-700 hover:bg-gray-50">Back to runs</button>
        </div>
      </>
    )
  }

  return (
    <>
      <div className="min-h-0 flex-1 overflow-y-auto px-5 py-4">
        <div className="mb-2 flex items-center justify-between gap-3">
          <p className="text-xs text-gray-500">Runs of "{name}", newest first.</p>
          {runs !== null && runs.length > 0 && (
            <span className="flex flex-shrink-0 gap-3">
              <a href={`/playbooks/${id}/results.csv`} download className="text-xs text-brand hover:underline">Download results (CSV)</a>
              <a href={`/playbooks/${id}/results.json`} download className="text-xs text-brand hover:underline">JSON</a>
            </span>
          )}
        </div>
        {error && <p role="alert" className="mb-2 rounded-lg bg-red-50 px-3 py-2 text-xs text-red-700">{error}</p>}
        {runs === null && !error && <p className="py-8 text-center text-xs text-gray-400">Loading…</p>}
        {runs !== null && runs.length === 0 && <p className="py-8 text-center text-xs text-gray-400">No runs yet.</p>}
        <ul className="space-y-1.5">
          {runs?.map((r) => (
            <li key={r.run_id}>
              <button onClick={() => show(r.run_id)} className="flex w-full items-center justify-between gap-3 rounded-lg border border-gray-200 px-3 py-2 text-left hover:bg-gray-50">
                <div className="min-w-0">
                  <p className="text-xs text-gray-900">{formatWhen(r.started_at)} · {r.ok_steps}/{r.steps} steps · {r.seconds}s{!r.attended ? ' · scheduled' : ''}</p>
                  {r.error && <p className="truncate text-[11px] text-gray-500">{r.error}</p>}
                </div>
                <span className="flex flex-shrink-0 items-center gap-1.5">
                  {r.watch && WATCH_LABEL[r.watch] && r.watch !== 'skipped' && (
                    <span className={`rounded-full px-2 py-0.5 text-[11px] font-medium ${WATCH_LABEL[r.watch].cls}`}>{WATCH_LABEL[r.watch].label}</span>
                  )}
                  <StatusBadge status={r.status} />
                </span>
              </button>
            </li>
          ))}
        </ul>
      </div>
      <div className="flex justify-end border-t border-gray-100 px-5 py-3">
        <button onClick={onBack} className="rounded-lg border border-gray-300 px-3 py-1.5 text-xs font-medium text-gray-700 hover:bg-gray-50">Back</button>
      </div>
    </>
  )
}
