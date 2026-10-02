import { afterEach, describe, expect, it, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import type { PlaybookStep } from '@/types'
import ScrapeEditor from './ScrapeEditor'
import { PickContext, defaultRowChoice, nameFromSelector } from './pick'

const step = (extra: Partial<PlaybookStep> = {}): PlaybookStep => ({
  id: 's2', kind: 'scrape', tag: 'deterministic', note: '', ask: false, row_selector: null, max_rows: null,
  fields: [{ name: 'Field 1', selector: '', type: 'text' }], ...extra,
})

const json = (status: number, body: unknown) => ({ ok: status < 400, status, json: async () => body })

interface Script { start?: unknown; polls: unknown[] }  // each poll is a ready-made response

function stub(script: Script) {
  const polls = [...script.polls]
  const f = vi.fn(async (url: string, init?: RequestInit) => {
    const method = init?.method ?? 'GET'
    if (url === '/playbooks/scrape-pick' && method === 'POST') return script.start ?? json(200, { ok: true, url: 'https://x.test/' })
    if (url === '/playbooks/scrape-pick' && method === 'DELETE') return json(200, { ok: true })
    if (url === '/playbooks/scrape-pick') return polls.length > 1 ? polls.shift() : polls[0]
    return json(404, { ok: false, reason: 'nope' })
  })
  vi.stubGlobal('fetch', f)
  return f
}

function setup(s: PlaybookStep, onChange = vi.fn()) {
  const ctl = { begin: vi.fn(), end: vi.fn() }
  const view = render(<PickContext.Provider value={ctl}><ScrapeEditor step={s} index={0} onChange={onChange} /></PickContext.Provider>)
  return { ctl, onChange, view }
}

const answer = (picks: unknown[], active = true) => json(200, { ok: true, active, picks })
const slow = { timeout: 4000 }

afterEach(() => { vi.unstubAllGlobals() })

describe('defaultRowChoice', () => {
  const c = (selector: string, count: number) => ({ selector, count, text: '' })
  it('takes the biggest repeated item that is still a sensible list', () => {
    expect(defaultRowChoice([c('td', 30), c('tr', 10), c('tbody', 1)])?.selector).toBe('tr')
    expect(defaultRowChoice([c('td', 30), c('tr', 900)])?.selector).toBe('td')
  })
  it('falls back to the first when none is in range, and to nothing when empty', () => {
    expect(defaultRowChoice([c('a', 1), c('b', 1000)])?.selector).toBe('a')
    expect(defaultRowChoice([])).toBeNull()
  })
})

describe('nameFromSelector', () => {
  it('uses the last class, in words', () => {
    expect(nameFromSelector('.price')).toBe('Price')
    expect(nameFromSelector('div.card > span.product-price')).toBe('Product price')
    expect(nameFromSelector('td.unit_cost')).toBe('Unit cost')
  })
  it('gives nothing when there is no class to go on', () => {
    expect(nameFromSelector('td:nth-of-type(2)')).toBe('')
    expect(nameFromSelector(':scope')).toBe('')
    expect(nameFromSelector('')).toBe('')
  })
  it('never makes a name longer than a column name can be', () => {
    expect(nameFromSelector('.' + 'a'.repeat(100)).length).toBeLessThanOrEqual(40)
  })
})

describe('picking rows on the page', () => {
  it('starts the picker, hides the dialog, takes the biggest list and puts the page back', async () => {
    const f = stub({ polls: [answer([{ candidates: [{ selector: 'td.c', count: 30, text: 'A' }, { selector: 'tr.item', count: 10, text: 'A 5' }] }])] })
    const { ctl, onChange } = setup(step())
    await userEvent.click(screen.getByRole('button', { name: /Pick on the page/ }))
    expect(JSON.parse(String((f.mock.calls[0][1] as RequestInit).body))).toEqual({ mode: 'row' })
    expect(ctl.begin).toHaveBeenCalledWith('Click one item of the list on the page', expect.any(Function))
    await vi.waitFor(() => expect(onChange).toHaveBeenCalledWith({ row_selector: 'tr.item' }), slow)
    await vi.waitFor(() => expect(ctl.end).toHaveBeenCalled(), slow)
    expect(f.mock.calls.some((c) => (c[1] as RequestInit | undefined)?.method === 'DELETE')).toBe(true)
    expect(await screen.findByText(/Each row is "tr.item" \(10 on this page\)/)).toBeInTheDocument()
  })

  it('a stray click is explained and picking goes on', async () => {
    stub({ polls: [answer([{ error: 'Click an item that appears several times, like one row of a list.' }]), answer([{ candidates: [{ selector: 'li.r', count: 4, text: '' }] }])] })
    const { ctl, onChange } = setup(step())
    await userEvent.click(screen.getByRole('button', { name: /Pick on the page/ }))
    expect(await screen.findByText(/appears several times/, undefined, slow)).toBeInTheDocument()
    expect(ctl.end).not.toHaveBeenCalled()
    await vi.waitFor(() => expect(onChange).toHaveBeenCalledWith({ row_selector: 'li.r' }), slow)
  })

  it('Cancel puts the page back and keeps what was there', async () => {
    const f = stub({ polls: [answer([])] })
    const { ctl, onChange } = setup(step({ row_selector: 'old' }))
    await userEvent.click(screen.getByRole('button', { name: /Pick on the page/ }))
    const cancel = ctl.begin.mock.calls[0][1] as () => void
    cancel()
    await vi.waitFor(() => expect(ctl.end).toHaveBeenCalled())
    expect(f.mock.calls.some((c) => (c[1] as RequestInit | undefined)?.method === 'DELETE')).toBe(true)
    expect(onChange).not.toHaveBeenCalled()
    expect(await screen.findByText('Picking cancelled.')).toBeInTheDocument()
  })

  it('a refusal is shown in words and the dialog stays', async () => {
    stub({ start: json(409, { ok: false, reason: 'A task is running. Try again when it has finished.' }), polls: [] })
    const { ctl } = setup(step())
    await userEvent.click(screen.getByRole('button', { name: /Pick on the page/ }))
    expect(await screen.findByRole('alert')).toHaveTextContent('A task is running')
    expect(ctl.begin).not.toHaveBeenCalled()
  })

  it('says so when the page went away while picking', async () => {
    stub({ polls: [answer([], false)] })
    const { ctl } = setup(step())
    await userEvent.click(screen.getByRole('button', { name: /Pick on the page/ }))
    expect(await screen.findByText(/Picking ended/, undefined, slow)).toBeInTheDocument()
    expect(ctl.end).toHaveBeenCalled()
  })

  it('offers the other sizes so a wrong guess is one click away', async () => {
    stub({ polls: [answer([{ candidates: [{ selector: 'td.c', count: 30, text: 'A' }, { selector: 'tr.item', count: 10, text: 'A 5' }] }])] })
    const { onChange } = setup(step({ row_selector: 'tr.item' }))
    await userEvent.click(screen.getByRole('button', { name: /Pick on the page/ }))
    const other = await screen.findByRole('radio', { name: /td\.c/ }, slow)
    await userEvent.click(other)
    expect(onChange).toHaveBeenLastCalledWith({ row_selector: 'td.c' })
  })
})

describe('picking a column on the page', () => {
  it('needs the rows first', () => {
    setup(step())
    expect(screen.getByRole('button', { name: /Pick column 1 of step 1/ })).toBeDisabled()
  })

  it('sends the row selector and fills the column from the click', async () => {
    const f = stub({ polls: [answer([{ selector: '.price', sample: '$7.50', href: '', suggest: 'number', hits: 3, rows: 4 }])] })
    const { ctl, onChange } = setup(step({ row_selector: 'tr.item' }))
    await userEvent.click(screen.getByRole('button', { name: /Pick column 1 of step 1/ }))
    expect(JSON.parse(String((f.mock.calls[0][1] as RequestInit).body))).toEqual({ mode: 'field', row_selector: 'tr.item' })
    expect(ctl.begin).toHaveBeenCalledWith('Click the value for "Field 1" inside one row', expect.any(Function))
    await vi.waitFor(() => expect(onChange).toHaveBeenCalled(), slow)
    expect(onChange.mock.calls[0][0].fields[0]).toMatchObject({ selector: '.price', type: 'number', attr: null, name: 'Price' })
    expect(await screen.findByText(/Found in 3 of 4 rows: the others will be left empty/)).toBeInTheDocument()
  })

  it('does not use a name another column already has, and never renames one the user chose', async () => {
    stub({ polls: [answer([{ selector: '.title', sample: 'Anything', href: '', suggest: 'text', hits: 2, rows: 2 }])] })
    const named = step({ row_selector: 'tr', fields: [{ name: 'Title', selector: 'a', type: 'text' }, { name: 'Field 2', selector: '', type: 'text' }] })
    const { onChange } = setup(named)
    await userEvent.click(screen.getByRole('button', { name: /Pick column 2 of step 1/ }))
    await vi.waitFor(() => expect(onChange).toHaveBeenCalled(), slow)
    expect(onChange.mock.calls[0][0].fields[1].name).toBe('Field 2')
    expect(onChange.mock.calls[0][0].fields[0].name).toBe('Title')
  })

  it('a click outside every row is explained and picking goes on', async () => {
    stub({ polls: [answer([{ error: 'Click inside one of the rows.' }]), answer([{ selector: '.n', sample: 'A', href: '', suggest: 'text', hits: 2, rows: 2 }])] })
    const { ctl, onChange } = setup(step({ row_selector: 'tr' }))
    await userEvent.click(screen.getByRole('button', { name: /Pick column 1 of step 1/ }))
    expect(await screen.findByText('Click inside one of the rows.', undefined, slow)).toBeInTheDocument()
    expect(ctl.end).not.toHaveBeenCalled()
    await vi.waitFor(() => expect(onChange).toHaveBeenCalled(), slow)
  })

  it('leaves the page alone if the editor is closed while picking', async () => {
    const f = stub({ polls: [answer([])] })
    const { ctl, view } = setup(step({ row_selector: 'tr' }))
    await userEvent.click(screen.getByRole('button', { name: /Pick column 1 of step 1/ }))
    view.unmount()
    expect(ctl.end).toHaveBeenCalled()
    expect(f.mock.calls.some((c) => (c[1] as RequestInit | undefined)?.method === 'DELETE')).toBe(true)
  })
})
