import type { PlaybookStep, PlaybookVariable } from '@/types'
import { KIND_LABEL, MAX_LOOP_ROWS, loopBody, stepDetail } from './shared'

interface Props {
  steps: PlaybookStep[]
  step: PlaybookStep
  index: number
  variable: PlaybookVariable | undefined
  onChange: (patch: Partial<PlaybookStep>) => void
  onVariable: (patch: Partial<PlaybookVariable>) => void
}

const input = 'rounded-md border border-gray-300 px-2 py-1 text-xs focus:border-brand focus:outline-none'

/** The settings of a "For each row" step: which column to go through, what to repeat, and how a bad row is treated. */
export default function ForEachEditor({ steps, step, index, variable, onChange, onVariable }: Props) {
  const tables = steps.slice(0, index).filter((s) => s.kind === 'scrape')
  const source = tables.find((s) => s.id === step.source)
  const columns = (source?.fields ?? []).map((f) => f.name)
  const body = loopBody(steps, index)
  const label = `step ${index + 1}`
  return (
    <div className="mt-1 space-y-2">
      <div className="flex flex-wrap items-center gap-1.5 text-xs text-gray-700">
        <span>Go through the</span>
        <select aria-label={`Column for ${label}`} value={step.column ?? ''} onChange={(e) => onChange({ column: e.target.value })} className={input}>
          {!columns.includes(step.column ?? '') && <option value={step.column ?? ''}>{step.column || 'Choose a column'}</option>}
          {columns.map((c) => <option key={c} value={c}>{c}</option>)}
        </select>
        <span>column of</span>
        <select aria-label={`Table for ${label}`} value={step.source ?? ''} onChange={(e) => onChange({ source: e.target.value, column: '' })} className={input}>
          {!source && <option value={step.source ?? ''}>{step.source ? `${step.source} (not a table step)` : 'Choose a table'}</option>}
          {tables.map((t) => <option key={t.id} value={t.id}>{t.note || t.id}</option>)}
        </select>
        <span>, repeating the next</span>
        <input aria-label={`Steps to repeat for ${label}`} type="number" min={1} max={20} value={step.body ?? 1} className={`${input} w-14`}
          onChange={(e) => onChange({ body: Math.max(1, Math.min(20, Number(e.target.value) || 1)) })} />
        <span>step{(step.body ?? 1) === 1 ? '' : 's'}:</span>
      </div>
      <ol className="ml-4 list-decimal text-[11px] text-gray-500">
        {body.map((b) => <li key={b.id} className="break-all">{b.note || KIND_LABEL[b.kind] || b.kind}{stepDetail(b) ? ` · ${stepDetail(b)}` : ''}</li>)}
        {body.length < (step.body ?? 1) && <li className="text-amber-700">There are not that many steps after this one.</li>}
      </ol>
      <div className="flex flex-wrap items-center gap-1.5 text-xs text-gray-700">
        <span>Each value is called</span>
        <span className="rounded bg-gray-100 px-1.5 py-0.5 font-mono">{`{{${step.variable ?? ''}}}`}</span>
        <select aria-label={`What each value is for ${label}`} value={variable?.type ?? 'url'} onChange={(e) => onVariable({ type: e.target.value as PlaybookVariable['type'] })} className={input}>
          <option value="url">a web address</option><option value="text">text</option><option value="number">a number</option>
        </select>
        {(variable?.type ?? 'url') === 'url' && (
          <label className="flex items-center gap-1">
            only on
            <input aria-label={`Sites each value may open for ${label}`} value={(variable?.allowed_hosts ?? []).join(', ')} placeholder="example.com"
              onChange={(e) => onVariable({ allowed_hosts: e.target.value.split(',').map((h) => h.trim().toLowerCase()).filter(Boolean) })} className={`${input} w-44 font-mono`} />
          </label>
        )}
      </div>
      <div className="flex flex-wrap items-center gap-1.5 text-xs text-gray-700">
        <label className="flex items-center gap-1">At most
          <input aria-label={`Most rows for ${label}`} type="number" min={1} max={MAX_LOOP_ROWS} value={step.max_rows ?? ''} placeholder="20" className={`${input} w-16`}
            onChange={(e) => onChange({ max_rows: e.target.value === '' ? null : Math.max(1, Math.min(MAX_LOOP_ROWS, Number(e.target.value))) })} />
          rows.
        </label>
        <label className="flex items-center gap-1">If a row fails
          <select aria-label={`When a row fails for ${label}`} value={step.on_failure ?? 'stop'} onChange={(e) => onChange({ on_failure: e.target.value as 'stop' | 'skip' })} className={input}>
            <option value="stop">stop the run</option><option value="skip">skip that row and carry on</option>
          </select>
        </label>
      </div>
      <p className="text-[11px] text-gray-400">
        Values come from the page, so each one is checked before it is used (a web address must be on the sites above). Steps that send or post can't be repeated, and loops
        aren't available on LinkedIn: use a schedule with one row per profile there.
      </p>
    </div>
  )
}
