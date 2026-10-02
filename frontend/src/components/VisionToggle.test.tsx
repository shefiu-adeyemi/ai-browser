import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import VisionToggle from '@/components/VisionToggle'

function serverSays(auto: boolean) {
  const fetchMock = vi.fn().mockResolvedValue({ ok: true, json: async () => ({ ok: true, auto }) })
  vi.stubGlobal('fetch', fetchMock)
  return fetchMock
}

describe('VisionToggle', () => {
  beforeEach(() => vi.unstubAllGlobals())
  afterEach(() => vi.unstubAllGlobals())

  it('asks the server what the app would decide, for this provider and model', async () => {
    const f = serverSays(true)
    render(<VisionToggle providerId="qwen" model="qwen3.7-plus" value="" onChange={vi.fn()} />)
    await waitFor(() => expect(screen.getByRole('checkbox')).toBeChecked())
    expect(f.mock.calls[0][0]).toBe('/settings/vision?provider=qwen&model=qwen3.7-plus')
    expect(screen.getByText(/Detected automatically/)).toBeInTheDocument()
  })

  it('shows unticked when the app thinks the model is text-only', async () => {
    serverSays(false)
    render(<VisionToggle providerId="deepseek" model="deepseek-v4-pro" value="" onChange={vi.fn()} />)
    await waitFor(() => expect(screen.getByText(/Detected automatically from the model's name \(no\)/)).toBeInTheDocument())
    expect(screen.getByRole('checkbox')).not.toBeChecked()
    expect(screen.getByText(/end-of-run check is off/)).toBeInTheDocument()
  })

  it('ticking against the guess stores yes; unticking against it stores no', async () => {
    serverSays(false)
    const onChange = vi.fn()
    const { rerender } = render(<VisionToggle providerId="deepseek" model="m" value="" onChange={onChange} />)
    await waitFor(() => expect(screen.getByText(/\(no\)/)).toBeInTheDocument())
    await userEvent.click(screen.getByRole('checkbox'))
    expect(onChange).toHaveBeenLastCalledWith('1')

    serverSays(true)
    rerender(<VisionToggle providerId="claude" model="n" value="" onChange={onChange} />)
    await waitFor(() => expect(screen.getByRole('checkbox')).toBeChecked())
    await userEvent.click(screen.getByRole('checkbox'))
    expect(onChange).toHaveBeenLastCalledWith('0')
  })

  it('choosing what the guess already is stores nothing (stays automatic)', async () => {
    serverSays(true)
    const onChange = vi.fn()
    render(<VisionToggle providerId="claude" model="m" value="0" onChange={onChange} />)
    await waitFor(() => expect(screen.getByText(/You set this by hand/)).toBeInTheDocument())
    expect(screen.getByRole('checkbox')).not.toBeChecked()                    // the user's "no" is shown, not the guess
    await userEvent.click(screen.getByRole('checkbox'))
    expect(onChange).toHaveBeenLastCalledWith('')
  })

  it('"Use automatic" clears the override and says what automatic is', async () => {
    serverSays(true)
    const onChange = vi.fn()
    render(<VisionToggle providerId="claude" model="m" value="0" onChange={onChange} />)
    const link = await screen.findByRole('button', { name: /Use automatic \(yes\)/ })
    await userEvent.click(link)
    expect(onChange).toHaveBeenCalledWith('')
  })

  it('an override works even if the server cannot be reached', async () => {
    vi.stubGlobal('fetch', vi.fn().mockRejectedValue(new Error('offline')))
    const onChange = vi.fn()
    render(<VisionToggle providerId="qwen" model="x" value="1" onChange={onChange} />)
    expect(screen.getByRole('checkbox')).toBeChecked()
    expect(screen.getByRole('checkbox')).toBeEnabled()
    await userEvent.click(screen.getByRole('button', { name: /Use automatic/ }))
    expect(onChange).toHaveBeenCalledWith('')
  })

  it('is disabled while the automatic answer is still loading', () => {
    vi.stubGlobal('fetch', vi.fn(() => new Promise(() => {})))
    render(<VisionToggle providerId="qwen" model="x" value="" onChange={vi.fn()} />)
    expect(screen.getByRole('checkbox')).toBeDisabled()
    expect(screen.getByText('Checking…')).toBeInTheDocument()
  })
})
