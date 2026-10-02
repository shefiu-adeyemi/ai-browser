import { createContext } from 'react'

/**
 * While the user is clicking on the page to pick a row or a column, the dialog must get out of the way so the
 * page can be seen and clicked, and a small bar stays with a Cancel. The dialog provides this; the editor uses it.
 */
export interface PickControl {
  begin: (label: string, cancel: () => void) => void
  end: () => void
}

export const PickContext = createContext<PickControl>({ begin: () => {}, end: () => {} })

/** A column name suggested by the last class in its selector: ".product-price" gives "Product price". */
export function nameFromSelector(selector: string): string {
  const m = [...selector.matchAll(/\.([A-Za-z][A-Za-z0-9_-]*)/g)].pop()
  if (!m) return ''
  const words = m[1].replace(/[-_]+/g, ' ').trim()
  return words ? words.charAt(0).toUpperCase() + words.slice(1, 40) : ''
}

export interface RowChoice { selector: string; count: number; text: string }

/** Of the repeated items around a click, the biggest one that is still a sensible list (2 to 500 items). */
export function defaultRowChoice(choices: RowChoice[]): RowChoice | null {
  const ok = choices.filter((c) => c.count >= 2 && c.count <= 500)
  return ok.length ? ok[ok.length - 1] : choices[0] ?? null
}
