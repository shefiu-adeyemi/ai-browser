import { useEffect, useRef, useState } from 'react'
import type { ApprovalRequest } from '@/types'

interface Props {
  request: ApprovalRequest
  /** Resolves true once the server has the answer; false if it couldn't be delivered. */
  onAnswer: (id: string, allow: boolean, text?: string, remember?: boolean) => Promise<boolean>
  /** The agent step that asked, so the prompt can be matched to the log. */
  step?: number
}

/**
 * Blocks the agent until the user answers. No answer in `timeout` seconds counts as a denial on the
 * server, so we count down here.
 *
 *  - "approve" (allow or deny ONE action) is a card in the chat panel, above the task box. The page
 *    stays visible and undimmed, because the user has to look at it to decide.
 *  - "handoff" (the user does something in the browser, e.g. a CAPTCHA) is a banner on top of the
 *    browser view, where the user's attention and hands already are.
 */
export default function ApprovalPrompt({ request, onAnswer, step }: Props) {
  const [left, setLeft] = useState(request.timeout)
  const [sending, setSending] = useState(false)
  const [failed, setFailed] = useState(false)
  const [text, setText] = useState('')
  const [remember, setRemember] = useState(false)
  const cardRef = useRef<HTMLDivElement>(null)
  const denyRef = useRef<HTMLButtonElement>(null)

  useEffect(() => {
    setLeft(request.timeout)
    setSending(false)
    setFailed(false)
    setText('')
    setRemember(false)
    const t = setInterval(() => setLeft((s) => Math.max(0, s - 1)), 1000)
    return () => clearInterval(t)
  }, [request.id, request.timeout])

  const handoff = request.kind === 'handoff'
  const typed = request.kind === 'input'

  // An approval takes the focus, on the SAFE button: a stray Enter or Space must never send, post or
  // buy. Focus goes back to where it was afterwards. A hand-off does not steal focus: the user is
  // about to click in the browser view.
  useEffect(() => {
    if (handoff || typed) return
    const before = document.activeElement as HTMLElement | null
    denyRef.current?.focus()
    cardRef.current?.scrollIntoView({ block: 'nearest' })
    return () => before?.focus?.()
  }, [request.id, handoff, typed])

  const answer = async (allow: boolean) => {
    setSending(true)
    setFailed(false)
    const delivered = typed ? await onAnswer(request.id, allow, text, remember) : await onAnswer(request.id, allow)
    if (!delivered) {
      setSending(false)
      setFailed(true)   // keep the prompt up: the agent is still waiting for this answer
    }
  }

  const onKeyDown = (e: React.KeyboardEvent) => {
    if (!handoff && e.key === 'Escape' && !sending) {
      e.preventDefault()
      answer(false)
    }
  }

  const label = `${handoff ? 'Agent needs your help' : typed ? 'Your answer needed' : 'Approval needed'}${step ? ` \u00b7 step ${step}` : ''} \u00b7 ${left}s`

  const buttons = (
    <div className={handoff ? 'flex flex-shrink-0 items-center gap-2' : 'mt-3 flex justify-end gap-2'}>
      <button
        ref={denyRef}
        disabled={sending}
        onClick={() => answer(false)}
        className="rounded-md disabled:opacity-50 border border-gray-300 bg-white px-3 py-1.5 text-xs font-medium text-gray-700 hover:bg-gray-50"
      >
        {handoff ? 'Skip' : typed ? 'Cancel' : 'Deny'}
      </button>
      <button
        disabled={sending || (typed && !text.trim())}
        onClick={() => answer(true)}
        className="rounded-md disabled:opacity-50 bg-brand px-3 py-1.5 text-xs font-semibold text-white hover:opacity-90"
      >
        {handoff ? "I'm done \u2014 continue" : typed ? 'Send' : 'Approve'}
      </button>
    </div>
  )

  const problem = failed && (
    <p className="mt-2 text-xs font-medium text-red-600">
      {"Couldn't reach the agent \u2014 check the server is running, then try again."}
    </p>
  )

  if (handoff) {
    return (
      <div
        role="status"
        aria-labelledby="approval-title"
        className="flex flex-shrink-0 items-center gap-4 border-b border-amber-300 bg-amber-50 px-4 py-2.5"
      >
        <div className="min-w-0 flex-1">
          <p className="text-[11px] font-semibold uppercase tracking-wide text-amber-700">{label}</p>
          <p id="approval-title" className="truncate text-sm font-semibold text-gray-900" title={request.title}>
            {request.title}
          </p>
          {request.detail && <p className="truncate text-xs text-gray-600" title={request.detail}>{request.detail}</p>}
          {problem}
        </div>
        {buttons}
      </div>
    )
  }

  return (
    <div
      ref={cardRef}
      onKeyDown={onKeyDown}
      role="alertdialog"
      aria-labelledby="approval-title"
      className="mx-4 mb-2 flex-shrink-0 rounded-xl border border-amber-300 bg-amber-50 p-3.5 shadow-sm"
    >
      <p className="text-[11px] font-semibold uppercase tracking-wide text-amber-700">{label}</p>
      <p id="approval-title" className="mt-1 text-sm font-semibold text-gray-900 break-words">
        {request.title}
      </p>
      {request.detail && (
        <p className="mt-1 max-h-28 overflow-y-auto whitespace-pre-wrap break-words text-xs text-gray-700">
          {request.detail}
        </p>
      )}
      {request.url && <p className="mt-1 truncate text-[11px] text-gray-500">{request.url}</p>}
      {typed && (
        <input aria-label="Your answer" value={text} maxLength={2000} autoFocus onChange={(e) => setText(e.target.value)}
          onKeyDown={(e) => { if (e.key === 'Enter' && text.trim() && !sending) { e.preventDefault(); answer(true) } }}
          className="mt-2 w-full rounded-md border border-gray-300 px-2 py-1.5 text-xs focus:border-brand focus:outline-none" />
      )}
      {typed && (
        <label className="mt-1.5 flex items-center gap-1.5 text-[11px] text-gray-600">
          <input type="checkbox" checked={remember} onChange={(e) => setRemember(e.target.checked)} />
          Remember this for next runs (saved on this computer)
        </label>
      )}
      {problem}
      {buttons}
    </div>
  )
}
