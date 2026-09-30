import { useEffect, useRef, useCallback } from 'react'
import { Status, InputMessage } from '@/types'
import { cn } from '@/lib/utils'

interface BrowserPaneProps {
  screenshot: string | null
  viewport?: { w: number; h: number }
  status: Status
  score?: number
  duration?: number
  maxSteps?: number
  stepCount?: number
  currentAction?: string
  currentUrl?: string
  wsRef: React.RefObject<WebSocket | null>
}

const STATUS_CONFIG: Record<Status, { label: string; dot: string; color: string }> = {
  idle:    { label: 'Idle',    dot: 'bg-gray-400',                color: 'text-gray-500'  },
  running: { label: 'Running', dot: 'bg-brand animate-pulse',     color: 'text-brand'     },
  paused:  { label: 'Paused',  dot: 'bg-amber-500',              color: 'text-amber-600' },
  done:    { label: 'Done',    dot: 'bg-blue-500',               color: 'text-blue-600'  },
  error:   { label: 'Error',   dot: 'bg-red-500',                color: 'text-red-600'   },
}

function IdleIllustration() {
  return (
    <div className="flex flex-col items-center gap-6 select-none">
      <div className="relative">
        <div
          className="absolute inset-x-8 bottom-0 h-12 blur-2xl rounded-full"
          style={{ background: 'rgba(47,107,79,0.12)' }}
        />
        <div
          className="relative rounded-xl overflow-hidden"
          style={{
            width: 300,
            boxShadow: '0 4px 6px -1px rgba(0,0,0,0.07), 0 20px 40px -8px rgba(0,0,0,0.12)',
            border: '1px solid #E2E5E8',
          }}
        >
          <div style={{ background: '#F5F6F7', borderBottom: '1px solid #E2E5E8', padding: '10px 14px', display: 'flex', alignItems: 'center', gap: 10 }}>
            <div style={{ display: 'flex', gap: 6 }}>
              <div style={{ width: 10, height: 10, borderRadius: '50%', background: '#FF5F57' }} />
              <div style={{ width: 10, height: 10, borderRadius: '50%', background: '#FFBD2E' }} />
              <div style={{ width: 10, height: 10, borderRadius: '50%', background: '#28C840' }} />
            </div>
            <div style={{ flex: 1, background: '#EBEBEC', borderRadius: 6, padding: '4px 10px', display: 'flex', alignItems: 'center', gap: 5 }}>
              <svg width="9" height="9" viewBox="0 0 12 12" fill="none">
                <circle cx="6" cy="6" r="5" stroke="#9CA3AF" strokeWidth="1.5"/>
                <path d="M6 1C6 1 4 4 4 6s2 5 2 5M6 1c0 0 2 3 2 5s-2 5-2 5M1 6h10" stroke="#9CA3AF" strokeWidth="1.2"/>
              </svg>
              <span style={{ fontSize: 10, color: '#9CA3AF', fontFamily: 'system-ui' }}>localhost:8000</span>
            </div>
          </div>
          <div style={{ background: '#FFFFFF', padding: '14px 16px', display: 'flex', flexDirection: 'column', gap: 10 }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: 8, paddingBottom: 10, borderBottom: '1px solid #F0F1F2' }}>
              <div style={{ width: 22, height: 22, borderRadius: 4, background: '#2F6B4F' }} />
              <div style={{ height: 8, width: 80, borderRadius: 4, background: '#EDEEF0' }} />
              <div style={{ marginLeft: 'auto', display: 'flex', gap: 8 }}>
                <div style={{ height: 8, width: 36, borderRadius: 4, background: '#EDEEF0' }} />
                <div style={{ height: 8, width: 36, borderRadius: 4, background: '#EDEEF0' }} />
                <div style={{ height: 8, width: 48, borderRadius: 4, background: '#2F6B4F', opacity: 0.15 }} />
              </div>
            </div>
            <div style={{ height: 10, width: '65%', borderRadius: 4, background: '#E4E5E7' }} />
            <div style={{ height: 7, width: '90%', borderRadius: 4, background: '#EDEEF0' }} />
            <div style={{ height: 7, width: '78%', borderRadius: 4, background: '#EDEEF0' }} />
            <div style={{ height: 7, width: '55%', borderRadius: 4, background: '#EDEEF0' }} />
            <div style={{ display: 'flex', gap: 8, marginTop: 4 }}>
              {[1, 2, 3].map((i) => (
                <div key={i} style={{ flex: 1, height: 44, borderRadius: 8, background: '#F4F5F6', border: '1px solid #EDEEF0' }} />
              ))}
            </div>
          </div>
        </div>
      </div>
      <div className="text-center">
        <p className="text-sm font-semibold text-gray-600">Browser view</p>
        <p className="text-xs text-gray-400 mt-1">Run a task to see the agent work</p>
      </div>
    </div>
  )
}

export default function BrowserPane({
  screenshot,
  viewport,
  status,
  currentAction,
  currentUrl,
  wsRef,
}: BrowserPaneProps) {
  const canvasRef = useRef<HTMLCanvasElement>(null)
  // Browser viewport size in CSS px. Frames may be downscaled, so clicks must be
  // mapped to this, not to the image's own size.
  const nativeSizeRef = useRef({ w: 1280, h: 800 })
  const frameSeqRef = useRef(0)   // latest frame wins if decodes finish out of order
  useEffect(() => {
    if (viewport) nativeSizeRef.current = viewport
  }, [viewport])

  useEffect(() => {
    if (!screenshot || !canvasRef.current) return
    const canvas = canvasRef.current
    const ctx = canvas.getContext('2d')
    if (!ctx) return
    const img = new Image()
    const seq = ++frameSeqRef.current
    img.onload = () => {
      if (seq !== frameSeqRef.current) return
      canvas.width = img.width
      canvas.height = img.height
      if (!viewport) nativeSizeRef.current = { w: img.width, h: img.height }
      ctx.drawImage(img, 0, 0)
    }
    img.src = `data:image/jpeg;base64,${screenshot}`
  }, [screenshot, viewport])

  // Scale canvas coordinates → browser native coordinates
  const toNative = useCallback((canvas: HTMLCanvasElement, clientX: number, clientY: number) => {
    const rect = canvas.getBoundingClientRect()
    const scaleX = nativeSizeRef.current.w / rect.width
    const scaleY = nativeSizeRef.current.h / rect.height
    return {
      x: Math.round((clientX - rect.left) * scaleX),
      y: Math.round((clientY - rect.top) * scaleY),
    }
  }, [])

  const send = useCallback((msg: InputMessage) => {
    if (wsRef.current?.readyState === WebSocket.OPEN) {
      wsRef.current.send(JSON.stringify(msg))
    }
  }, [wsRef])

  const getModifiers = (e: React.MouseEvent | React.KeyboardEvent) => {
    let m = 0
    if (e.altKey)   m |= 1
    if (e.ctrlKey)  m |= 2
    if (e.metaKey)  m |= 4
    if (e.shiftKey) m |= 8
    return m
  }

  const onMouseDown = useCallback((e: React.MouseEvent<HTMLCanvasElement>) => {
    if (!canvasRef.current) return
    const { x, y } = toNative(canvasRef.current, e.clientX, e.clientY)
    send({ type: 'input', kind: 'mouse', event: 'mousePressed', x, y, button: e.button === 2 ? 'right' : 'left', clickCount: 1, deltaX: 0, deltaY: 0, modifiers: getModifiers(e) })
  }, [send, toNative])

  const onMouseUp = useCallback((e: React.MouseEvent<HTMLCanvasElement>) => {
    if (!canvasRef.current) return
    const { x, y } = toNative(canvasRef.current, e.clientX, e.clientY)
    send({ type: 'input', kind: 'mouse', event: 'mouseReleased', x, y, button: e.button === 2 ? 'right' : 'left', clickCount: 1, deltaX: 0, deltaY: 0, modifiers: getModifiers(e) })
  }, [send, toNative])

  const onMouseMove = useCallback((e: React.MouseEvent<HTMLCanvasElement>) => {
    if (!canvasRef.current) return
    const { x, y } = toNative(canvasRef.current, e.clientX, e.clientY)
    send({ type: 'input', kind: 'mouse', event: 'mouseMoved', x, y, button: 'none', clickCount: 0, deltaX: 0, deltaY: 0, modifiers: getModifiers(e) })
  }, [send, toNative])

  const onWheel = useCallback((e: React.WheelEvent<HTMLCanvasElement>) => {
    if (!canvasRef.current) return
    const { x, y } = toNative(canvasRef.current, e.clientX, e.clientY)
    send({ type: 'input', kind: 'mouse', event: 'mouseWheel', x, y, button: 'none', clickCount: 0, deltaX: e.deltaX, deltaY: e.deltaY, modifiers: getModifiers(e) })
  }, [send, toNative])

  const onKeyDown = useCallback((e: React.KeyboardEvent<HTMLCanvasElement>) => {
    e.preventDefault()
    send({ type: 'input', kind: 'key', event: 'keyDown', key: e.key, code: e.code, text: '', keyCode: e.keyCode, modifiers: getModifiers(e) })
    if (e.key.length === 1) {
      send({ type: 'input', kind: 'key', event: 'char', key: e.key, code: e.code, text: e.key, keyCode: e.keyCode, modifiers: getModifiers(e) })
    }
  }, [send])

  const onKeyUp = useCallback((e: React.KeyboardEvent<HTMLCanvasElement>) => {
    e.preventDefault()
    send({ type: 'input', kind: 'key', event: 'keyUp', key: e.key, code: e.code, text: '', keyCode: e.keyCode, modifiers: getModifiers(e) })
  }, [send])

  const cfg = STATUS_CONFIG[status]
  const hasScreenshot = !!screenshot

  return (
    <div
      className="flex flex-col w-full h-full overflow-hidden"
      style={{ background: 'radial-gradient(ellipse at 50% 40%, #ECEEF0 0%, #E2E4E6 100%)' }}
    >
      {/* ── Browser toolbar — single row like a real browser ── */}
      <div
        className="flex-shrink-0 flex items-center gap-3 px-4"
        style={{
          height: 42,
          background: 'rgba(255,255,255,0.88)',
          backdropFilter: 'blur(12px)',
          borderBottom: '1px solid rgba(0,0,0,0.07)',
        }}
      >
        {/* Traffic lights */}
        <div className="flex items-center gap-1.5 flex-shrink-0">
          <span className="w-2.5 h-2.5 rounded-full bg-[#FF5F57]" />
          <span className="w-2.5 h-2.5 rounded-full bg-[#FFBD2E]" />
          <span className="w-2.5 h-2.5 rounded-full bg-[#28C840]" />
        </div>

        {/* Address bar — takes remaining space, URL truncates inside */}
        <div
          className="flex items-center gap-2 px-3 h-7 rounded-lg min-w-0 flex-1 overflow-hidden"
          style={{ background: 'rgba(0,0,0,0.05)' }}
        >
          <svg width="9" height="10" viewBox="0 0 9 10" fill="none" className="flex-shrink-0 text-gray-400">
            <rect x="1" y="4" width="7" height="6" rx="1" stroke="currentColor" strokeWidth="1.2"/>
            <path d="M2.5 4V3a2 2 0 0 1 4 0v1" stroke="currentColor" strokeWidth="1.2"/>
          </svg>
          <span className="text-[11px] text-gray-600 truncate min-w-0">
            {currentUrl || 'google.com'}
          </span>
        </div>
      </div>

      {/* ── Viewport ── */}
      <div className="flex-1 min-h-0 flex items-center justify-center overflow-hidden relative">
        <canvas
            ref={canvasRef}
            tabIndex={0}
            className="max-w-full max-h-full object-contain outline-none"
            style={{
              display: hasScreenshot ? 'block' : 'none',
              boxShadow: '0 8px 32px rgba(0,0,0,0.18)',
              cursor: 'default',
            }}
            onMouseDown={onMouseDown}
            onMouseUp={onMouseUp}
            onMouseMove={onMouseMove}
            onWheel={onWheel}
            onKeyDown={onKeyDown}
            onKeyUp={onKeyUp}
          />
        {!hasScreenshot && <IdleIllustration />}

        {/* Running pulse overlay on screenshot */}
        {hasScreenshot && status === 'running' && (
          <div className="absolute inset-0 pointer-events-none">
            <div
              className="absolute inset-0"
              style={{ boxShadow: 'inset 0 0 0 2px rgba(47,107,79,0.35)' }}
            />
          </div>
        )}
      </div>

      {/* ── Bottom status bar ── */}
      {hasScreenshot && (
        <div
          className="flex-shrink-0 flex items-center gap-2 px-4 py-1.5"
          style={{
            background: 'rgba(255,255,255,0.75)',
            backdropFilter: 'blur(8px)',
            borderTop: '1px solid rgba(0,0,0,0.06)',
          }}
        >
          <span className={cn('w-1.5 h-1.5 rounded-full flex-shrink-0', cfg.dot)} />
          <span className={cn('text-[11px] font-medium', cfg.color)}>{cfg.label}</span>
          {status === 'running' && currentAction && (
            <span className="text-[11px] text-gray-400 truncate">— {currentAction}</span>
          )}
        </div>
      )}
    </div>
  )
}
