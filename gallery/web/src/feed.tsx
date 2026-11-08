import type React from 'react'
import { useEffect, useMemo, useRef, useState, type RefObject } from 'react'
import { createPortal } from 'react-dom'
import { useVirtualizer } from '@tanstack/react-virtual'
import { Star, MagnifyingGlass, X, CloudArrowDown, Laptop, Desktop, Sparkle } from '@phosphor-icons/react'
import { api, type Host, type Img } from './api'
import { Tile, useWidth, timeLabel, shortMonth } from './parts'
import type { Filter, Gallery } from './state'

export type Jump = { toId: (id: string) => void; toFraction: (f: number) => void }
export type FeedState = { date?: Date; offset: number; fraction: number; dir: 1 | -1 }

export const glass = 'border border-white/[0.08] bg-[#16161a]/75 shadow-[0_8px_32px_rgba(0,0,0,.45)] backdrop-blur-2xl backdrop-saturate-150'

export function MasonryFeed({ g, top = 20, bottom = 20, right = 20, left = 20, jump, onState }: {
  g: Gallery; top?: number; bottom?: number; right?: number; left?: number
  jump?: React.RefObject<Jump | null>; onState?: (s: FeedState) => void
}) {
  const GAP = 10
  const scroller = useRef<HTMLDivElement>(null)
  const [measure, width] = useWidth<HTMLDivElement>()
  const inner = Math.max(0, width - left - right)
  const lanes = Math.max(2, Math.round(inner / 280))
  const colW = inner > 0 ? (inner - GAP * (lanes - 1)) / lanes : 200

  const v = useVirtualizer({
    count: g.list.length,
    getScrollElement: () => scroller.current,
    estimateSize: i => colW * (g.list[i].h / g.list[i].w) + GAP,
    getItemKey: i => g.list[i].id,
    lanes,
    overscan: 10,
    paddingStart: top,
    paddingEnd: bottom,
  })
  useEffect(() => { v.measure() }, [colW, lanes, g.list]) // eslint-disable-line react-hooks/exhaustive-deps

  const items = v.getVirtualItems()
  const offset = v.scrollOffset ?? 0
  const last = useRef(0)
  const firstIdx = useMemo(() => {
    const vis = items.filter(it => it.end > offset + top)
    return vis.length ? Math.min(...vis.map(x => x.index)) : 0
  }, [items, offset, top])

  useEffect(() => {
    const total = v.getTotalSize() - (scroller.current?.clientHeight ?? 0)
    const dir = offset >= last.current ? 1 : -1
    last.current = offset
    onState?.({ date: g.list[firstIdx]?.date, offset, fraction: total > 0 ? Math.min(1, offset / total) : 0, dir })
  }, [offset, firstIdx, g.list]) // eslint-disable-line react-hooks/exhaustive-deps

  if (jump) jump.current = {
    toId: id => { const i = g.list.findIndex(x => x.id === id); if (i >= 0) v.scrollToIndex(i, { align: 'center' }) },
    toFraction: f => v.scrollToIndex(Math.round(f * (g.list.length - 1)), { align: 'start' }),
  }
  useEffect(() => { if (g.highlight && !g.openId) jump?.current?.toId(g.highlight) }, [g.highlight]) // eslint-disable-line react-hooks/exhaustive-deps

  return (
    <div ref={measure} className="absolute inset-0">
      <div ref={scroller} className="h-full overflow-y-auto [scrollbar-width:none] [&::-webkit-scrollbar]:hidden" data-shoot="scroller">
        <div className="relative" style={{ height: v.getTotalSize() }}>
          {items.map(it => {
            const img = g.list[it.index]
            if (!img) return null
            const h = colW * (img.h / img.w)
            return (
              <div key={it.key} className="absolute" style={{ top: it.start, left: left + it.lane * (colW + GAP), width: colW, height: h }}>
                <Tile img={img} highlight={g.highlight === img.id} onOpen={() => g.setOpenId(img.id)} rounded="rounded-xl" className="h-full w-full">
                  <span className="pointer-events-none absolute inset-0 bg-gradient-to-t from-black/70 via-transparent to-transparent opacity-0 transition group-hover:opacity-100" />
                  <span className="pointer-events-none absolute inset-x-3 bottom-3 line-clamp-2 text-left text-[12px] leading-[1.4] text-white/90 opacity-0 transition group-hover:opacity-100">{img.prompt}</span>
                  {img.starred && <span className="pointer-events-none absolute left-2.5 top-2.5 grid size-6 place-items-center rounded-full bg-black/45 text-amber-300 backdrop-blur"><Star size={12} weight="fill" /></span>}
                  <span className="pointer-events-none absolute right-2.5 top-2.5 rounded-full bg-black/45 px-2 py-0.5 text-[11px] text-white/80 opacity-0 backdrop-blur transition group-hover:opacity-100">{timeLabel(img.date)}</span>
                </Tile>
              </div>
            )
          })}
        </div>
      </div>
    </div>
  )
}

function monthsOf(list: Img[]) {
  const out: { key: string; label: string; f: number; date: Date }[] = []
  list.forEach((img, i) => {
    const k = `${img.date.getFullYear()}-${img.date.getMonth()}`
    if (!out.length || out[out.length - 1].key !== k) out.push({ key: k, label: shortMonth(img.date), f: i / Math.max(1, list.length - 1), date: img.date })
  })
  return out
}

export function Timeline({ g, state, jump, orientation = 'vertical', className = '' }: {
  g: Gallery; state: FeedState; jump: React.RefObject<Jump | null>; orientation?: 'vertical' | 'horizontal'; className?: string
}) {
  const ref = useRef<HTMLDivElement>(null)
  const [drag, setDrag] = useState<{ pos: number; label: string } | null>(null)
  const vert = orientation === 'vertical'
  const months = useMemo(() => {
    const minGap = vert ? 0.06 : 0.09
    let prev = -1
    return monthsOf(g.list).map(m => { const pos = prev < 0 ? m.f : Math.max(m.f, prev + minGap); prev = pos; return { ...m, f: pos } })
  }, [g.list, vert])
  if (g.list.length < 2) return null
  const at = (e: { clientX: number; clientY: number }) => {
    const b = ref.current!.getBoundingClientRect()
    const f = Math.min(1, Math.max(0, vert ? (e.clientY - b.top) / b.height : (e.clientX - b.left) / b.width))
    jump.current?.toFraction(f)
    const d = g.list[Math.round(f * (g.list.length - 1))]?.date
    if (d) setDrag({ pos: f, label: `${shortMonth(d)} ${d.getDate()}` })
  }
  const cur = state.date ? `${state.date.getFullYear()}-${state.date.getMonth()}` : ''
  const thumb = drag ? drag.pos : state.fraction
  return (
    <div
      ref={ref} data-shoot="timeline"
      className={`absolute z-20 touch-none select-none ${vert ? 'w-9 cursor-ns-resize' : 'h-9 cursor-ew-resize'} ${className}`}
      onPointerDown={e => { (e.currentTarget as HTMLElement).setPointerCapture(e.pointerId); at(e) }}
      onPointerMove={e => { if (drag) at(e) }}
      onPointerUp={() => setDrag(null)}
      onPointerCancel={() => setDrag(null)}
    >
      <span className={`absolute ${vert ? 'inset-y-0 right-[1px] w-1.5' : 'inset-x-0 bottom-[1px] h-1.5'}`}>
        <span className={`absolute rounded-full bg-white/10 ${vert ? 'inset-y-0 left-[2px] w-[2px]' : 'inset-x-0 top-[2px] h-[2px]'}`} />
        <span className={`absolute rounded-full bg-white transition-[top,left] duration-75 ${vert ? 'left-0 h-6 w-1.5 -translate-y-1/2' : 'top-0 h-1.5 w-6 -translate-x-1/2'}`}
          style={vert ? { top: `${thumb * 100}%` } : { left: `${thumb * 100}%` }} />
      </span>
      {months.map(m => (
        <span key={m.key}
          className={`absolute text-[10px] font-medium transition-colors ${m.key === cur ? 'text-white' : 'text-white/40'} ${vert ? 'right-3 -translate-y-1/2' : 'top-0 -translate-x-1/2'}`}
          style={vert ? { top: `${m.f * 100}%` } : { left: `${m.f * 100}%` }}>{m.label}</span>
      ))}
      {drag && (
        <span className={`absolute whitespace-nowrap rounded-lg bg-white px-2.5 py-1 text-[12px] font-medium text-black shadow-xl ${vert ? 'right-12 -translate-y-1/2' : 'bottom-11 -translate-x-1/2'}`}
          style={vert ? { top: `${drag.pos * 100}%` } : { left: `${drag.pos * 100}%` }}>{drag.label}</span>
      )}
    </div>
  )
}

export function Logo({ compact, round }: { compact?: boolean; round?: boolean }) {
  return (
    <span className="flex items-center gap-2">
      <span className={`grid size-7 shrink-0 place-items-center ${round ? 'rounded-full' : 'rounded-[9px]'} bg-white text-black`}><Sparkle size={15} weight="fill" /></span>
      {!compact && <span className="text-[14px] font-semibold tracking-[-0.01em]">codex-image</span>}
    </span>
  )
}

export function FilterTabs({ g, small, sliding }: { g: Gallery; small?: boolean; sliding?: boolean }) {
  const refs = useRef<Record<string, HTMLButtonElement | null>>({})
  const [box, setBox] = useState<{ left: number; width: number } | null>(null)
  useEffect(() => {
    if (!sliding) return
    const measure = () => { const el = refs.current[g.filter]; if (el) setBox({ left: el.offsetLeft, width: el.offsetWidth }) }
    measure()
    const ro = new ResizeObserver(measure)
    Object.values(refs.current).forEach(el => el && ro.observe(el))
    return () => ro.disconnect()
  }, [g.filter, sliding])
  return (
    <div className="relative flex shrink-0 rounded-full bg-white/[0.06] p-0.5 text-[13px]" role="tablist">
      {sliding && box && (
        <span aria-hidden className="absolute inset-y-0.5 rounded-full bg-white/[0.14] transition-[left,width] duration-[420ms] ease-[cubic-bezier(.3,1.25,.4,1)] motion-reduce:transition-none"
          style={{ left: box.left, width: box.width }} />
      )}
      {(['all', 'skill'] as Filter[]).map(f => (
        <button key={f} ref={el => { refs.current[f] = el }} role="tab" aria-selected={g.filter === f} onClick={() => g.setFilter(f)} data-shoot={`filter-${f}`}
          className={`relative ${small ? 'h-7 px-2.5' : 'h-8 px-3'} rounded-full transition-colors duration-300 ${g.filter === f ? `${sliding ? '' : 'bg-white/[0.14] '}text-white` : 'text-white/55 hover:text-white/85'}`}>
          {f === 'all' ? 'All' : 'Skill runs'}
        </button>
      ))}
    </div>
  )
}

export function SearchField({ g, className = '', autoFocus, onBlur }: { g: Gallery; className?: string; autoFocus?: boolean; onBlur?: () => void }) {
  return (
    <label className={`flex h-9 min-w-0 items-center gap-2 rounded-full bg-white/[0.06] px-3 text-[13px] text-white/50 focus-within:bg-white/[0.1] focus-within:text-white/80 ${className}`}>
      <MagnifyingGlass size={15} className="shrink-0" />
      <input value={g.query} onChange={e => g.setQuery(e.target.value)} placeholder="Search prompts" data-shoot="search" autoFocus={autoFocus} onBlur={onBlur}
        onKeyDown={e => { if (e.key === 'Escape') { e.preventDefault(); (e.target as HTMLInputElement).blur() } }}
        className="min-w-0 flex-1 bg-transparent text-[16px] text-white outline-none placeholder:text-white/40" />
      {g.query && <button onMouseDown={e => e.preventDefault()} onClick={() => g.setQuery('')} aria-label="Clear search" className="text-white/50 hover:text-white"><X size={14} /></button>}
    </label>
  )
}

export function RoundBtn({ label, onClick, active, children, shoot }: { label: string; onClick?: () => void; active?: boolean; children: React.ReactNode; shoot?: string }) {
  return (
    <button type="button" aria-label={label} title={label} onClick={onClick} data-shoot={shoot}
      className={`grid size-9 shrink-0 place-items-center rounded-full transition ${active ? 'bg-amber-300/15 text-amber-300' : 'text-white/65 hover:bg-white/[0.08] hover:text-white'}`}>
      {children}
    </button>
  )
}

export function StarFilter({ g }: { g: Gallery }) {
  return <RoundBtn label={g.starredOnly ? 'Show all' : 'Starred only'} active={g.starredOnly} onClick={() => g.setStarredOnly(!g.starredOnly)} shoot="starred"><Star size={17} weight={g.starredOnly ? 'fill' : 'regular'} /></RoundBtn>
}

type Hosts = { state: 'loading' } | { state: 'error'; message: string } | { state: 'ready'; hosts: Host[]; self: string | null }

const sshShort = (ssh: string) => ssh.split('@').pop() ?? ssh

function hostNote(h: Host) {
  if (h.new) return `${h.new.toLocaleString()} new`
  if (h.new === 0) return 'up to date'
  return h.last_import ? '' : 'never'
}

export function ImportBtn({ g, container }: { g: Gallery; container: RefObject<HTMLElement | null> }) {
  const [open, setOpen] = useState(false)
  const btn = useRef<HTMLDivElement>(null)
  const menuRef = useRef<HTMLDivElement>(null)
  const [pos, setPos] = useState<{ top: number; right: number } | null>(null)
  const [mounted, setMounted] = useState(false)
  const [visible, setVisible] = useState(false)
  const [hosts, setHosts] = useState<Hosts>({ state: 'loading' })

  useEffect(() => {
    if (open) { setMounted(true); const r = requestAnimationFrame(() => requestAnimationFrame(() => setVisible(true))); return () => cancelAnimationFrame(r) }
    setVisible(false)
    const t = window.setTimeout(() => setMounted(false), 220)
    return () => window.clearTimeout(t)
  }, [open])

  useEffect(() => {
    if (!open) return
    let live = true
    Promise.all([api.hosts(), api.server().then(s => s.host).catch(() => null)])
      .then(([h, self]) => { if (live) setHosts({ state: 'ready', hosts: h, self }) })
      .catch(e => { if (live) setHosts({ state: 'error', message: (e as Error).message }) })
    const onDown = (e: PointerEvent) => {
      const t = e.target as Node
      if (!menuRef.current?.contains(t) && !btn.current?.contains(t)) setOpen(false)
    }
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') setOpen(false) }
    window.addEventListener('pointerdown', onDown)
    window.addEventListener('keydown', onKey)
    return () => { live = false; window.removeEventListener('pointerdown', onDown); window.removeEventListener('keydown', onKey) }
  }, [open])

  const toggle = () => {
    if (!open && container.current && btn.current) {
      const c = container.current.getBoundingClientRect(), b = btn.current.getBoundingClientRect()
      setPos({ top: b.bottom - c.top + 10, right: c.right - b.right - 6 })
    }
    setOpen(o => !o)
  }

  const others = hosts.state === 'ready' ? hosts.hosts.filter(h => h.name !== hosts.self) : []
  const menu = (cls: string, style?: React.CSSProperties) => (
    <div ref={menuRef} data-opened-by="[data-shoot=import]" style={style} className={`${cls} w-64 rounded-2xl p-1.5 text-[13px] ${glass}`}>
      <p className="px-2.5 pb-1.5 pt-1 text-[11px] text-white/45">Copy images and prompts over Tailscale</p>
      {hosts.state === 'loading' && <p className="px-2.5 py-2 text-white/45">Looking for Macs</p>}
      {hosts.state === 'error' && <p className="px-2.5 py-2 text-rose-300/90">Could not list Macs: {hosts.message}</p>}
      {hosts.state === 'ready' && others.length === 0 && (
        <p className="px-2.5 py-2 text-[12px] leading-[1.5] text-white/50">No other Macs yet. Add them to <span className="font-[Geist_Mono] text-white/70">~/.codex-image/config.json</span>.</p>
      )}
      {others.map(h => (
        <button key={h.name} onClick={() => { setOpen(false); void g.importFrom(h.name, h.new) }}
          className="flex w-full items-center gap-2.5 rounded-xl px-2.5 py-2 text-left hover:bg-white/[0.06] disabled:opacity-40 disabled:hover:bg-transparent">
          <Laptop size={16} className="text-white/60" />
          <span className="min-w-0 flex-1 truncate">{h.name}<span className="ml-1.5 text-white/40">{sshShort(h.ssh)}</span></span>
          <span className="text-[12px] text-sky-300">{hostNote(h)}</span>
        </button>
      ))}
      {hosts.state === 'ready' && hosts.self && (
        <button disabled className="flex w-full items-center gap-2.5 rounded-xl px-2.5 py-2 text-left hover:bg-white/[0.06] disabled:opacity-40 disabled:hover:bg-transparent">
          <Desktop size={16} className="text-white/60" />
          <span className="min-w-0 flex-1 truncate">{hosts.self}<span className="ml-1.5 text-white/40">this Mac</span></span>
        </button>
      )}
    </div>
  )
  return (
    <div className="relative" ref={btn}>
      <RoundBtn label="Import from another Mac" onClick={toggle} shoot="import"><CloudArrowDown size={17} /></RoundBtn>
      {container.current && pos && mounted && createPortal(menu(
        `absolute z-50 origin-top-right bg-[#16161a]/55! transition-[opacity,transform,filter] motion-reduce:transition-none ${visible
          ? 'translate-y-0 scale-100 opacity-100 blur-0 duration-[260ms] ease-[cubic-bezier(.2,.9,.25,1.15)]'
          : 'pointer-events-none -translate-y-1.5 scale-[0.94] opacity-0 blur-[2px] duration-200 ease-in'}`,
        { top: pos.top, right: pos.right }), container.current)}
    </div>
  )
}
