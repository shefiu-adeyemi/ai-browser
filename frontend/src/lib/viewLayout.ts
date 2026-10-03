/** Fill never zooms more than this over Fit: a wide, short pane would otherwise hide most of a page's height. */
export const MAX_ZOOM = 1.15

/**
 * How big the live browser view is drawn in its pane.
 *
 * Fit shows the whole page, with grey bands where the shapes differ. Fill zooms in a little so the bands
 * shrink, but only as far as the page still fits the pane's WIDTH: the sides are never cropped, because a
 * page's left edge (menus, labels, buttons) must stay visible and clickable. Any extra height is cropped
 * at the bottom, never the top. Fill is therefore never smaller than Fit.
 */
export function viewSize(area: { w: number; h: number }, page: { w: number; h: number }, fill: boolean): { w: number; h: number } | null {
  if (!area.w || !area.h || !page.w || !page.h) return null
  const contain = Math.min(area.w / page.w, area.h / page.h)
  const k = fill ? Math.min(area.w / page.w, contain * MAX_ZOOM) : contain
  return { w: Math.round(page.w * k), h: Math.round(page.h * k) }
}
