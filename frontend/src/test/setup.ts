import '@testing-library/jest-dom/vitest'
import { afterEach } from 'vitest'
import { cleanup } from '@testing-library/react'

afterEach(() => cleanup())

// jsdom has no layout engine; the few components that measure themselves just need these to exist.
class NoopObserver {
  observe() {}
  unobserve() {}
  disconnect() {}
}
;(globalThis as unknown as { ResizeObserver: typeof NoopObserver }).ResizeObserver = NoopObserver
Element.prototype.scrollIntoView = () => {}
