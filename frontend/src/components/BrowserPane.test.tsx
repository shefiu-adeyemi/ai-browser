import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { fireEvent, render } from '@testing-library/react'
import BrowserPane from '@/components/BrowserPane'

function setup() {
  const sent: Array<Record<string, unknown>> = []
  const ws = { readyState: WebSocket.OPEN, send: (d: string) => sent.push(JSON.parse(d)) }
  const { container } = render(
    <BrowserPane
      screenshot={null}
      status="idle"
      wsRef={{ current: ws as unknown as WebSocket }}
      browser={null}
      locked={false}
      actions={{ go: vi.fn(), back: vi.fn(), forward: vi.fn(), reload: vi.fn(), newTab: vi.fn(), switchTab: vi.fn(), closeTab: vi.fn() }}
    />
  )
  return { sent, canvas: container.querySelector('canvas')! }
}

const wheels = (sent: Array<Record<string, unknown>>) => sent.filter((m) => m.event === 'mouseWheel')

describe('BrowserPane scrolling', () => {
  beforeEach(() => {
    vi.useFakeTimers({ toFake: ['requestAnimationFrame', 'cancelAnimationFrame'] })
  })
  afterEach(() => {
    vi.useRealTimers()
  })

  it('eases a wheel movement out over several frames and adds up to exactly the distance', () => {
    const { sent, canvas } = setup()
    fireEvent.wheel(canvas, { deltaY: 300, clientX: 10, clientY: 10 })
    expect(wheels(sent)).toHaveLength(0)                          // nothing is sent until the next frame
    vi.advanceTimersByTime(16 * 40)
    const sizes = wheels(sent).map((m) => m.deltaY as number)
    expect(sizes.length).toBeGreaterThan(5)                        // spread over frames, not one jump
    expect(sizes.reduce((a, b) => a + b, 0)).toBe(300)             // and not a pixel lost or added
    expect(sizes[0]).toBeGreaterThan(sizes[sizes.length - 2])      // biggest first, then slowing down
  })

  it('movements that arrive while it is still scrolling are added to it', () => {
    const { sent, canvas } = setup()
    fireEvent.wheel(canvas, { deltaY: 100 })
    vi.advanceTimersByTime(32)
    fireEvent.wheel(canvas, { deltaY: 100 })
    fireEvent.wheel(canvas, { deltaY: -40 })
    vi.advanceTimersByTime(16 * 60)
    expect(wheels(sent).reduce((a, m) => a + (m.deltaY as number), 0)).toBe(160)
  })

  it('treats line-based wheels (deltaMode 1) as pixels', () => {
    const { sent, canvas } = setup()
    fireEvent.wheel(canvas, { deltaY: 3, deltaMode: 1 })
    vi.advanceTimersByTime(16 * 40)
    expect(wheels(sent).reduce((a, m) => a + (m.deltaY as number), 0)).toBe(120)
  })

  it('sends nothing for a wheel that does not move', () => {
    const { sent, canvas } = setup()
    fireEvent.wheel(canvas, { deltaY: 0 })
    vi.advanceTimersByTime(100)
    expect(wheels(sent)).toHaveLength(0)
  })
})
