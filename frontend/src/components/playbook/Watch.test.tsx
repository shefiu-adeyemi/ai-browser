import { describe, expect, it } from 'vitest'
import { fireEvent, render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { useState } from 'react'
import type { WatchResult, WatchSettings } from '@/types'
import ChangeList from './ChangeList'
import WatchForm from './WatchForm'
import { NO_WATCH, WATCH_LABEL, splitList } from './shared'

describe('splitList', () => {
  it('trims, drops empties and repeats (ignoring case), and splits on commas and new lines', () => {
    expect(splitList(' Link, name ,,LINK\nPrice ,')).toEqual(['Link', 'name', 'Price'])
    expect(splitList('')).toEqual([])
    expect(splitList(' , \n ')).toEqual([])
  })
})

function Harness({ start = NO_WATCH, unavailable = '' }: { start?: WatchSettings; unavailable?: string }) {
  const [w, setW] = useState(start)
  return (
    <div>
      <WatchForm value={w} onChange={setW} unavailable={unavailable} />
      <div data-testid="w">{JSON.stringify(w)}</div>
    </div>
  )
}
const current = (): WatchSettings => JSON.parse(screen.getByTestId('w').textContent ?? '{}')

describe('WatchForm', () => {
  it('is off to begin with and shows no settings', () => {
    render(<Harness />)
    expect(screen.getByRole('checkbox', { name: /Tell me only when what it reads changes/ })).not.toBeChecked()
    expect(screen.queryByLabelText('Columns that identify a row')).toBeNull()
  })

  it('switching it on shows the settings', async () => {
    render(<Harness />)
    await userEvent.click(screen.getByRole('checkbox', { name: /Tell me only when what it reads changes/ }))
    expect(current().enabled).toBe(true)
    expect(screen.getByLabelText('Columns that identify a row')).toBeInTheDocument()
  })

  it('turns what is typed into clean lists', async () => {
    render(<Harness start={{ ...NO_WATCH, enabled: true }} />)
    await userEvent.type(screen.getByLabelText('Columns that identify a row'), 'Link, Name ,')
    await userEvent.type(screen.getByLabelText('Columns to leave out'), 'Seen')
    fireEvent.change(screen.getByLabelText('Patterns to ignore'), { target: { value: '\\d+ ago\n\n  Updated.*  \n' } })
    expect(current()).toMatchObject({ key_columns: ['Link', 'Name'], ignore_columns: ['Seen'], ignore_patterns: ['\\d+ ago', 'Updated.*'] })
  })

  it('shows what was saved before', () => {
    render(<Harness start={{ ...NO_WATCH, enabled: true, key_columns: ['Link'], ignore_patterns: ['a', 'b'], min_change_percent: 5 }} />)
    expect(screen.getByLabelText('Columns that identify a row')).toHaveValue('Link')
    expect(screen.getByLabelText('Patterns to ignore')).toHaveValue('a\nb')
    expect(screen.getByLabelText('Smallest change that matters, in percent')).toHaveValue(5)
  })

  it('keeps the smallest change between 0 and 100', () => {
    render(<Harness start={{ ...NO_WATCH, enabled: true }} />)
    const box = screen.getByLabelText('Smallest change that matters, in percent')
    fireEvent.change(box, { target: { value: '250' } })
    expect(current().min_change_percent).toBe(100)
    fireEvent.change(box, { target: { value: '-4' } })
    expect(current().min_change_percent).toBe(0)
    fireEvent.change(box, { target: { value: '2.5' } })
    expect(current().min_change_percent).toBe(2.5)
  })

  it('chooses which kinds of change to hear about', async () => {
    render(<Harness start={{ ...NO_WATCH, enabled: true }} />)
    await userEvent.click(screen.getByRole('checkbox', { name: 'rows that are gone' }))
    expect(current()).toMatchObject({ report_added: true, report_removed: false, report_changed: true })
  })

  it('cannot be switched on for a playbook that reads no table, and says why', () => {
    render(<Harness unavailable="Watching needs a playbook with a step that reads a table." start={{ ...NO_WATCH, enabled: true }} />)
    const box = screen.getByRole('checkbox', { name: /Tell me only when what it reads changes/ })
    expect(box).toBeDisabled()
    expect(box).not.toBeChecked()                                  // even if it was saved on: it does nothing here
    expect(screen.getByText(/needs a playbook with a step that reads a table/)).toBeInTheDocument()
    expect(screen.queryByLabelText('Columns that identify a row')).toBeNull()
  })
})

describe('ChangeList', () => {
  const changed: WatchResult = {
    status: 'changed', changed: 1, added: 1, removed: 1,
    diff: {
      cut: false,
      changed_rows: [{ table: 's2', key: 'Ann', columns: [{ column: 'Price', before: '5', after: '6' }] }],
      added_rows: [{ table: 's2', key: 'Dee', cells: {} }], removed_rows: [{ table: 's2', key: 'Bob', cells: {} }],
    },
  }

  it('shows every change in words', () => {
    render(<ChangeList watch={changed} />)
    expect(screen.getByText('Changed')).toBeInTheDocument()
    expect(screen.getByText('1 changed, 1 new, 1 gone since the last look.')).toBeInTheDocument()
    expect(screen.getByText('Ann')).toBeInTheDocument()
    expect(screen.getByText('5')).toBeInTheDocument()
    expect(screen.getByText('6')).toBeInTheDocument()
    expect(screen.getByText('Dee', { exact: false })).toBeInTheDocument()
    expect(screen.getByText('Bob', { exact: false })).toBeInTheDocument()
  })

  it('an empty value is called empty', () => {
    render(<ChangeList watch={{ ...changed, diff: { ...changed.diff!, changed_rows: [{ table: 's', key: 'K', columns: [{ column: 'C', before: '', after: 'x' }] }] } }} />)
    expect(screen.getByText('(empty)')).toBeInTheDocument()
  })

  it('says each other outcome', () => {
    const cases: [WatchResult, string, string][] = [
      [{ status: 'baseline' }, 'First look', 'The first look: saved to compare the next run with.'],
      [{ status: 'unchanged' }, 'No change', 'The same as the last look.'],
      [{ status: 'reset', reason: 'The watch settings changed.' }, 'New comparison', 'The watch settings changed.'],
      [{ status: 'skipped', reason: 'Some steps failed.' }, 'Not compared', 'Some steps failed.'],
    ]
    for (const [watch, badge, text] of cases) {
      const { unmount } = render(<ChangeList watch={watch} />)
      expect(screen.getByText(badge)).toBeInTheDocument()
      expect(screen.getByText(text)).toBeInTheDocument()
      unmount()
    }
  })

  it('says when the list was cut and when more changed than is shown', () => {
    render(<ChangeList watch={{ ...changed, changed: 80, diff: { ...changed.diff!, cut: true } }} />)
    expect(screen.getByText(/cut at its limit/)).toBeInTheDocument()
    expect(screen.getByText(/more changed rows than are shown/)).toBeInTheDocument()
  })

  it('shows what a page said as text, never as markup', () => {
    render(<ChangeList watch={{ ...changed, diff: { ...changed.diff!, added_rows: [{ table: 's', key: '<img src=x onerror=alert(1)>', cells: {} }] } }} />)
    expect(screen.getByText('<img src=x onerror=alert(1)>', { exact: false })).toBeInTheDocument()
    expect(document.querySelector('img')).toBeNull()
  })

  it('has a label for every outcome the server can give', () => {
    for (const s of ['changed', 'baseline', 'reset', 'unchanged', 'skipped']) expect(WATCH_LABEL[s]).toBeTruthy()
  })
})
