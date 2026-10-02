import { AlertTriangle, CheckCircle2, CircleSlash, Hand, XCircle } from 'lucide-react'
import type { DescribedDraft, DraftReportItem } from '@/types'
import ResultTable from './ResultTable'

const LOOK: Record<DraftReportItem['status'], { icon: JSX.Element; label: string; cls: string }> = {
  verified: { icon: <CheckCircle2 size={14} className="text-green-600" aria-hidden />, label: 'Worked on the page', cls: 'text-green-700' },
  not_run: { icon: <Hand size={14} className="text-amber-600" aria-hidden />, label: 'Added, not run', cls: 'text-amber-700' },
  unresolved: { icon: <AlertTriangle size={14} className="text-red-600" aria-hidden />, label: "Couldn't do this", cls: 'text-red-700' },
  failed: { icon: <XCircle size={14} className="text-red-600" aria-hidden />, label: 'Failed on the page', cls: 'text-red-700' },
  left_out: { icon: <CircleSlash size={14} className="text-gray-400" aria-hidden />, label: 'Not tried', cls: 'text-gray-500' },
}

/** How each step of a drafted playbook fared when it was tried on the page, what it will read, and what drafting cost. */
export default function DraftReport({ draft }: { draft: Pick<DescribedDraft, 'report' | 'tokens' | 'model' | 'counts'> }) {
  const bad = (draft.counts.unresolved ?? 0) + (draft.counts.failed ?? 0)
  const tokens = draft.tokens >= 1000 ? `${(draft.tokens / 1000).toFixed(1)}k` : String(draft.tokens)
  return (
    <div className="rounded-lg border border-purple-200 bg-purple-50/40 px-3 py-2.5">
      <p className="text-xs font-medium text-purple-900">How the draft did when it was tried in the browser</p>
      <p className="mt-0.5 text-[11px] text-gray-600">
        {bad > 0 ? `${bad} step${bad === 1 ? '' : 's'} need${bad === 1 ? 's' : ''} your attention. ` : 'Everything it tried worked. '}
        Drafting used about {tokens} tokens{draft.model ? ` (${draft.model})` : ''}. Replays cost nothing.
      </p>
      <ol className="mt-2 space-y-1.5">
        {draft.report.map((r) => {
          const look = LOOK[r.status] ?? LOOK.left_out
          return (
            <li key={r.n} className="flex gap-2 text-xs">
              <span className="mt-0.5 flex-shrink-0">{look.icon}</span>
              <div className="min-w-0 flex-1">
                <p className="break-words text-gray-900">{r.text}</p>
                <p className={`text-[11px] ${look.cls}`}>{look.label}{r.detail ? `: ${r.detail}` : ''}</p>
                {r.sample && (
                  <div className="mt-1">
                    <ResultTable table={{ columns: r.sample.columns, rows: r.sample.rows, truncated: r.sample.total > r.sample.rows.length, total: r.sample.total }} limit={3} />
                  </div>
                )}
              </div>
            </li>
          )
        })}
      </ol>
    </div>
  )
}
