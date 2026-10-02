import { useEffect, useState } from 'react'
import { errText, readJson, reasonOf } from './shared'
import { rowsFromCsv } from './csv'

const MAX_ROWS = 200
const MAX_FILE = 1_000_000

interface Variable { name: string; label?: string }
interface CheckedRow { row: number; ok: boolean; problems: string[]; steps: string[] }
interface Check { rows: CheckedRow[]; total: number; bad: number; days: number | null; needs: Record<string, number> }
interface Source { id: string; name: string }
interface Results { columns: string[]; rows: string[][]; total: number }

interface Props {
  playbookId: string
  variables: Variable[]
  rows: Record<string, string>[]
  setRows: (rows: Record<string, string>[]) => void
}

const filled = (rows: Record<string, string>[]) => rows.filter((r) => Object.values(r).some((v) => v.trim() !== ''))

/** More ways to fill the rows of a schedule: a CSV file or paste, another playbook's results, and a dry run. */
export default function BatchInputs({ playbookId, variables, rows, setRows }: Props) {
  const [pasted, setPasted] = useState('')
  const [note, setNote] = useState('')
  const [sources, setSources] = useState<Source[]>([])
  const [sourceId, setSourceId] = useState('')
  const [results, setResults] = useState<Results | null>(null)
  const [column, setColumn] = useState('')
  const [target, setTarget] = useState(variables[0]?.name ?? '')
  const [check, setCheck] = useState<Check | null>(null)
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)

  useEffect(() => {
    fetch('/playbooks').then(readJson).then((d) => setSources(Array.isArray(d.playbooks) ? d.playbooks.filter((p: Source) => p.id !== playbookId) : [])).catch(() => setSources([]))
  }, [playbookId])

  const add = (incoming: Record<string, string>[], extra = '') => {
    const keep = filled(rows)
    const all = [...keep, ...incoming]
    const cut = all.length > MAX_ROWS
    setRows(all.length ? all.slice(0, MAX_ROWS) : [{}])
    setCheck(null)
    setNote(`Added ${Math.min(incoming.length, Math.max(0, MAX_ROWS - keep.length))} row${incoming.length === 1 ? '' : 's'}.${cut ? ` Only ${MAX_ROWS} rows fit in one schedule, so the rest were left out.` : ''}${extra}`)
  }

  const fromText = (text: string) => {
    const out = rowsFromCsv(text, variables)
    if (out.rows.length === 0) { setNote('No rows were found in that. Each line needs a value for ' + (variables[0].label || variables[0].name) + '.'); return }
    add(out.rows, out.ignored.length ? ` Left out columns that match no input: ${out.ignored.join(', ')}.` : '')
  }

  const onFile = async (file: File | undefined) => {
    if (!file) return
    setError('')
    if (file.size > MAX_FILE) { setNote("That file is too big (the limit is 1 MB). Split it and add the parts one at a time."); return }
    try { fromText(await file.text()) } catch (e) { setError(`Couldn't read that file (${errText(e)}).`) }
  }

  const loadResults = async (id: string) => {
    setSourceId(id); setResults(null); setColumn(''); setError('')
    if (!id) return
    try {
      const res = await fetch(`/playbooks/${id}/results/table`)
      const data = await readJson(res)
      if (!res.ok || !data.ok) { setError(reasonOf(res, data)); return }
      setResults(data)
      setColumn(data.columns[0] ?? '')
    } catch (e) { setError(`Couldn't load those results (${errText(e)}).`) }
  }

  const useColumn = () => {
    if (!results || !column || !target) return
    const at = results.columns.indexOf(column)
    const values = Array.from(new Set(results.rows.map((r) => (r[at] ?? '').trim()).filter(Boolean)))
    if (values.length === 0) { setNote('That column has no values yet.'); return }
    add(values.map((v) => ({ [target]: v })), values.length < results.rows.length ? ' Repeated values were added once.' : '')
  }

  const dryRun = async () => {
    setBusy(true); setError(''); setCheck(null)
    try {
      const res = await fetch(`/playbooks/${playbookId}/check-rows`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ rows: filled(rows) }),
      })
      const data = await readJson(res)
      if (!res.ok || !data.ok) { setError(reasonOf(res, data)); return }
      setCheck(data)
    } catch (e) { setError(`Couldn't check them (${errText(e)}).`) } finally { setBusy(false) }
  }

  const btn = 'rounded-md border border-gray-300 px-2 py-1 text-xs text-gray-700 hover:bg-gray-50 disabled:opacity-40'
  return (
    <div className="mt-2 space-y-2">
      <details>
        <summary className="cursor-pointer text-xs text-gray-500">Add rows from a file, a pasted list, or another playbook's results</summary>
        <div className="mt-2 space-y-3 rounded-lg bg-gray-50 p-2.5">
          <div>
            <label className="text-xs font-medium text-gray-700">A CSV file
              <input type="file" accept=".csv,.tsv,.txt,text/csv,text/plain" aria-label="Choose a CSV file"
                onChange={(e) => { onFile(e.target.files?.[0]); e.target.value = '' }} className="mt-1 block text-xs" />
            </label>
            <p className="mt-0.5 text-[11px] text-gray-400">
              A first line with the input names ({variables.map((v) => v.label || v.name).join(', ')}) picks the columns; without one, columns are taken in order.
            </p>
          </div>
          <div>
            <textarea value={pasted} onChange={(e) => setPasted(e.target.value)} rows={4} aria-label="Paste a list"
              placeholder={variables.length > 1 ? 'Paste rows copied from a spreadsheet' : `One ${variables[0].label || variables[0].name} per line`}
              className="w-full rounded-lg border border-gray-300 px-2 py-1.5 text-xs focus:border-brand focus:outline-none" />
            <button type="button" disabled={!pasted.trim()} onClick={() => { fromText(pasted); setPasted('') }} className={`mt-1 ${btn}`}>Add these as rows</button>
          </div>
          {sources.length > 0 && (
            <div>
              <p className="text-xs font-medium text-gray-700">Another playbook's results</p>
              <div className="mt-1 flex flex-wrap items-center gap-1.5">
                <select aria-label="Playbook whose results to use" value={sourceId} onChange={(e) => loadResults(e.target.value)} className="rounded-md border border-gray-300 px-1.5 py-1 text-xs">
                  <option value="">Choose a playbook…</option>
                  {sources.map((s) => <option key={s.id} value={s.id}>{s.name}</option>)}
                </select>
                {results && results.columns.length > 0 && (
                  <>
                    <select aria-label="Column to use" value={column} onChange={(e) => setColumn(e.target.value)} className="rounded-md border border-gray-300 px-1.5 py-1 text-xs">
                      {results.columns.map((c) => <option key={c} value={c}>{c}</option>)}
                    </select>
                    {variables.length > 1 && (
                      <select aria-label="Input to fill" value={target} onChange={(e) => setTarget(e.target.value)} className="rounded-md border border-gray-300 px-1.5 py-1 text-xs">
                        {variables.map((v) => <option key={v.name} value={v.name}>{v.label || v.name}</option>)}
                      </select>
                    )}
                    <button type="button" onClick={useColumn} className={btn}>Use these values</button>
                  </>
                )}
              </div>
              {results && results.columns.length === 0 && <p className="mt-1 text-[11px] text-gray-500">That playbook has not read a table yet.</p>}
            </div>
          )}
        </div>
      </details>
      {note && <p role="status" className="text-[11px] text-gray-600">{note}</p>}
      {error && <p role="alert" className="rounded-md bg-red-50 px-2 py-1 text-[11px] text-red-700">{error}</p>}
      <div>
        <button type="button" disabled={busy || filled(rows).length === 0} onClick={dryRun} className={btn}>
          {busy ? 'Checking…' : 'Check the first 3 rows (nothing is run)'}
        </button>
        {check && (
          <div role="status" className="mt-1.5 space-y-1.5 rounded-lg border border-gray-200 p-2 text-xs text-gray-700">
            <p className={check.bad ? 'font-medium text-amber-700' : 'font-medium text-green-700'}>
              {check.bad ? `${check.bad} of ${check.total} rows can't run: fix them before saving.` : `All ${check.total} rows are fine.`}
              {check.days !== null && check.days > 1 ? ` The daily limit spreads this over about ${check.days} days.` : ''}
            </p>
            {check.rows.map((r) => (
              <div key={r.row} className="rounded bg-gray-50 px-2 py-1">
                <p className="font-medium">Row {r.row + 1}: {r.ok ? 'would run' : "can't run"}</p>
                {r.problems.map((p) => <p key={p} className="text-red-700">{p}</p>)}
                {r.steps.length > 0 && <ol className="ml-4 list-decimal text-gray-600">{r.steps.map((s, i) => <li key={i} className="break-all">{s}</li>)}</ol>}
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  )
}
