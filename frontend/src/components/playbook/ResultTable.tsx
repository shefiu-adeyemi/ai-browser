import type { ScrapedTable } from '@/types'

interface Props {
  table: ScrapedTable
  /** How many rows to draw; the rest are in the download. */
  limit?: number
}

/** A scraped table: the first rows, with a note when the page had more than was read or shown. */
export default function ResultTable({ table, limit = 50 }: Props) {
  const shown = table.rows.slice(0, limit)
  return (
    <div>
      <div className="max-h-64 overflow-auto rounded border border-gray-200 bg-white">
        <table className="w-full border-collapse text-left text-xs">
          <thead className="sticky top-0 bg-gray-100 text-gray-700">
            <tr>{table.columns.map((c) => <th key={c} className="whitespace-nowrap px-2 py-1 font-medium">{c}</th>)}</tr>
          </thead>
          <tbody>
            {shown.map((row, i) => (
              <tr key={i} className="border-t border-gray-100 text-gray-700">
                {table.columns.map((c, j) => <td key={c} className="max-w-[16rem] truncate px-2 py-1" title={row[j] ?? ''}>{row[j] ?? ''}</td>)}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <p className="mt-1 text-[11px] text-gray-500">
        {table.rows.length} row{table.rows.length === 1 ? '' : 's'}
        {table.truncated ? ` read (the page has ${table.total}; the rest were left out)` : ''}
        {table.rows.length > limit ? `, the first ${limit} shown here. Download the results for all of them.` : ''}
      </p>
    </div>
  )
}
