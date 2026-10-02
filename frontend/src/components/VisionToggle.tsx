import { useEffect, useState } from 'react'

interface Props {
  providerId: string
  model: string
  /** '' = automatic, '1' = the user says yes, '0' = the user says no. */
  value: string
  onChange: (v: string) => void
}

/**
 * "This model can see screenshots." The app guesses from the model's name (the guess is asked of the server,
 * which is the one place that knows). The user can overrule it; the tick then means what they said, and a link
 * puts it back to automatic. Choosing what the guess already is stores nothing (stays automatic).
 */
export default function VisionToggle({ providerId, model, value, onChange }: Props) {
  const [auto, setAuto] = useState<boolean | null>(null)

  useEffect(() => {
    let live = true
    setAuto(null)
    const q = `provider=${encodeURIComponent(providerId)}&model=${encodeURIComponent(model)}`
    fetch(`/settings/vision?${q}`)
      .then((r) => (r.ok ? r.json() : null))
      .then((d) => { if (live && d && typeof d.auto === 'boolean') setAuto(d.auto) })
      .catch(() => {})
    return () => { live = false }
  }, [providerId, model])

  const overridden = value === '1' || value === '0'
  const checked = overridden ? value === '1' : auto === true

  return (
    <div className="mb-4">
      <label className="flex items-start gap-2.5 text-[13px] text-gray-800">
        <input
          type="checkbox"
          checked={checked}
          disabled={auto === null && !overridden}
          onChange={(e) => onChange(e.target.checked === auto ? '' : e.target.checked ? '1' : '0')}
          className="mt-0.5 h-4 w-4 flex-shrink-0 accent-[#2F6B4F]"
        />
        <span>
          This model can see screenshots
          <span className="mt-0.5 block text-[11px] leading-snug text-gray-400">
            {checked
              ? 'The agent looks at the page and its element list, and the end-of-run check is on.'
              : 'The agent reads only the page text and element list, and the end-of-run check is off.'}
          </span>
          <span className="mt-0.5 block text-[11px] leading-snug text-gray-400">
            {overridden ? (
              <>
                You set this by hand.{' '}
                <button type="button" onClick={() => onChange('')} className="text-brand underline">
                  Use automatic{auto === null ? '' : ` (${auto ? 'yes' : 'no'})`}
                </button>
              </>
            ) : auto === null ? (
              'Checking…'
            ) : (
              `Detected automatically from the model's name (${auto ? 'yes' : 'no'}). Untick or tick to overrule it.`
            )}
          </span>
        </span>
      </label>
    </div>
  )
}
