/** A small CSV reader: quotes, doubled quotes, commas or semicolons or tabs, CRLF, a byte-order mark, blank lines. */
export function parseCsv(text: string): string[][] {
  const src = text.replace(/^﻿/, '')
  const first = src.split(/\r?\n/, 1)[0] ?? ''
  const count = (ch: string) => first.split(ch).length - 1
  // the separator is whichever of tab / semicolon / comma the header line uses most (a comma wins a tie)
  const sep = count('\t') > Math.max(count(','), count(';')) ? '\t' : count(';') > count(',') ? ';' : ','
  const rows: string[][] = []
  let row: string[] = []
  let cell = ''
  let quoted = false
  const endRow = () => {
    row.push(cell)
    cell = ''
    if (row.some((c) => c.trim() !== '')) rows.push(row.map((c) => c.trim()))
    row = []
  }
  for (let i = 0; i < src.length; i++) {
    const ch = src[i]
    if (quoted) {
      if (ch === '"') {
        if (src[i + 1] === '"') { cell += '"'; i++ } else quoted = false
      } else cell += ch
    } else if (ch === '"' && cell === '') quoted = true
    else if (ch === sep) { row.push(cell); cell = '' }
    else if (ch === '\n') endRow()
    else if (ch === '\r') { if (src[i + 1] !== '\n') endRow() }
    else cell += ch
  }
  if (cell !== '' || row.length > 0) endRow()
  return rows
}

export interface InputColumn { name: string; label?: string }

export interface CsvRows {
  rows: Record<string, string>[]
  /** Columns in the file that matched none of the playbook's inputs (they are left out). */
  ignored: string[]
  /** True when the first line was taken as a header. */
  header: boolean
}

const norm = (s: string) => s.toLowerCase().replace(/[^a-z0-9]+/g, '')

/**
 * Turn a pasted or uploaded CSV into input rows. A header line whose names match the playbook's inputs (by name
 * or label, ignoring case and punctuation) maps columns to inputs. Without one, a playbook with a single input
 * takes the first column as its values, and a playbook with several takes the columns in order.
 */
export function rowsFromCsv(text: string, inputs: InputColumn[]): CsvRows {
  const grid = parseCsv(text)
  if (grid.length === 0 || inputs.length === 0) return { rows: [], ignored: [], header: false }
  const byName = new Map<string, string>()
  for (const v of inputs) { byName.set(norm(v.name), v.name); if (v.label) byName.set(norm(v.label), v.name) }
  const head = grid[0].map((h) => byName.get(norm(h)) ?? null)
  const header = head.some((h) => h !== null)
  if (header) {
    const rows = grid.slice(1).map((cells) => {
      const r: Record<string, string> = {}
      head.forEach((name, i) => { if (name && cells[i] !== undefined) r[name] = cells[i] })
      return r
    }).filter((r) => Object.values(r).some((v) => v !== ''))
    return { rows, header, ignored: grid[0].filter((_, i) => head[i] === null && grid[0][i] !== '') }
  }
  const rows = grid.map((cells) => Object.fromEntries(inputs.map((v, i) => [v.name, cells[i] ?? '']).filter(([, v]) => v !== '')))
  return { rows: rows.filter((r) => Object.keys(r).length > 0), ignored: [], header }
}
