import { useEffect, useRef, useState, type CSSProperties, type ReactNode } from 'react'
import { MagnifyingGlass, Sparkle, WifiSlash, ArrowsClockwise, Images } from '@phosphor-icons/react'
import { thumb, type Img } from './api'
import type { Gallery } from './state'

const dayFmt = new Intl.DateTimeFormat('en', { weekday: 'short', month: 'short', day: 'numeric' })
const timeFmt = new Intl.DateTimeFormat('en', { hour: '2-digit', minute: '2-digit', hour12: false })
export const dayKey = (d: Date) => `${d.getFullYear()}-${d.getMonth()}-${d.getDate()}`
export function dayLabel(d: Date) {
  const now = Date.now()
  if (dayKey(d) === dayKey(new Date(now))) return 'Today'
  if (dayKey(d) === dayKey(new Date(now - 864e5))) return 'Yesterday'
  return dayFmt.format(d)
}
export const timeLabel = (d: Date) => timeFmt.format(d)
export const shortMonth = (d: Date) => d.toLocaleString('en', { month: 'short' })

export function bytesLabel(n: number) {
  if (n >= 1e9) return `${(n / 1e9).toFixed(1)} GB`
  if (n >= 1e6) return `${Math.round(n / 1e6)} MB`
  if (n >= 1e3) return `${Math.round(n / 1e3)} KB`
  return `${n} B`
}

export const tint = (img: Img) => `hsl(${img.hue} 22% 16%)`

export function Tile({ img, className = '', highlight, onOpen, rounded = 'rounded-[10px]', cover = true, style, children }: {
  img: Img; className?: string; highlight?: boolean; onOpen: () => void; rounded?: string; cover?: boolean; style?: CSSProperties; children?: ReactNode
}) {
  const [loaded, setLoaded] = useState(false)
  return (
    <button
      type="button"
      onClick={onOpen}
      data-shoot="tile"
      className={`group relative block overflow-hidden ${rounded} bg-white/[0.04] outline-none focus-visible:ring-2 focus-visible:ring-sky-400 ${className}`}
      style={{ background: tint(img), ...style }}
      aria-label={img.prompt || 'Image'}
    >
      {!img.pending && (
        <img
          src={thumb(img.id, 480)}
          alt=""
          loading="lazy"
          decoding="async"
          onLoad={() => setLoaded(true)}
          className={`h-full w-full ${cover ? 'object-cover' : 'object-contain'} transition duration-500 ${loaded ? 'opacity-100 scale-100' : 'opacity-0 scale-[1.02]'} group-hover:scale-[1.02]`}
        />
      )}
      {img.pending && (
        <div className="absolute inset-0 grid place-items-center">
          <div className="absolute inset-0 animate-pulse bg-gradient-to-br from-white/[0.06] to-transparent" />
          <span className="relative flex items-center gap-1.5 text-[11px] text-white/70"><Sparkle size={13} weight="fill" className="animate-spin [animation-duration:3s]" />Generating</span>
        </div>
      )}
      {children}
      {highlight && <span className="pointer-events-none absolute inset-0 rounded-[inherit] ring-2 ring-inset ring-sky-400 animate-[hl_2.4s_ease-out_forwards]" />}
    </button>
  )
}

export function Toast({ g }: { g: Gallery }) {
  const t = g.toast ?? (g.status === 'ready' && g.conn === 'down' ? { text: 'Lost the gallery server, reconnecting', undo: undefined } : null)
  if (!t) return null
  return (
    <div className="pointer-events-none absolute inset-x-0 bottom-5 z-50 flex justify-center px-4" role="status" aria-live="polite">
      <div className="pointer-events-auto flex items-center gap-3 rounded-xl border border-white/10 bg-[#1a1a1e] py-2 pl-4 pr-2 text-[13px] shadow-2xl">
        <span className="min-h-8 py-1.5 text-white/85">{t.text}</span>
        {t.undo && <button onClick={t.undo} data-shoot="undo" className="h-8 rounded-lg px-3 font-medium text-sky-300 hover:bg-white/[0.06]">Undo</button>}
      </div>
    </div>
  )
}

export function Empty({ g }: { g: Gallery }) {
  if (g.total === 0) {
    return (
      <div className="grid h-full place-items-center p-10 text-center">
        <div className="max-w-[340px]">
          <Images size={28} className="mx-auto text-white/30" />
          <p className="mt-3 text-[14px] text-white/70">No images yet</p>
          <p className="mt-1.5 text-[13px] leading-[1.5] text-white/40">Every image Codex makes on this Mac shows up here as soon as it lands.</p>
        </div>
      </div>
    )
  }
  return (
    <div className="grid h-full place-items-center p-10 text-center">
      <div>
        <MagnifyingGlass size={28} className="mx-auto text-white/30" />
        <p className="mt-3 text-[14px] text-white/70">Nothing matches{g.query ? ` "${g.query}"` : ''}</p>
        <button onClick={() => { g.setQuery(''); g.setStarredOnly(false); g.setFilter('all') }} className="mt-3 h-9 rounded-lg bg-white/[0.07] px-3 text-[13px] hover:bg-white/[0.12]">Clear filters</button>
      </div>
    </div>
  )
}

export function Loading() {
  return (
    <div className="grid h-full place-items-center p-10 text-center" role="status">
      <span className="flex items-center gap-2 text-[13px] text-white/50"><Sparkle size={14} weight="fill" className="animate-spin [animation-duration:3s]" />Loading images</span>
    </div>
  )
}

export function Unreachable({ g }: { g: Gallery }) {
  return (
    <div className="grid h-full place-items-center p-10 text-center">
      <div className="max-w-[360px]">
        <WifiSlash size={28} className="mx-auto text-white/30" />
        <p className="mt-3 text-[14px] text-white/70">Can't reach the gallery server</p>
        <p className="mt-1.5 text-[13px] leading-[1.5] text-white/40">It may have stopped after being idle. Start it again on the Mac, this page retries on its own.</p>
        <p className="mt-3 rounded-lg bg-white/[0.05] px-3 py-2 font-[Geist_Mono] text-[12px] text-white/60">python3 gallery/server/cli.py start</p>
        <button onClick={() => void g.retry()} className="mx-auto mt-3 flex h-9 items-center gap-1.5 rounded-lg bg-white/[0.07] px-3 text-[13px] hover:bg-white/[0.12]"><ArrowsClockwise size={14} />Try again</button>
      </div>
    </div>
  )
}

export function Root({ children }: { children: ReactNode }) {
  return (
    <div className="relative flex h-full flex-col overflow-hidden bg-[#0b0b0d] text-white antialiased" style={{ fontFamily: 'Geist, ui-sans-serif, system-ui, sans-serif' }}>
      {children}
    </div>
  )
}

export function useWidth<T extends HTMLElement>() {
  const ref = useRef<T>(null)
  const [w, setW] = useState(0)
  useEffect(() => {
    if (!ref.current) return
    const ro = new ResizeObserver(([e]) => setW(e.contentRect.width))
    ro.observe(ref.current)
    return () => ro.disconnect()
  }, [])
  return [ref, w] as const
}
