import { useState } from 'react'
import { Wrench } from 'lucide-react'
import type { RunRepair } from '@/types'
import { errText, readJson, reasonOf } from './shared'

interface Props {
  playbookId: string
  runId: string
  repairs: RunRepair[]
  /** The playbook was changed; the new version has to be approved again. */
  onApplied: (info: { version: number; schedulesAffected: number }) => void
}

/** What a run had to repair to get through, and a way to write it into the playbook (never done by itself). */
export default function RepairsPanel({ playbookId, runId, repairs, onApplied }: Props) {
  const [picked, setPicked] = useState<Set<string>>(() => new Set(repairs.map((r) => r.step_id)))
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [done, setDone] = useState<string[] | null>(null)

  const toggle = (id: string) => setPicked((p) => { const n = new Set(p); if (n.has(id)) n.delete(id); else n.add(id); return n })

  const apply = async () => {
    setBusy(true); setError('')
    try {
      const res = await fetch(`/playbooks/${playbookId}/runs/${runId}/apply-repairs`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ step_ids: [...picked] }),
      })
      const data = await readJson(res)
      if (!res.ok || !data.ok) { setError([reasonOf(res, data), ...(data.problems ?? [])].join(' ')); return }
      setDone(data.applied)
      onApplied({ version: data.playbook?.version ?? 0, schedulesAffected: data.schedules_affected ?? 0 })
    } catch (e) {
      setError(`Couldn't update the playbook (${errText(e)}).`)
    } finally { setBusy(false) }
  }

  return (
    <div className="rounded-lg border border-amber-200 bg-amber-50/60 px-3 py-2">
      <p className="flex items-center gap-1.5 text-xs font-medium text-amber-900"><Wrench size={13} /> This run repaired {repairs.length} step{repairs.length === 1 ? '' : 's'} to get through</p>
      <p className="mt-0.5 text-[11px] text-amber-900/80">The page no longer matched what was recorded, so an AI model picked the closest match. The playbook itself is unchanged until you update it.</p>
      <ul className="mt-2 space-y-1.5">
        {repairs.map((r) => (
          <li key={r.step_id} className="text-xs text-gray-800">
            <label className="flex items-start gap-1.5">
              <input type="checkbox" checked={picked.has(r.step_id)} disabled={done !== null} onChange={() => toggle(r.step_id)} className="mt-0.5" />
              <span>
                <span className="font-medium">{r.note || r.step_id}</span>: {r.summary}
                {r.why && <span className="block text-[11px] text-gray-500">Why: {r.why}</span>}
              </span>
            </label>
          </li>
        ))}
      </ul>
      {done ? (
        <p role="status" className="mt-2 text-xs text-green-800">Updated {done.length} step{done.length === 1 ? '' : 's'}. It is a new version: open the playbook and approve it again before it runs by itself.</p>
      ) : (
        <div className="mt-2 flex items-center gap-2">
          <button onClick={apply} disabled={busy || picked.size === 0} className="rounded-md bg-brand px-2.5 py-1 text-xs font-medium text-white disabled:opacity-40">
            {busy ? 'Updating…' : 'Update the playbook with these'}
          </button>
          <span className="text-[11px] text-gray-500">You will approve the new version.</span>
        </div>
      )}
      {error && <p role="alert" className="mt-2 rounded-md bg-red-50 px-2 py-1 text-[11px] text-red-700">{error}</p>}
    </div>
  )
}
