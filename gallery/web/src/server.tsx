import type React from 'react'
import { useEffect, useRef, useState, type RefObject } from 'react'
import { Popover } from '@base-ui/react/popover'
import { Sparkle, Copy, Check, Desktop, Laptop, FolderSimple, Database, Globe, Power, Clock } from '@phosphor-icons/react'
import { api, type ServerInfo as Info } from './api'
import { glass } from './feed'
import { bytesLabel } from './parts'
import { copyText } from './clipboard'
import type { Gallery } from './state'

const bare = (u: string) => u.replace(/^https?:\/\//, '').replace(/\/$/, '')

function hm(s: number) {
  const h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60)
  return h ? `${h} h ${m} m` : `${m} m`
}
function limit(s: number) {
  if (s % 3600 === 0) return `${s / 3600} h`
  return hm(s)
}
const shortDate = (iso: string) => new Date(iso).toLocaleString('en', { month: 'short', day: 'numeric' })

export function ServerInfo({ g, container }: { g: Gallery; container: RefObject<HTMLElement | null> }) {
  const [copied, setCopied] = useState(false)
  const [open, setOpen] = useState(false)
  const [info, setInfo] = useState<Info | null>(null)
  const [error, setError] = useState<string | null>(null)
  const popup = useRef<HTMLDivElement>(null)

  useEffect(() => {
    if (!open && info) return
    let live = true
    api.server().then(i => { if (live) { setInfo(i); setError(null) } }).catch(e => { if (live) setError((e as Error).message) })
    return () => { live = false }
  }, [open]) // eslint-disable-line react-hooks/exhaustive-deps

  const link = info ? info.url || info.local : ''
  const copy = () => {
    if (!link) return
    void copyText(link).then(() => { setCopied(true); window.setTimeout(() => setCopied(false), 1300) })
  }
  const down = g.conn !== 'live' || !!error

  return (
    <Popover.Root open={open} onOpenChange={setOpen}>
      <Popover.Trigger data-shoot="server" aria-label="Server info"
        className="grid size-9 shrink-0 place-items-center rounded-full bg-white text-black outline-none transition hover:scale-105 active:scale-95 focus-visible:ring-2 focus-visible:ring-sky-400 data-popup-open:scale-95">
        <Sparkle size={16} weight="fill" />
      </Popover.Trigger>
      <Popover.Portal container={container}>
        <Popover.Positioner side="bottom" align="start" sideOffset={12} className="z-50">
          <Popover.Popup ref={popup} initialFocus={popup} data-opened-by="[data-shoot=server]"
            tabIndex={-1} className={`w-[340px] max-w-[calc(100vw-32px)] origin-[var(--transform-origin)] rounded-[22px] p-1.5 text-[13px] text-white outline-none ${glass} bg-[#16161a]/60! transition-[opacity,transform,filter] duration-[260ms] ease-[cubic-bezier(.2,.9,.25,1.15)] data-starting-style:scale-[0.94] data-starting-style:opacity-0 data-starting-style:blur-[2px] data-ending-style:scale-[0.96] data-ending-style:opacity-0 data-ending-style:duration-150 data-ending-style:ease-in motion-reduce:transition-none`}>
            <div className="flex items-center gap-3 px-3 pb-3 pt-2.5">
              <span className="grid size-9 place-items-center rounded-xl bg-white/[0.08]"><Desktop size={18} /></span>
              <div className="min-w-0 flex-1">
                <Popover.Title className="truncate text-[14px] font-semibold">{info?.host ?? (down ? 'Gallery server' : 'Loading')}</Popover.Title>
                {down ? (
                  <p className="flex items-center gap-1.5 text-[12px] text-rose-300"><span className="size-1.5 rounded-full bg-rose-400" />{g.conn === 'stopped' ? 'Stopped' : 'Not reachable'}</p>
                ) : (
                  <p className="flex items-center gap-1.5 text-[12px] text-emerald-300"><span className="size-1.5 rounded-full bg-emerald-400" />{info && !info.watching ? 'Serving' : 'Serving, watching for new images'}</p>
                )}
              </div>
            </div>

            {info && (
              <>
                <div className="rounded-2xl bg-white/[0.04] p-1">
                  <button onClick={copy} className="flex w-full items-center gap-2.5 rounded-xl px-2.5 py-2 text-left hover:bg-white/[0.06]" data-shoot="copy-server-link">
                    <Globe size={16} className="shrink-0 text-white/55" />
                    <span className="min-w-0 flex-1">
                      <span className="block truncate font-[Geist_Mono] text-[12px] text-white/90">{bare(link)}</span>
                      <span className="block text-[11px] text-white/40">{info.url ? (info.tailnet_only ? 'Your tailnet only (tailscale serve)' : 'Shared link') : 'This Mac only'}</span>
                    </span>
                    {copied ? <Check size={15} className="text-emerald-300" /> : <Copy size={15} className="text-white/45" />}
                  </button>
                  {info.idle_limit_s
                    ? <Row icon={<Clock size={16} />} label={`Stops after ${limit(info.idle_limit_s)} idle`} value={info.idle_left_s != null ? `${hm(Math.max(0, info.idle_left_s))} left` : undefined} />
                    : <Row icon={<Clock size={16} />} label="Runs until you stop it" />}
                </div>

                <p className="px-3 pb-1.5 pt-3 text-[11px] font-medium uppercase tracking-[0.08em] text-white/35">Reading</p>
                <div className="space-y-0.5 px-1 pb-1">
                  {info.sources.map((s, i) => s.kind === 'codex'
                    ? <Row key={i} icon={<FolderSimple size={16} />} label={s.path} sub={`${s.images.toLocaleString()} images · ${bytesLabel(s.bytes)}`} mono />
                    : s.kind === 'skill'
                      ? <Row key={i} icon={<Sparkle size={16} />} label={s.label} value={`${s.runs.toLocaleString()} runs`} />
                      : <Row key={i} icon={<Database size={16} />} label={s.path} sub={`Index, thumbnails and references · ${bytesLabel(s.bytes)}`} mono />)}
                  {info.imports.map(m => <Row key={m.name} icon={<Laptop size={16} />} label={`Imported: ${m.name}`} value={m.last ? shortDate(m.last) : 'never'} />)}
                </div>
              </>
            )}
            {!info && error && <p className="px-3 pb-3 text-[12px] text-white/45">Could not load server info: {error}</p>}

            {g.conn !== 'stopped' && !error && (
              <div className="mt-1 border-t border-white/[0.06] px-1 pt-1.5">
                <Popover.Close onClick={() => void g.stopServer()}
                  className="flex h-10 w-full items-center gap-2.5 rounded-xl px-2.5 text-left text-rose-300 hover:bg-rose-500/10">
                  <Power size={16} />Stop the server
                </Popover.Close>
              </div>
            )}
          </Popover.Popup>
        </Popover.Positioner>
      </Popover.Portal>
    </Popover.Root>
  )
}

function Row({ icon, label, value, sub, mono }: { icon: React.ReactNode; label: string; value?: string; sub?: string; mono?: boolean }) {
  return (
    <div className="flex items-center gap-2.5 rounded-xl px-2.5 py-2">
      <span className="shrink-0 text-white/50">{icon}</span>
      <span className="min-w-0 flex-1">
        <span className={`block truncate text-white/80 ${mono ? 'font-[Geist_Mono] text-[12px]' : ''}`}>{label}</span>
        {sub && <span className="block text-[11px] text-white/40">{sub}</span>}
      </span>
      {value && <span className="shrink-0 text-[12px] text-white/45">{value}</span>}
    </div>
  )
}
