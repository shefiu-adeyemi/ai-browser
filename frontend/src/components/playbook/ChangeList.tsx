import type { WatchResult } from '@/types'
import { WATCH_LABEL } from './shared'

/** How a watched run compared with the last one: the badge, a plain sentence, and every changed, new and gone row. */
export default function ChangeList({ watch }: { watch: WatchResult }) {
  const label = WATCH_LABEL[watch.status] ?? WATCH_LABEL.skipped
  const d = watch.diff
  return (
    <div className="rounded-lg border border-gray-200 px-3 py-2">
      <div className="flex flex-wrap items-center gap-2">
        <span className={`rounded-full px-2 py-0.5 text-[11px] font-medium ${label.cls}`}>{label.label}</span>
        <p className="text-xs text-gray-700">
          {watch.status === 'changed' && `${watch.changed ?? 0} changed, ${watch.added ?? 0} new, ${watch.removed ?? 0} gone since the last look.`}
          {watch.status === 'baseline' && 'The first look: saved to compare the next run with.'}
          {watch.status === 'unchanged' && 'The same as the last look.'}
          {(watch.status === 'reset' || watch.status === 'skipped') && (watch.reason ?? '')}
        </p>
      </div>
      {d && (
        <ul className="mt-2 space-y-1 text-xs">
          {d.changed_rows.map((c, i) => (
            <li key={`c${i}`} className="break-words text-gray-700">
              <span className="font-medium">{c.key}</span>:{' '}
              {c.columns.map((x) => <span key={x.column} className="mr-2">{x.column} <span className="text-red-700 line-through">{x.before || '(empty)'}</span> → <span className="text-green-700">{x.after || '(empty)'}</span></span>)}
            </li>
          ))}
          {d.added_rows.map((r, i) => <li key={`a${i}`} className="break-words text-green-800"><span className="font-medium">New:</span> {r.key}</li>)}
          {d.removed_rows.map((r, i) => <li key={`r${i}`} className="break-words text-red-800"><span className="font-medium">Gone:</span> {r.key}</li>)}
          {d.cut && <li className="text-[11px] text-gray-500">The list was cut at its limit, so rows past it were not compared.</li>}
          {(watch.changed ?? 0) > d.changed_rows.length && <li className="text-[11px] text-gray-500">And more changed rows than are shown here.</li>}
        </ul>
      )}
    </div>
  )
}
