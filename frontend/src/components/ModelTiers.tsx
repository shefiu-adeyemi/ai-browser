import { useEffect, useState } from 'react'
import type { ModelCost, ModelPrices, ModelUsage, Settings } from '@/types'

type Update = <K extends keyof Settings>(k: K, v: Settings[K]) => void

const SELECT =
  'w-full rounded-lg border border-gray-300 bg-white px-2 py-1.5 text-xs text-gray-800 focus:border-brand focus:outline-none'

const TIER_BADGE: Record<string, string> = {
  fast: 'bg-green-50 text-green-700',
  standard: 'bg-blue-50 text-blue-700',
  strong: 'bg-purple-50 text-purple-700',
}

function money(v: number): string {
  return v > 0 && v < 0.01 ? 'under 0.01' : v.toFixed(2)
}

function fmt(n: number): string {
  return n >= 1_000_000 ? `${(n / 1_000_000).toFixed(1)}M` : n >= 1000 ? `${(n / 1000).toFixed(1)}k` : String(n)
}

/**
 * The model tiers part of Settings: a cheap Fast model for routine work, the Standard model (chosen above)
 * for the agent's own steps, and a Strong model for hard jobs and retries. Anything left unset uses the
 * Standard model, so nothing here has to be filled in.
 */
/** A price per million tokens, typed by the person: the app ships none. */
function PriceRow({ model, price, onSaved }: { model: string; price?: { in: number; out: number }; onSaved: (p: ModelPrices) => void }) {
  const [input, setInput] = useState(price ? String(price.in) : '')
  const [output, setOutput] = useState(price ? String(price.out) : '')
  const [problem, setProblem] = useState('')
  useEffect(() => { setInput(price ? String(price.in) : ''); setOutput(price ? String(price.out) : '') }, [price?.in, price?.out])   // eslint-disable-line react-hooks/exhaustive-deps

  const save = async () => {
    const a = input.trim(), b = output.trim()
    if (a === '' && b === '') {
      if (!price) { setProblem(''); return }
    } else if (a === '' || b === '' || Number.isNaN(Number(a)) || Number.isNaN(Number(b))) {
      setProblem('Enter a number for both, or clear both.')
      return
    }
    if (price && a !== '' && b !== '' && Number(a) === price.in && Number(b) === price.out) { setProblem(''); return }
    try {
      const res = await fetch('/models/prices', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ model, input: a === '' ? null : Number(a), output: b === '' ? null : Number(b) }),
      })
      const data = await res.json().catch(() => ({}))
      if (res.ok && data.ok) { setProblem(''); onSaved(data.prices) } else { setProblem(data.reason || `Could not save it (error ${res.status}).`) }
    } catch {
      setProblem('Could not reach the app.')
    }
  }
  const field = 'w-20 rounded-md border border-gray-300 px-1.5 py-1 text-xs focus:border-brand focus:outline-none'
  return (
    <li className="text-[11px] text-gray-600">
      {/* saved when focus leaves the row, so moving from the input price to the output price is not "half a price" */}
      <div className="flex flex-wrap items-center gap-1.5"
        onBlur={(e) => { if (!e.currentTarget.contains(e.relatedTarget as Node | null)) save() }}>
        <span className="min-w-0 flex-1 truncate font-mono" title={model}>{model}</span>
        <input aria-label={`Input price for ${model}`} inputMode="decimal" placeholder="in" className={field}
          value={input} onChange={(e) => setInput(e.target.value)} />
        <input aria-label={`Output price for ${model}`} inputMode="decimal" placeholder="out" className={field}
          value={output} onChange={(e) => setOutput(e.target.value)} />
      </div>
      {problem && <p role="alert" className="mt-0.5 text-red-600">{problem}</p>}
    </li>
  )
}

export default function ModelTiers({ settings, update }: { settings: Settings; update: Update }) {
  const providers = settings.PROVIDERS ?? []
  const tiers = settings.TIERS
  const [usage, setUsage] = useState<ModelUsage | null>(null)
  const [cost, setCost] = useState<ModelCost | null>(null)
  const [prices, setPrices] = useState<ModelPrices>({})
  const [showJobs, setShowJobs] = useState(false)

  useEffect(() => {
    let alive = true
    fetch('/models/usage?days=7')
      .then((r) => (r.ok ? r.json() : null))
      .then((d) => { if (alive && d?.ok) { setUsage(d.usage); setCost(d.cost ?? null); setPrices(d.prices ?? {}) } })
      .catch(() => { /* the usage line is optional */ })
    return () => { alive = false }
  }, [])

  const suggestion = tiers?.suggestions?.[settings.LLM_PROVIDER]

  const slot = (tier: 'fast' | 'strong', label: string, hint: string) => {
    const provKey = tier === 'fast' ? 'LLM_FAST_PROVIDER' : 'LLM_STRONG_PROVIDER'
    const modelKey = tier === 'fast' ? 'LLM_FAST_MODEL' : 'LLM_STRONG_MODEL'
    const provider = settings[provKey] as string
    const model = settings[modelKey] as string
    const info = providers.find((p) => p.id === (provider || settings.LLM_PROVIDER))
    const state = tiers?.slots?.[tier]
    return (
      <div className="mb-3">
        <div className="mb-1 flex items-center justify-between">
          <span className="text-xs font-medium text-gray-700">{label}</span>
          <span className="text-[11px] text-gray-400">{hint}</span>
        </div>
        <div className="grid grid-cols-2 gap-1.5">
          <select
            aria-label={`${label} provider`}
            className={SELECT}
            value={provider}
            onChange={(e) => { update(provKey, e.target.value as never); update(modelKey, '' as never) }}
          >
            <option value="">Same as Standard</option>
            {providers.map((p) => <option key={p.id} value={p.id}>{p.label}</option>)}
          </select>
          <select
            aria-label={`${label} model`}
            className={SELECT}
            value={model}
            disabled={!provider && !model}
            onChange={(e) => update(modelKey, e.target.value as never)}
          >
            <option value="">{info ? `Default (${info.default_model})` : 'Default'}</option>
            {(info?.models ?? []).map((m) => <option key={m} value={m}>{m}</option>)}
            {model && info && !info.models.includes(model) && <option value={model}>{model}</option>}
          </select>
        </div>
        {state && (
          <p className={`mt-1 text-[11px] ${state.collapsed && state.explicit ? 'text-amber-700' : 'text-gray-500'}`}>
            {state.collapsed
              ? `Uses the Standard model (${state.model}): ${state.reason}.`
              : `Using ${state.model}.`}
          </p>
        )}
      </div>
    )
  }

  const useSuggested = () => {
    if (!suggestion) return
    update('LLM_FAST_PROVIDER', settings.LLM_PROVIDER as never)
    update('LLM_FAST_MODEL', suggestion.fast as never)
    update('LLM_STRONG_PROVIDER', settings.LLM_PROVIDER as never)
    update('LLM_STRONG_MODEL', suggestion.strong as never)
  }

  const total = usage ? Object.values(usage.totals).reduce((a, t) => a + t.calls, 0) : 0
  const modelsInUse = Array.from(new Set(Object.values(tiers?.slots ?? {}).map((s) => s.model).filter(Boolean)))
  const priced = Object.keys(prices).length > 0

  // A price changes the estimate: ask for the usage again (the server does the sums, so there is one place for them).
  const pricesSaved = (p: ModelPrices) => {
    setPrices(p)
    fetch('/models/usage?days=7').then((r) => (r.ok ? r.json() : null)).then((d) => { if (d?.ok) setCost(d.cost ?? null) }).catch(() => {})
  }

  return (
    <div>
      <div className="mt-5 mb-2.5">
        <hr className="mb-3 border-gray-200" />
        <p className="text-[11px] font-semibold text-gray-600">Model tiers</p>
      </div>
      <p className="mb-3 text-[11px] leading-snug text-gray-500">
        Use a cheaper, faster model for routine work (reading a page, summarising) and a stronger one for hard jobs
        (a second attempt after the check says a task was incomplete). Leave them on "Same as Standard" to use one
        model for everything. If a call fails on one tier it is tried once on the next tier up.
      </p>

      <div className="mb-3 flex flex-wrap items-center gap-3 text-xs text-gray-700">
        <span className="font-medium">Router</span>
        {([['auto', 'Automatic'], ['off', 'Off: always Standard']] as const).map(([v, label]) => (
          <label key={v} className="flex items-center gap-1.5">
            <input type="radio" name="router" checked={settings.LLM_ROUTER === v} onChange={() => update('LLM_ROUTER', v as never)} />
            {label}
          </label>
        ))}
      </div>

      {slot('fast', 'Fast', 'quick and cheap')}
      <div className="mb-3 rounded-lg bg-gray-50 px-2.5 py-1.5 text-[11px] text-gray-600">
        <span className="font-medium text-gray-700">Standard</span>: the model chosen above
        {tiers?.slots?.standard ? ` (${tiers.slots.standard.model})` : ''}
      </div>
      {slot('strong', 'Strong', 'slowest, most capable')}

      {suggestion && (
        <button type="button" onClick={useSuggested}
          className="mb-3 rounded-md border border-gray-300 px-2.5 py-1 text-xs text-gray-700 hover:bg-gray-50">
          Use suggested models ({suggestion.fast} / {suggestion.strong})
        </button>
      )}

      {tiers && (
        <div className="mb-3">
          <button type="button" onClick={() => setShowJobs(!showJobs)} className="text-xs text-brand">
            {showJobs ? 'Hide' : 'Show'} which job uses which tier
          </button>
          {showJobs && (
            <ul className="mt-1.5 space-y-1">
              {tiers.purposes.map((p) => (
                <li key={p.purpose} className="flex items-center justify-between gap-2 text-[11px] text-gray-600">
                  <span>{p.label}</span>
                  <span className={`flex-shrink-0 rounded-full px-1.5 py-0.5 font-medium ${TIER_BADGE[p.tier] ?? ''}`}>
                    {p.tier === 'fast' ? 'Fast' : p.tier === 'strong' ? 'Strong' : 'Standard'}
                  </span>
                </li>
              ))}
            </ul>
          )}
        </div>
      )}

      {usage && (
        <div className="rounded-lg bg-gray-50 px-2.5 py-2">
          <p className="text-[11px] font-medium text-gray-700">Last 7 days</p>
          {total === 0 ? (
            <p className="text-[11px] text-gray-400">No AI calls counted yet.</p>
          ) : (
            <ul className="mt-1 space-y-0.5 text-[11px] text-gray-600">
              {(['fast', 'standard', 'strong'] as const).map((t) => (
                <li key={t} className="flex justify-between">
                  <span>{t === 'fast' ? 'Fast' : t === 'strong' ? 'Strong' : 'Standard'}</span>
                  <span>
                    {usage.totals[t].calls} calls · {fmt(usage.totals[t].prompt)} in · {fmt(usage.totals[t].completion)} out
                    {priced && cost && cost.per_tier[t] > 0 ? ` · about ${money(cost.per_tier[t])}` : ''}
                  </span>
                </li>
              ))}
              {priced && cost && (
                <li className="flex justify-between border-t border-gray-200 pt-0.5 font-medium text-gray-700">
                  <span>Estimated total</span><span>about {money(cost.total)}</span>
                </li>
              )}
            </ul>
          )}
          {priced && cost && cost.unpriced_tokens > 0 && (
            <p className="mt-1 text-[11px] text-amber-700">{fmt(cost.unpriced_tokens)} tokens are from models with no price, so they are not in the estimate.</p>
          )}
        </div>
      )}

      {modelsInUse.length > 0 && (
        <div className="mt-3">
          <p className="text-[11px] font-medium text-gray-700">Prices, if you want cost estimates (optional)</p>
          <p className="mb-1.5 text-[11px] leading-snug text-gray-500">
            Type what your provider charges per million tokens (input, then output), in your own currency. The app
            does not know prices, so it only multiplies what you enter.
          </p>
          <ul className="space-y-1.5">
            {modelsInUse.map((m) => <PriceRow key={m} model={m} price={prices[m]} onSaved={pricesSaved} />)}
          </ul>
        </div>
      )}
    </div>
  )
}
