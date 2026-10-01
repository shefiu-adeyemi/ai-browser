import { useCallback, useEffect, useRef, useState } from 'react'
import { AlertTriangle, Bell, CheckCircle2, Info, X } from 'lucide-react'
import type { NotificationItem } from '@/types'
import { errText, formatWhen, readJson, reasonOf } from './playbook/shared'

interface Props {
  onClose: () => void
  /** The server's unread count changed (marked read, cleared). */
  onUnread: (n: number) => void
}

const ICON = {
  info: <Info size={15} className="mt-0.5 flex-shrink-0 text-blue-500" aria-label="information" />,
  warning: <AlertTriangle size={15} className="mt-0.5 flex-shrink-0 text-amber-600" aria-label="warning" />,
  error: <AlertTriangle size={15} className="mt-0.5 flex-shrink-0 text-red-600" aria-label="needs you" />,
}

/** What the scheduler wants you to know: what needs you, what failed, what finished. */
export default function NotificationsDialog({ onClose, onUnread }: Props) {
  const [items, setItems] = useState<NotificationItem[] | null>(null)
  const [desktop, setDesktop] = useState(true)
  const [error, setError] = useState('')
  const ref = useRef<HTMLDivElement>(null)

  const load = useCallback(async () => {
    try {
      const res = await fetch('/notifications?limit=100')
      const data = await readJson(res)
      if (!res.ok || !data.ok) throw new Error(reasonOf(res, data))
      setItems(data.items)
      setDesktop(!!data.desktop)
      onUnread(data.unread)
      setError('')
    } catch (e) {
      setError(`Couldn't load the inbox (${errText(e)}).`)
    }
  }, [onUnread])

  useEffect(() => { load() }, [load])
  useEffect(() => {
    const before = document.activeElement as HTMLElement | null
    ref.current?.focus()
    return () => before?.focus?.()
  }, [])

  const send = async (url: string, init: RequestInit) => {
    try {
      const res = await fetch(url, init)
      const data = await readJson(res)
      if (!res.ok || !data.ok) { setError(reasonOf(res, data)); return }
      if (typeof data.unread === 'number') onUnread(data.unread)
      await load()
    } catch (e) {
      setError(errText(e))
    }
  }
  const json = (body: unknown): RequestInit => ({ method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) })
  const unread = items?.filter((i) => !i.read).length ?? 0

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4" onMouseDown={(e) => { if (e.target === e.currentTarget) onClose() }}>
      <div
        ref={ref}
        tabIndex={-1}
        role="dialog"
        aria-modal="true"
        aria-label="Inbox"
        onKeyDown={(e) => { if (e.key === 'Escape') { e.stopPropagation(); onClose() } }}
        className="flex max-h-[90vh] w-full max-w-xl flex-col rounded-2xl bg-white shadow-xl outline-none"
      >
        <div className="flex items-center justify-between border-b border-gray-100 px-5 py-3">
          <h2 className="flex items-center gap-2 text-sm font-semibold text-gray-900"><Bell size={16} className="text-brand" /> Inbox</h2>
          <button aria-label="Close" onClick={onClose} className="rounded-lg p-1 text-gray-400 hover:bg-gray-100 hover:text-gray-700"><X size={16} /></button>
        </div>
        <div className="flex items-center justify-between gap-3 px-5 py-2">
          <label className="flex items-center gap-2 text-xs text-gray-600">
            <input type="checkbox" checked={desktop} onChange={(e) => send('/notifications/desktop', json({ enabled: e.target.checked }))} />
            Also show a desktop notification when something needs me
          </label>
          <div className="flex gap-2">
            <button disabled={unread === 0} onClick={() => send('/notifications/read', json({}))} className="text-xs text-brand disabled:text-gray-300">Mark all read</button>
            <button disabled={!items?.length} onClick={() => send('/notifications', { method: 'DELETE' })} className="text-xs text-gray-500 hover:text-red-600 disabled:text-gray-300">Clear</button>
          </div>
        </div>
        {error && <p role="alert" className="mx-5 mb-2 rounded-lg bg-red-50 px-3 py-2 text-xs text-red-700">{error}</p>}
        <div className="min-h-0 flex-1 overflow-y-auto px-5 pb-5">
          {items === null && !error && <p className="py-8 text-center text-xs text-gray-400">Loading…</p>}
          {items !== null && items.length === 0 && (
            <p className="flex flex-col items-center gap-2 py-8 text-center text-xs text-gray-400"><CheckCircle2 size={20} /> Nothing needs you.</p>
          )}
          <ul className="space-y-1.5">
            {items?.map((n) => (
              <li key={n.id}>
                <button onClick={() => !n.read && send('/notifications/read', json({ ids: [n.id] }))}
                  className={`flex w-full gap-2 rounded-lg border px-3 py-2 text-left ${n.read ? 'border-gray-100 bg-white' : 'border-brand/30 bg-brand/5'}`}>
                  {ICON[n.level] ?? ICON.info}
                  <span className="min-w-0 flex-1">
                    <span className={`block text-xs ${n.read ? 'text-gray-700' : 'font-medium text-gray-900'}`}>{n.title}</span>
                    {n.text && <span className="mt-0.5 block text-xs text-gray-600">{n.text}</span>}
                    <span className="mt-0.5 block text-[11px] text-gray-400">{formatWhen(new Date(n.ts).getTime() / 1000)}</span>
                  </span>
                </button>
              </li>
            ))}
          </ul>
        </div>
      </div>
    </div>
  )
}
