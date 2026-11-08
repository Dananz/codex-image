import { useState, type ReactNode, type RefObject } from 'react'
import { Drawer } from '@base-ui/react/drawer'
import { Sparkle, X, ImageBroken, Plus, CircleNotch, WarningCircle } from '@phosphor-icons/react'
import { api, refSrc, type Img } from './api'
import type { Gallery } from './state'

export function DrawerShell({ g, container, wide, title = 'Edit and regenerate', children, footer }: {
  g: Gallery; container: RefObject<HTMLElement | null>; wide: boolean; title?: string; children: ReactNode; footer: ReactNode
}) {
  const side = wide
  const popup = side
    ? 'absolute right-3 top-3 bottom-3 flex w-[420px] max-w-[calc(100%-24px)] flex-col overflow-hidden rounded-[24px] [transform:translateX(var(--drawer-swipe-movement-x))] data-ending-style:[transform:translateX(calc(100%+24px))] data-starting-style:[transform:translateX(calc(100%+24px))]'
    : '-mb-[3rem] flex max-h-[calc(86%+3rem)] w-full flex-col overflow-hidden rounded-t-[28px] pb-[3rem] [transform:translateY(var(--drawer-swipe-movement-y))] data-ending-style:[transform:translateY(calc(100%-3rem+2px))] data-starting-style:[transform:translateY(calc(100%-3rem+2px))]'
  return (
    <Drawer.Root open={!!g.composer} onOpenChange={o => { if (!o) g.setComposer(null) }} swipeDirection={side ? 'right' : 'down'}>
      <Drawer.Portal container={container}>
        <Drawer.Backdrop className="absolute inset-0 z-[60] bg-black opacity-[calc(0.55*(1-var(--drawer-swipe-progress)))] transition-opacity duration-[450ms] ease-[cubic-bezier(0.32,0.72,0,1)] data-swiping:duration-0 data-ending-style:opacity-0 data-starting-style:opacity-0" />
        <Drawer.Viewport className={`absolute inset-0 z-[61] flex ${side ? '' : 'items-end justify-center'}`}>
          <Drawer.Popup data-shoot="composer"
            className={`${popup} border border-white/[0.08] bg-[#141417] text-white shadow-[0_-12px_48px_rgba(0,0,0,.5)] outline-none transition-transform duration-[450ms] ease-[cubic-bezier(0.32,0.72,0,1)] data-swiping:select-none data-ending-style:duration-[calc(var(--drawer-swipe-strength)*400ms)]`}>
            {!side && <div className="flex justify-center pb-1 pt-2.5"><span className="h-1 w-9 rounded-full bg-white/25" /></div>}
            <Drawer.Content className="flex min-h-0 flex-1 flex-col">
              <div className={`flex items-center justify-between px-5 ${side ? 'pt-5' : 'pt-1'}`}>
                <Drawer.Title className="text-[16px] font-semibold tracking-[-0.01em]">{title}</Drawer.Title>
                <Drawer.Close aria-label="Close" className="grid size-9 place-items-center rounded-full text-white/55 hover:bg-white/[0.07] hover:text-white"><X size={16} /></Drawer.Close>
              </div>
              <div className="min-h-0 flex-1 overflow-y-auto px-5 pb-4 pt-4">{children}</div>
              <div className="border-t border-white/[0.06] p-4">{footer}</div>
            </Drawer.Content>
          </Drawer.Popup>
        </Drawer.Viewport>
      </Drawer.Portal>
    </Drawer.Root>
  )
}

export function GenerateButton({ onClick, label = 'Generate', disabled }: { onClick: () => void; label?: string; disabled?: boolean }) {
  return (
    <button onClick={onClick} data-shoot="run" disabled={disabled}
      className="flex h-12 w-full items-center justify-center gap-2 rounded-2xl bg-white text-[15px] font-medium text-black transition hover:bg-white/90 active:scale-[0.99] disabled:opacity-60 disabled:hover:bg-white disabled:active:scale-100">
      <Sparkle size={16} weight="fill" />{label}
    </button>
  )
}

export function useComposing(g: Gallery) {
  const img = g.composer
  const [last, setLast] = useState(img)
  if (img && img !== last) setLast(img)
  return img ?? last
}

export type Added = { key: string; url: string; id: string | null; failed?: boolean }
type SetAdded = (f: (x: Added[]) => Added[]) => void

export function RefEditor({ img, keep, setKeep, added, setAdded }: {
  img: Img; keep: string[]; setKeep: (f: (x: string[]) => string[]) => void
  added?: Added[]; setAdded?: SetAdded
}) {
  const addFiles = (files: FileList | null) => {
    if (!files || !setAdded) return
    for (const f of [...files].filter(f => f.type.startsWith('image/'))) {
      const key = `${f.name}-${f.lastModified}-${Math.random()}`
      setAdded(x => [...x, { key, url: URL.createObjectURL(f), id: null }])
      api.upload(f)
        .then(({ id }) => setAdded(x => x.map(a => (a.key === key ? { ...a, id } : a))))
        .catch(() => setAdded(x => x.map(a => (a.key === key ? { ...a, failed: true } : a))))
    }
  }
  const drop = (a: Added) => { URL.revokeObjectURL(a.url); setAdded?.(x => x.filter(y => y.key !== a.key)) }
  return (
    <div className="flex flex-wrap gap-2"
      onDragOver={e => { if (setAdded) e.preventDefault() }}
      onDrop={e => { if (setAdded) { e.preventDefault(); addFiles(e.dataTransfer.files) } }}>
      {img.refs.map((r, i) => r.missing ? (
        <span key={r.id} title={r.path} className="grid size-16 place-items-center rounded-xl border border-dashed border-white/15 text-white/35"><span className="text-center"><ImageBroken size={16} className="mx-auto" /><span className="text-[10px]">missing</span></span></span>
      ) : keep.includes(r.id) && (
        <span key={r.id} className="relative">
          <img src={refSrc(r.id)} title={r.path} className="size-16 rounded-xl bg-white/[0.04] object-cover" alt={`Reference ${i + 1}`} />
          <button onClick={() => setKeep(x => x.filter(id => id !== r.id))} aria-label="Remove reference" className="absolute -right-1.5 -top-1.5 grid size-6 place-items-center rounded-full bg-[#2a2a2f] text-white/80 ring-2 ring-[#141417] hover:text-white"><X size={11} weight="bold" /></button>
        </span>
      ))}
      {added?.map(a => (
        <span key={a.key} className="relative" title={a.failed ? 'Upload failed' : a.id ? undefined : 'Uploading'}>
          <img src={a.url} className={`size-16 rounded-xl object-cover ${a.id ? '' : 'opacity-50'} ${a.failed ? 'ring-2 ring-rose-400/70' : ''}`} alt="Added reference" />
          {!a.id && (
            <span className="pointer-events-none absolute inset-0 grid place-items-center">
              {a.failed ? <WarningCircle size={18} weight="fill" className="text-rose-300" /> : <CircleNotch size={16} className="animate-spin text-white/85" />}
            </span>
          )}
          <button onClick={() => drop(a)} aria-label="Remove reference" className="absolute -right-1.5 -top-1.5 grid size-6 place-items-center rounded-full bg-[#2a2a2f] text-white/80 ring-2 ring-[#141417] hover:text-white"><X size={11} weight="bold" /></button>
        </span>
      ))}
      {setAdded ? (
        <label data-shoot="add-ref" className="grid size-16 cursor-pointer place-items-center rounded-xl border border-dashed border-white/20 text-white/50 transition hover:border-white/40 hover:bg-white/[0.04] hover:text-white" title="Add references (or drop images here)">
          <span className="text-center"><Plus size={16} className="mx-auto" /><span className="text-[10px]">Add</span></span>
          <input type="file" accept="image/*" multiple className="sr-only" onChange={e => { addFiles(e.target.files); e.target.value = '' }} />
        </label>
      ) : img.refs.length === 0 && <span className="text-[13px] text-white/40">None</span>}
    </div>
  )
}

export const Label = ({ children }: { children: ReactNode }) => <span className="mb-2 block text-[12px] text-white/45">{children}</span>
