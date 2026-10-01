import { useEffect, useState } from 'react'
import * as DropdownMenu from '@radix-ui/react-dropdown-menu'
import { ChevronDown, Gauge } from 'lucide-react'
import { cn } from '@/lib/utils'
import type { ModelTier, TiersInfo } from '@/types'
import { TIER_LABEL, fetchTiers, tierModelText } from './playbook/shared'

export type TierChoice = 'auto' | ModelTier

const HELP: Record<TierChoice, string> = {
  auto: 'The app picks per job: Standard for the steps, Fast for reading pages, Strong for a second attempt.',
  fast: 'The cheapest model does every step. Best for simple tasks.',
  standard: 'The model chosen in Settings does every step.',
  strong: 'The most capable model does every step. Slower and dearer.',
}

/**
 * Which model TIER runs the next task (not a model name: the models behind Fast, Standard and Strong are set in
 * Settings and can change at any time). "Auto" lets the app choose per job. A tier chosen here is used for
 * the whole task, with no automatic step up on failure, so what you pick is what you pay for.
 */
export default function TierPicker({ value, onChange, disabled, refreshKey }: {
  value: TierChoice
  onChange: (v: TierChoice) => void
  disabled: boolean
  refreshKey: number
}) {
  const [tiers, setTiers] = useState<TiersInfo | null>(null)
  useEffect(() => { fetchTiers().then(setTiers) }, [refreshKey])

  if (tiers?.router === 'off') return null          // one model for everything: nothing to choose
  const shown = `${value === 'auto' ? 'Auto' : TIER_LABEL[value]} tier`

  return (
    <DropdownMenu.Root>
      <DropdownMenu.Trigger asChild>
        <button
          disabled={disabled}
          aria-label={`Model tier: ${value === 'auto' ? 'Auto' : TIER_LABEL[value]}`}
          title={disabled ? 'Stop the task to change the tier' : 'Which model tier runs this task'}
          className="flex items-center gap-1 whitespace-nowrap rounded px-1 py-0.5 text-xs text-gray-400 transition-colors hover:text-gray-600 disabled:cursor-not-allowed disabled:opacity-60"
        >
          <Gauge size={11} className="flex-shrink-0" />
          {shown}
          <ChevronDown size={11} className="flex-shrink-0" />
        </button>
      </DropdownMenu.Trigger>
      <DropdownMenu.Portal>
        <DropdownMenu.Content side="top" align="start" sideOffset={6}
          className="z-50 w-[260px] rounded-xl border border-gray-100 bg-white p-1 shadow-card animate-fade-in">
          {(['auto', 'fast', 'standard', 'strong'] as TierChoice[]).map((opt) => (
            <DropdownMenu.Item key={opt} onSelect={() => onChange(opt)}
              className={cn('cursor-pointer rounded-lg px-3 py-1.5 outline-none transition-colors',
                value === opt ? 'bg-brand-tint' : 'hover:bg-gray-50 focus:bg-gray-50')}>
              <p className={cn('text-xs', value === opt ? 'font-medium text-brand' : 'text-gray-800')}>
                {opt === 'auto' ? 'Auto' : TIER_LABEL[opt]}
                {opt !== 'auto' && tiers && <span className="ml-1.5 font-normal text-gray-400">{tierModelText(tiers, opt)}</span>}
              </p>
              <p className="text-[11px] leading-snug text-gray-500">{HELP[opt]}</p>
            </DropdownMenu.Item>
          ))}
        </DropdownMenu.Content>
      </DropdownMenu.Portal>
    </DropdownMenu.Root>
  )
}
