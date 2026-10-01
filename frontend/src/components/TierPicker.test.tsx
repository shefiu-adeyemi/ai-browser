import { afterEach, describe, expect, it, vi } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import TierPicker from './TierPicker'

const slot = (model: string, over = {}) => ({ provider: 'claude', model, label: '', explicit: true, usable: true, collapsed: false, reason: '', ...over })
const tiers = (router: 'auto' | 'off') => ({
  ok: true,
  tiers: {
    router, suggestions: {}, purposes: [],
    slots: { fast: slot('haiku'), standard: slot('sonnet'), strong: slot('sonnet', { collapsed: true, reason: 'not set' }) },
  },
})
const answer = (body: unknown) => vi.stubGlobal('fetch', vi.fn(async () => new Response(JSON.stringify(body))))

afterEach(() => vi.unstubAllGlobals())

describe('TierPicker', () => {
  it('shows the chosen tier, and each tier with the model it will really use', async () => {
    answer(tiers('auto'))
    const onChange = vi.fn()
    render(<TierPicker value="auto" onChange={onChange} disabled={false} refreshKey={0} />)
    await userEvent.click(await screen.findByRole('button', { name: 'Model tier: Auto' }))
    expect(await screen.findByText('haiku')).toBeInTheDocument()
    expect(screen.getByText("sonnet (Strong isn't set up: not set)")).toBeInTheDocument()
    await userEvent.click(screen.getByText('Fast'))
    expect(onChange).toHaveBeenCalledWith('fast')
  })

  it('is not offered when tiers are off (one model does everything)', async () => {
    answer(tiers('off'))
    render(<TierPicker value="auto" onChange={vi.fn()} disabled={false} refreshKey={0} />)
    await waitFor(() => expect(fetch).toHaveBeenCalled())
    await waitFor(() => expect(screen.queryByRole('button', { name: /Model tier/ })).toBeNull())
  })

  it('cannot be changed while a task runs', async () => {
    answer(tiers('auto'))
    render(<TierPicker value="strong" onChange={vi.fn()} disabled refreshKey={0} />)
    expect(await screen.findByRole('button', { name: 'Model tier: Strong' })).toBeDisabled()
  })
})
