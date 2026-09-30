import { useEffect, useState } from 'react'
import type { ApprovalRequest } from '@/types'

interface Props {
  request: ApprovalRequest
  /** Resolves true once the server has the answer; false if it couldn't be delivered. */
  onAnswer: (id: string, allow: boolean) => Promise<boolean>
}

/**
 * Blocks the agent until the user answers. "approve" = allow/deny one action;
 * "handoff" = the user does something in the browser pane (e.g. a CAPTCHA) and continues.
 * No answer in `timeout` seconds counts as a denial on the server, so we count down here.
 */
export default function ApprovalPrompt({ request, onAnswer }: Props) {
  const [left, setLeft] = useState(request.timeout)
  const [sending, setSending] = useState(false)
  const [failed, setFailed] = useState(false)

  useEffect(() => {
    setLeft(request.timeout)
    setSending(false)
    setFailed(false)
    const t = setInterval(() => setLeft((s) => Math.max(0, s - 1)), 1000)
    return () => clearInterval(t)
  }, [request.id, request.timeout])

  const handoff = request.kind === 'handoff'
  const answer = async (allow: boolean) => {
    setSending(true)
    setFailed(false)
    const delivered = await onAnswer(request.id, allow)
    if (!delivered) {
      setSending(false)
      setFailed(true)   // keep the prompt up: the agent is still waiting for this answer
    }
  }
  return (
    <div
      role="alertdialog"
      aria-labelledby="approval-title"
      className="fixed left-1/2 top-4 z-50 w-[520px] max-w-[92vw] -translate-x-1/2 rounded-xl border border-amber-300 bg-amber-50 p-4 shadow-xl"
    >
      <p className="text-[11px] font-semibold uppercase tracking-wide text-amber-700">
        {handoff ? 'Agent needs your help' : 'Approval needed'} · {left}s
      </p>
      <p id="approval-title" className="mt-1 text-sm font-semibold text-gray-900 break-words">
        {request.title}
      </p>
      {request.detail && (
        <p className="mt-1 max-h-28 overflow-y-auto whitespace-pre-wrap break-words text-xs text-gray-700">
          {request.detail}
        </p>
      )}
      {request.url && <p className="mt-1 truncate text-[11px] text-gray-500">{request.url}</p>}
      {failed && (
        <p className="mt-2 text-xs font-medium text-red-600">
          Couldn't reach the agent — check the server is running, then try again.
        </p>
      )}
      <div className="mt-3 flex justify-end gap-2">
        <button
          disabled={sending}
          onClick={() => answer(false)}
          className="rounded-md disabled:opacity-50 border border-gray-300 bg-white px-3 py-1.5 text-xs font-medium text-gray-700 hover:bg-gray-50"
        >
          {handoff ? 'Skip' : 'Deny'}
        </button>
        <button
          autoFocus
          disabled={sending}
          onClick={() => answer(true)}
          className="rounded-md disabled:opacity-50 bg-brand px-3 py-1.5 text-xs font-semibold text-white hover:opacity-90"
        >
          {handoff ? "I'm done — continue" : 'Approve'}
        </button>
      </div>
    </div>
  )
}
