import { describe, expect, it, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import BrowserChrome, { type BrowserActions } from '@/components/BrowserChrome'
import type { BrowserState } from '@/types'

const state = (over: Partial<BrowserState> = {}): BrowserState => ({
  ready: true,
  tabs: [{ id: 'T1', title: 'Example', url: 'https://example.com/a' }],
  active: 'T1',
  can_back: true,
  can_forward: false,
  url: 'https://example.com/a',
  title: 'Example',
  locked: false,
  ...over,
})

const actions = (): BrowserActions => ({
  go: vi.fn(), back: vi.fn(), forward: vi.fn(), reload: vi.fn(), newTab: vi.fn(), switchTab: vi.fn(), closeTab: vi.fn(),
})

function show(s: BrowserState | null, over: { locked?: boolean; a?: BrowserActions; currentUrl?: string } = {}) {
  const a = over.a ?? actions()
  render(
    <BrowserChrome
      state={s}
      currentUrl={over.currentUrl ?? ''}
      locked={over.locked ?? false}
      controlling={false}
      fullscreen={false}
      onFullscreen={vi.fn()}
      fill={true}
      onToggleFill={vi.fn()}
      actions={a}
    />
  )
  return a
}

describe('BrowserChrome', () => {
  it('shows the page address without the https:// prefix', () => {
    show(state())
    expect(screen.getByLabelText('Address bar')).toHaveValue('example.com/a')
  })

  it('opens what is typed when Enter is pressed', async () => {
    const a = show(state())
    const bar = screen.getByLabelText('Address bar')
    await userEvent.click(bar)
    await userEvent.clear(bar)
    await userEvent.type(bar, 'python jobs{Enter}')
    expect(a.go).toHaveBeenCalledWith('python jobs')
  })

  it('Esc puts the real address back and opens nothing', async () => {
    const a = show(state())
    const bar = screen.getByLabelText('Address bar')
    await userEvent.click(bar)
    await userEvent.clear(bar)
    await userEvent.type(bar, 'half typed{Escape}')
    expect(a.go).not.toHaveBeenCalled()
    expect(bar).toHaveValue('example.com/a')
  })

  it('does nothing when the address was not changed', async () => {
    const a = show(state())
    await userEvent.click(screen.getByLabelText('Address bar'))
    await userEvent.keyboard('{Enter}')
    expect(a.go).not.toHaveBeenCalled()
  })

  it('is look-only while the agent works', async () => {
    const a = show(state({ can_forward: true }), { locked: true })
    expect(screen.getByLabelText('Address bar')).toBeDisabled()
    for (const name of ['Back', 'Forward', 'Reload']) expect(screen.getByRole('button', { name })).toBeDisabled()
    await userEvent.click(screen.getByRole('button', { name: 'Back' }))
    expect(a.back).not.toHaveBeenCalled()
  })

  it('enables back / forward only when there is somewhere to go', () => {
    show(state({ can_back: true, can_forward: false }))
    expect(screen.getByRole('button', { name: 'Back' })).toBeEnabled()
    expect(screen.getByRole('button', { name: 'Forward' })).toBeDisabled()
  })

  it('labels a blank tab "New tab" and offers Close only with more than one tab', () => {
    show(state({
      tabs: [{ id: 'T1', title: 'Example', url: 'https://example.com/a' }, { id: 'T2', title: '', url: 'about:blank' }],
    }))
    expect(screen.getByText('New tab', { selector: 'span' })).toBeInTheDocument()
    expect(screen.getAllByRole('button', { name: /Close tab/ })).toHaveLength(2)
  })

  it('a single tab has no close button', () => {
    show(state())
    expect(screen.queryByRole('button', { name: /Close tab/ })).toBeNull()
  })

  it('clicking another tab switches to it', async () => {
    const a = actions()
    show(state({
      tabs: [{ id: 'T1', title: 'One', url: 'https://one.test/' }, { id: 'T2', title: 'Two', url: 'https://two.test/' }],
    }), { a })
    await userEvent.click(screen.getByText('Two'))
    expect(a.switchTab).toHaveBeenCalledWith('T2')
  })

  it('marks plain http as not secure', () => {
    show(state({ url: 'http://localhost:3000/' }))
    expect(screen.getByText('Not secure')).toBeInTheDocument()
  })

  it('is look-only before the browser is ready', () => {
    show(null, { currentUrl: 'https://pushed.test/' })
    expect(screen.getByLabelText('Address bar')).toBeDisabled()
  })
})
