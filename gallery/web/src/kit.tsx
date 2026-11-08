import type React from 'react'
import { useEffect, useRef, useState, type ReactNode } from 'react'
import { ArrowLeft, Star, Copy, DownloadSimple, LinkSimple, Trash, Check, ArrowsClockwise, ImageBroken, Lightning, CaretLeft, CaretRight } from '@phosphor-icons/react'
import { refSrc, thumb, type Img } from './api'
import { dayLabel, timeLabel, tint } from './parts'
import { glass } from './feed'
import { canCopyImage, copyImage, copyText, download } from './clipboard'
import { lineage, type Gallery } from './state'

export function useCurrent(g: Gallery, back: (img: Img) => void) {
  const img = g.openId ? g.all_by_id.get(g.openId) : undefined
  const idx = img ? g.list.findIndex(i => i.id === img.id) : -1
  const prev = idx > 0 ? g.list[idx - 1] : undefined
  const next = idx >= 0 ? g.list[idx + 1] : undefined
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (!img || g.composer) return
      if (e.metaKey || e.ctrlKey || e.altKey) return
      const t = e.target as HTMLElement
      if (t?.tagName === 'TEXTAREA' || t?.tagName === 'INPUT' || t?.isContentEditable) return
      if (e.key === 'ArrowLeft' && prev) g.setOpenId(prev.id)
      if (e.key === 'ArrowRight' && next) g.setOpenId(next.id)
      if (e.key === 'Escape') { e.preventDefault(); back(img) }
      if (e.key === 's') g.toggleStar(img.id)
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  })
  const fam = img ? lineage(img, g.all_by_id) : { parent: undefined, children: [] as Img[] }
  return { img, idx, prev, next, ...fam }
}

export function useSwipe(onPrev?: () => void, onNext?: () => void) {
  const start = useRef<{ x: number; y: number } | null>(null)
  const [dx, setDx] = useState(0)
  return {
    dx,
    handlers: {
      onPointerDown: (e: React.PointerEvent) => { if (e.pointerType !== 'mouse') start.current = { x: e.clientX, y: e.clientY } },
      onPointerMove: (e: React.PointerEvent) => { if (start.current) setDx(e.clientX - start.current.x) },
      onPointerUp: () => { const d = dx; start.current = null; setDx(0); if (d > 70) onPrev?.(); if (d < -70) onNext?.() },
      onPointerCancel: () => { start.current = null; setDx(0) },
    },
  }
}

export function Picture({ img, className = '', style }: { img: Img; className?: string; style?: React.CSSProperties }) {
  const [loaded, setLoaded] = useState(false)
  useEffect(() => setLoaded(false), [img.id])
  if (img.pending) {
    return <div className={`h-full max-h-full max-w-full animate-pulse bg-white/[0.05] ${className}`} style={{ aspectRatio: `${img.w}/${img.h}`, ...style }} aria-label="Generating" />
  }
  return (
    <img key={img.id} src={thumb(img.id, 1600)} alt={img.prompt} draggable={false} onLoad={() => setLoaded(true)}
      width={img.w} height={img.h}
      className={`max-h-full max-w-full object-contain transition-opacity duration-300 ${loaded ? 'opacity-100' : 'opacity-50'} ${className}`}
      style={{ aspectRatio: `${img.w}/${img.h}`, background: tint(img), height: 'auto', width: 'auto', ...style }} />
  )
}

export function useFlash() {
  const [k, setK] = useState<string | null>(null)
  return [k, (x: string) => { setK(x); window.setTimeout(() => setK(null), 1300) }] as const
}

export function BackChip({ img, back, className = '' }: { img: Img; back: (i: Img) => void; className?: string }) {
  return (
    <button onClick={() => back(img)} data-shoot="back" className={`flex h-10 items-center gap-2 rounded-full pl-3 pr-4 text-[13px] font-medium ${glass} ${className}`}>
      <ArrowLeft size={16} />{dayLabel(img.date)}
    </button>
  )
}

export function ActionBtn({ label, onClick, children, active, danger, shoot }: { label: string; onClick?: () => void; children: ReactNode; active?: boolean; danger?: boolean; shoot?: string }) {
  return (
    <button type="button" aria-label={label} title={label} onClick={onClick} data-shoot={shoot}
      className={`grid size-10 shrink-0 place-items-center rounded-full transition ${active ? 'text-amber-300' : danger ? 'text-white/65 hover:bg-rose-500/15 hover:text-rose-300' : 'text-white/70 hover:bg-white/[0.08] hover:text-white'}`}>
      {children}
    </button>
  )
}

export function Actions({ g, img, withDelete = true }: { g: Gallery; img: Img; withDelete?: boolean }) {
  const [k, flash] = useFlash()
  const star = img.starred
  const link = `${location.origin}/i/${img.short}`
  const copyLink = (note?: string) => copyText(link).then(() => { flash('link'); g.showToast(note ?? `Copied ${location.host}/i/${img.short}`) }, () => g.showToast('Could not copy the link'))
  const onCopyImage = () => {
    if (!canCopyImage()) { void copyLink('This browser cannot copy images, copied the link instead'); return }
    copyImage(img.id).then(() => flash('img'), () => g.showToast('Could not copy the image'))
  }
  return (
    <>
      <ActionBtn label={star ? 'Unstar' : 'Star'} active={star} onClick={() => g.toggleStar(img.id)} shoot="star"><Star size={18} weight={star ? 'fill' : 'regular'} /></ActionBtn>
      {!img.pending && <ActionBtn label="Copy image" onClick={onCopyImage}>{k === 'img' ? <Check size={18} /> : <Copy size={18} />}</ActionBtn>}
      {!img.pending && <ActionBtn label="Download original" onClick={() => { download(img.id); flash('dl') }}>{k === 'dl' ? <Check size={18} /> : <DownloadSimple size={18} />}</ActionBtn>}
      <ActionBtn label="Copy link" onClick={() => void copyLink()} shoot="copy-link">{k === 'link' ? <Check size={18} /> : <LinkSimple size={18} />}</ActionBtn>
      {withDelete && !img.pending && <ActionBtn label="Delete" danger onClick={() => g.remove(img.id)} shoot="delete"><Trash size={18} /></ActionBtn>}
    </>
  )
}

export function RegenButton({ g, img, compact }: { g: Gallery; img: Img; compact?: boolean }) {
  return (
    <button onClick={() => g.setComposer(img)} data-shoot="regenerate" disabled={img.pending}
      className={`flex h-10 shrink-0 items-center gap-2 rounded-full bg-white font-medium text-black transition hover:bg-white/90 active:scale-[0.98] disabled:opacity-50 ${compact ? 'px-3 text-[13px]' : 'px-4 text-[14px]'}`}>
      <ArrowsClockwise size={16} weight="bold" />{compact ? 'Regenerate' : 'Edit and regenerate'}
    </button>
  )
}

export function CopyPrompt({ img }: { img: Img }) {
  const [k, flash] = useFlash()
  if (!img.prompt) return null
  return (
    <button onClick={() => void copyText(img.prompt).then(() => flash('p'))} className="flex h-7 shrink-0 items-center gap-1 rounded-full px-2 text-[12px] text-white/55 hover:bg-white/[0.07] hover:text-white">
      {k ? <Check size={13} /> : <Copy size={13} />}{k ? 'Copied' : 'Copy'}
    </button>
  )
}

export function metaRows(img: Img): [string, ReactNode][] {
  return [
    ['Created', `${dayLabel(img.date)}, ${timeLabel(img.date)}`],
    ['Size', `${img.w} × ${img.h}`],
    ['Quality', img.quality || 'auto'],
    ['Source', img.source === 'skill' ? <span className="inline-flex items-center gap-1"><Lightning size={12} weight="fill" className="text-amber-300" />Skill run</span> : 'Codex'],
    ['Mac', img.mac || 'Unknown'],
    ['Session', img.session ? <span className="font-[Geist_Mono] text-[12px]">{img.session.slice(0, 8)}</span> : 'None'],
  ]
}

export function RefThumbs({ img, size = 56 }: { img: Img; size?: number }) {
  if (!img.refs.length) return <p className="text-[13px] text-white/40">No references</p>
  return (
    <div className="flex flex-wrap gap-2">
      {img.refs.map((r, i) => r.missing ? (
        <div key={r.id} title={r.path} style={{ width: size, height: size }} className="grid shrink-0 place-items-center rounded-xl border border-dashed border-white/15 text-white/35">
          <span className="text-center"><ImageBroken size={16} className="mx-auto" /><span className="text-[10px]">missing</span></span>
        </div>
      ) : (
        <span key={r.id} className="relative shrink-0">
          <img title={r.path} src={refSrc(r.id)} loading="lazy" style={{ width: size, height: size }} className="rounded-xl bg-white/[0.04] object-cover" alt={`Reference ${i + 1}`} />
          <span className="absolute bottom-1 left-1 rounded-md bg-black/55 px-1 text-[10px] text-white/85 backdrop-blur">{i + 1}</span>
        </span>
      ))}
    </div>
  )
}

export function Thumb({ img, size = 44, onClick, label, current }: { img: Img; size?: number; onClick?: () => void; label?: string; current?: boolean }) {
  const Tag = onClick ? 'button' : 'span'
  return (
    <Tag onClick={onClick} title={label ?? img.prompt} aria-label={onClick ? label ?? img.prompt : undefined}
      className={`relative block shrink-0 overflow-hidden rounded-lg transition ${current ? 'ring-2 ring-white' : 'opacity-80 hover:opacity-100'}`} style={{ width: size, height: size, background: tint(img) }}>
      {img.pending
        ? <span className="absolute inset-0 animate-pulse bg-gradient-to-br from-white/[0.06] to-transparent" />
        : <img src={thumb(img.id, 480)} className="h-full w-full object-cover" alt="" loading="lazy" />}
    </Tag>
  )
}

export function Arrows({ prev, next, g }: { prev?: Img; next?: Img; g: Gallery }) {
  return (
    <>
      {prev && <button onClick={() => g.setOpenId(prev.id)} aria-label="Previous" className={`absolute left-4 top-1/2 z-20 hidden size-11 -translate-y-1/2 place-items-center rounded-full md:grid ${glass}`}><CaretLeft size={18} weight="bold" /></button>}
      {next && <button onClick={() => g.setOpenId(next.id)} aria-label="Next" className={`absolute right-4 top-1/2 z-20 hidden size-11 -translate-y-1/2 place-items-center rounded-full md:grid ${glass}`}><CaretRight size={18} weight="bold" /></button>}
    </>
  )
}
