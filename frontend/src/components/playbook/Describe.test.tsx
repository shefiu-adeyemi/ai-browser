import { afterEach, describe, expect, it, vi } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import type { DescribedDraft } from '@/types'
import DescribeBox from './DescribeBox'
import DraftReport from './DraftReport'
import { PickContext } from './pick'

const json = (status: number, body: unknown) => ({ ok: status < 400, status, json: async () => body })

const DRAFT = {
  playbook: { id: '', name: 'Read stories', description: '', version: 1, variables: [], steps: [], domains: [] },
  report: [], notes: [], tokens: 800, model: 'strong-model', counts: {},
} as unknown as DescribedDraft

type Reply = unknown | ((url: string, init?: RequestInit) => unknown)

function stub(routes: Record<string, Reply>) {
  const f = vi.fn(async (url: string, init?: RequestInit) => {
    for (const [key, r] of Object.entries(routes)) {
      const [method, path] = key.split(' ')
      if ((init?.method ?? 'GET') === method && url.startsWith(path)) {
        const body = typeof r === 'function' ? (r as (u: string, i?: RequestInit) => unknown)(url, init) : r
        return body as never
      }
    }
    return json(404, { ok: false, reason: 'nope' }) as never
  })
  vi.stubGlobal('fetch', f)
  return f
}

function setup(props: Partial<React.ComponentProps<typeof DescribeBox>> = {}) {
  const ctl = { begin: vi.fn(), end: vi.fn() }
  const onDrafted = vi.fn()
  const view = render(<PickContext.Provider value={ctl}><DescribeBox onDrafted={onDrafted} pollMs={20} {...props} /></PickContext.Provider>)
  return { ctl, onDrafted, view }
}

const type = async (text = 'open the list and read it') => userEvent.type(screen.getByLabelText('Describe the task'), text)
const draftIt = () => userEvent.click(screen.getByRole('button', { name: 'Draft it' }))

afterEach(() => { vi.unstubAllGlobals() })

describe('DescribeBox', () => {
  it('cannot draft nothing', () => {
    setup()
    expect(screen.getByRole('button', { name: 'Draft it' })).toBeDisabled()
  })

  it('sends the words and the two choices, off unless ticked', async () => {
    const f = stub({ 'POST /playbooks/describe': json(200, { ok: true, token: 't1' }), 'GET /playbooks/describe/t1': json(200, { ok: true, status: 'running', progress: [] }) })
    setup()
    await type()
    await draftIt()
    expect(JSON.parse(String((f.mock.calls[0][1] as RequestInit).body))).toEqual({ description: 'open the list and read it', use_page: false, allow_unnamed: false })
  })

  it('passes on both choices when they are ticked', async () => {
    const f = stub({ 'POST /playbooks/describe': json(200, { ok: true, token: 't1' }), 'GET /playbooks/describe/t1': json(200, { ok: true, status: 'running', progress: [] }) })
    setup()
    await type()
    await userEvent.click(screen.getByRole('checkbox', { name: /look at the page in the browser first/ }))
    await userEvent.click(screen.getByRole('checkbox', { name: /open sites I did not mention/ }))
    await draftIt()
    expect(JSON.parse(String((f.mock.calls[0][1] as RequestInit).body))).toMatchObject({ use_page: true, allow_unnamed: true })
  })

  it('steps the dialog aside while it works, showing the latest progress line with a way to stop', async () => {
    stub({ 'POST /playbooks/describe': json(200, { ok: true, token: 't1' }),
           'GET /playbooks/describe/t1': json(200, { ok: true, status: 'running', progress: ['Planning the steps…', 'Trying step 2 of 4: Open https://x.test'] }) })
    const { ctl } = setup()
    await type()
    await draftIt()
    await waitFor(() => expect(ctl.begin).toHaveBeenCalledWith('Drafting: Trying step 2 of 4: Open https://x.test', expect.any(Function)))
    expect(screen.getByRole('button', { name: 'Drafting…' })).toBeDisabled()
    expect(screen.getByLabelText('Describe the task')).toBeDisabled()
  })

  it('hands the finished draft over and puts the dialog back', async () => {
    stub({ 'POST /playbooks/describe': json(200, { ok: true, token: 't1' }),
           'GET /playbooks/describe/t1': json(200, { ok: true, status: 'done', progress: [], result: DRAFT }) })
    const { ctl, onDrafted } = setup()
    await type()
    await draftIt()
    await waitFor(() => expect(onDrafted).toHaveBeenCalledWith(DRAFT))
    expect(ctl.end).toHaveBeenCalled()
    expect(screen.getByRole('button', { name: 'Draft it' })).toBeEnabled()
  })

  it('shows why a draft failed, in words, and can be tried again', async () => {
    stub({ 'POST /playbooks/describe': json(200, { ok: true, token: 't1' }),
           'GET /playbooks/describe/t1': json(200, { ok: true, status: 'failed', progress: [], error: "The model's plan had no steps that could be used." }) })
    const { onDrafted, ctl } = setup()
    await type()
    await draftIt()
    expect(await screen.findByRole('alert')).toHaveTextContent('no steps that could be used')
    expect(onDrafted).not.toHaveBeenCalled()
    expect(ctl.end).toHaveBeenCalled()
    expect(screen.getByRole('button', { name: 'Draft it' })).toBeEnabled()
  })

  it('says so when it was stopped', async () => {
    stub({ 'POST /playbooks/describe': json(200, { ok: true, token: 't1' }),
           'GET /playbooks/describe/t1': json(200, { ok: true, status: 'stopped', progress: [], error: 'Stopped by you.' }) })
    setup()
    await type()
    await draftIt()
    expect(await screen.findByRole('alert')).toHaveTextContent('Stopped by you.')
  })

  it('the stop in the bar asks the server to stop', async () => {
    const f = stub({ 'POST /playbooks/describe': json(200, { ok: true, token: 't1' }), 'GET /playbooks/describe/t1': json(200, { ok: true, status: 'running', progress: [] }),
                     'POST /stop': json(200, { ok: true }) })
    const { ctl } = setup()
    await type()
    await draftIt()
    await waitFor(() => expect(ctl.begin).toHaveBeenCalled())
    const stop = ctl.begin.mock.calls[0][1] as () => void
    stop()
    await waitFor(() => expect(f.mock.calls.some((c) => c[0] === '/stop')).toBe(true))
  })

  it('a refusal at the start is shown and nothing is polled', async () => {
    const f = stub({ 'POST /playbooks/describe': json(409, { ok: false, reason: 'No AI model is set up yet (see Settings), so there is nothing to draft with.' }) })
    const { ctl } = setup()
    await type()
    await draftIt()
    expect(await screen.findByRole('alert')).toHaveTextContent('No AI model is set up')
    expect(ctl.begin).not.toHaveBeenCalled()
    expect(f).toHaveBeenCalledTimes(1)
  })

  it('a draft that has been forgotten by the server is explained', async () => {
    stub({ 'POST /playbooks/describe': json(200, { ok: true, token: 't1' }), 'GET /playbooks/describe/t1': json(404, { ok: false, reason: 'That draft is no longer kept.' }) })
    setup()
    await type()
    await draftIt()
    expect(await screen.findByRole('alert')).toHaveTextContent('no longer kept')
  })

  it('stops asking when the box goes away', async () => {
    const f = stub({ 'POST /playbooks/describe': json(200, { ok: true, token: 't1' }), 'GET /playbooks/describe/t1': json(200, { ok: true, status: 'running', progress: [] }) })
    const { view } = setup()
    await type()
    await draftIt()
    await waitFor(() => expect(f.mock.calls.length).toBeGreaterThan(1))
    view.unmount()
    const n = f.mock.calls.length
    await new Promise((r) => setTimeout(r, 120))
    expect(f.mock.calls.length).toBe(n)
  })
})

describe('DraftReport', () => {
  const base = { tokens: 800, model: 'strong-model' }

  it('says how each step fared, what it will read, and what drafting cost', () => {
    render(<DraftReport draft={{
      ...base, counts: { verified: 2, unresolved: 1 },
      report: [
        { n: 1, kind: 'open', text: 'Open https://x.test', status: 'verified', detail: '' },
        { n: 2, kind: 'read_table', text: 'Read a table: each story', status: 'verified', detail: '', sample: { columns: ['Title'], rows: [['Story A'], ['Story B']], total: 30 } },
        { n: 3, kind: 'click', text: 'Click "Next"', status: 'unresolved', detail: "Couldn't find \"Next\" on the page." },
      ],
    }} />)
    expect(screen.getByText('Open https://x.test')).toBeInTheDocument()
    expect(screen.getAllByText(/Worked on the page/)).toHaveLength(2)
    expect(screen.getByText("Couldn't do this: Couldn't find \"Next\" on the page.")).toBeInTheDocument()
    expect(screen.getByText('Story A')).toBeInTheDocument()
    expect(screen.getByText(/1 step needs your attention/)).toBeInTheDocument()
    expect(screen.getByText(/Drafting used about 800 tokens \(strong-model\). Replays cost nothing./)).toBeInTheDocument()
  })

  it('says all went well when nothing needs attention', () => {
    render(<DraftReport draft={{ ...base, counts: { verified: 1 }, report: [{ n: 1, kind: 'open', text: 'Open x', status: 'verified', detail: '' }] }} />)
    expect(screen.getByText(/Everything it tried worked/)).toBeInTheDocument()
  })

  it('counts failed steps too, in the plural', () => {
    render(<DraftReport draft={{ ...base, counts: { unresolved: 1, failed: 1 }, report: [] }} />)
    expect(screen.getByText(/2 steps need your attention/)).toBeInTheDocument()
  })

  it('says what was added but not run, and what was not tried', () => {
    render(<DraftReport draft={{
      ...base, counts: { not_run: 1, left_out: 1 },
      report: [
        { n: 1, kind: 'click', text: 'Click "Send"', status: 'not_run', detail: 'It looks like it sends, so it was NOT clicked.' },
        { n: 2, kind: 'wait', text: 'Wait 1 s', status: 'left_out', detail: 'It comes after something that sends.' },
      ],
    }} />)
    expect(screen.getByText(/Added, not run: It looks like it sends/)).toBeInTheDocument()
    expect(screen.getByText(/Not tried: It comes after something that sends/)).toBeInTheDocument()
  })

  it('shows what a page said as text, never as markup', () => {
    render(<DraftReport draft={{ ...base, counts: {}, report: [{ n: 1, kind: 'click', text: 'Click "<img src=x onerror=alert(1)>"', status: 'verified', detail: '' }] }} />)
    expect(screen.getByText('Click "<img src=x onerror=alert(1)>"')).toBeInTheDocument()
    expect(document.querySelector('img')).toBeNull()
  })

  it('shows large token counts in thousands', () => {
    render(<DraftReport draft={{ tokens: 12500, model: '', counts: {}, report: [] }} />)
    expect(screen.getByText(/about 12.5k tokens\./)).toBeInTheDocument()
  })
})
