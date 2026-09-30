import { type ClassValue, clsx } from 'clsx'
import { twMerge } from 'tailwind-merge'

export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs))
}

export function formatDuration(seconds: number): string {
  if (seconds < 60) return `${seconds}s`
  const m = Math.floor(seconds / 60)
  const s = seconds % 60
  return `${m}m ${s}s`
}

export function formatDate(dateStr: string): string {
  // Python strftime "%Y-%m-%d %H:%M" uses space — not ISO-standard.
  // Replace space with 'T' so all browsers parse it correctly.
  const date = new Date(dateStr.replace(' ', 'T'))
  const now = new Date()
  const diff = now.getTime() - date.getTime()
  const days = Math.floor(diff / (1000 * 60 * 60 * 24))

  if (days === 0) {
    return date.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })
  } else if (days === 1) {
    return 'Yesterday'
  } else if (days < 7) {
    return date.toLocaleDateString([], { weekday: 'short' })
  } else {
    return date.toLocaleDateString([], { month: 'short', day: 'numeric' })
  }
}

export function generateId(): string {
  return Math.random().toString(36).slice(2, 11)
}

export function scoreColor(score: number): string {
  if (score >= 8) return 'text-green-700 bg-green-100'
  if (score >= 6) return 'text-amber-700 bg-amber-100'
  return 'text-red-700 bg-red-100'
}
