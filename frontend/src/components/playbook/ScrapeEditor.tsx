import { useContext, useEffect, useRef, useState } from 'react'
import { MousePointer2, Plus, Sparkles, Trash2 } from 'lucide-react'
import type { PlaybookStep, ScrapeField, ScrapedTable } from '@/types'
import { errText, readJson, reasonOf } from './shared'
import { PickContext, defaultRowChoice, nameFromSelector, type RowChoice } from './pick'
import ResultTable from './ResultTable'

interface Props {
  step: PlaybookStep
  index: number
  onChange: (patch: Partial<PlaybookStep>) => void
}

type Picking = { mode: 'row' } | { mode: 'field'; at: number } | null

interface FieldPick { selector: string; sample: string; href: string; suggest: ScrapeField['type']; hits: number; rows: number }

const GENERIC_NAME = /^Field \d+$/

/** The settings of a "Read a table" step: which repeated item is a row, and what to read inside it. */
export default function ScrapeEditor({ step, index, onChange }: Props) {
  const fields = step.fields ?? []
  const { begin, end } = useContext(PickContext)
  const [testing, setTesting] = useState(false)
  const [preview, setPreview] = useState<{ table?: ScrapedTable; message?: string; url?: string } | null>(null)
  const [error, setError] = useState('')
  const [picking, setPicking] = useState<Picking>(null)
  const [choices, setChoices] = useState<RowChoice[]>([])
  const [note, setNote] = useState('')
  const [wish, setWish] = useState('')
  const [asking, setAsking] = useState(false)
  const [idea, setIdea] = useState<{ row_selector: string | null; fields: ScrapeField[]; dropped: string[]; table: ScrapedTable; url: string } | null>(null)
  const live = useRef({ step, fields, onChange })
  live.current = { step, fields, onChange }

  const setField = (i: number, patch: Partial<ScrapeField>) =>
    onChange({ fields: fields.map((f, j) => (j === i ? { ...f, ...patch, ...(patch.type && patch.type !== 'attr' ? { attr: null } : {}) } : f)) })

  const addField = () => {
    const taken = new Set(fields.map((f) => f.name.toLowerCase()))
    let n = fields.length + 1
    while (taken.has(`field ${n}`)) n += 1
    onChange({ fields: [...fields, { name: `Field ${n}`, selector: '', type: 'text' }] })
  }

  const test = async () => {
    setTesting(true); setError(''); setPreview(null)
    try {
      const res = await fetch('/playbooks/scrape-preview', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ row_selector: step.row_selector || null, max_rows: 10, fields }),
      })
      const data = await readJson(res)
      if (!res.ok || !data.ok) {
        setError([reasonOf(res, data), ...(data.problems ?? [])].join(' '))
        return
      }
      setPreview(data.found
        ? { url: data.url, table: { columns: data.columns, rows: data.rows, truncated: data.total > data.rows.length, total: data.total } }
        : { url: data.url, message: data.message })
    } catch (e) {
      setError(`Couldn't test it (${errText(e)}).`)
    } finally { setTesting(false) }
  }

  const suggest = async () => {
    setAsking(true); setError(''); setIdea(null)
    try {
      const res = await fetch('/playbooks/scrape-suggest', {
        method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ description: wish }),
      })
      const data = await readJson(res)
      if (!res.ok || !data.ok) { setError(reasonOf(res, data)); return }
      setIdea({ row_selector: data.row_selector ?? null, fields: data.fields, dropped: data.dropped ?? [], table: data.table, url: data.url })
    } catch (e) {
      setError(`Couldn't ask for a suggestion (${errText(e)}).`)
    } finally { setAsking(false) }
  }

  const useIdea = () => {
    if (!idea) return
    onChange({ row_selector: idea.row_selector, fields: idea.fields })
    setNote('Applied. Check the rows below, and test it again if you change anything.')
    setPreview({ url: idea.url, table: idea.table })
    setIdea(null)
  }

  // ── clicking on the page ──
  const finish = async (message?: string) => {
    setPicking(null)
    end()
    if (message) setNote(message)
    try { await fetch('/playbooks/scrape-pick', { method: 'DELETE' }) } catch { /* the page is gone: nothing to put back */ }
  }

  const startPicking = async (what: NonNullable<Picking>) => {
    setError(''); setNote(''); setChoices([])
    try {
      const res = await fetch('/playbooks/scrape-pick', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(what.mode === 'row' ? { mode: 'row' } : { mode: 'field', row_selector: step.row_selector }),
      })
      const data = await readJson(res)
      if (!res.ok || !data.ok) { setError(reasonOf(res, data)); return }
      setPicking(what)
      begin(what.mode === 'row' ? 'Click one item of the list on the page' : `Click the value for "${fields[what.at]?.name ?? 'the column'}" inside one row`,
        () => { finish('Picking cancelled.') })
    } catch (e) {
      setError(`Couldn't start picking (${errText(e)}).`)
    }
  }

  const chooseRows = (c: RowChoice) => {
    onChange({ row_selector: c.selector })
    setNote(`Each row is "${c.selector}" (${c.count} on this page).`)
  }

  const takePick = (pick: Record<string, unknown>, what: NonNullable<Picking>) => {
    if (typeof pick.error === 'string') { setNote(pick.error); return false }     // keep picking: it was a stray click
    if (what.mode === 'row') {
      const list = (pick.candidates as RowChoice[]) ?? []
      const best = defaultRowChoice(list)
      setChoices(list)
      if (best) chooseRows(best)
      return true
    }
    const p = pick as unknown as FieldPick
    const f = live.current.fields[what.at]
    if (!f) return true
    const patch: Partial<ScrapeField> = { selector: p.selector, type: p.suggest, attr: null }
    if (GENERIC_NAME.test(f.name)) {                                    // a name the user chose is never replaced
      const name = nameFromSelector(p.selector)
      if (name && !live.current.fields.some((x, j) => j !== what.at && x.name.toLowerCase() === name.toLowerCase())) patch.name = name
    }
    live.current.onChange({ fields: live.current.fields.map((x, j) => (j === what.at ? { ...x, ...patch } : x)) })
    setNote(p.hits < p.rows ? `Found in ${p.hits} of ${p.rows} rows: the others will be left empty.` : `Found in all ${p.rows} rows. Example: ${p.sample || '(empty)'}`)
    return true
  }

  useEffect(() => {
    if (!picking) return
    let alive = true
    const timer = setInterval(async () => {
      try {
        const res = await fetch('/playbooks/scrape-pick')
        const data = await readJson(res)
        if (!alive) return
        if (!res.ok || !data.ok) { await finish(reasonOf(res, data)); return }
        for (const pick of data.picks ?? []) if (takePick(pick, picking)) { await finish(); return }
        if (!data.active && !(data.picks ?? []).length) await finish('Picking ended (the page changed or took too long). Start it again if you need to.')
      } catch { /* a missed poll is fine: the next one asks again */ }
    }, 600)
    return () => { alive = false; clearInterval(timer) }
  }, [picking])        // eslint-disable-line react-hooks/exhaustive-deps

  const pickingRef = useRef(false)
  pickingRef.current = picking !== null
  useEffect(() => () => { if (pickingRef.current) { end(); fetch('/playbooks/scrape-pick', { method: 'DELETE' }).catch(() => {}) } }, [])   // eslint-disable-line react-hooks/exhaustive-deps

  const input = 'rounded-md border border-gray-300 px-2 py-1 text-xs focus:border-brand focus:outline-none'
  const pickBtn = 'inline-flex items-center gap-1 rounded-md border border-brand/40 px-1.5 py-1 text-[11px] font-medium text-brand disabled:opacity-40'
  return (
    <div className="mt-1 space-y-2">
      <div className="grid grid-cols-1 gap-1.5 sm:grid-cols-[1fr_auto_6rem]">
        <input aria-label={`Row selector for step ${index + 1}`} value={step.row_selector ?? ''} maxLength={300}
          placeholder="Each row's CSS selector, e.g. tr.item (empty: read the page once)"
          onChange={(e) => onChange({ row_selector: e.target.value === '' ? null : e.target.value })} className={`${input} font-mono`} />
        <button type="button" onClick={() => startPicking({ mode: 'row' })} disabled={picking !== null} className={pickBtn}>
          <MousePointer2 size={11} /> Pick on the page
        </button>
        <input aria-label={`Most rows for step ${index + 1}`} type="number" min={1} max={500} value={step.max_rows ?? ''} placeholder="Up to 100"
          onChange={(e) => onChange({ max_rows: e.target.value === '' ? null : Math.max(1, Math.min(500, Number(e.target.value))) })} className={input} />
      </div>
      {choices.length > 1 && (
        <fieldset className="rounded-md bg-gray-50 px-2 py-1.5">
          <legend className="px-1 text-[11px] text-gray-500">Not the right rows? Choose another size:</legend>
          {choices.map((c) => (
            <label key={c.selector} className="flex items-start gap-1.5 py-0.5 text-[11px] text-gray-700">
              <input type="radio" name={`rows-${step.id}`} checked={step.row_selector === c.selector} onChange={() => chooseRows(c)} className="mt-0.5" />
              <span><span className="font-mono">{c.selector}</span> · {c.count} items{c.text ? ` · "${c.text}"` : ''}</span>
            </label>
          ))}
        </fieldset>
      )}
      <div className="rounded-md bg-purple-50/60 p-2">
        <div className="flex flex-wrap items-center gap-1.5">
          <input aria-label={`What to read for step ${index + 1}`} value={wish} maxLength={500} onChange={(e) => setWish(e.target.value)}
            onKeyDown={(e) => { if (e.key === 'Enter' && wish.trim() && !asking) { e.preventDefault(); suggest() } }}
            placeholder="Or describe it: the product names and prices" className={`${input} min-w-0 flex-1`} />
          <button type="button" onClick={suggest} disabled={asking || !wish.trim()} className="inline-flex items-center gap-1 rounded-md border border-purple-300 px-2 py-1 text-[11px] font-medium text-purple-700 disabled:opacity-40">
            <Sparkles size={11} /> {asking ? 'Thinking…' : 'Suggest with AI'}
          </button>
        </div>
        <p className="mt-1 text-[11px] text-gray-500">Sends a short outline of the page in the browser (its structure and clipped visible text, no scripts or typed values) to your AI model once. The answer is tried on the page before you see it, and nothing changes until you use it.</p>
        {idea && (
          <div className="mt-2 space-y-1.5 rounded-md border border-purple-200 bg-white p-2" role="status">
            <p className="text-[11px] text-gray-700">
              {idea.row_selector ? <>One row per <span className="font-mono">{idea.row_selector}</span>. </> : 'One record from the page. '}
              Columns: {idea.fields.map((f) => f.name).join(', ')}.
              {idea.dropped.length > 0 && <span className="text-amber-700"> Left out (not found on this page): {idea.dropped.join(', ')}.</span>}
            </p>
            <ResultTable table={idea.table} limit={5} />
            <div className="flex gap-1.5">
              <button type="button" onClick={useIdea} className="rounded-md bg-brand px-2 py-1 text-[11px] font-medium text-white">Use this</button>
              <button type="button" onClick={() => setIdea(null)} className="rounded-md border border-gray-300 px-2 py-1 text-[11px] text-gray-700">No thanks</button>
            </div>
          </div>
        )}
      </div>
      <ul className="space-y-1">
        {fields.map((f, i) => (
          <li key={i} className="grid grid-cols-[1fr_1.4fr_auto_5.5rem_auto] items-center gap-1.5">
            <input aria-label={`Column name ${i + 1} of step ${index + 1}`} value={f.name} maxLength={40} onChange={(e) => setField(i, { name: e.target.value })} className={input} />
            <input aria-label={`Selector ${i + 1} of step ${index + 1}`} value={f.selector} maxLength={300} placeholder="CSS selector inside the row"
              onChange={(e) => setField(i, { selector: e.target.value })} className={`${input} font-mono`} />
            <button type="button" aria-label={`Pick column ${i + 1} of step ${index + 1} on the page`} title={step.row_selector ? 'Click the value on the page' : 'Pick the rows first'}
              disabled={picking !== null || !step.row_selector} onClick={() => startPicking({ mode: 'field', at: i })} className={pickBtn}><MousePointer2 size={11} /></button>
            <select aria-label={`Kind ${i + 1} of step ${index + 1}`} value={f.type} onChange={(e) => setField(i, { type: e.target.value as ScrapeField['type'] })} className={input}>
              <option value="text">text</option><option value="number">number</option><option value="link">link</option><option value="attr">attribute</option>
            </select>
            <button aria-label={`Remove column ${i + 1} of step ${index + 1}`} disabled={fields.length <= 1}
              onClick={() => onChange({ fields: fields.filter((_, j) => j !== i) })} className="rounded p-1 text-gray-300 hover:text-red-600 disabled:opacity-30"><Trash2 size={12} /></button>
            {f.type === 'attr' && (
              <input aria-label={`Attribute name ${i + 1} of step ${index + 1}`} value={f.attr ?? ''} maxLength={60} placeholder="attribute, e.g. data-id"
                onChange={(e) => setField(i, { attr: e.target.value })} className={`${input} col-span-5 font-mono`} />
            )}
          </li>
        ))}
      </ul>
      <div className="flex flex-wrap items-center gap-2">
        <button onClick={addField} disabled={fields.length >= 30} className="inline-flex items-center gap-1 rounded-md border border-gray-300 px-2 py-1 text-[11px] text-gray-700 disabled:opacity-40"><Plus size={11} /> Add a column</button>
        <button onClick={test} disabled={testing || fields.length === 0} className="rounded-md border border-brand/40 px-2 py-1 text-[11px] font-medium text-brand disabled:opacity-40">
          {testing ? 'Reading…' : 'Test on the page in the browser'}
        </button>
        <span className="text-[11px] text-gray-400">Open the page in the browser on the left first. Picking and testing only read. Inside a shadow root or a frame from the same site, join selectors with <span className="font-mono">&gt;&gt;&gt;</span>.</span>
      </div>
      {note && <p role="status" className="text-[11px] text-gray-600">{note}</p>}
      {error && <p role="alert" className="rounded-md bg-red-50 px-2 py-1 text-[11px] text-red-700">{error}</p>}
      {preview?.message && <p role="status" className="rounded-md bg-amber-50 px-2 py-1 text-[11px] text-amber-800">{preview.message}</p>}
      {preview?.table && (
        <div role="status">
          <p className="mb-1 truncate text-[11px] text-gray-500">Read from {preview.url}</p>
          <ResultTable table={preview.table} limit={10} />
        </div>
      )}
    </div>
  )
}
