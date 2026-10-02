import { afterEach, describe, expect, it, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import type { PlaybookStep } from '@/types'
import ScrapeEditor from './ScrapeEditor'
import ResultTable from './ResultTable'

const step = (extra: Partial<PlaybookStep> = {}): PlaybookStep => ({
  id: 's2', kind: 'scrape', tag: 'deterministic', note: '', ask: false, row_selector: 'tr.item', max_rows: null,
  fields: [{ name: 'Name', selector: '.n', type: 'text' }, { name: 'Price', selector: '.p', type: 'number' }], ...extra,
})

const reply = (status: number, body: unknown) => vi.fn().mockResolvedValue({ ok: status < 400, status, json: async () => body })

afterEach(() => { vi.unstubAllGlobals() })

describe('ScrapeEditor', () => {
  it('shows the row selector and every column', () => {
    render(<ScrapeEditor step={step()} index={1} onChange={vi.fn()} />)
    expect(screen.getByLabelText('Row selector for step 2')).toHaveValue('tr.item')
    expect(screen.getByLabelText('Selector 2 of step 2')).toHaveValue('.p')
    expect(screen.getByLabelText('Kind 2 of step 2')).toHaveValue('number')
  })

  it('an emptied row selector becomes none, meaning read the page once', async () => {
    const onChange = vi.fn()
    render(<ScrapeEditor step={step({ row_selector: 'a' })} index={0} onChange={onChange} />)
    await userEvent.clear(screen.getByLabelText('Row selector for step 1'))
    expect(onChange).toHaveBeenLastCalledWith({ row_selector: null })
  })

  it('adds a column with a name that is not taken, and cannot remove the last one', async () => {
    const onChange = vi.fn()
    const { rerender } = render(<ScrapeEditor step={step({ fields: [{ name: 'Field 2', selector: 'a', type: 'text' }] })} index={0} onChange={onChange} />)
    await userEvent.click(screen.getByRole('button', { name: /Add a column/ }))
    const added = onChange.mock.calls[0][0].fields
    expect(added.map((f: { name: string }) => f.name)).toEqual(['Field 2', 'Field 3'])
    expect(screen.getByRole('button', { name: 'Remove column 1 of step 1' })).toBeDisabled()
    rerender(<ScrapeEditor step={step()} index={0} onChange={onChange} />)
    expect(screen.getByRole('button', { name: 'Remove column 1 of step 1' })).toBeEnabled()
  })

  it('asks for an attribute name only for an attribute column, and drops it when the kind changes', async () => {
    const onChange = vi.fn()
    render(<ScrapeEditor step={step({ fields: [{ name: 'Id', selector: ':scope', type: 'attr', attr: 'data-id' }] })} index={0} onChange={onChange} />)
    expect(screen.getByLabelText('Attribute name 1 of step 1')).toHaveValue('data-id')
    await userEvent.selectOptions(screen.getByLabelText('Kind 1 of step 1'), 'text')
    expect(onChange.mock.calls[0][0].fields[0]).toMatchObject({ type: 'text', attr: null })
  })

  it('tests on the page in view and shows the rows', async () => {
    const fetchMock = reply(200, { ok: true, found: true, url: 'https://shop.test/', columns: ['Name'], rows: [['Ann'], ['Bob']], total: 12 })
    vi.stubGlobal('fetch', fetchMock)
    render(<ScrapeEditor step={step()} index={0} onChange={vi.fn()} />)
    await userEvent.click(screen.getByRole('button', { name: /Test on the page/ }))
    expect(await screen.findByText('Ann')).toBeInTheDocument()
    expect(screen.getByText(/Read from https:\/\/shop.test\//)).toBeInTheDocument()
    const sent = JSON.parse(fetchMock.mock.calls[0][1].body)
    expect(sent).toMatchObject({ row_selector: 'tr.item', max_rows: 10 })
    expect(sent.fields).toHaveLength(2)
  })

  it('says why nothing was found instead of showing an empty table', async () => {
    vi.stubGlobal('fetch', reply(200, { ok: true, found: false, message: 'No rows were found on the page.', url: 'https://x.test/', total: 0 }))
    render(<ScrapeEditor step={step()} index={0} onChange={vi.fn()} />)
    await userEvent.click(screen.getByRole('button', { name: /Test on the page/ }))
    expect(await screen.findByText('No rows were found on the page.')).toBeInTheDocument()
    expect(screen.queryByRole('table')).toBeNull()
  })

  it('shows a refusal in words', async () => {
    vi.stubGlobal('fetch', reply(409, { ok: false, reason: 'A task is running. Try again when it has finished.' }))
    render(<ScrapeEditor step={step()} index={0} onChange={vi.fn()} />)
    await userEvent.click(screen.getByRole('button', { name: /Test on the page/ }))
    expect(await screen.findByRole('alert')).toHaveTextContent('A task is running')
  })
})

describe('ResultTable', () => {
  const table = { columns: ['A', 'B'], rows: [['1', '2'], ['3']], truncated: false, total: 2 }

  it('draws the rows and tolerates a short one', () => {
    render(<ResultTable table={table} />)
    expect(screen.getAllByRole('row')).toHaveLength(3)
    expect(screen.getByText('2 rows')).toBeInTheDocument()
  })

  it('says when the page had more than was read, and when only some are shown', () => {
    const many = { columns: ['A'], rows: Array.from({ length: 60 }, (_, i) => [String(i)]), truncated: true, total: 900 }
    render(<ResultTable table={many} limit={50} />)
    expect(screen.getByText(/the page has 900/)).toBeInTheDocument()
    expect(screen.getByText(/the first 50 shown here/)).toBeInTheDocument()
    expect(screen.getAllByRole('row')).toHaveLength(51)
  })

  it('shows cell text as text, never as markup', () => {
    render(<ResultTable table={{ columns: ['A'], rows: [['<img src=x onerror=alert(1)>']], truncated: false, total: 1 }} />)
    expect(screen.getByText('<img src=x onerror=alert(1)>')).toBeInTheDocument()
    expect(document.querySelector('img')).toBeNull()
  })
})
