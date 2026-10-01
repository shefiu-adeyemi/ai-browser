import { afterEach, describe, expect, it, vi } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import ModelTiers from './ModelTiers'
import type { Settings } from '@/types'

const slot = (model: string) => ({ provider: 'claude', model, label: '', explicit: true, usable: true, collapsed: false, reason: '' })
const settings = {
  LLM_PROVIDER: 'claude', LLM_MODEL: '', LLM_FAST_PROVIDER: '', LLM_FAST_MODEL: '', LLM_STRONG_PROVIDER: '', LLM_STRONG_MODEL: '',
  LLM_ROUTER: 'auto', PROVIDERS: [],
  TIERS: { router: 'auto', suggestions: {}, purposes: [], slots: { fast: slot('haiku'), standard: slot('sonnet'), strong: slot('sonnet') } },
} as unknown as Settings

const usage = (cost: unknown, prices: unknown) => ({
  ok: true, prices, cost,
  usage: { range_days: 7, totals: { fast: { calls: 2, prompt: 1000, completion: 100 }, standard: { calls: 1, prompt: 10, completion: 1 }, strong: { calls: 0, prompt: 0, completion: 0 } } },
})

afterEach(() => vi.unstubAllGlobals())

describe('prices in the model tiers settings', () => {
  it('lists each model once, saves a price when the field is left, and shows the estimate it gives', async () => {
    const calls: { url: string; body?: unknown }[] = []
    let current = usage({ per_tier: { fast: 0, standard: 0, strong: 0 }, total: 0, unpriced_tokens: 1111 }, {})
    vi.stubGlobal('fetch', vi.fn(async (url: string, init?: RequestInit) => {
      calls.push({ url, body: init?.body ? JSON.parse(String(init.body)) : undefined })
      if (url === '/models/prices') {
        current = usage({ per_tier: { fast: 0.5, standard: 0, strong: 0 }, total: 0.5, unpriced_tokens: 11 }, { haiku: { in: 1, out: 5 } })
        return new Response(JSON.stringify({ ok: true, prices: { haiku: { in: 1, out: 5 } } }))
      }
      return new Response(JSON.stringify(current))
    }))
    render(<ModelTiers settings={settings} update={vi.fn()} />)
    expect(await screen.findAllByLabelText(/Input price for/)).toHaveLength(2)          // sonnet fills two slots: one row
    await userEvent.type(screen.getByLabelText('Input price for haiku'), '1')
    await userEvent.type(screen.getByLabelText('Output price for haiku'), '5')
    await userEvent.tab()
    await waitFor(() => expect(calls.some((c) => c.url === '/models/prices')).toBe(true))
    expect(calls.find((c) => c.url === '/models/prices')?.body).toEqual({ model: 'haiku', input: 1, output: 5 })
    expect(await screen.findByText('about 0.50')).toBeInTheDocument()
    expect(screen.getByText(/11 tokens are from models with no price/)).toBeInTheDocument()
  })

  it('asks for both prices instead of saving half of one', async () => {
    const fetchMock = vi.fn(async () => new Response(JSON.stringify(usage({ per_tier: { fast: 0, standard: 0, strong: 0 }, total: 0, unpriced_tokens: 0 }, {}))))
    vi.stubGlobal('fetch', fetchMock)
    render(<ModelTiers settings={settings} update={vi.fn()} />)
    await userEvent.type(await screen.findByLabelText('Input price for haiku'), '3')
    await userEvent.tab()                                                       // on to the output price: still typing the pair
    expect(screen.queryByRole('alert')).toBeNull()
    await userEvent.tab()                                                       // left the row with only one of the two
    expect(await screen.findByText('Enter a number for both, or clear both.')).toBeInTheDocument()
    expect(fetchMock.mock.calls.some((c) => (c as unknown[])[0] === '/models/prices')).toBe(false)
  })

  it('shows a tiny cost as under 0.01, not as 0.00', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => new Response(JSON.stringify(
      usage({ per_tier: { fast: 0.0004, standard: 0, strong: 0 }, total: 0.0004, unpriced_tokens: 0 }, { haiku: { in: 1, out: 5 } })))))
    render(<ModelTiers settings={settings} update={vi.fn()} />)
    expect(await screen.findAllByText(/about under 0.01/)).toHaveLength(2)
  })
})
