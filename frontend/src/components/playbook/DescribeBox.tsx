import { useContext, useEffect, useRef, useState } from 'react'
import { Sparkles } from 'lucide-react'
import type { DescribedDraft } from '@/types'
import { errText, readJson, reasonOf } from './shared'
import { PickContext } from './pick'

interface Props {
  /** The draft is ready: open it for review (nothing is stored yet). */
  onDrafted: (draft: DescribedDraft) => void
  /** Poll interval in ms (tests make it short). */
  pollMs?: number
}

const MAX = 1500

/** "Describe a task": the AI plans it, tries the plan in the browser on the left, and hands back a draft to review. */
export default function DescribeBox({ onDrafted, pollMs = 1000 }: Props) {
  const { begin, end } = useContext(PickContext)
  const [text, setText] = useState('')
  const [usePage, setUsePage] = useState(false)
  const [allowUnnamed, setAllowUnnamed] = useState(false)
  const [running, setRunning] = useState<string | null>(null)       // the token while drafting
  const [error, setError] = useState('')
  const timer = useRef<ReturnType<typeof setInterval> | null>(null)
  const alive = useRef(true)

  const stopPolling = () => { if (timer.current) { clearInterval(timer.current); timer.current = null } }
  useEffect(() => () => { alive.current = false; stopPolling() }, [])

  const stop = async () => {
    try { await fetch('/stop', { method: 'POST' }) } catch { /* the poll will say what happened */ }
  }

  const poll = (token: string) => {
    stopPolling()
    timer.current = setInterval(async () => {
      try {
        const res = await fetch(`/playbooks/describe/${token}`)
        const data = await readJson(res)
        if (!alive.current) return
        if (!res.ok || !data.ok) { finish(reasonOf(res, data)); return }
        if (data.status === 'running') {
          const last = (data.progress as string[]).slice(-1)[0]
          begin(last ? `Drafting: ${last}` : 'Drafting your playbook in the browser…', stop)
          return
        }
        if (data.status === 'done' && data.result) { finish(''); onDrafted(data.result as DescribedDraft); return }
        finish(data.error || (data.status === 'stopped' ? 'Stopped.' : 'The draft could not be made.'))
      } catch { /* a missed poll is fine: the next one asks again */ }
    }, pollMs)
  }

  const finish = (message: string) => {
    stopPolling()
    setRunning(null)
    end()
    setError(message)
  }

  const start = async () => {
    setError('')
    try {
      const res = await fetch('/playbooks/describe', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ description: text, use_page: usePage, allow_unnamed: allowUnnamed }),
      })
      const data = await readJson(res)
      if (!res.ok || !data.ok) { setError(reasonOf(res, data)); return }
      setRunning(data.token)
      begin('Drafting your playbook in the browser…', stop)
      poll(data.token)
    } catch (e) {
      setError(`Couldn't start the draft (${errText(e)}).`)
    }
  }

  return (
    <div className="mx-5 mb-3 rounded-xl border border-purple-200 bg-purple-50/50 p-3">
      <p className="flex items-center gap-1.5 text-xs font-medium text-purple-900"><Sparkles size={13} /> Describe a task and get a playbook</p>
      <textarea aria-label="Describe the task" rows={2} maxLength={MAX} value={text} disabled={running !== null} onChange={(e) => setText(e.target.value)}
        placeholder="For example: open https://news.ycombinator.com and read the title and link of every story"
        className="mt-1.5 w-full rounded-lg border border-gray-300 bg-white px-2 py-1.5 text-xs focus:border-brand focus:outline-none disabled:opacity-60" />
      <div className="mt-1.5 flex flex-wrap items-center gap-x-4 gap-y-1">
        <label className="flex items-center gap-1.5 text-[11px] text-gray-700">
          <input type="checkbox" checked={usePage} disabled={running !== null} onChange={(e) => setUsePage(e.target.checked)} />
          Let it look at the page in the browser first
        </label>
        <label className="flex items-center gap-1.5 text-[11px] text-gray-700">
          <input type="checkbox" checked={allowUnnamed} disabled={running !== null} onChange={(e) => setAllowUnnamed(e.target.checked)} />
          Let it open sites I did not mention
        </label>
        <button onClick={start} disabled={running !== null || !text.trim()}
          className="ml-auto rounded-md bg-brand px-3 py-1 text-xs font-medium text-white disabled:opacity-40">{running ? 'Drafting…' : 'Draft it'}</button>
      </div>
      <p className="mt-1 text-[11px] text-gray-500">
        Your AI model plans the steps once, then they are tried in the browser on the left (never anything that sends or posts). You review the draft before
        anything is saved, and every replay after that is free.
      </p>
      {error && <p role="alert" className="mt-2 rounded-md bg-red-50 px-2 py-1 text-[11px] text-red-700">{error}</p>}
    </div>
  )
}
