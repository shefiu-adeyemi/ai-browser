import { useEffect } from 'react'
import { X } from 'lucide-react'

export interface ToastMessage {
  id: number
  text: string
}

/** A short notice for something that failed without a place of its own on screen. */
export default function Toast({ toast, onClose }: { toast: ToastMessage | null; onClose: () => void }) {
  useEffect(() => {
    if (!toast) return
    const t = setTimeout(onClose, 6000)
    return () => clearTimeout(t)
  }, [toast, onClose])

  if (!toast) return null
  return (
    <div className="pointer-events-none fixed inset-x-0 bottom-16 z-[60] flex justify-center px-4">
      <div
        role="alert"
        className="pointer-events-auto flex max-w-md items-start gap-3 rounded-xl bg-gray-900 px-4 py-3 text-sm text-white shadow-xl"
      >
        <span className="leading-snug">{toast.text}</span>
        <button aria-label="Dismiss" onClick={onClose} className="mt-0.5 text-gray-400 hover:text-white">
          <X size={14} />
        </button>
      </div>
    </div>
  )
}
