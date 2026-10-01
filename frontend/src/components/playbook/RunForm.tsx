import { useEffect, useState } from 'react'
import type { Playbook, PlaybookVariable } from '@/types'
import { errText, readJson, reasonOf } from './shared'

const INPUT_HINT: Record<PlaybookVariable['type'], string> = {
  text: '',
  number: 'A number',
  url: 'A web address starting with https://',
  linkedin_url: 'A LinkedIn profile address, like https://www.linkedin.com/in/name',
}

interface Props {
  id: string
  busy: boolean
  onCancel: () => void
  onStarted: (name: string) => void
}

/** Asks for the playbook's inputs, then starts the run in the current chat. */
export default function RunForm({ id, busy, onCancel, onStarted }: Props) {
  const [pb, setPb] = useState<Playbook | null>(null)
  const [approved, setApproved] = useState(false)
  const [values, setValues] = useState<Record<string, string>>({})
  const [loadError, setLoadError] = useState('')
  const [problems, setProblems] = useState<string[]>([])
  const [starting, setStarting] = useState(false)

  useEffect(() => {
    let alive = true
    ;(async () => {
      try {
        const res = await fetch(`/playbooks/${id}`)
        const data = await readJson(res)
        if (!alive) return
        if (!res.ok || !data.ok) { setLoadError(reasonOf(res, data)); return }
        const p: Playbook = data.playbook
        setPb(p); setApproved(!!data.approved)
        setValues(Object.fromEntries(p.variables.filter((v) => !v.runtime).map((v) => [v.name, v.default ?? ''])))
      } catch (e) {
        if (alive) setLoadError(`Couldn't open it (${errText(e)}).`)
      }
    })()
    return () => { alive = false }
  }, [id])

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

  const askCount = pb.steps.filter((s) => s.ask).length
  const aiCount = pb.steps.filter((s) => s.tag === 'ai').length
  const handoffCount = pb.steps.filter((s) => s.kind === 'handoff').length

  const start = async () => {
    setStarting(true)
    setProblems([])
    try {
      const res = await fetch(`/playbooks/${id}/run`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ inputs: values }),
      })
      const data = await readJson(res)
      if (!res.ok || !data.ok) {
        setProblems(data.problems?.length ? data.problems : [reasonOf(res, data)])
        return
      }
      onStarted(pb.name)
    } catch (e) {
      setProblems([`Couldn't start it (${errText(e)}).`])
    } finally { setStarting(false) }
  }

  return (
    <>
      <div className="min-h-0 flex-1 space-y-4 overflow-y-auto px-5 py-4">
        <div>
          <p className="text-sm font-medium text-gray-900">{pb.name}</p>
          <p className="mt-0.5 text-xs text-gray-500">
            {pb.steps.length} step{pb.steps.length === 1 ? '' : 's'}
            {aiCount > 0 ? ` · ${aiCount} read the page with AI` : ' · no AI needed'}
            {pb.domains.length > 0 ? ` · sites: ${pb.domains.join(', ')}` : ''}
          </p>
        </div>

        {pb.variables.every((v) => v.runtime) && <p className="text-xs text-gray-500">This playbook has no inputs. It runs exactly as recorded.</p>}
        {pb.variables.filter((v) => !v.runtime).map((v) => (
          <label key={v.name} className="block">
            <span className="text-xs font-medium text-gray-700">
              {v.label || v.name}{v.required ? '' : ' (optional)'}
            </span>
            <input
              value={values[v.name] ?? ''}
              onChange={(e) => setValues({ ...values, [v.name]: e.target.value })}
              inputMode={v.type === 'number' ? 'decimal' : v.type === 'text' ? 'text' : 'url'}
              className="mt-1 w-full rounded-lg border border-gray-300 px-3 py-2 text-sm focus:border-brand focus:outline-none"
            />
            {INPUT_HINT[v.type] && <span className="mt-0.5 block text-[11px] text-gray-400">{INPUT_HINT[v.type]}</span>}
          </label>
        ))}

        {handoffCount > 0 && (
          <p className="rounded-lg bg-blue-50 px-3 py-2 text-xs text-blue-800">
            {handoffCount === 1 ? 'One step needs' : `${handoffCount} steps need`} you (for example signing in). The run waits for you there.
          </p>
        )}
        {!approved && (
          <p className="rounded-lg bg-amber-50 px-3 py-2 text-xs text-amber-800">
            This playbook isn't approved{askCount > 0 ? `, so its ${askCount} sensitive step${askCount === 1 ? '' : 's'} will ask you first` : ''}.
            Anything that sends, posts or can't be undone is asked about as it happens.
          </p>
        )}
        {approved && askCount > 0 && (
          <p className="rounded-lg bg-green-50 px-3 py-2 text-xs text-green-800">
            Approved. Its {askCount} sensitive step{askCount === 1 ? '' : 's'} run without asking, as you approved them.
          </p>
        )}
        {busy && <p className="rounded-lg bg-gray-50 px-3 py-2 text-xs text-gray-600">Another task is running. Wait for it to finish, or stop it.</p>}

        {problems.length > 0 && (
          <ul role="alert" className="space-y-1 rounded-lg bg-red-50 px-3 py-2 text-xs text-red-700">
            {problems.map((p, i) => <li key={i}>• {p}</li>)}
          </ul>
        )}
      </div>
      <div className="flex justify-end gap-2 border-t border-gray-100 px-5 py-3">
        <button onClick={onCancel} className="rounded-lg border border-gray-300 px-3 py-1.5 text-xs font-medium text-gray-700 hover:bg-gray-50">Back</button>
        <button
          disabled={starting || busy}
          onClick={start}
          className="rounded-lg bg-brand px-4 py-1.5 text-xs font-medium text-white disabled:opacity-40"
        >
          Run now
        </button>
      </div>
    </>
  )
}
