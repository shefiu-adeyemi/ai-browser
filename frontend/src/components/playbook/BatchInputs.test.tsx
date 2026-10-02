import { afterEach, describe, expect, it, vi } from 'vitest'
import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { useState } from 'react'
import BatchInputs from './BatchInputs'

const VARS = [{ name: 'profile_url', label: 'Profile address' }]

function Harness({ initial = [{}], vars = VARS }: { initial?: Record<string, string>[]; vars?: { name: string; label?: string }[] }) {
  const [rows, setRows] = useState<Record<string, string>[]>(initial)
  return (
    <div>
      <BatchInputs playbookId="pb_1" variables={vars} rows={rows} setRows={setRows} />
      <div data-testid="rows">{JSON.stringify(rows)}</div>
    </div>
  )
}

const json = (status: number, body: unknown) => ({ ok: status < 400, status, json: async () => body })
const rowsNow = () => JSON.parse(screen.getByTestId('rows').textContent ?? '[]')

function stub(routes: Record<string, unknown | ((init?: RequestInit) => unknown)>) {
  const f = vi.fn(async (url: string, init?: RequestInit) => {
    for (const [prefix, body] of Object.entries(routes)) {
      if (url.startsWith(prefix)) return json(200, typeof body === 'function' ? (body as (i?: RequestInit) => unknown)(init) : body)
    }
    return json(404, { ok: false, reason: 'nope' })
  })
  vi.stubGlobal('fetch', f)
  return f
}

afterEach(() => { vi.unstubAllGlobals() })

describe('BatchInputs', () => {
  it('a pasted list becomes rows, replacing the empty starter row', async () => {
    stub({ '/playbooks': { ok: true, playbooks: [] } })
    render(<Harness />)
    await userEvent.click(screen.getByText(/Add rows from a file/))
    await userEvent.type(screen.getByLabelText('Paste a list'), 'https://a.test/1{Enter}https://a.test/2')
    await userEvent.click(screen.getByRole('button', { name: 'Add these as rows' }))
    expect(rowsNow()).toEqual([{ profile_url: 'https://a.test/1' }, { profile_url: 'https://a.test/2' }])
    expect(screen.getByRole('status')).toHaveTextContent('Added 2 rows.')
  })

  it('a CSV file with a header is mapped by input name and says what it left out', async () => {
    stub({ '/playbooks': { ok: true, playbooks: [] } })
    render(<Harness />)
    await userEvent.click(screen.getByText(/Add rows from a file/))
    const file = new File(['Profile address,junk\nhttps://a.test/1,x\nhttps://a.test/2,y'], 'rows.csv', { type: 'text/csv' })
    await userEvent.upload(screen.getByLabelText('Choose a CSV file'), file)
    expect(await screen.findByText(/Left out columns that match no input: junk/)).toBeInTheDocument()
    expect(rowsNow()).toEqual([{ profile_url: 'https://a.test/1' }, { profile_url: 'https://a.test/2' }])
  })

  it('a file that is too big is refused without reading it', async () => {
    stub({ '/playbooks': { ok: true, playbooks: [] } })
    render(<Harness />)
    await userEvent.click(screen.getByText(/Add rows from a file/))
    const big = new File(['x'], 'big.csv', { type: 'text/csv' })
    Object.defineProperty(big, 'size', { value: 2_000_000 })
    await userEvent.upload(screen.getByLabelText('Choose a CSV file'), big)
    expect(await screen.findByText(/too big/)).toBeInTheDocument()
    expect(rowsNow()).toEqual([{}])
  })

  it('no more than 200 rows are kept and it says so', async () => {
    stub({ '/playbooks': { ok: true, playbooks: [] } })
    const many = Array.from({ length: 199 }, (_, i) => ({ profile_url: `https://a.test/${i}` }))
    render(<Harness initial={many} />)
    await userEvent.click(screen.getByText(/Add rows from a file/))
    await userEvent.type(screen.getByLabelText('Paste a list'), 'a{Enter}b{Enter}c')
    await userEvent.click(screen.getByRole('button', { name: 'Add these as rows' }))
    expect(rowsNow()).toHaveLength(200)
    expect(screen.getByRole('status')).toHaveTextContent('Only 200 rows fit')
  })

  it("fills one input from a column of another playbook's results, each value once", async () => {
    stub({
      '/playbooks/pb_2/results/table': { ok: true, columns: ['Name', 'Link'], rows: [['A', 'https://x.test/1'], ['B', 'https://x.test/1'], ['C', 'https://x.test/2']], total: 3 },
      '/playbooks': { ok: true, playbooks: [{ id: 'pb_1', name: 'Me' }, { id: 'pb_2', name: 'List reader' }] },
    })
    render(<Harness />)
    await userEvent.click(screen.getByText(/Add rows from a file/))
    expect(within(screen.getByLabelText('Playbook whose results to use')).queryByText('Me')).toBeNull()      // not itself
    await userEvent.selectOptions(await screen.findByLabelText('Playbook whose results to use'), 'pb_2')
    await userEvent.selectOptions(await screen.findByLabelText('Column to use'), 'Link')
    await userEvent.click(screen.getByRole('button', { name: 'Use these values' }))
    expect(rowsNow()).toEqual([{ profile_url: 'https://x.test/1' }, { profile_url: 'https://x.test/2' }])
    expect(screen.getByRole('status')).toHaveTextContent('Repeated values were added once.')
  })

  it('says when the other playbook has read no table yet', async () => {
    stub({
      '/playbooks/pb_2/results/table': { ok: true, columns: [], rows: [], total: 0 },
      '/playbooks': { ok: true, playbooks: [{ id: 'pb_2', name: 'List reader' }] },
    })
    render(<Harness />)
    await userEvent.click(screen.getByText(/Add rows from a file/))
    await userEvent.selectOptions(await screen.findByLabelText('Playbook whose results to use'), 'pb_2')
    expect(await screen.findByText(/has not read a table yet/)).toBeInTheDocument()
  })

  it('the dry run sends only the filled rows and shows what would happen and the days it needs', async () => {
    const f = stub({
      '/playbooks/pb_1/check-rows': {
        ok: true, total: 120, bad: 1, days: 3, needs: { linkedin_profile_view: 120 },
        rows: [{ row: 0, ok: true, problems: [], steps: ['Open https://a.test/1'] }, { row: 1, ok: false, problems: ["'profile_url' must be a LinkedIn profile address"], steps: [] }],
      },
      '/playbooks': { ok: true, playbooks: [] },
    })
    render(<Harness initial={[{ profile_url: 'https://a.test/1' }, { profile_url: 'bad' }, {}]} />)
    await userEvent.click(screen.getByRole('button', { name: /Check the first 3 rows/ }))
    expect(await screen.findByText(/1 of 120 rows can't run/)).toBeInTheDocument()
    expect(screen.getByText(/about 3 days/)).toBeInTheDocument()
    expect(screen.getByText('Open https://a.test/1')).toBeInTheDocument()
    expect(screen.getByText(/must be a LinkedIn profile address/)).toBeInTheDocument()
    const call = f.mock.calls.find((c) => String(c[0]).includes('check-rows'))!
    expect(JSON.parse(String((call[1] as RequestInit).body)).rows).toEqual([{ profile_url: 'https://a.test/1' }, { profile_url: 'bad' }])
  })

  it('the dry run is off when there is nothing to check', () => {
    stub({ '/playbooks': { ok: true, playbooks: [] } })
    render(<Harness />)
    expect(screen.getByRole('button', { name: /Check the first 3 rows/ })).toBeDisabled()
  })

  it('shows a refusal in words', async () => {
    stub({ '/playbooks/pb_1/check-rows': undefined as never, '/playbooks': { ok: true, playbooks: [] } })
    vi.stubGlobal('fetch', vi.fn(async (url: string) => String(url).includes('check-rows') ? json(404, { ok: false, reason: 'That playbook no longer exists.' }) : json(200, { ok: true, playbooks: [] })))
    render(<Harness initial={[{ profile_url: 'x' }]} />)
    await userEvent.click(screen.getByRole('button', { name: /Check the first 3 rows/ }))
    expect(await screen.findByRole('alert')).toHaveTextContent('no longer exists')
  })
})
