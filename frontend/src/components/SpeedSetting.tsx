interface Props {
  providerId: string
  /** '' = automatic, 'fast' or 'careful'. */
  value: string
  onChange: (v: string) => void
}

const OPTIONS = [
  { value: '', label: 'Automatic', hint: (p: string) => (p === 'deepseek' ? 'Fast for DeepSeek, careful for the others.' : 'Careful for this provider.') },
  { value: 'fast', label: 'Faster', hint: () => 'The model writes less each step, may do up to 3 actions per step and skips separate planning.' },
  { value: 'careful', label: 'More careful', hint: () => 'The full format, one action per step, with planning. Slower, and uses more tokens.' },
]

/** How hard the agent thinks per step. Fast mode cuts the time (and cost) of every step; the price is less planning. */
export default function SpeedSetting({ providerId, value, onChange }: Props) {
  return (
    <fieldset className="mb-4" role="radiogroup" aria-label="Agent speed">
      <legend className="mb-1 text-[13px] text-gray-800">Speed</legend>
      <div className="space-y-1">
        {OPTIONS.map((o) => (
          <label key={o.value || 'auto'} className="flex items-start gap-2 text-[12.5px] text-gray-700">
            <input
              type="radio"
              name="agent-speed"
              checked={value === o.value}
              onChange={() => onChange(o.value)}
              className="mt-0.5 h-3.5 w-3.5 flex-shrink-0 accent-[#2F6B4F]"
            />
            <span>
              {o.label}
              <span className="block text-[11px] leading-snug text-gray-400">{o.hint(providerId)}</span>
            </span>
          </label>
        ))}
      </div>
    </fieldset>
  )
}
