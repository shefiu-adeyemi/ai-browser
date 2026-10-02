import { afterEach, describe, expect, it, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import type { RunRepair } from '@/types'
import RepairsPanel from './RepairsPanel'

const repairs: RunRepair[] = [
  { step_id: 's2', kind: 'element', note: 'Send it', summary: '"Send" was not on the page, so "Dispatch" was used instead', why: 'same purpose' },
  { step_id: 's3', kind: 'scrape', note: 'The list', summary: "the table's selectors stopped matching, so new ones were found" },
]

const reply = (status: number, body: unknown) => vi.fn().mockResolvedValue({ ok: status < 400, status, json: async () => body })
afterEach(() => { vi.unstubAllGlobals() })

describe('RepairsPanel', () => {
  it('says what was repaired and why, and that the playbook is unchanged so far', () => {
    render(<RepairsPanel playbookId="pb_1" runId="run_1" repairs={repairs} onApplied={vi.fn()} />)
    expect(screen.getByText('This run repaired 2 steps to get through')).toBeInTheDocument()
    expect(screen.getByText(/"Dispatch" was used instead/)).toBeInTheDocument()
    expect(screen.getByText(/Why: same purpose/)).toBeInTheDocument()
    expect(screen.getByText(/unchanged until you update it/)).toBeInTheDocument()
  })

  it('says one step in the singular', () => {
    render(<RepairsPanel playbookId="pb_1" runId="run_1" repairs={[repairs[0]]} onApplied={vi.fn()} />)
    expect(screen.getByText('This run repaired 1 step to get through')).toBeInTheDocument()
  })

  it('sends the steps that are ticked and tells the user to approve the new version', async () => {
    const f = reply(200, { ok: true, applied: ['s3'], playbook: { version: 4 }, schedules_affected: 2 })
    vi.stubGlobal('fetch', f)
    const onApplied = vi.fn()
    render(<RepairsPanel playbookId="pb_1" runId="run_1" repairs={repairs} onApplied={onApplied} />)
    await userEvent.click(screen.getByRole('checkbox', { name: /Send it/ }))
    await userEvent.click(screen.getByRole('button', { name: 'Update the playbook with these' }))
    expect(f.mock.calls[0][0]).toBe('/playbooks/pb_1/runs/run_1/apply-repairs')
    expect(JSON.parse(f.mock.calls[0][1].body)).toEqual({ step_ids: ['s3'] })
    expect(await screen.findByRole('status')).toHaveTextContent('Updated 1 step. It is a new version: open the playbook and approve it again')
    expect(onApplied).toHaveBeenCalledWith({ version: 4, schedulesAffected: 2 })
    expect(screen.queryByRole('button', { name: 'Update the playbook with these' })).toBeNull()
  })

  it('cannot update with nothing ticked', async () => {
    render(<RepairsPanel playbookId="pb_1" runId="run_1" repairs={repairs} onApplied={vi.fn()} />)
    await userEvent.click(screen.getByRole('checkbox', { name: /Send it/ }))
    await userEvent.click(screen.getByRole('checkbox', { name: /The list/ }))
    expect(screen.getByRole('button', { name: 'Update the playbook with these' })).toBeDisabled()
  })

  it('shows why it was refused, and can be tried again', async () => {
    vi.stubGlobal('fetch', reply(409, { ok: false, reason: 'The playbook has changed since that run (it is now version 3).' }))
    const onApplied = vi.fn()
    render(<RepairsPanel playbookId="pb_1" runId="run_1" repairs={repairs} onApplied={onApplied} />)
    await userEvent.click(screen.getByRole('button', { name: 'Update the playbook with these' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('has changed since that run')
    expect(onApplied).not.toHaveBeenCalled()
    expect(screen.getByRole('button', { name: 'Update the playbook with these' })).toBeEnabled()
  })

  it('shows what could not be applied alongside the reason', async () => {
    vi.stubGlobal('fetch', reply(409, { ok: false, reason: 'Nothing could be applied.', problems: ['step s2 is no longer in the playbook'] }))
    render(<RepairsPanel playbookId="pb_1" runId="run_1" repairs={repairs} onApplied={vi.fn()} />)
    await userEvent.click(screen.getByRole('button', { name: 'Update the playbook with these' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('no longer in the playbook')
  })

  it('a network failure is shown in words', async () => {
    vi.stubGlobal('fetch', vi.fn().mockRejectedValue(new Error('offline')))
    render(<RepairsPanel playbookId="pb_1" runId="run_1" repairs={repairs} onApplied={vi.fn()} />)
    await userEvent.click(screen.getByRole('button', { name: 'Update the playbook with these' }))
    expect(await screen.findByRole('alert')).toHaveTextContent("Couldn't update the playbook")
  })
})
