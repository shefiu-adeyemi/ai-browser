import { describe, expect, it, vi } from 'vitest'
import { fireEvent, render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import type { Playbook, PlaybookStep, PlaybookVariable } from '@/types'
import ForEachEditor from './ForEachEditor'
import { addLoop, loopBody, stepDetail } from './shared'

const base = { tag: 'deterministic' as const, note: '', ask: false }
const open = (id: string, url: string): PlaybookStep => ({ ...base, id, kind: 'open', url })
const table = (id: string, fields: PlaybookStep['fields'], note = 'The list'): PlaybookStep => ({ ...base, id, kind: 'scrape', note, row_selector: 'tr', fields })
const pb = (steps: PlaybookStep[], variables: PlaybookVariable[] = []): Playbook =>
  ({ id: 'p', name: 'x', description: '', version: 1, variables, steps } as unknown as Playbook)

const LIST = table('s2', [{ name: 'Name', selector: '.n', type: 'text' }, { name: 'Page', selector: 'a', type: 'link' }])

describe('addLoop', () => {
  it('adds a loop over the link column that opens each page and reads it', () => {
    const out = addLoop(pb([open('s1', 'https://shop.test/list'), LIST]))!
    const [loop, visit, read] = out.steps.slice(2)
    expect(loop).toMatchObject({ kind: 'foreach', source: 's2', column: 'Page', variable: 'item', body: 2, on_failure: 'skip' })
    expect(visit).toMatchObject({ kind: 'open', url: '{{item}}' })
    expect(read.kind).toBe('scrape')
    expect(out.variables).toEqual([expect.objectContaining({ name: 'item', type: 'url', loop: true, allowed_hosts: ['shop.test'] })])
  })

  it('falls back to the first column when no column is a link', () => {
    const out = addLoop(pb([open('s1', 'https://a.test/'), table('s2', [{ name: 'Name', selector: '.n', type: 'text' }])]))!
    expect(out.steps[2].column).toBe('Name')
  })

  it('loops over the LAST table', () => {
    const out = addLoop(pb([open('s1', 'https://a.test/'), LIST, table('s3', [{ name: 'Link', selector: 'a', type: 'link' }], 'Second')]))!
    expect(out.steps[3]).toMatchObject({ source: 's3', column: 'Link' })
  })

  it('has no allowed site yet when the first page is not a plain address', () => {
    const out = addLoop(pb([open('s1', '{{start}}'), LIST]))!
    expect(out.variables[0].allowed_hosts).toEqual([])
  })

  it('numbers new steps and a new variable after the ones there', () => {
    const first = addLoop(pb([open('s1', 'https://a.test/'), LIST]))!
    const second = addLoop(first)!
    expect(new Set(second.steps.map((s) => s.id)).size).toBe(second.steps.length)
    expect(second.variables.map((v) => v.name)).toEqual(['item', 'item_2'])
  })

  it('does nothing without a table to loop over, or one with no columns', () => {
    expect(addLoop(pb([open('s1', 'https://a.test/')]))).toBeNull()
    expect(addLoop(pb([open('s1', 'https://a.test/'), table('s2', [])]))).toBeNull()
  })

  it('does not change the playbook it was given', () => {
    const before = pb([open('s1', 'https://a.test/'), LIST])
    const copy = JSON.stringify(before)
    addLoop(before)
    expect(JSON.stringify(before)).toBe(copy)
  })
})

describe('loopBody and stepDetail', () => {
  const steps: PlaybookStep[] = [open('s1', 'https://a.test/'), LIST, { ...base, id: 's3', kind: 'foreach', source: 's2', column: 'Page', variable: 'item', body: 2 }, open('s4', '{{item}}'), table('s5', []), open('s6', 'https://done.test/')]
  it('is the steps right after it', () => {
    expect(loopBody(steps, 2).map((s) => s.id)).toEqual(['s4', 's5'])
    expect(loopBody(steps, 0)).toEqual([])
    expect(loopBody(steps, 99)).toEqual([])
  })
  it('is described in words', () => {
    expect(stepDetail(steps[2])).toBe('Page of s2, repeat the next 2')
  })
})

describe('ForEachEditor', () => {
  const loop: PlaybookStep = { ...base, id: 's3', kind: 'foreach', source: 's2', column: 'Page', variable: 'item', body: 2, max_rows: null, on_failure: 'stop' }
  const steps = [open('s1', 'https://a.test/'), LIST, loop, open('s4', '{{item}}'), table('s5', [{ name: 'Role', selector: '.r', type: 'text' }], 'Read each')]
  const variable: PlaybookVariable = { name: 'item', type: 'url', label: '', required: true, allowed_hosts: ['a.test'], loop: true }

  function setup(extra: Partial<PlaybookStep> = {}, v: PlaybookVariable | undefined = variable) {
    const onChange = vi.fn()
    const onVariable = vi.fn()
    const step = { ...loop, ...extra }
    render(<ForEachEditor steps={steps.map((s) => (s.id === 's3' ? step : s))} step={step} index={2} variable={v} onChange={onChange} onVariable={onVariable} />)
    return { onChange, onVariable }
  }

  it('shows the column, the table, what is repeated and the site limit', () => {
    setup()
    expect(screen.getByLabelText('Column for step 3')).toHaveValue('Page')
    expect(screen.getByLabelText('Table for step 3')).toHaveValue('s2')
    expect(screen.getByLabelText('Steps to repeat for step 3')).toHaveValue(2)
    expect(screen.getByLabelText('Sites each value may open for step 3')).toHaveValue('a.test')
    expect(screen.getByText('{{item}}')).toBeInTheDocument()
  })

  it('lists the steps that will repeat', () => {
    setup()
    expect(screen.getByText(/Read each/)).toBeInTheDocument()
  })

  it('warns when there are not that many steps after it', () => {
    setup({ body: 5 })
    expect(screen.getByText(/not that many steps/)).toBeInTheDocument()
  })

  it('changing the table clears the column so a wrong one is not kept', async () => {
    const { onChange } = setup()
    await userEvent.selectOptions(screen.getByLabelText('Table for step 3'), 's2')
    expect(onChange).toHaveBeenLastCalledWith({ source: 's2', column: '' })
  })

  it('offers only earlier table steps and their columns', () => {
    setup()
    const options = Array.from((screen.getByLabelText('Column for step 3') as HTMLSelectElement).options).map((o) => o.value)
    expect(options).toEqual(['Name', 'Page'])
    const tables = Array.from((screen.getByLabelText('Table for step 3') as HTMLSelectElement).options).map((o) => o.value)
    expect(tables).toEqual(['s2'])                                           // not s5, which comes after the loop
  })

  it('keeps the number of repeated steps and of rows in range', () => {
    const { onChange } = setup()
    const body = screen.getByLabelText('Steps to repeat for step 3')
    fireEvent.change(body, { target: { value: '99' } })
    expect(onChange).toHaveBeenLastCalledWith({ body: 20 })
    fireEvent.change(body, { target: { value: '0' } })
    expect(onChange).toHaveBeenLastCalledWith({ body: 1 })
    const rows = screen.getByLabelText('Most rows for step 3')
    fireEvent.change(rows, { target: { value: '500' } })
    expect(onChange).toHaveBeenLastCalledWith({ max_rows: 50 })
    fireEvent.change(rows, { target: { value: '7' } })
    expect(onChange).toHaveBeenLastCalledWith({ max_rows: 7 })
  })

  it('an emptied row limit goes back to the default', () => {
    const { onChange } = setup({ max_rows: 7 })
    fireEvent.change(screen.getByLabelText('Most rows for step 3'), { target: { value: '' } })
    expect(onChange).toHaveBeenLastCalledWith({ max_rows: null })
  })

  it('a bad row can stop the run or be skipped', async () => {
    const { onChange } = setup()
    await userEvent.selectOptions(screen.getByLabelText('When a row fails for step 3'), 'skip')
    expect(onChange).toHaveBeenLastCalledWith({ on_failure: 'skip' })
  })

  it('what each value is changes the variable, and sites are cleaned into a list', async () => {
    const { onVariable } = setup()
    fireEvent.change(screen.getByLabelText('Sites each value may open for step 3'), { target: { value: 'A.com, b.org ,' } })
    expect(onVariable).toHaveBeenLastCalledWith({ allowed_hosts: ['a.com', 'b.org'] })
    await userEvent.selectOptions(screen.getByLabelText('What each value is for step 3'), 'text')
    expect(onVariable).toHaveBeenLastCalledWith({ type: 'text' })
  })

  it('a text loop has no site list', () => {
    setup({}, { ...variable, type: 'text', allowed_hosts: [] })
    expect(screen.queryByLabelText('Sites each value may open for step 3')).toBeNull()
  })

  it('says values are checked and that LinkedIn is not for loops', () => {
    setup()
    expect(screen.getByText(/each one is checked before it is used/)).toBeInTheDocument()
    expect(screen.getByText(/aren't available on LinkedIn/)).toBeInTheDocument()
  })
})
