import { useState } from 'react'
import type { WatchSettings } from '@/types'
import { splitList } from './shared'

interface Props {
  value: WatchSettings
  onChange: (next: WatchSettings) => void
  /** Why watching can't be switched on for this playbook (it reads no table), or '' when it can. */
  unavailable: string
}

const input = 'rounded-md border border-gray-300 px-2 py-1 text-xs focus:border-brand focus:outline-none'

/** "Tell me only when it changes": what to compare by, what to ignore, and which kinds of change to hear about. */
export default function WatchForm({ value, onChange, unavailable }: Props) {
  // text boxes keep what is typed (a trailing comma, a blank line) and only the cleaned list goes up
  const [keys, setKeys] = useState(value.key_columns.join(', '))
  const [ignored, setIgnored] = useState(value.ignore_columns.join(', '))
  const [patterns, setPatterns] = useState(value.ignore_patterns.join('\n'))
  const set = (patch: Partial<WatchSettings>) => onChange({ ...value, ...patch })

  return (
    <fieldset className="rounded-lg border border-gray-200 p-3">
      <legend className="px-1 text-xs font-medium text-gray-700">Watch for changes</legend>
      <label className="flex items-start gap-2 text-xs text-gray-700">
        <input type="checkbox" checked={value.enabled && !unavailable} disabled={!!unavailable} onChange={(e) => set({ enabled: e.target.checked })} className="mt-0.5" />
        <span>
          <span className="font-medium">Tell me only when what it reads changes.</span>
          {' '}Each run is compared with the last one. Nothing is said when it is the same, and a whole batch gives one message.
        </span>
      </label>
      {unavailable && <p className="mt-1 text-[11px] text-amber-700">{unavailable}</p>}
      {value.enabled && !unavailable && (
        <div className="mt-3 space-y-2.5">
          <label className="block text-xs text-gray-700">
            Match rows by these columns
            <input aria-label="Columns that identify a row" value={keys} placeholder="Link, Name (empty: the first column)"
              onChange={(e) => { setKeys(e.target.value); set({ key_columns: splitList(e.target.value) }) }} className={`${input} mt-1 block w-full`} />
            <span className="text-[11px] text-gray-400">A row whose value in another column moved is "changed"; without a good key it looks like one row gone and one new.</span>
          </label>
          <label className="block text-xs text-gray-700">
            Leave out these columns
            <input aria-label="Columns to leave out" value={ignored} placeholder="Last seen, Views"
              onChange={(e) => { setIgnored(e.target.value); set({ ignore_columns: splitList(e.target.value) }) }} className={`${input} mt-1 block w-full`} />
          </label>
          <label className="block text-xs text-gray-700">
            Cut this out of every cell before comparing (one pattern per line)
            <textarea aria-label="Patterns to ignore" rows={2} value={patterns} placeholder={'\\d+ (minutes?|hours?) ago'}
              onChange={(e) => { setPatterns(e.target.value); set({ ignore_patterns: e.target.value.split('\n').map((l) => l.trim()).filter(Boolean) }) }}
              className={`${input} mt-1 block w-full font-mono`} />
            <span className="text-[11px] text-gray-400">For things that change by themselves, like "3 minutes ago" or a counter.</span>
          </label>
          <label className="flex flex-wrap items-center gap-1.5 text-xs text-gray-700">
            Ignore numbers that moved by less than
            <input aria-label="Smallest change that matters, in percent" type="number" min={0} max={100} step="any" value={value.min_change_percent}
              onChange={(e) => set({ min_change_percent: Math.max(0, Math.min(100, Number(e.target.value) || 0)) })} className={`${input} w-16`} />
            %
            <span className="text-[11px] text-gray-400">Small moves add up: it still tells you once they pass this.</span>
          </label>
          <div className="flex flex-wrap gap-x-4 gap-y-1 text-xs text-gray-700">
            <span className="font-medium">Tell me about</span>
            <label className="flex items-center gap-1"><input type="checkbox" checked={value.report_added} onChange={(e) => set({ report_added: e.target.checked })} /> new rows</label>
            <label className="flex items-center gap-1"><input type="checkbox" checked={value.report_removed} onChange={(e) => set({ report_removed: e.target.checked })} /> rows that are gone</label>
            <label className="flex items-center gap-1"><input type="checkbox" checked={value.report_changed} onChange={(e) => set({ report_changed: e.target.checked })} /> changed values</label>
          </div>
        </div>
      )}
    </fieldset>
  )
}
