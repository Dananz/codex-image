import type { Plugin } from 'vite'
import type { IncomingMessage, ServerResponse } from 'node:http'
import type { ApiImage } from '../src/api'

function rng(seed: number) {
  return () => {
    seed |= 0; seed = (seed + 0x6d2b79f5) | 0
    let t = Math.imul(seed ^ (seed >>> 15), 1 | seed)
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296
  }
}

const PROMPTS = [
  'Minimal emerald app icon, soft glass bevel, centered glyph, dark background',
  'Cozy balcony at golden hour, plants, warm film grain, 35mm',
  'Product shot of a matte black wireless speaker on travertine, studio light',
  'Isometric illustration of a tiny coffee kiosk, pastel palette, clean lines',
  'Abstract gradient mesh, deep indigo into coral, subtle noise, wallpaper',
  'Fuel station price board at night, neon digits, rain on asphalt',
  'Watercolor map of a Mediterranean old town, muted tones, hand lettering',
  'Mechanical keyboard close-up, translucent keycaps, rim light, macro',
]
const SIZES: [number, number][] = [[1024, 1024], [1024, 1536], [1536, 1024], [1672, 941], [941, 1672]]
const ALPHA = 'abcdefghjkmnpqrstuvwxyz23456789'

const r = rng(7)
const now = Date.now()
const images = new Map<string, ApiImage>()
let t = now - 3600e3
for (let i = 0; i < 900; i++) {
  t -= r() < 0.7 ? r() * 9 * 60e3 : r() * 30 * 3600e3
  const [w, h] = SIZES[Math.floor(r() * SIZES.length)]
  const id = Math.floor(r() * 2 ** 32).toString(16).padStart(8, '0') + i.toString(16).padStart(8, '0')
  const short = Array.from({ length: 6 }, () => ALPHA[Math.floor(r() * ALPHA.length)]).join('')
  const source = r() < 0.38 ? 'skill' : 'codex'
  const nRefs = r() < 0.55 ? 0 : r() < 0.7 ? 1 : 2
  images.set(id, {
    id, short, w, h, bytes: Math.floor(1e6 + r() * 2e6),
    created: new Date(t).toISOString(),
    prompt: PROMPTS[Math.floor(r() * PROMPTS.length)],
    context: 'make a few options, keep them calm',
    source, mac: r() < 0.8 ? 'Mac Studio' : 'MoltBook',
    quality: source === 'skill' ? 'high' : 'auto',
    session: '01a1b58c-' + id.slice(0, 4),
    files: [`/Users/me/.codex/generated_images/x/${id}.png`],
    refs: Array.from({ length: nRefs }, (_, k) => ({ id: `ref${i}${k}`, missing: r() < 0.3, path: `/tmp/refs/input-${k + 1}.png` })),
    parent: null, starred: i % 17 === 3, pending: false,
  })
}
const list = [...images.values()]
for (let i = 1; i < list.length; i++) if (r() < 0.12) list[i - 1].parent = list[i].id

const clients = new Set<ServerResponse>()
const send = (event: string, data: unknown) => { for (const c of clients) c.write(`event: ${event}\ndata: ${JSON.stringify(data)}\n\n`) }

function svg(seed: string, w: number, h: number) {
  const hue = parseInt(seed.slice(0, 4), 16) % 360 || 200
  return `<svg xmlns="http://www.w3.org/2000/svg" width="${w}" height="${h}" viewBox="0 0 ${w} ${h}"><defs><linearGradient id="g" x1="0" y1="0" x2="1" y2="1"><stop offset="0" stop-color="hsl(${hue} 55% 55%)"/><stop offset="1" stop-color="hsl(${(hue + 60) % 360} 45% 22%)"/></linearGradient></defs><rect width="100%" height="100%" fill="url(#g)"/><circle cx="${w * 0.62}" cy="${h * 0.4}" r="${Math.min(w, h) * 0.22}" fill="hsl(${(hue + 180) % 360} 60% 70% / .55)"/></svg>`
}

const body = (req: IncomingMessage) => new Promise<string>(res => { let s = ''; req.on('data', c => { s += c }); req.on('end', () => res(s)) })
const json = (res: ServerResponse, data: unknown, status = 200) => { res.statusCode = status; res.setHeader('Content-Type', 'application/json'); res.end(JSON.stringify(data)) }

export function mockApi(): Plugin {
  return {
    name: 'gallery-mock-api',
    apply: 'serve',
    configureServer(server) {
      server.middlewares.use(async (req, res, next) => {
        const url = new URL(req.url ?? '/', 'http://x')
        const p = url.pathname
        const m = req.method ?? 'GET'
        let k: RegExpMatchArray | null
        if (p === '/api/images' && m === 'GET') return json(res, { total: images.size, items: [...images.values()].sort((a, b) => b.created.localeCompare(a.created)) })
        if ((k = p.match(/^\/api\/short\/(\w+)$/))) { const hit = [...images.values()].find(i => i.short === k![1]); return hit ? json(res, { id: hit.id }) : json(res, { error: 'not found' }, 404) }
        if ((k = p.match(/^\/api\/images\/(\w+)\/star$/))) { const img = images.get(k[1])!; img.starred = JSON.parse(await body(req)).starred; send('image', img); return json(res, img) }
        if ((k = p.match(/^\/api\/images\/(\w+)\/restore$/))) { const img = images.get(k[1])!; send('image', img); return json(res, img) }
        if ((k = p.match(/^\/api\/images\/(\w+)$/)) && m === 'DELETE') { send('deleted', { id: k[1] }); return json(res, { ok: true }) }
        if ((k = p.match(/^\/(thumb|file|ref)\/(\w+)$/))) {
          const img = images.get(k[2])
          const scale = k[1] === 'thumb' ? Number(url.searchParams.get('w') ?? 480) / (img?.w ?? 1024) : 0.25
          res.setHeader('Content-Type', 'image/svg+xml')
          return res.end(svg(k[2], Math.round((img?.w ?? 512) * scale), Math.round((img?.h ?? 512) * scale)))
        }
        if (p === '/api/uploads') { await body(req); return json(res, { id: `up${Date.now()}` }) }
        if (p === '/api/regenerate') {
          const b = JSON.parse(await body(req))
          const from = images.get(b.from)!
          const jobs: string[] = []
          for (let i = 0; i < b.count; i++) {
            const [w, h] = b.size.split('x').map(Number)
            const id = Math.floor(Math.random() * 2 ** 32).toString(16).padStart(8, '0') + 'ffff' + String(i).padStart(4, '0')
            const img: ApiImage = { ...from, id, short: id.slice(0, 6), w, h, created: new Date().toISOString(), prompt: b.prompt, parent: from.id, pending: true, starred: false, source: 'skill' }
            images.set(id, img)
            jobs.push(`job${id}`)
            send('image', img)
            setTimeout(() => { img.pending = false; send('image', img); send('job', { id: `job${id}`, kind: 'regenerate', status: 'done', message: '', image: id }) }, 3000 + i * 1500)
          }
          return json(res, { jobs })
        }
        if (p === '/api/server') return json(res, {
          host: 'Mac Studio', url: 'https://studio.tail08ad7c.ts.net:8445/', local: 'http://127.0.0.1:8765/', tailnet_only: true,
          idle_limit_s: 14400, idle_left_s: 11520, watching: true,
          sources: [
            { kind: 'codex', path: '~/.codex/generated_images', images: images.size, bytes: 2469606400 },
            { kind: 'skill', label: 'Skill runs (codex-image)', runs: 784 },
            { kind: 'own', path: '~/.codex-image', bytes: 327155712 },
          ],
          imports: [{ name: 'MoltBook', last: new Date(now - 3 * 864e5).toISOString() }, { name: 'M4 laptop', last: null }],
        })
        if (p === '/api/server/stop') return json(res, { ok: true })
        if (p === '/api/hosts') return json(res, [
          { name: 'MoltBook', ssh: 'tomerdanan@m1', last_import: new Date(now - 3 * 864e5).toISOString(), new: 412 },
          { name: 'M4 laptop', ssh: 'tomer@m4', last_import: null, new: 57 },
        ])
        if (p === '/api/import') { const b = JSON.parse(await body(req)); const job = `imp${Date.now()}`; setTimeout(() => send('job', { id: job, kind: 'import', status: 'done', message: `Imported 12 images from ${b.host}`, image: null }), 2500); return json(res, { job }) }
        if (p === '/api/events') {
          res.writeHead(200, { 'Content-Type': 'text/event-stream', 'Cache-Control': 'no-store', Connection: 'keep-alive' })
          res.write(': hi\n\n')
          clients.add(res)
          const ping = setInterval(() => res.write(': ping\n\n'), 20000)
          req.on('close', () => { clearInterval(ping); clients.delete(res) })
          return
        }
        next()
      })
    },
  }
}
