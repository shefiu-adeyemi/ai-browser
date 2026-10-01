import { describe, expect, it, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import ValueAnswer from './ValueAnswer'

describe('ValueAnswer', () => {
  it('cannot send nothing, then sends what was typed (Enter or button)', async () => {
    const onSend = vi.fn()
    render(<ValueAnswer label="Answer" onSend={onSend} onCancel={vi.fn()} />)
    expect(screen.getByRole('button', { name: 'Send' })).toBeDisabled()
    await userEvent.type(screen.getByLabelText('Answer'), '   ')
    expect(screen.getByRole('button', { name: 'Send' })).toBeDisabled()
    await userEvent.type(screen.getByLabelText('Answer'), '42{Enter}')
    expect(onSend).toHaveBeenCalledWith('   42', false)
    await userEvent.click(screen.getByRole('button', { name: 'Send' }))
    expect(onSend).toHaveBeenCalledTimes(2)
  })

  it('passes on whether to remember it', async () => {
    const onSend = vi.fn()
    render(<ValueAnswer label="Answer" onSend={onSend} onCancel={vi.fn()} />)
    await userEvent.type(screen.getByLabelText('Answer'), '5')
    await userEvent.click(screen.getByRole('checkbox', { name: /Remember this/ }))
    await userEvent.click(screen.getByRole('button', { name: 'Send' }))
    expect(onSend).toHaveBeenCalledWith('5', true)
  })

  it("Don't says no", async () => {
    const onCancel = vi.fn()
    render(<ValueAnswer label="Answer" onSend={vi.fn()} onCancel={onCancel} />)
    await userEvent.click(screen.getByRole('button', { name: "Don't" }))
    expect(onCancel).toHaveBeenCalled()
  })
})
