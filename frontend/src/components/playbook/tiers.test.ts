import { describe, expect, it } from 'vitest'
import type { TiersInfo } from '@/types'
import { autoTier, tierModelText } from './shared'

const slot = (model: string, extra = {}) => ({ provider: 'claude', model, label: '', explicit: true, usable: true, collapsed: false, reason: '', ...extra })
const tiers = (over: Partial<TiersInfo> = {}): TiersInfo => ({
  router: 'auto',
  slots: {
    fast: slot('haiku'), standard: slot('sonnet'),
    strong: slot('sonnet', { collapsed: true, explicit: false, reason: 'not set' }),
  },
  suggestions: {},
  purposes: [{ purpose: 'playbook_extract', tier: 'fast', label: '' }, { purpose: 'agent', tier: 'standard', label: '' }],
  ...over,
})

describe('what a tier will use', () => {
  it('names the model of a set-up tier', () => {
    expect(tierModelText(tiers(), 'fast')).toBe('haiku')
  })
  it('says so when a tier is not set up and the Standard model answers', () => {
    expect(tierModelText(tiers(), 'strong')).toBe("sonnet (Strong isn't set up: not set)")
  })
  it('says tiers are off when the router is off', () => {
    expect(tierModelText(tiers({ router: 'off' }), 'fast')).toBe('sonnet (tiers are off in Settings)')
  })
  it('says nothing while the settings have not loaded', () => {
    expect(tierModelText(null, 'fast')).toBe('')
  })
})

describe('the tier chosen by the app itself', () => {
  it('follows the router table', () => {
    expect(autoTier(tiers(), 'playbook_extract')).toBe('fast')
    expect(autoTier(tiers(), 'agent')).toBe('standard')
  })
  it('falls back to Standard for an unknown job or before loading', () => {
    expect(autoTier(tiers(), 'nope')).toBe('standard')
    expect(autoTier(null, 'playbook_extract')).toBe('standard')
  })
})
