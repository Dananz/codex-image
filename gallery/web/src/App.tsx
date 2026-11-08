import { useCallback, useEffect, useRef, useState } from 'react'
import { MagnifyingGlass } from '@phosphor-icons/react'
import NumberFlow from '@number-flow/react'
import { ServerInfo } from './server'
import { Root, Toast, Empty, Loading, Unreachable, useWidth, dayLabel } from './parts'
import { MasonryFeed, Timeline, Logo, FilterTabs, SearchField, StarFilter, ImportBtn, glass, type Jump, type FeedState } from './feed'
import { Editorial } from './page'
import { useGallery } from './state'

const weekday = (d: Date) => d.toLocaleString('en', { weekday: 'short' })
const month = (d: Date) => d.toLocaleString('en', { month: 'short' })
const SPRING = 'linear(0, 0.157 4%, 0.505 9.6%, 0.773 15.3%, 0.931 21.1%, 1.012 27.3%, 1.032 33.7%, 1.025 40.2%, 1.008 50%, 0.998 62%, 1)'
const FOLDED_W = 232

export default function App() {
  const jump = useRef<Jump | null>(null)
  const highlightRef = useRef<(id: string) => void>(() => {})
  const onClosed = useCallback((id: string) => highlightRef.current(id), [])
  const g = useGallery({ onClosed })
  highlightRef.current = id => { g.setHighlight(id); requestAnimationFrame(() => jump.current?.toId(id)) }

  const [st, setSt] = useState<FeedState>({ offset: 0, fraction: 0, dir: 1 })
  const [pinned, setPinned] = useState(false)
  const [foldWanted, setFoldWanted] = useState(false)
  const anchor = useRef(0)
  useEffect(() => {
    const y = st.offset
    if (y < 120) { setFoldWanted(false); anchor.current = y; return }
    if (foldWanted ? y > anchor.current : y < anchor.current) anchor.current = y
    if (!foldWanted && y - anchor.current > 28) { setFoldWanted(true); anchor.current = y }
    if (foldWanted && anchor.current - y > 28) { setFoldWanted(false); anchor.current = y }
  }, [st.offset]) // eslint-disable-line react-hooks/exhaustive-deps
  const folded = !pinned && !g.query && foldWanted
  const back = () => g.setOpenId(null)

  const [stage, stageW] = useWidth<HTMLDivElement>()
  const day = st.date ? dayLabel(st.date) : ''
  const openW = Math.min(760, Math.max(0, stageW - 32))
  const width = folded ? Math.min(FOLDED_W, openW) : openW

  const foldedContent = (
    <span className="flex h-9 items-center gap-2.5 whitespace-nowrap pl-0.5 pr-2.5" style={{ width: FOLDED_W - 12 }}>
      <Logo compact round />
      <span className="flex-1 text-center text-[13px] font-medium tabular-nums">
        {!st.date || day === 'Today' || day === 'Yesterday' ? day : (
          <>{weekday(st.date)}, {month(st.date)} <NumberFlow value={st.date.getDate()} trend={st.dir === 1 ? -1 : 1} /></>
        )}
      </span>
      <MagnifyingGlass size={15} className="text-white/50" />
    </span>
  )

  const body = g.status === 'loading' ? <Loading />
    : g.status === 'error' ? <Unreachable g={g} />
    : g.list.length === 0 ? <Empty g={g} />
    : <MasonryFeed g={g} jump={jump} onState={setSt} top={84} right={64} />

  return (
    <Root>
      <div className="relative flex-1" ref={stage}>
        {body}
        <div className="pointer-events-none absolute inset-x-0 top-0 z-10 h-32 bg-gradient-to-b from-[#0b0b0d] via-[#0b0b0d]/75 to-transparent" />
        <div data-shoot="island"
          style={{ width: width || undefined, ['--spring' as string]: SPRING }}
          className={`absolute left-1/2 top-4 z-30 h-12 -translate-x-1/2 overflow-hidden rounded-full p-1.5 transition-[width] duration-[640ms] [transition-timing-function:var(--spring)] motion-reduce:transition-none ${glass}`}>
          <button onClick={() => setPinned(true)} data-shoot="unfold" tabIndex={folded ? 0 : -1} aria-hidden={!folded}
            className={`absolute inset-y-1.5 left-1.5 flex origin-left items-center transition-[opacity,filter,transform] ease-out motion-reduce:transition-none ${folded ? 'opacity-100 blur-0 scale-100 duration-300 delay-[120ms]' : 'pointer-events-none scale-[0.92] opacity-0 blur-[3px] duration-150'}`}>
            {foldedContent}
          </button>
          <div className={`flex h-9 origin-left items-center gap-1.5 transition-[opacity,filter,transform] ease-out motion-reduce:transition-none ${folded ? 'pointer-events-none scale-[0.97] opacity-0 blur-[3px] duration-150' : 'scale-100 opacity-100 blur-0 duration-300 delay-[140ms]'}`} style={{ width: openW ? openW - 12 : undefined }}>
            <ServerInfo g={g} container={stage} />
            <SearchField g={g} className="flex-1" onBlur={() => setPinned(false)} />
            <span className="hidden sm:block"><FilterTabs g={g} sliding /></span>
            <StarFilter g={g} />
            <ImportBtn g={g} container={stage} />
          </div>
        </div>
        {g.status === 'ready' && g.list.length > 0 && <Timeline g={g} state={st} jump={jump} className="bottom-6 right-3 top-24" />}
      </div>
      {g.openId && <Editorial g={g} back={back} />}
      <Toast g={g} />
    </Root>
  )
}
