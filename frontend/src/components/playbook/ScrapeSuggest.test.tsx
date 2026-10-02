import { afterEach, describe, expect, it, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import type { PlaybookStep } from '@/types'
import ScrapeEditor from './ScrapeEditor'

const step = (extra: Partial<PlaybookStep> = {}): PlaybookStep => ({
  id: 's2', kind: 'scrape', tag: 'deterministic', note: '', ask: false, row_selector: null, max_rows: null,
  fields: [{ name: 'Field 1', selector: '', type: 'text' }], ...extra,
})

const reply = (status: number, body: unknown) => vi.fn().mockResolvedValue({ ok: status < 400, status, json: async () => body })

const IDEA = {
  ok: true, url: 'https://shop.test/', row_selector: 'div.card',
  fields: [{ name: 'Name', selector: '.title', type: 'text', attr: null }, { name: 'Price', selector: '.price', type: 'number', attr: null }],
  dropped: ['Ghost'], table: { columns: ['Name', 'Price'], rows: [['Alpha', '5'], ['Beta', '7.5']], truncated: false, total: 2 },
}

afterEach(() => { vi.unstubAllGlobals() })

async function ask(text = 'names and prices') {
  await userEvent.type(screen.getByLabelText('What to read for step 1'), text)
  await userEvent.click(screen.getByRole('button', { name: /Suggest with AI/ }))
}

describe('Suggest with AI', () => {
  it('cannot ask without saying what to read', () => {
    render(<ScrapeEditor step={step()} index={0} onChange={vi.fn()} />)
    expect(screen.getByRole('button', { name: /Suggest with AI/ })).toBeDisabled()
  })

  it('sends the description and shows the proposal with its rows, nothing changed yet', async () => {
    const f = reply(200, IDEA)
    vi.stubGlobal('fetch', f)
    const onChange = vi.fn()
    render(<ScrapeEditor step={step()} index={0} onChange={onChange} />)
    await ask()
    expect(JSON.parse(f.mock.calls[0][1].body)).toEqual({ description: 'names and prices' })
    expect(await screen.findByText('Alpha')).toBeInTheDocument()
    expect(screen.getByText(/Left out \(not found on this page\): Ghost/)).toBeInTheDocument()
    expect(screen.getByText('div.card')).toBeInTheDocument()
    expect(onChange).not.toHaveBeenCalled()
  })

  it('Use this applies the rows and columns and shows the preview', async () => {
    vi.stubGlobal('fetch', reply(200, IDEA))
    const onChange = vi.fn()
    render(<ScrapeEditor step={step()} index={0} onChange={onChange} />)
    await ask()
    await userEvent.click(await screen.findByRole('button', { name: 'Use this' }))
    expect(onChange).toHaveBeenCalledWith({ row_selector: 'div.card', fields: IDEA.fields })
    expect(screen.queryByRole('button', { name: 'Use this' })).toBeNull()
    expect(screen.getByText(/Read from https:\/\/shop.test\//)).toBeInTheDocument()
  })

  it('No thanks changes nothing', async () => {
    vi.stubGlobal('fetch', reply(200, IDEA))
    const onChange = vi.fn()
    render(<ScrapeEditor step={step()} index={0} onChange={onChange} />)
    await ask()
    await userEvent.click(await screen.findByRole('button', { name: 'No thanks' }))
    expect(onChange).not.toHaveBeenCalled()
    expect(screen.queryByText('Alpha')).toBeNull()
  })

  it('a proposal for one record says so', async () => {
    vi.stubGlobal('fetch', reply(200, { ...IDEA, row_selector: null, dropped: [] }))
    render(<ScrapeEditor step={step()} index={0} onChange={vi.fn()} />)
    await ask()
    expect(await screen.findByText(/One record from the page/)).toBeInTheDocument()
  })

  it('shows why no suggestion could be made, in words', async () => {
    vi.stubGlobal('fetch', reply(409, { ok: false, reason: 'No AI model is set up yet (see Settings), so there is nothing to suggest with.' }))
    render(<ScrapeEditor step={step()} index={0} onChange={vi.fn()} />)
    await ask()
    expect(await screen.findByRole('alert')).toHaveTextContent('No AI model is set up')
  })

  it('a new question clears the old proposal', async () => {
    vi.stubGlobal('fetch', reply(200, IDEA))
    render(<ScrapeEditor step={step()} index={0} onChange={vi.fn()} />)
    await ask()
    await screen.findByText('Alpha')
    vi.stubGlobal('fetch', reply(422, { ok: false, reason: 'The suggested rows were not found on this page.' }))
    await userEvent.click(screen.getByRole('button', { name: /Suggest with AI/ }))
    expect(await screen.findByRole('alert')).toHaveTextContent('not found on this page')
    expect(screen.queryByText('Alpha')).toBeNull()
  })

  it('Enter in the box asks too', async () => {
    const f = reply(200, IDEA)
    vi.stubGlobal('fetch', f)
    render(<ScrapeEditor step={step()} index={0} onChange={vi.fn()} />)
    await userEvent.type(screen.getByLabelText('What to read for step 1'), 'prices{Enter}')
    expect(f).toHaveBeenCalledTimes(1)
  })
})
