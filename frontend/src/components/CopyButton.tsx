import { useState } from 'react'
import { Check, Copy } from 'lucide-react'

async function copy(text: string): Promise<boolean> {
  try {
    await navigator.clipboard.writeText(text)
    return true
  } catch {
    return false
  }
}

export default function CopyButton({ text, label = 'Copy', className = '' }: { text: string; label?: string; className?: string }) {
  const [done, setDone] = useState(false)
  return (
    <button
      type="button"
      aria-label={label}
      onClick={async () => {
        if (await copy(text)) {
          setDone(true)
          setTimeout(() => setDone(false), 1500)
        }
      }}
      className={`flex items-center gap-1 text-[11px] text-gray-400 hover:text-gray-600 ${className}`}
    >
      {done ? <Check size={12} /> : <Copy size={12} />}
      {done ? 'Copied' : label}
    </button>
  )
}
