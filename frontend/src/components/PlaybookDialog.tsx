import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { X, Trash2, BookMarked, History as HistoryIcon, Pencil, AlertTriangle } from 'lucide-react'
import type { DamagedPlaybook, DescribedDraft, PlaybookSummary, ScheduleView } from '@/types'
import DescribeBox from './playbook/DescribeBox'
import Editor from './playbook/Editor'
import History from './playbook/History'
import RunForm from './playbook/RunForm'
import ScheduleForm from './playbook/ScheduleForm'
import Schedules from './playbook/Schedules'
import { errText, readJson, reasonOf } from './playbook/shared'
import { PickContext } from './playbook/pick'

interface Props {
  onClose: () => void
  /** Tells the user something happened (saved, deleted) after the dialog is gone. */
  notify: (text: string) => void
  /** A finished run exists in this chat, so it can be saved. */
  canSave: boolean
  /** The agent (or another playbook) is running or paused, so a new run can't start now. */
  busy: boolean
}

type View =
  | { kind: 'library' }
  | { kind: 'save' }
  | { kind: 'described'; draft: DescribedDraft }
  | { kind: 'edit'; id: string }
  | { kind: 'run'; id: string }
  | { kind: 'history'; id: string; name: string }
  | { kind: 'schedules' }
  | { kind: 'schedule'; existing: ScheduleView | null }

const TITLE: Record<View['kind'], string> = {
  library: 'Playbooks', save: 'Save this run as a playbook', described: 'Review the drafted playbook', edit: 'Edit playbook', run: 'Run a playbook', history: 'Run history',
  schedules: 'Schedules', schedule: 'Schedule',
}

/**
 * One dialog, several screens: the library of saved playbooks, saving the last finished run, editing a
 * saved playbook, running one, and its history. Nothing is stored until the user presses Save.
 */
export default function PlaybookDialog({ onClose, notify, canSave, busy }: Props) {
  const [view, setView] = useState<View>({ kind: 'library' })
  const dialogRef = useRef<HTMLDivElement>(null)
  const [picking, setPicking] = useState<{ label: string; cancel: () => void } | null>(null)
  const pickControl = useMemo(() => ({ begin: (label: string, cancel: () => void) => setPicking({ label, cancel }), end: () => setPicking(null) }), [])

  useEffect(() => {
    const before = document.activeElement as HTMLElement | null
    dialogRef.current?.focus()
    return () => before?.focus?.()
  }, [])

  const onKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === 'Escape') { e.stopPropagation(); onClose(); return }
    if (e.key !== 'Tab') return
    // Keep Tab inside the dialog: the page behind it is not reachable while it is open.
    const focusable = Array.from(
      dialogRef.current?.querySelectorAll<HTMLElement>('button, input, a[href], [tabindex]:not([tabindex="-1"])') ?? [],
    ).filter((el) => !(el as HTMLButtonElement).disabled)
    if (focusable.length === 0) { e.preventDefault(); return }
    const first = focusable[0], last = focusable[focusable.length - 1]
    const active = document.activeElement
    if (e.shiftKey && (active === first || active === dialogRef.current)) { e.preventDefault(); last.focus() }
    else if (!e.shiftKey && active === last) { e.preventDefault(); first.focus() }
  }

  const library = () => setView({ kind: 'library' })

  return (
    <PickContext.Provider value={pickControl}>
    {picking && (
      <div role="status" className="fixed bottom-4 left-1/2 z-50 flex -translate-x-1/2 items-center gap-3 rounded-full bg-gray-900 px-4 py-2 text-xs text-white shadow-lg">
        <span>{picking.label}</span>
        <button onClick={picking.cancel} className="rounded-full bg-white/15 px-2.5 py-0.5 font-medium hover:bg-white/25">Cancel</button>
      </div>
    )}
    <div
      className={`fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4 ${picking ? 'hidden' : ''}`}
      onMouseDown={(e) => { if (e.target === e.currentTarget) onClose() }}
    >
      <div
        ref={dialogRef}
        tabIndex={-1}
        role="dialog"
        aria-modal="true"
        aria-label={TITLE[view.kind]}
        onKeyDown={onKeyDown}
        className="flex max-h-[90vh] w-full max-w-2xl flex-col rounded-2xl bg-white shadow-xl outline-none"
      >
        <div className="flex items-center justify-between border-b border-gray-100 px-5 py-3">
          <h2 className="flex items-center gap-2 text-sm font-semibold text-gray-900">
            <BookMarked size={16} className="text-brand" />
            {TITLE[view.kind]}
          </h2>
          <button aria-label="Close" onClick={onClose} className="rounded-lg p-1 text-gray-400 hover:bg-gray-100 hover:text-gray-700">
            <X size={16} />
          </button>
        </div>
        {(view.kind === 'library' || view.kind === 'schedules') && (
          <div role="tablist" aria-label="Playbooks or schedules" className="flex gap-1 border-b border-gray-100 px-5">
            {([['library', 'Playbooks'], ['schedules', 'Schedules']] as const).map(([k, label]) => (
              <button key={k} role="tab" aria-selected={view.kind === k} onClick={() => setView({ kind: k })}
                className={`border-b-2 px-3 py-2 text-xs font-medium ${view.kind === k ? 'border-brand text-brand' : 'border-transparent text-gray-500 hover:text-gray-700'}`}>
                {label}
              </button>
            ))}
          </div>
        )}
        {view.kind === 'schedules' && (
          <Schedules notify={notify} onNew={() => setView({ kind: 'schedule', existing: null })}
            onEdit={(s) => setView({ kind: 'schedule', existing: s })} />
        )}
        {view.kind === 'schedule' && (
          <ScheduleForm existing={view.existing} onCancel={() => setView({ kind: 'schedules' })}
            onSaved={(name) => { notify(`Saved the schedule "${name}".`); setView({ kind: 'schedules' }) }} />
        )}
        {view.kind === 'library' && (
          <Library
            canSave={canSave}
            busy={busy}
            notify={notify}
            onSave={() => setView({ kind: 'save' })}
            onDrafted={(draft) => setView({ kind: 'described', draft })}
            onRun={(id) => setView({ kind: 'run', id })}
            onEdit={(id) => setView({ kind: 'edit', id })}
            onHistory={(id, name) => setView({ kind: 'history', id, name })}
          />
        )}
        {view.kind === 'save' && (
          <Editor mode="draft" onCancel={library} onSaved={(name) => { notify(`Saved the playbook "${name}".`); onClose() }} />
        )}
        {view.kind === 'described' && (
          <Editor mode="draft" initial={view.draft} onCancel={library} onSaved={(name) => { notify(`Saved the playbook "${name}".`); onClose() }} />
        )}
        {view.kind === 'edit' && (
          <Editor mode="edit" id={view.id} onCancel={library} onSaved={(name, affected) => {
            notify(affected > 0
              ? `Saved "${name}". ${affected} schedule${affected === 1 ? '' : 's'} will pause until you approve it again.`
              : `Saved "${name}".`)
            library()
          }} />
        )}
        {view.kind === 'run' && (
          <RunForm
            id={view.id}
            busy={busy}
            onCancel={library}
            onStarted={(name) => { notify(`Running "${name}". Follow it in the chat.`); onClose() }}
          />
        )}
        {view.kind === 'history' && <History id={view.id} name={view.name} onBack={library} onEdit={() => setView({ kind: 'edit', id: view.id })} />}
      </div>
    </div>
    </PickContext.Provider>
  )
}

// ─── Templates ────────────────────────────────────────────────────────────────

function Templates({ onCreated, onError }: { onCreated: (id: string, name: string) => void; onError: (t: string) => void }) {
  const [templates, setTemplates] = useState<{ id: string; name: string; description: string }[]>([])
  const [busy, setBusy] = useState(false)

  useEffect(() => {
    fetch('/playbooks/templates').then((r) => (r.ok ? r.json() : null)).then((d) => d?.ok && setTemplates(d.templates)).catch(() => {})
  }, [])

  const create = async (id: string) => {
    setBusy(true)
    try {
      const res = await fetch('/playbooks/from-template', {
        method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ id }),
      })
      const data = await readJson(res)
      if (!res.ok || !data.ok) { onError(reasonOf(res, data)); return }
      onCreated(data.playbook.id, data.playbook.name)
    } catch (e) {
      onError(errText(e))
    } finally { setBusy(false) }
  }

  if (templates.length === 0) return null
  return (
    <div className="mt-4 rounded-xl bg-gray-50 p-3">
      <p className="text-xs font-medium text-gray-700">Start from a template</p>
      <ul className="mt-1.5 space-y-1.5">
        {templates.map((t) => (
          <li key={t.id} className="flex items-center justify-between gap-3">
            <div className="min-w-0 text-xs"><p className="text-gray-800">{t.name}</p><p className="text-gray-500">{t.description}</p></div>
            <button disabled={busy} onClick={() => create(t.id)} className="flex-shrink-0 rounded-md border border-gray-300 bg-white px-2 py-1 text-xs font-medium text-gray-700 hover:bg-gray-50 disabled:opacity-50">Use</button>
          </li>
        ))}
      </ul>
    </div>
  )
}

// ─── Library ──────────────────────────────────────────────────────────────────

function Library({ canSave, busy, onSave, onDrafted, onRun, onEdit, onHistory, notify }: {
  canSave: boolean
  busy: boolean
  onSave: () => void
  onDrafted: (draft: DescribedDraft) => void
  onRun: (id: string) => void
  onEdit: (id: string) => void
  onHistory: (id: string, name: string) => void
  notify: (t: string) => void
}) {
  const [rows, setRows] = useState<PlaybookSummary[] | null>(null)
  const [damaged, setDamaged] = useState<DamagedPlaybook[]>([])
  const [error, setError] = useState('')
  const [confirming, setConfirming] = useState<string | null>(null)

  const load = useCallback(async () => {
    try {
      const res = await fetch('/playbooks')
      const data = await readJson(res)
      if (!res.ok || !data.ok) throw new Error(reasonOf(res, data))
      setRows(data.playbooks)
      setDamaged(data.damaged ?? [])
      setError('')
    } catch (e) {
      setError(`Couldn't load your playbooks (${errText(e)}).`)
    }
  }, [])
  useEffect(() => { load() }, [load])

  const remove = async (id: string, name: string) => {
    try {
      const res = await fetch(`/playbooks/${id}`, { method: 'DELETE' })
      const data = await readJson(res)
      if (!res.ok) throw new Error(reasonOf(res, data))
      const gone = Number(data.schedules_deleted) || 0
      notify(gone > 0 ? `Deleted "${name}" and ${gone} schedule${gone === 1 ? '' : 's'} that used it.` : `Deleted "${name}".`)
      setConfirming(null)
      load()
    } catch (e) {
      setError(`Couldn't delete it (${errText(e)}).`)
    }
  }

  const deleteControls = (id: string, name: string) =>
    confirming === id ? (
      <>
        <button onClick={() => remove(id, name)} className="rounded-md bg-red-600 px-2 py-1 text-xs font-medium text-white">Delete</button>
        <button onClick={() => setConfirming(null)} className="rounded-md border border-gray-300 px-2 py-1 text-xs text-gray-700">Keep</button>
      </>
    ) : (
      <button aria-label={`Delete ${name}`} onClick={() => setConfirming(id)} className="rounded-md p-1 text-gray-400 hover:bg-gray-100 hover:text-red-600">
        <Trash2 size={14} />
      </button>
    )

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <div className="flex items-center justify-between gap-3 px-5 py-3">
        <p className="text-xs text-gray-500">Record a task once, then replay it without an AI choosing every step.</p>
        <button
          disabled={!canSave}
          onClick={onSave}
          title={canSave ? undefined : 'Finish a task in this chat first'}
          className="flex-shrink-0 rounded-lg bg-brand px-3 py-1.5 text-xs font-medium text-white disabled:cursor-not-allowed disabled:opacity-40"
        >
          Save last run as playbook
        </button>
      </div>
      <DescribeBox onDrafted={onDrafted} />
      {error && <p role="alert" className="mx-5 mb-2 rounded-lg bg-red-50 px-3 py-2 text-xs text-red-700">{error}</p>}
      <div className="min-h-0 flex-1 overflow-y-auto px-5 pb-5">
        {rows === null && !error && <p className="py-8 text-center text-xs text-gray-400">Loading…</p>}
        {rows !== null && rows.length === 0 && damaged.length === 0 && (
          <p className="py-8 text-center text-xs text-gray-400">No playbooks yet. Finish a task, then save it here.</p>
        )}
        <ul className="space-y-2">
          {rows?.map((p) => (
            <li key={p.id} className="rounded-xl border border-gray-200 p-3">
              <div className="flex items-start justify-between gap-3">
                <div className="min-w-0">
                  <p className="truncate text-sm font-medium text-gray-900">{p.name}</p>
                  <p className="mt-0.5 text-xs text-gray-500">
                    {p.steps} step{p.steps === 1 ? '' : 's'}
                    {p.ai_steps > 0 ? ` · ${p.ai_steps} ${p.ai_steps === 1 ? 'needs' : 'need'} AI` : ' · no AI needed'}
                    {p.variables.length > 0 ? ` · inputs: ${p.variables.join(', ')}` : ''}
                  </p>
                  {p.domains.length > 0 && <p className="mt-0.5 truncate text-xs text-gray-400">Sites: {p.domains.join(', ')}</p>}
                </div>
                <span className={`flex-shrink-0 rounded-full px-2 py-0.5 text-[11px] font-medium ${p.approved ? 'bg-green-50 text-green-700' : 'bg-amber-50 text-amber-700'}`}>
                  {p.approved ? 'Approved' : 'Not approved'}
                </span>
              </div>
              <div className="mt-2 flex items-center justify-end gap-1.5">
                {confirming !== p.id && (
                  <>
                    <button aria-label={`Run history of ${p.name}`} title="Run history" onClick={() => onHistory(p.id, p.name)} className="rounded-md p-1 text-gray-400 hover:bg-gray-100 hover:text-gray-700">
                      <HistoryIcon size={14} />
                    </button>
                    <button aria-label={`Edit ${p.name}`} title="Edit" onClick={() => onEdit(p.id)} className="rounded-md p-1 text-gray-400 hover:bg-gray-100 hover:text-gray-700">
                      <Pencil size={14} />
                    </button>
                  </>
                )}
                {deleteControls(p.id, p.name)}
                {confirming !== p.id && (
                  <button
                    disabled={busy}
                    onClick={() => onRun(p.id)}
                    title={busy ? 'Wait for the current task to finish' : undefined}
                    className="rounded-md bg-brand px-2.5 py-1 text-xs font-medium text-white disabled:cursor-not-allowed disabled:opacity-40"
                  >
                    Run
                  </button>
                )}
              </div>
            </li>
          ))}
        </ul>

        <Templates onCreated={(id, name) => { notify(`Created "${name}". Read it, then approve it.`); onEdit(id) }} onError={setError} />

        {damaged.length > 0 && (
          <div className="mt-4">
            <p className="flex items-center gap-1 text-xs font-medium text-amber-800"><AlertTriangle size={13} /> Can't be read</p>
            <p className="text-[11px] text-gray-500">These files are damaged or break the rules, so they can't run. You can delete them.</p>
            <ul className="mt-1.5 space-y-1.5">
              {damaged.map((d) => (
                <li key={d.id} className="flex items-center justify-between gap-3 rounded-lg border border-amber-200 bg-amber-50 px-3 py-2">
                  <div className="min-w-0 text-xs">
                    <p className="font-mono text-gray-700">{d.id}</p>
                    <p className="truncate text-amber-800">{d.problem}</p>
                  </div>
                  <div className="flex flex-shrink-0 items-center gap-1.5">{deleteControls(d.id, d.id)}</div>
                </li>
              ))}
            </ul>
          </div>
        )}
      </div>
    </div>
  )
}
