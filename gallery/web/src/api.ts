export type Ref = { id: string; missing: boolean; path: string }

export type ApiImage = {
  id: string
  short: string
  w: number
  h: number
  bytes: number
  created: string
  prompt: string
  context: string
  source: 'codex' | 'skill'
  mac: string
  quality: string
  session: string
  files: string[]
  refs: Ref[]
  parent: string | null
  starred: boolean
  pending: boolean
}

export type Img = Omit<ApiImage, 'created' | 'parent'> & { date: Date; parent?: string; hue: number }

export type Job = { id: string; kind: 'regenerate' | 'import'; status: 'queued' | 'running' | 'done' | 'failed'; message?: string; image: string | null }

export type ServerInfo = {
  host: string
  url: string | null
  local: string
  tailnet_only: boolean
  idle_limit_s: number | null
  idle_left_s: number | null
  watching: boolean
  sources: (
    | { kind: 'codex'; path: string; images: number; bytes: number }
    | { kind: 'skill'; label: string; runs: number }
    | { kind: 'own'; path: string; bytes: number }
  )[]
  imports: { name: string; last: string | null }[]
}

export type Host = { name: string; ssh: string; last_import: string | null; new: number | null; online: boolean | null }

export type Strength = 'subtle' | 'balanced' | 'strong' | 'new'

export type RegenerateBody = {
  from: string
  prompt: string
  strength: Strength
  count: number
  size: string
  scale: number
  refs: string[]
}

function hueOf(id: string) {
  return parseInt(id.slice(0, 4), 16) % 360 || 0
}

export function toImg(a: ApiImage): Img {
  const { created, parent, ...rest } = a
  return {
    ...rest,
    w: a.w || 1024,
    h: a.h || 1024,
    refs: a.refs ?? [],
    prompt: a.prompt ?? '',
    context: a.context ?? '',
    date: new Date(created),
    parent: parent ?? undefined,
    hue: hueOf(a.id),
  }
}

export class ApiError extends Error {
  constructor(public status: number, message: string) { super(message) }
}

async function call<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(path, init)
  if (!res.ok) {
    let msg = `${res.status} ${res.statusText}`
    try { const j = await res.json(); if (j?.error) msg = String(j.error) } catch { /* not json */ }
    throw new ApiError(res.status, msg)
  }
  return res.json() as Promise<T>
}

const json = (method: string, body?: unknown): RequestInit => ({
  method,
  headers: body === undefined ? undefined : { 'Content-Type': 'application/json' },
  body: body === undefined ? undefined : JSON.stringify(body),
})

export const api = {
  images: () => call<{ total: number; items: ApiImage[] }>('/api/images'),
  short: (short: string) => call<{ id: string }>(`/api/short/${encodeURIComponent(short)}`),
  image: (id: string) => call<{ image: ApiImage; parent: ApiImage | null; children: ApiImage[] }>(`/api/images/${id}`),
  star: (id: string, starred: boolean) => call<ApiImage>(`/api/images/${id}/star`, json('POST', { starred })),
  remove: (id: string) => call<{ ok: boolean }>(`/api/images/${id}`, json('DELETE')),
  restore: (id: string) => call<ApiImage>(`/api/images/${id}/restore`, json('POST')),
  upload: (file: Blob) => call<{ id: string }>('/api/uploads', { method: 'POST', headers: { 'Content-Type': file.type || 'image/png' }, body: file }),
  regenerate: (body: RegenerateBody) => call<{ jobs: string[] }>('/api/regenerate', json('POST', body)),
  server: () => call<ServerInfo>('/api/server'),
  stop: () => call<{ ok: boolean }>('/api/server/stop', json('POST')),
  hosts: () => call<Host[]>('/api/hosts'),
  import: (host: string) => call<{ job: string }>('/api/import', json('POST', { host })),
}

export const thumb = (id: string, w: 480 | 1600 = 480) => `/thumb/${id}?w=${w}`
export const refSrc = (refId: string) => `/ref/${refId}`
export const fileSrc = (id: string, download = false) => `/file/${id}${download ? '?download=1' : ''}`
