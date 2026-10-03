import { useEffect, useRef, useState, useCallback, type ReactNode } from 'react'
import BrowserChrome, { type BrowserActions } from '@/components/BrowserChrome'
import { Status, InputMessage, type BrowserState } from '@/types'
import { cn } from '@/lib/utils'
import { viewSize } from '@/lib/viewLayout'

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
  banner?: ReactNode            // a hand-off request, shown across the top of the page
  browser: BrowserState | null
  locked: boolean               // the agent is working: the toolbar is look-only
  actions: BrowserActions
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
  banner,
  browser,
  locked,
  actions,
}: BrowserPaneProps) {
  const canvasRef = useRef<HTMLCanvasElement>(null)
  const rootRef = useRef<HTMLDivElement>(null)

  // "You're in control": shown while the user is clicking or typing in the page, so it is clear who
  // is driving. It fades a few seconds after the last input.
  const [controlling, setControlling] = useState(false)
  const controlTimer = useRef<ReturnType<typeof setTimeout> | null>(null)
  const markControl = useCallback(() => {
    setControlling(true)
    if (controlTimer.current) clearTimeout(controlTimer.current)
    controlTimer.current = setTimeout(() => setControlling(false), 4000)
  }, [])
  useEffect(() => () => { if (controlTimer.current) clearTimeout(controlTimer.current) }, [])

  const [fullscreen, setFullscreen] = useState(false)
  useEffect(() => {
    const on = () => setFullscreen(document.fullscreenElement === rootRef.current)
    document.addEventListener('fullscreenchange', on)
    return () => document.removeEventListener('fullscreenchange', on)
  }, [])
  const toggleFullscreen = () => {
    if (document.fullscreenElement) document.exitFullscreen?.()
    else rootRef.current?.requestFullscreen?.().catch(() => {})
  }
  // Browser viewport size in CSS px. Frames may be downscaled, so clicks must be
  // mapped to this, not to the image's own size.
  const nativeSizeRef = useRef({ w: 1280, h: 800 })
  const frameSeqRef = useRef(0)   // latest frame wins if decodes finish out of order

  // How the page is laid out in the pane (lib/viewLayout): "Fit" shows the whole page with bands where the
  // shapes differ; "Fill" zooms in a little so the bands shrink, never cropping the sides.
  const areaRef = useRef<HTMLDivElement>(null)
  const [area, setArea] = useState({ w: 0, h: 0 })
  const [frameSize, setFrameSize] = useState({ w: 1280, h: 800 })
  const [fill, setFill] = useState(() => {
    try { return localStorage.getItem('aiba_view_fill') !== '0' } catch { return true }
  })
  const toggleFill = () => {
    setFill((f) => {
      try { localStorage.setItem('aiba_view_fill', f ? '0' : '1') } catch { /* not remembered */ }
      return !f
    })
  }
  useEffect(() => {
    const el = areaRef.current
    if (!el) return
    const ro = new ResizeObserver(() => setArea({ w: el.clientWidth, h: el.clientHeight }))
    ro.observe(el)
    setArea({ w: el.clientWidth, h: el.clientHeight })
    return () => ro.disconnect()
  }, [])
  const shown = viewSize(area, { w: viewport?.w ?? frameSize.w, h: viewport?.h ?? frameSize.h }, fill)
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
      setFrameSize((s) => (s.w === img.width && s.h === img.height ? s : { w: img.width, h: img.height }))
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
    markControl()
    const { x, y } = toNative(canvasRef.current, e.clientX, e.clientY)
    send({ type: 'input', kind: 'mouse', event: 'mousePressed', x, y, button: e.button === 2 ? 'right' : 'left', clickCount: 1, deltaX: 0, deltaY: 0, modifiers: getModifiers(e) })
  }, [send, toNative, markControl])

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

  // The page is drawn smaller than it really is, so a wheel movement of N screen pixels has to be N times
  // the shrink factor in the page's own pixels, or scrolling feels slow. A remote browser also jumps the
  // whole distance at once, so the movement is eased out over a few frames (each frame sends a share of
  // what is left), which looks like smooth scrolling and adds up to exactly the distance asked for.
  const wheelRef = useRef<{ x: number; y: number; dx: number; dy: number; mods: number; raf: number } | null>(null)
  const onWheel = useCallback((e: React.WheelEvent<HTMLCanvasElement>) => {
    if (!canvasRef.current) return
    const rect = canvasRef.current.getBoundingClientRect()
    const k = rect.width > 0 ? nativeSizeRef.current.w / rect.width : 1
    const { x, y } = toNative(canvasRef.current, e.clientX, e.clientY)
    const unit = e.deltaMode === 1 ? 40 : e.deltaMode === 2 ? rect.height : 1   // lines / pages -> pixels
    const w = wheelRef.current ?? { x, y, dx: 0, dy: 0, mods: getModifiers(e), raf: 0 }
    w.x = x
    w.y = y
    w.dx += e.deltaX * unit * k
    w.dy += e.deltaY * unit * k
    if (wheelRef.current) return        // the loop below is already running and will pick this up
    wheelRef.current = w
    const step = () => {
      const share = (v: number) => (Math.abs(v) < 4 ? v : v * 0.3)   // the last few pixels go in one go
      const sx = Math.round(share(w.dx))
      const sy = Math.round(share(w.dy))
      w.dx -= sx
      w.dy -= sy
      if (sx !== 0 || sy !== 0) {
        send({ type: 'input', kind: 'mouse', event: 'mouseWheel', x: w.x, y: w.y, button: 'none', clickCount: 0, deltaX: sx, deltaY: sy, modifiers: w.mods })
      }
      if (Math.abs(w.dx) < 0.5 && Math.abs(w.dy) < 0.5) wheelRef.current = null
      else w.raf = requestAnimationFrame(step)
    }
    w.raf = requestAnimationFrame(step)
  }, [send, toNative])
  useEffect(() => () => { if (wheelRef.current) cancelAnimationFrame(wheelRef.current.raf) }, [])

  // Text that does not come from a plain key press (an emoji picker, an IME such as Chinese/Japanese,
  // dictation, a phone keyboard) raises no usable key events on a canvas. A hidden text box takes the
  // focus instead; whatever it receives is sent as finished text.
  const textRef = useRef<HTMLTextAreaElement>(null)
  const sendText = useCallback((text: string) => {
    if (text) send({ type: 'input', kind: 'key', event: 'insertText', text })
  }, [send])
  const onTextInput = useCallback((e: React.FormEvent<HTMLTextAreaElement>) => {
    if ((e.nativeEvent as InputEvent).isComposing) return   // wait for compositionend
    sendText(e.currentTarget.value)
    e.currentTarget.value = ''
  }, [sendText])
  const onCompositionEnd = useCallback((e: React.CompositionEvent<HTMLTextAreaElement>) => {
    sendText(e.data)
    e.currentTarget.value = ''
  }, [sendText])

  const onKeyDown = useCallback((e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    markControl()
    // An IME is composing: let the text box have it, it is sent when the composition ends.
    if (e.nativeEvent.isComposing || e.key === 'Process' || e.key === 'Unidentified') return
    // Ctrl/Cmd+V: let the browser fire its paste event (handled below) instead of swallowing the key.
    if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 'v') return
    e.preventDefault()
    send({ type: 'input', kind: 'key', event: 'keyDown', key: e.key, code: e.code, text: '', keyCode: e.keyCode, modifiers: getModifiers(e) })
    if ([...e.key].length === 1) {   // one character, including emoji (which are 2 UTF-16 units)
      send({ type: 'input', kind: 'key', event: 'char', key: e.key, code: e.code, text: e.key, keyCode: e.keyCode, modifiers: getModifiers(e) })
    }
  }, [send, markControl])

  const onPaste = useCallback((e: React.ClipboardEvent<HTMLTextAreaElement>) => {
    e.preventDefault()
    const text = e.clipboardData.getData('text')
    if (text) send({ type: 'input', kind: 'key', event: 'insertText', text })
  }, [send])

  const onKeyUp = useCallback((e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    e.preventDefault()
    send({ type: 'input', kind: 'key', event: 'keyUp', key: e.key, code: e.code, text: '', keyCode: e.keyCode, modifiers: getModifiers(e) })
  }, [send])

  const cfg = STATUS_CONFIG[status]
  const hasScreenshot = !!screenshot

  return (
    <div
      ref={rootRef}
      className="flex flex-col w-full h-full overflow-hidden"
      style={{ background: 'radial-gradient(ellipse at 50% 40%, #ECEEF0 0%, #E2E4E6 100%)' }}
    >
      <BrowserChrome
        state={browser}
        currentUrl={currentUrl ?? ''}
        locked={locked}
        controlling={controlling}
        fullscreen={fullscreen}
        onFullscreen={toggleFullscreen}
        fill={fill}
        onToggleFill={toggleFill}
        actions={actions}
      />

      {banner}

      {/* ── Viewport ── */}
      <div ref={areaRef} className={cn('flex-1 min-h-0 flex justify-center overflow-hidden relative', fill ? 'items-start' : 'items-center')}>
        <canvas
            ref={canvasRef}
            tabIndex={0}
            className="flex-shrink-0 outline-none"
            style={{
              display: hasScreenshot ? 'block' : 'none',
              width: shown ? shown.w : undefined,
              height: shown ? shown.h : undefined,
              boxShadow: fill ? undefined : '0 8px 32px rgba(0,0,0,0.18)',
              cursor: 'default',
            }}
            onMouseDown={onMouseDown}
            onMouseUp={onMouseUp}
            onMouseMove={onMouseMove}
            onWheel={onWheel}
            onFocus={() => textRef.current?.focus()}
          />
        <textarea
          ref={textRef}
          aria-label="Type into the browser"
          autoCapitalize="off"
          autoCorrect="off"
          spellCheck={false}
          tabIndex={-1}
          className="absolute h-px w-px opacity-0 pointer-events-none"
          style={{ left: 0, top: 0 }}
          onKeyDown={onKeyDown}
          onKeyUp={onKeyUp}
          onPaste={onPaste}
          onInput={onTextInput}
          onCompositionEnd={onCompositionEnd}
        />
        {!hasScreenshot && <IdleIllustration />}

        {/* Running pulse overlay on screenshot */}
        {hasScreenshot && status === 'running' && (
          <div className="absolute inset-0 pointer-events-none">
            <div
              className="absolute inset-0"
              style={{ boxShadow: `inset 0 0 0 2px ${controlling ? 'rgba(37,99,235,0.45)' : 'rgba(47,107,79,0.35)'}` }}
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
            <span title={currentAction} className="text-[11px] text-gray-400 truncate">— {currentAction}</span>
          )}
        </div>
      )}
    </div>
  )
}
