import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { api, toImg, type ApiImage, type Img, type Job, type RegenerateBody } from './api'

export type Filter = 'all' | 'skill'
export type Status = 'loading' | 'ready' | 'error'
export type Conn = 'live' | 'down' | 'stopped'
type Toast = { text: string; undo?: () => void; sticky?: boolean }

const parseShort = () => location.pathname.match(/^\/i\/([a-z0-9]+)\/?$/)?.[1] ?? null

function initialShort() {
  const s = parseShort()
  history.scrollRestoration = 'manual'
  if (s) {
    history.replaceState(null, '', '/')
    history.pushState({ open: s }, '', `/i/${s}`)
  } else if (location.pathname !== '/') {
    history.replaceState(null, '', '/')
  }
  return s
}
let startShort = initialShort()

export function useGallery({ onClosed }: { onClosed?: (id: string) => void } = {}) {
  const [byId, setById] = useState<Map<string, Img>>(new Map())
  const [status, setStatus] = useState<Status>('loading')
  const [conn, setConn] = useState<Conn>('live')
  const [hidden, setHidden] = useState<Set<string>>(new Set())
  const [filter, setFilter] = useState<Filter>('all')
  const [query, setQuery] = useState('')
  const [starredOnly, setStarredOnly] = useState(false)
  const [openId, setOpen] = useState<string | null>(null)
  const [highlight, setHl] = useState<string | null>(null)
  const [toast, setToast] = useState<Toast | null>(null)
  const [composer, setComposer] = useState<Img | null>(null)

  const toastTimer = useRef<number | undefined>(undefined)
  const hlTimer = useRef<number | undefined>(undefined)
  const openRef = useRef<string | null>(null)
  const byIdRef = useRef(byId)
  byIdRef.current = byId
  const wantShort = useRef<string | null>(startShort)
  const quietClose = useRef(false)
  const expecting = useRef<Map<string, number>>(new Map())
  const importJobs = useRef<Map<string, string>>(new Map())
  const onClosedRef = useRef(onClosed)
  onClosedRef.current = onClosed
  const stoppedRef = useRef(false)
  const deferredHl = useRef<string | null>(null)

  const showToast = useCallback((text: string, undo?: () => void, sticky?: boolean) => {
    window.clearTimeout(toastTimer.current)
    setToast({ text, undo, sticky })
    if (!sticky) toastTimer.current = window.setTimeout(() => setToast(null), 5000)
  }, [])

  const setHighlight = useCallback((id: string | null) => {
    window.clearTimeout(hlTimer.current)
    setHl(id)
    if (id) hlTimer.current = window.setTimeout(() => setHl(null), 2600)
  }, [])

  const upsert = useCallback((items: Img[]) => {
    setById(m => { const n = new Map(m); for (const i of items) n.set(i.id, i); return n })
  }, [])

  const onImage = useCallback((img: Img) => {
    upsert([img])
    if (img.pending && img.parent && expecting.current.get(img.parent)) {
      const left = expecting.current.get(img.parent)! - 1
      if (left > 0) expecting.current.set(img.parent, left); else expecting.current.delete(img.parent)
      if (!openRef.current) setHighlight(img.id)
      else if (!deferredHl.current) deferredHl.current = img.id
    }
  }, [upsert, setHighlight])

  const load = useCallback(async () => {
    try {
      const r = await api.images()
      setById(new Map(r.items.map(a => { const i = toImg(a); return [i.id, i] as const })))
      setHidden(new Set())
      setStatus('ready')
      return true
    } catch {
      setStatus(s => (s === 'ready' ? s : 'error'))
      return false
    }
  }, [])

  useEffect(() => { void load() }, [load])

  useEffect(() => {
    if (status !== 'error') return
    const t = window.setInterval(() => { void load() }, 5000)
    return () => window.clearInterval(t)
  }, [status, load])

  useEffect(() => {
    if (status !== 'ready') return
    let es: EventSource | null = null
    let retry: number | undefined
    let downTimer: number | undefined
    let dropped = false
    let alive = true
    const connect = () => {
      es = new EventSource('/api/events')
      es.onopen = () => {
        window.clearTimeout(downTimer)
        if (dropped) { dropped = false; void load() }
        stoppedRef.current = false
        setConn('live')
      }
      es.onerror = () => {
        dropped = true
        window.clearTimeout(downTimer)
        if (stoppedRef.current) setConn('stopped')
        else downTimer = window.setTimeout(() => { if (es?.readyState !== EventSource.OPEN) setConn('down') }, 3000)
        if (es?.readyState === EventSource.CLOSED && alive) {
          es.close()
          retry = window.setTimeout(connect, 3000)
        }
      }
      es.addEventListener('image', e => onImage(toImg(JSON.parse((e as MessageEvent).data) as ApiImage)))
      es.addEventListener('deleted', e => {
        const { id } = JSON.parse((e as MessageEvent).data) as { id: string }
        setById(m => { if (!m.has(id)) return m; const n = new Map(m); n.delete(id); return n })
      })
      es.addEventListener('job', e => {
        const job = JSON.parse((e as MessageEvent).data) as Job
        if (job.kind === 'import') {
          const host = importJobs.current.get(job.id) ?? 'the other Mac'
          if (job.status === 'done') { importJobs.current.delete(job.id); showToast(job.message || `Imported from ${host}`) }
          if (job.status === 'failed') { importJobs.current.delete(job.id); showToast(job.message || `Import from ${host} failed`) }
        } else if (job.status === 'failed') {
          showToast(job.message ? `Regenerate failed: ${job.message}` : 'Regenerate failed')
        }
      })
    }
    connect()
    return () => { alive = false; window.clearTimeout(retry); window.clearTimeout(downTimer); es?.close() }
  }, [status, load, onImage, showToast])

  const all = useMemo(() => {
    const out: Img[] = []
    for (const i of byId.values()) if (!hidden.has(i.id)) out.push(i)
    return out.sort((a, b) => b.date.getTime() - a.date.getTime() || (a.id < b.id ? -1 : 1))
  }, [byId, hidden])

  const list = useMemo(() => {
    const q = query.trim().toLowerCase()
    return all.filter(i =>
      (filter === 'all' || i.source === 'skill') &&
      (!starredOnly || i.starred) &&
      (!q || i.prompt.toLowerCase().includes(q) || i.context.toLowerCase().includes(q)))
  }, [all, filter, starredOnly, query])

  const closed = useCallback(() => {
    const id = openRef.current
    openRef.current = null
    setOpen(null)
    setComposer(null)
    if (id && !quietClose.current) onClosedRef.current?.(id)
    quietClose.current = false
    const hl = deferredHl.current
    deferredHl.current = null
    if (hl) onClosedRef.current?.(hl)
  }, [])

  const setOpenId = useCallback((id: string | null) => {
    if (id === null) {
      if (!openRef.current) return
      if (history.state?.open) history.back()
      else { history.replaceState(null, '', '/'); closed() }
      return
    }
    const img = byIdRef.current.get(id)
    if (!img || !img.short) return
    if (openRef.current) history.replaceState({ open: img.short }, '', `/i/${img.short}`)
    else history.pushState({ open: img.short }, '', `/i/${img.short}`)
    openRef.current = id
    setOpen(id)
  }, [closed])

  const openShort = useCallback(async (s: string) => {
    const hit = [...byIdRef.current.values()].find(i => i.short === s)
    if (hit) { openRef.current = hit.id; setOpen(hit.id); return }
    try {
      const { id } = await api.short(s)
      if (byIdRef.current.has(id)) { openRef.current = id; setOpen(id); return }
    } catch { /* not found */ }
    showToast(`No image at /i/${s}`)
    if (history.state?.open) history.back()
    else history.replaceState(null, '', '/')
  }, [showToast])

  useEffect(() => {
    if (status !== 'ready' || !wantShort.current) return
    const s = wantShort.current
    wantShort.current = null
    startShort = null
    void openShort(s)
  }, [status, openShort])

  useEffect(() => {
    if (status === 'ready' && openId && !byId.has(openId)) setOpenId(null)
  }, [status, openId, byId, setOpenId])

  useEffect(() => {
    const onPop = () => {
      const s = parseShort()
      if (!s) { if (openRef.current) closed(); return }
      const img = [...byIdRef.current.values()].find(i => i.short === s)
      if (img) { openRef.current = img.id; setOpen(img.id) }
      else void openShort(s)
    }
    window.addEventListener('popstate', onPop)
    return () => window.removeEventListener('popstate', onPop)
  }, [closed, openShort])

  const toggleStar = useCallback((id: string) => {
    const img = byIdRef.current.get(id)
    if (!img) return
    const starred = !img.starred
    upsert([{ ...img, starred }])
    api.star(id, starred).then(a => upsert([toImg(a)])).catch(() => {
      const cur = byIdRef.current.get(id)
      if (cur) upsert([{ ...cur, starred: !starred }])
      showToast('Could not save the star')
    })
  }, [upsert, showToast])

  const remove = useCallback((id: string) => {
    const idx = list.findIndex(i => i.id === id)
    const next = list[idx + 1] ?? list[idx - 1]
    setHidden(s => new Set(s).add(id))
    if (openRef.current === id) {
      if (next) setOpenId(next.id)
      else setOpenId(null)
    }
    const unhide = () => setHidden(s => { const n = new Set(s); n.delete(id); return n })
    api.remove(id).then(() => {
      showToast('Deleted from ~/.codex/generated_images', () => {
        setToast(null)
        api.restore(id).then(a => { upsert([toImg(a)]); unhide() }).catch(() => showToast('Could not restore it'))
      })
    }).catch(() => { unhide(); showToast('Could not delete it') })
  }, [list, setOpenId, showToast, upsert])

  const regenerate = useCallback(async (body: RegenerateBody) => {
    expecting.current.set(body.from, (expecting.current.get(body.from) ?? 0) + body.count)
    try {
      await api.regenerate(body)
    } catch (e) {
      expecting.current.delete(body.from)
      showToast(`Could not start: ${(e as Error).message}`)
      return false
    }
    setComposer(null)
    if (openRef.current) { quietClose.current = true; setOpenId(null) }
    showToast(body.count > 1 ? `Generating ${body.count} with Codex, they appear here when ready` : 'Generating with Codex, it appears here when ready')
    return true
  }, [setOpenId, showToast])

  const importFrom = useCallback(async (host: string, count?: number | null) => {
    try {
      const { job } = await api.import(host)
      importJobs.current.set(job, host)
      showToast(count ? `Importing ${count.toLocaleString()} from ${host}` : `Importing from ${host}`)
    } catch (e) {
      showToast(`Could not import from ${host}: ${(e as Error).message}`)
    }
  }, [showToast])

  const stopServer = useCallback(async () => {
    try {
      await api.stop()
      stoppedRef.current = true
      setConn('stopped')
      showToast('Gallery server stopped. The link stops working until you start it again.', undefined, true)
    } catch (e) {
      showToast(`Could not stop the server: ${(e as Error).message}`)
    }
  }, [showToast])

  useEffect(() => {
    if (conn === 'live' && toast?.sticky) setToast(null)
  }, [conn]) // eslint-disable-line react-hooks/exhaustive-deps

  return {
    status, conn, retry: load,
    list, all_by_id: byId, total: all.length,
    filter, setFilter, query, setQuery, starredOnly, setStarredOnly, toggleStar,
    openId, setOpenId, highlight, setHighlight, toast, showToast, remove,
    composer, setComposer, regenerate, importFrom, stopServer,
  }
}
export type Gallery = ReturnType<typeof useGallery>

export function lineage(img: Img, byId: Map<string, Img>) {
  const parent = img.parent ? byId.get(img.parent) : undefined
  const children: Img[] = []
  for (const x of byId.values()) if (x.parent === img.id) children.push(x)
  children.sort((a, b) => a.date.getTime() - b.date.getTime())
  return { parent, children }
}
