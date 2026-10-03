import { describe, expect, it } from 'vitest'
import { MAX_ZOOM, viewSize } from '@/lib/viewLayout'

const page = { w: 1920, h: 1080 }

describe('viewSize', () => {
  it('Fit shows the whole page', () => {
    expect(viewSize({ w: 960, h: 800 }, page, false)).toEqual({ w: 960, h: 540 })
  })

  it('Fill never crops the sides: the page is never wider than the pane', () => {
    for (const area of [{ w: 590, h: 520 }, { w: 960, h: 800 }, { w: 400, h: 900 }, { w: 1200, h: 300 }, { w: 1920, h: 1080 }]) {
      const v = viewSize(area, page, true)!
      expect(v.w, JSON.stringify(area)).toBeLessThanOrEqual(area.w)
    }
  })

  it('Fill zooms a little when the pane is wider than the page, cropping only the bottom', () => {
    const area = { w: 1000, h: 400 }                         // height is the limit: Fit is 711 x 400
    const fit = viewSize(area, page, false)!
    const fill = viewSize(area, page, true)!
    expect(fill.w).toBeGreaterThan(fit.w)
    expect(fill.w).toBeLessThanOrEqual(Math.round(fit.w * MAX_ZOOM) + 1)
    expect(fill.w).toBeLessThanOrEqual(area.w)
  })

  it('Fill is never smaller than Fit', () => {
    for (const area of [{ w: 590, h: 520 }, { w: 1000, h: 400 }, { w: 300, h: 300 }]) {
      expect(viewSize(area, page, true)!.w).toBeGreaterThanOrEqual(viewSize(area, page, false)!.w)
    }
  })

  it('waits for real sizes', () => {
    expect(viewSize({ w: 0, h: 500 }, page, true)).toBeNull()
    expect(viewSize({ w: 500, h: 500 }, { w: 0, h: 0 }, true)).toBeNull()
  })
})
