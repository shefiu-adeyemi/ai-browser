import { useEffect, useRef, useState } from 'react'
import ScrapeEditor from './ScrapeEditor'
import ForEachEditor from './ForEachEditor'
import { Trash2, ShieldCheck, Sparkles, MousePointerClick, RotateCcw } from 'lucide-react'
import type { ModelTier, Playbook, PlaybookStep, TiersInfo, VariableSuggestion } from '@/types'
import { KIND_LABEL, TIER_LABEL, addLoop, askForText, askedVariable, autoTier, errText, fetchTiers, isGuarded, readJson, stepDetail, tierModelText, stepTarget, stopAskingForText, usedVariables, reasonOf } from './shared'

interface Props {
  /** 'draft': the last finished run, not stored until saved. 'edit': a saved playbook. */
  mode: 'draft' | 'edit'
  id?: string
  onCancel: () => void
  /** `schedulesAffected`: how many switched-on schedules lost their approval because of this save. */
  onSaved: (name: string, schedulesAffected: number) => void
}

/**
 * The review screen. Both ways in end here: saving a finished run (nothing is stored until Save) and
 * changing a playbook that is already saved (name, steps, inputs, approval, older versions).
 */
/** The job a model step does, as the router names it (so the screen can say which tier "automatic" means). */
const purposeOf = (s: PlaybookStep) => (s.kind === 'extract' ? 'playbook_extract' : 'agent')

export default function Editor({ mode, id, onCancel, onSaved }: Props) {
  const [pb, setPb] = useState<Playbook | null>(null)
  const [suggestions, setSuggestions] = useState<VariableSuggestion[]>([])
  const [notes, setNotes] = useState<string[]>([])
  const [versions, setVersions] = useState<number[]>([])
  const [wasApproved, setWasApproved] = useState(false)
  const [loadError, setLoadError] = useState('')
  const [problems, setProblems] = useState<string[]>([])
  const [approve, setApprove] = useState(false)
  const [changed, setChanged] = useState(false)
  const [busy, setBusy] = useState(false)
  const [tiers, setTiers] = useState<TiersInfo | null>(null)
  const [remembered, setRemembered] = useState<string[]>([])      // typed answers kept for later runs (names only)
  const askedText = useRef<Record<string, string>>({})        // what a step typed before it was turned into a question

  const load = async () => {
    try {
      const res = mode === 'draft'
        ? await fetch('/playbooks/draft', { method: 'POST' })
        : await fetch(`/playbooks/${id}`)
      const data = await readJson(res)
      if (!res.ok || !data.ok) { setLoadError(reasonOf(res, data)); return }
      setPb(data.playbook)
      setSuggestions(data.suggestions ?? [])
      setNotes(data.notes ?? [])
      setVersions(data.versions ?? [])
      setWasApproved(!!data.approved)
      setApprove(!!data.approved)
      setChanged(false)
      setProblems([])
    } catch (e) {
      setLoadError(`Couldn't open it (${errText(e)}).`)
    }
  }

  useEffect(() => { load() }, [mode, id])      // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => { fetchTiers().then(setTiers) }, [])
  useEffect(() => {
    if (mode !== 'edit' || !id) { setRemembered([]); return }
    fetch(`/playbooks/${id}/remembered`).then(readJson).then((d) => setRemembered(Array.isArray(d.variables) ? d.variables : [])).catch(() => setRemembered([]))
  }, [mode, id])
  const forget = async (name: string) => {
    try {
      const res = await fetch(`/playbooks/${id}/remembered/${encodeURIComponent(name)}`, { method: 'DELETE' })
      const data = await readJson(res)
      if (!res.ok || !data.ok) { setProblems([reasonOf(res, data)]); return }
      setRemembered((r) => r.filter((n) => n !== name))
    } catch (e) {
      setProblems([`Couldn't forget it (${errText(e)}).`])
    }
  }

  if (loadError) {
    return (
      <div className="p-5">
        <p role="alert" className="rounded-lg bg-red-50 px-3 py-2 text-sm text-red-700">{loadError}</p>
        <div className="mt-4 flex justify-end">
          <button onClick={onCancel} className="rounded-lg border border-gray-300 px-3 py-1.5 text-xs text-gray-700">Back</button>
        </div>
      </div>
    )
  }
  if (!pb) return <p className="p-8 text-center text-xs text-gray-400">Opening…</p>

  const askSteps = pb.steps.filter((s) => s.ask)
  const aiSteps = pb.steps.filter((s) => s.tag === 'ai')
  const shown = suggestions.filter((s) => pb.steps.some((st) => st.id === s.step_id))

  // Any change to the steps takes the approval away (the server does the same); the user ticks it again.
  const stepsChanged = (next: Playbook) => { setPb(next); setApprove(false); setChanged(true) }

  const makeVariable = async (sug: VariableSuggestion) => {
    setBusy(true)
    try {
      const res = await fetch('/playbooks/variable', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ playbook: pb, step_id: sug.step_id, field: sug.field, name: sug.name, type: sug.type, allowed_hosts: sug.allowed_hosts }),
      })
      const data = await readJson(res)
      if (!res.ok || !data.ok) { setProblems([reasonOf(res, data)]); return }
      stepsChanged(data.playbook)
      setSuggestions((cur) => cur.filter((s) => s !== sug))
      setProblems([])
    } catch (e) {
      setProblems([`Couldn't change that (${errText(e)}).`])
    } finally { setBusy(false) }
  }

  const removeStep = (stepId: string) => {
    const gone = pb.steps.find((s) => s.id === stepId)
    if (gone?.kind === 'input' && gone.variable) {          // deleting the question puts the typed text back
      const typer = pb.steps.find((s) => s.kind === 'type' && s.text === `{{${gone.variable}}}`)
      stepsChanged(stopAskingForText(pb, gone.variable, (typer && askedText.current[typer.id]) || ''))
      return
    }
    const steps = pb.steps.filter((s) => s.id !== stepId)
    stepsChanged({ ...pb, steps, variables: usedVariables(steps, pb.variables) })
  }

  // "Ask me each time": put an approve step right before a risky step (or take it away again).
  const toggleGuard = (index: number) => {
    const step = pb.steps[index]
    if (isGuarded(pb.steps, index)) {
      stepsChanged({ ...pb, steps: pb.steps.filter((_, i) => i !== index - 1) })
      return
    }
    const nums = pb.steps.map((s) => Number(s.id.slice(1))).filter((n) => !Number.isNaN(n))
    const id = `s${Math.max(0, ...nums) + 1}`
    const label = stepTarget(step) || step.note || 'this step'
    const guard: PlaybookStep = { id, kind: 'approve', tag: 'deterministic', note: '', message: `Run "${label}"?`, ask: false }
    stepsChanged({ ...pb, steps: [...pb.steps.slice(0, index), guard, ...pb.steps.slice(index)] })
  }

  // "Ask me for this text each run": the text is typed in when the run gets there, not stored in the playbook.
  const toggleAsk = (index: number) => {
    const step = pb.steps[index]
    const name = askedVariable(step)
    if (name) {
      stepsChanged(stopAskingForText(pb, name, askedText.current[step.id] ?? ''))
      return
    }
    const { playbook, original } = askForText(pb, index)
    askedText.current[step.id] = original
    stepsChanged(playbook)
  }

  const canLoop = pb.steps.some((s) => s.kind === 'scrape' && (s.fields ?? []).length > 0)
  const addLoopSteps = () => { const next = addLoop(pb); if (next) stepsChanged(next) }
  // what a loop variable is (its type, the sites it may open) is part of what the user approves, unlike a label
  const changeVariable = (name: string, patch: Record<string, unknown>) =>
    stepsChanged({ ...pb, variables: pb.variables.map((v) => (v.name === name ? { ...v, ...patch } : v)) })

  const addTableStep = () => {
    const nums = pb.steps.map((s) => Number(s.id.slice(1))).filter((n) => !Number.isNaN(n))
    const step: PlaybookStep = {
      id: `s${Math.max(0, ...nums) + 1}`, kind: 'scrape', tag: 'deterministic', note: 'Read a table', ask: false,
      row_selector: null, max_rows: null, fields: [{ name: 'Name', selector: '', type: 'text' }],
    }
    stepsChanged({ ...pb, steps: [...pb.steps, step] })
  }

  const editStep = (id: string, patch: Partial<PlaybookStep>) => {
    stepsChanged({ ...pb, steps: pb.steps.map((s) => (s.id === id ? { ...s, ...patch } : s)) })
  }

  // Which model tier reads the page changes what a run costs, not what it does, so the approval stays.
  const setTier = (stepId: string, tier: ModelTier | '') => {
    setPb({ ...pb, steps: pb.steps.map((s) => (s.id === stepId ? { ...s, tier: tier || null } : s)) })
    setChanged(true)
  }

  const editVariable = (name: string, patch: Record<string, unknown>) => {
    // a label, default or "required" does not change what the playbook does, so the approval stays
    setPb({ ...pb, variables: pb.variables.map((v) => (v.name === name ? { ...v, ...patch } : v)) })
    setChanged(true)
  }

  const restore = async (version: number) => {
    setBusy(true)
    try {
      const res = await fetch(`/playbooks/${id}/restore`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ version }),
      })
      const data = await readJson(res)
      if (!res.ok || !data.ok) { setProblems(data.problems?.length ? data.problems : [reasonOf(res, data)]); return }
      await load()
    } catch (e) {
      setProblems([`Couldn't restore it (${errText(e)}).`])
    } finally { setBusy(false) }
  }

  const save = async () => {
    setBusy(true)
    try {
      const res = await fetch('/playbooks', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          playbook: pb,
          approve,
          revoke: mode === 'edit' && wasApproved && !approve,
          expected_version: mode === 'edit' ? pb.version : undefined,
        }),
      })
      const data = await readJson(res)
      if (!res.ok || !data.ok) { setProblems(data.problems?.length ? data.problems : [reasonOf(res, data)]); return }
      onSaved(data.playbook.name, Number(data.schedules_affected) || 0)
    } catch (e) {
      setProblems([`Couldn't save (${errText(e)}).`])
    } finally { setBusy(false) }
  }

  return (
    <>
      <div className="min-h-0 flex-1 space-y-4 overflow-y-auto px-5 py-4">
        <label className="block">
          <span className="text-xs font-medium text-gray-700">Name</span>
          <input
            value={pb.name}
            maxLength={120}
            onChange={(e) => { setPb({ ...pb, name: e.target.value }); setChanged(true) }}
            className="mt-1 w-full rounded-lg border border-gray-300 px-3 py-2 text-sm focus:border-brand focus:outline-none"
          />
        </label>

        <p className="text-xs text-gray-600">
          {pb.steps.length} step{pb.steps.length === 1 ? '' : 's'}:{' '}
          <span className="font-medium text-green-700">{pb.steps.length - aiSteps.length} run without AI</span>
          {aiSteps.length > 0 && <>, <span className="font-medium text-purple-700">{aiSteps.length} {aiSteps.length === 1 ? 'needs' : 'need'} AI</span></>}.
        </p>

        {notes.length > 0 && (
          <ul className="space-y-1 rounded-lg bg-amber-50 px-3 py-2 text-xs text-amber-800">
            {notes.map((n, i) => <li key={i}>• {n}</li>)}
          </ul>
        )}

        <ol className="space-y-1.5">
          {pb.steps.map((s, i) => (
            <li key={s.id} className="flex items-start gap-2 rounded-lg border border-gray-200 px-3 py-2">
              <span className="mt-0.5 w-5 flex-shrink-0 text-right text-xs text-gray-400">{i + 1}</span>
              <div className="min-w-0 flex-1">
                <div className="flex flex-wrap items-center gap-1.5">
                  <span className="text-xs font-medium text-gray-900">{KIND_LABEL[s.kind] ?? s.kind}</span>
                  {stepTarget(s) && (
                    <span className="inline-flex items-center gap-1 text-xs text-gray-600"><MousePointerClick size={11} />{stepTarget(s)}</span>
                  )}
                  {s.kind === 'approve' || s.kind === 'input' ? (
                    <span className="rounded-full bg-amber-50 px-1.5 py-0.5 text-[10px] font-medium text-amber-700">you answer</span>
                  ) : s.tag === 'ai' ? (
                    <span className="inline-flex items-center gap-1 rounded-full bg-purple-50 px-1.5 py-0.5 text-[10px] font-medium text-purple-700"><Sparkles size={10} />AI</span>
                  ) : s.kind === 'handoff' ? (
                    <span className="rounded-full bg-blue-50 px-1.5 py-0.5 text-[10px] font-medium text-blue-700">you do this</span>
                  ) : (
                    <span className="rounded-full bg-green-50 px-1.5 py-0.5 text-[10px] font-medium text-green-700">no AI</span>
                  )}
                  {s.ask && (
                    <span className="rounded-full bg-red-50 px-1.5 py-0.5 text-[10px] font-medium text-red-700" title="Reaches other people or can't be undone">needs approval</span>
                  )}
                </div>
                {s.kind === 'approve' || s.kind === 'input' ? (
                  <div className="mt-1 space-y-1">
                    <input aria-label={`Question for step ${i + 1}`} value={s.message ?? ''} maxLength={500}
                      onChange={(e) => editStep(s.id, { message: e.target.value })}
                      className="w-full rounded-md border border-gray-300 px-2 py-1 text-xs focus:border-brand focus:outline-none" />
                    <label className="flex items-center gap-1.5 text-[11px] text-gray-500">
                      If nobody answers within
                      <select aria-label={`Wait for step ${i + 1}`} value={s.timeout_minutes ?? 1440}
                        onChange={(e) => editStep(s.id, { timeout_minutes: Number(e.target.value) })}
                        className="rounded border border-gray-300 px-1 py-0.5 text-[11px]">
                        <option value={60}>1 hour</option><option value={360}>6 hours</option>
                        <option value={1440}>1 day</option><option value={4320}>3 days</option><option value={10080}>7 days</option>
                      </select>
                      {s.kind === 'input' ? 'the run is cancelled.' : isGuarded(pb.steps, i + 1) ? (
                        <select aria-label={`When nobody answers for step ${i + 1}`} value={s.on_timeout ?? 'cancel'}
                          onChange={(e) => editStep(s.id, { on_timeout: e.target.value as 'cancel' | 'skip' })}
                          className="rounded border border-gray-300 px-1 py-0.5 text-[11px]">
                          <option value="cancel">cancel the run, nothing is sent</option>
                          <option value="skip">skip that step and carry on</option>
                        </select>
                      ) : 'it is cancelled, never sent.'}
                    </label>
                  </div>
                ) : s.kind === 'foreach' ? (
                  <ForEachEditor steps={pb.steps} step={s} index={i} variable={pb.variables.find((v) => v.name === s.variable)}
                    onChange={(patch) => editStep(s.id, patch)} onVariable={(patch) => changeVariable(s.variable ?? '', patch)} />
                ) : s.kind === 'scrape' ? (
                  <ScrapeEditor step={s} index={i} onChange={(patch) => editStep(s.id, patch)} />
                ) : (
                  stepDetail(s) && <p className="mt-0.5 break-all text-xs text-gray-500">{stepDetail(s)}</p>
                )}
                {s.tag === 'ai' && (
                  <div className="mt-1 text-[11px] text-gray-600">
                    <label className="flex flex-wrap items-center gap-1.5">
                      Model
                      <select aria-label={`Model tier for step ${i + 1}`} value={s.tier ?? ''} disabled={tiers?.router === 'off'}
                        onChange={(e) => setTier(s.id, e.target.value as ModelTier | '')}
                        className="rounded border border-gray-300 px-1 py-0.5 text-[11px] disabled:opacity-60">
                        <option value="">{tiers ? `Automatic (${TIER_LABEL[autoTier(tiers, purposeOf(s))]})` : 'Automatic'}</option>
                        <option value="fast">Fast, cheapest</option>
                        <option value="standard">Standard</option>
                        <option value="strong">Strong, most capable</option>
                      </select>
                    </label>
                    <p className="mt-0.5 text-gray-400">
                      {s.tier
                        ? `${TIER_LABEL[s.tier]}: ${tierModelText(tiers, s.tier)}. Only this tier is used, even if it fails.`
                        : tiers ? `${TIER_LABEL[autoTier(tiers, purposeOf(s))]} first (${tierModelText(tiers, autoTier(tiers, purposeOf(s)))}), then a stronger one only if it fails.` : ''}
                    </p>
                  </div>
                )}
                {s.kind === 'type' && (
                  <label className="mt-1 flex items-center gap-1.5 text-[11px] text-gray-600">
                    <input type="checkbox" checked={askedVariable(s) !== null} onChange={() => toggleAsk(i)} />
                    Ask me for this text each time this runs
                  </label>
                )}
                {s.ask && (
                  <label className="mt-1 flex items-center gap-1.5 text-[11px] text-gray-600">
                    <input type="checkbox" checked={isGuarded(pb.steps, i)} onChange={() => toggleGuard(i)} />
                    Ask me each time this runs
                  </label>
                )}
              </div>
              <button aria-label={`Remove step ${i + 1}`} onClick={() => removeStep(s.id)} className="flex-shrink-0 rounded p-1 text-gray-300 hover:bg-gray-100 hover:text-red-600">
                <Trash2 size={13} />
              </button>
            </li>
          ))}
        </ol>
        <div className="flex flex-wrap gap-x-4 gap-y-1">
          <button onClick={addTableStep} className="text-xs text-brand hover:underline">+ Add a step that reads a table from the page</button>
          <button onClick={addLoopSteps} disabled={!canLoop} title={canLoop ? '' : 'Add a step that reads a table first'}
            className="text-xs text-brand hover:underline disabled:text-gray-300 disabled:no-underline">+ Add a loop: open each link and read it</button>
        </div>

        {remembered.length > 0 && (
          <div className="rounded-lg bg-gray-50 px-3 py-2 text-xs text-gray-600">
            <p className="font-medium text-gray-700">Answers kept for next runs</p>
            <p className="text-[11px] text-gray-500">These questions are not asked again until you forget the answer. They are saved on this computer.</p>
            <ul className="mt-1 space-y-1">
              {remembered.map((n) => (
                <li key={n} className="flex items-center justify-between gap-2">
                  <span className="font-mono">{n}</span>
                  <button onClick={() => forget(n)} aria-label={`Forget ${n}`} className="rounded-md border border-gray-300 px-2 py-0.5 text-[11px] text-gray-700 hover:text-red-600">Forget</button>
                </li>
              ))}
            </ul>
          </div>
        )}

        {pb.variables.some((v) => !v.runtime && !v.loop) && (
          <div>
            <p className="text-xs font-medium text-gray-700">Inputs you will fill in each time</p>
            <ul className="mt-1 space-y-2">
              {pb.variables.filter((v) => !v.runtime && !v.loop).map((v) => (
                <li key={v.name} className="rounded-lg bg-gray-50 px-3 py-2 text-xs text-gray-600">
                  <p><span className="font-mono">{v.name}</span> ({v.type.replace('_', ' ')})</p>
                  {mode === 'edit' ? (
                    <div className="mt-1.5 grid grid-cols-1 gap-1.5 sm:grid-cols-[1fr_1fr_auto]">
                      <input
                        aria-label={`Label for ${v.name}`}
                        value={v.label}
                        maxLength={100}
                        placeholder="Label"
                        onChange={(e) => editVariable(v.name, { label: e.target.value })}
                        className="rounded-md border border-gray-300 px-2 py-1 text-xs focus:border-brand focus:outline-none"
                      />
                      <input
                        aria-label={`Default for ${v.name}`}
                        value={v.default ?? ''}
                        placeholder="Default value"
                        onChange={(e) => editVariable(v.name, { default: e.target.value === '' ? null : e.target.value })}
                        className="rounded-md border border-gray-300 px-2 py-1 text-xs focus:border-brand focus:outline-none"
                      />
                      <label className="flex items-center gap-1 text-xs text-gray-600">
                        <input type="checkbox" checked={v.required} onChange={(e) => editVariable(v.name, { required: e.target.checked })} />
                        required
                      </label>
                    </div>
                  ) : (
                    v.default ? <p className="break-all text-gray-500">was {v.default}</p> : null
                  )}
                </li>
              ))}
            </ul>
          </div>
        )}

        {shown.length > 0 && (
          <div>
            <p className="text-xs font-medium text-gray-700">Make these changeable</p>
            <ul className="mt-1 space-y-1.5">
              {shown.map((s) => (
                <li key={`${s.step_id}-${s.field}`} className="flex items-center justify-between gap-3 rounded-lg bg-gray-50 px-3 py-2">
                  <div className="min-w-0 text-xs">
                    <p className="break-all text-gray-800">{s.value}</p>
                    <p className="text-gray-500">{s.reason} Name: <span className="font-mono">{s.name}</span></p>
                  </div>
                  <button
                    disabled={busy}
                    onClick={() => makeVariable(s)}
                    className="flex-shrink-0 rounded-md border border-gray-300 bg-white px-2 py-1 text-xs font-medium text-gray-700 hover:bg-gray-50 disabled:opacity-50"
                  >
                    Make an input
                  </button>
                </li>
              ))}
            </ul>
          </div>
        )}

        {pb.domains.length > 0 && (
          <p className="text-xs text-gray-600">It may visit: <span className="font-medium">{pb.domains.join(', ')}</span></p>
        )}

        <label className="flex items-start gap-2 rounded-lg border border-gray-200 px-3 py-2.5">
          <input type="checkbox" checked={approve} onChange={(e) => setApprove(e.target.checked)} className="mt-0.5" />
          <span className="text-xs text-gray-700">
            <span className="flex items-center gap-1 font-medium text-gray-900"><ShieldCheck size={13} /> I approve this playbook to run as shown</span>
            <span className="mt-0.5 block">
              {askSteps.length > 0
                ? `This includes ${askSteps.length} step${askSteps.length === 1 ? '' : 's'} marked "needs approval". `
                : ''}
              Editing any step later removes the approval.
            </span>
          </span>
        </label>

        {mode === 'edit' && versions.length > 0 && (
          <div>
            <p className="text-xs font-medium text-gray-700">Earlier versions</p>
            <p className="text-[11px] text-gray-400">This is version {pb.version}. Restoring one makes a new version, and has to be approved again.</p>
            <ul className="mt-1 flex flex-wrap gap-1.5">
              {versions.map((v) => (
                <li key={v}>
                  <button
                    disabled={busy || changed}
                    onClick={() => restore(v)}
                    title={changed ? 'Save or discard your changes first' : undefined}
                    className="inline-flex items-center gap-1 rounded-md border border-gray-300 px-2 py-1 text-xs text-gray-700 hover:bg-gray-50 disabled:opacity-40"
                  >
                    <RotateCcw size={11} /> Version {v}
                  </button>
                </li>
              ))}
            </ul>
          </div>
        )}

        {problems.length > 0 && (
          <ul role="alert" className="space-y-1 rounded-lg bg-red-50 px-3 py-2 text-xs text-red-700">
            {problems.map((p, i) => <li key={i}>• {p}</li>)}
          </ul>
        )}
      </div>
      <div className="flex justify-end gap-2 border-t border-gray-100 px-5 py-3">
        <button onClick={onCancel} className="rounded-lg border border-gray-300 px-3 py-1.5 text-xs font-medium text-gray-700 hover:bg-gray-50">
          {mode === 'edit' ? 'Back' : 'Cancel'}
        </button>
        <button
          disabled={busy || pb.steps.length === 0 || !pb.name.trim() || (mode === 'edit' && !changed && approve === wasApproved)}
          onClick={save}
          className="rounded-lg bg-brand px-4 py-1.5 text-xs font-medium text-white disabled:opacity-40"
        >
          {approve && !wasApproved ? 'Save and approve' : 'Save'}
        </button>
      </div>
    </>
  )
}
