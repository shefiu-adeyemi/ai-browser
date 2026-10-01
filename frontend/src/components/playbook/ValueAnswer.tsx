import { useState } from 'react'

interface Props {
  /** Send the typed value as the answer. */
  onSend: (value: string, remember: boolean) => void
  /** Say no instead: the run is cancelled. */
  onCancel: () => void
  label: string
}

/** A question that needs a typed answer (a code, a name) instead of a yes or no. */
export default function ValueAnswer({ onSend, onCancel, label }: Props) {
  const [value, setValue] = useState('')
  const [remember, setRemember] = useState(false)
  const ready = value.trim().length > 0
  return (
    <div className="mt-1.5">
    <div className="flex items-center gap-1.5">
      <input aria-label={label} value={value} maxLength={2000} onChange={(e) => setValue(e.target.value)}
        onKeyDown={(e) => { if (e.key === 'Enter' && ready) { e.preventDefault(); onSend(value, remember) } }}
        className="min-w-0 flex-1 rounded-md border border-gray-300 bg-white px-2 py-1 text-xs focus:border-brand focus:outline-none" />
      <button onClick={onCancel} className="rounded-md border border-gray-300 bg-white px-2.5 py-1 text-xs font-medium text-gray-700">Don't</button>
      <button disabled={!ready} onClick={() => onSend(value, remember)} className="rounded-md bg-brand px-2.5 py-1 text-xs font-medium text-white disabled:opacity-40">Send</button>
    </div>
    <label className="mt-1 flex items-center gap-1.5 text-[11px] text-gray-600">
      <input type="checkbox" checked={remember} onChange={(e) => setRemember(e.target.checked)} />
      Remember this for next runs (saved on this computer; otherwise it is used for this run only)
    </label>
    </div>
  )
}
