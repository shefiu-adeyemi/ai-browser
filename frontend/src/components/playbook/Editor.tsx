import { useEffect, useState } from 'react'
import { Trash2, ShieldCheck, Sparkles, MousePointerClick, RotateCcw } from 'lucide-react'
import type { Playbook, VariableSuggestion } from '@/types'
import { KIND_LABEL, errText, readJson, stepDetail, stepTarget, usedVariables, reasonOf } from './shared'

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
    const steps = pb.steps.filter((s) => s.id !== stepId)
    stepsChanged({ ...pb, steps, variables: usedVariables(steps, pb.variables) })
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
                  {s.tag === 'ai' ? (
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
                {stepDetail(s) && <p className="mt-0.5 break-all text-xs text-gray-500">{stepDetail(s)}</p>}
              </div>
              <button aria-label={`Remove step ${i + 1}`} onClick={() => removeStep(s.id)} className="flex-shrink-0 rounded p-1 text-gray-300 hover:bg-gray-100 hover:text-red-600">
                <Trash2 size={13} />
              </button>
            </li>
          ))}
        </ol>

        {pb.variables.length > 0 && (
          <div>
            <p className="text-xs font-medium text-gray-700">Inputs you will fill in each time</p>
            <ul className="mt-1 space-y-2">
              {pb.variables.map((v) => (
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
