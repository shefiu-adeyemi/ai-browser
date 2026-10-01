import { describe, expect, it, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import ApprovalPrompt from '@/components/ApprovalPrompt'
import type { ApprovalRequest } from '@/types'

const ask: ApprovalRequest = { id: 'a1', kind: 'approve', title: 'Click "Send"', detail: 'sends it', url: 'https://x.test/', timeout: 180 }
const handoff: ApprovalRequest = { ...ask, id: 'h1', kind: 'handoff', title: 'Solve the CAPTCHA' }

describe('ApprovalPrompt (approve)', () => {
  it('puts the focus on Deny, never on Approve, so a stray Enter cannot approve', () => {
    render(<ApprovalPrompt request={ask} onAnswer={vi.fn()} />)
    expect(screen.getByRole('button', { name: 'Deny' })).toHaveFocus()
    expect(screen.getByRole('button', { name: 'Approve' })).not.toHaveFocus()
  })

  it('Esc denies', async () => {
    const onAnswer = vi.fn().mockResolvedValue(true)
    render(<ApprovalPrompt request={ask} onAnswer={onAnswer} />)
    await userEvent.keyboard('{Escape}')
    expect(onAnswer).toHaveBeenCalledWith('a1', false)
  })

  it('Approve reports the choice', async () => {
    const onAnswer = vi.fn().mockResolvedValue(true)
    render(<ApprovalPrompt request={ask} onAnswer={onAnswer} />)
    await userEvent.click(screen.getByRole('button', { name: 'Approve' }))
    expect(onAnswer).toHaveBeenCalledWith('a1', true)
  })

  it('keeps the prompt up and says so when the answer could not be delivered', async () => {
    const onAnswer = vi.fn().mockResolvedValue(false)
    render(<ApprovalPrompt request={ask} onAnswer={onAnswer} />)
    await userEvent.click(screen.getByRole('button', { name: 'Approve' }))
    expect(await screen.findByText(/Couldn't reach the agent/)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Approve' })).toBeEnabled()
  })

  it('shows the step that asked', () => {
    render(<ApprovalPrompt request={ask} onAnswer={vi.fn()} step={7} />)
    expect(screen.getByText(/step 7/)).toBeInTheDocument()
  })

  it('is a card in the page flow, not a blocking overlay', () => {
    render(<ApprovalPrompt request={ask} onAnswer={vi.fn()} />)
    expect(screen.getByRole('alertdialog').closest('.fixed')).toBeNull()
  })
})

describe('ApprovalPrompt (hand-off)', () => {
  it('does not take the focus: the user is about to click in the browser', () => {
    render(
      <div>
        <input aria-label="page" />
        <ApprovalPrompt request={handoff} onAnswer={vi.fn()} />
      </div>
    )
    screen.getByLabelText('page').focus()
    expect(screen.getByLabelText('page')).toHaveFocus()
    expect(screen.getByRole('button', { name: 'Skip' })).not.toHaveFocus()
  })

  it('Esc does not skip it', async () => {
    const onAnswer = vi.fn()
    render(<ApprovalPrompt request={handoff} onAnswer={onAnswer} />)
    await userEvent.keyboard('{Escape}')
    expect(onAnswer).not.toHaveBeenCalled()
  })
})
