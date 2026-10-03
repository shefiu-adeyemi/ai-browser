import { describe, expect, it, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import SpeedSetting from '@/components/SpeedSetting'

describe('SpeedSetting', () => {
  it('shows the stored choice', () => {
    render(<SpeedSetting providerId="claude" value="fast" onChange={vi.fn()} />)
    expect(screen.getByRole('radio', { name: /Faster/ })).toBeChecked()
    expect(screen.getByRole('radio', { name: /Automatic/ })).not.toBeChecked()
  })

  it('reports each choice; automatic is the empty string', async () => {
    const onChange = vi.fn()
    render(<SpeedSetting providerId="claude" value="" onChange={onChange} />)
    await userEvent.click(screen.getByRole('radio', { name: /More careful/ }))
    expect(onChange).toHaveBeenLastCalledWith('careful')
    await userEvent.click(screen.getByRole('radio', { name: /Faster/ }))
    expect(onChange).toHaveBeenLastCalledWith('fast')
  })

  it('choosing automatic after a manual choice stores the empty string', async () => {
    const onChange = vi.fn()
    render(<SpeedSetting providerId="claude" value="fast" onChange={onChange} />)
    await userEvent.click(screen.getByRole('radio', { name: /Automatic/ }))
    expect(onChange).toHaveBeenLastCalledWith('')
  })

  it('says what automatic means for the provider', () => {
    const { rerender } = render(<SpeedSetting providerId="deepseek" value="" onChange={vi.fn()} />)
    expect(screen.getByText(/Fast for DeepSeek/)).toBeInTheDocument()
    rerender(<SpeedSetting providerId="claude" value="" onChange={vi.fn()} />)
    expect(screen.getByText(/Careful for this provider/)).toBeInTheDocument()
  })
})
