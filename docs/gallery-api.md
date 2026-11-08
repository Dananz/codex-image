# Gallery: server and API contract

The gallery is a local web app for every image Codex made on this Mac. A Python server
(`gallery/server/`, standard library plus Pillow) indexes the images and serves a prebuilt
React app (`gallery/dist/`, built from `gallery/web/`). Both halves follow this file.

## Data on disk

Everything the gallery owns lives in `~/.codex-image/` (`CODEX_IMAGE_HOME` overrides it, tests use it):

| Path | What |
|---|---|
| `gallery.db` | SQLite: images, files, refs, stars, lineage, short ids, index cursors |
| `thumbs/<id>-<w>.webp` | thumbnails, made on first request, `w` is 480 or 1600 |
| `refs/<sha256>.<ext>` | copies of reference images, made while the original still exists |
| `uploads/<sha256>.<ext>` | references the user added in the regenerate drawer |
| `generated/` | images made from the gallery's regenerate (via `scripts/generate.py --out`) |
| `trash/` | deleted Codex files, kept 24 h so Undo works |
| `bin/` | Real-ESRGAN, downloaded on first upscale |
| `server.json` | pid, port, local url, tailnet url, started, idle limit, last request |
| `config.json` | optional: `{ "hosts": [{ "name": "MoltBook", "ssh": "tomerdanan@m1" }] }` |

The server reads (never writes, except Delete) Codex's own data in `~/.codex/` (`CODEX_HOME` overrides):

- `generated_images/<thread>/<exec-id>.png`: the images.
- `sessions/**/rollout-*.jsonl` and `archived_sessions/**`: one JSON object per line. Facts used:
  - `{"type":"session_meta","payload":{"id": <thread>, "cwd": <dir>, ...}}`, the first line.
  - `{"type":"event_msg","payload":{"type":"item_completed","thread_id":..., "item":{"type":"Extension","kind":"image_gen.generation","id":"exec-…","status":"completed","revisedPrompt":"…","savedPath":"…/generated_images/<thread>/exec-….png","inputImages":[{"path":"…"}],"generationId":"…"}}}`
    with a top-level `"timestamp"`.
  - The first `item_completed` whose `item.type` is `UserMessage` holds the user's request
    (`item.content` is a list; its `{"type":"text","text":…}` parts are the words).
- A session whose `cwd` ends in `.codex-image-runs/<run>/work` was started by the skill. Its run
  folder (`cwd` minus `/work`) holds `request.txt` (the prompt as the user gave it), `job.json`
  (requested size and quality) and `result.json` (`outputs[].path`, the published copy). Such
  images are `source: "skill"`; their `context` is `request.txt`.

Index incrementally: remember each jsonl's size and mtime and only reread changed ones. Images in
`generated_images` with no event still appear, with an empty prompt. The same picture is one image:
its id is the first 16 hex characters of its content sha256, and every path it lives at is listed.

## Image JSON

```json
{
  "id": "e0a01c184fddc73c",
  "short": "k3f9a2",
  "w": 1536, "h": 1024, "bytes": 2210086,
  "created": "2026-11-07T10:28:15.075Z",
  "prompt": "revisedPrompt, or request.txt for skill runs, or \"\"",
  "context": "the user's request in that session, trimmed to 400 chars",
  "source": "codex | skill",
  "mac": "Mac Studio",
  "quality": "auto | high | medium | low",
  "session": "01a1b58c-…",
  "files": ["/Users/…/.codex/generated_images/…/exec-….png", "/Users/…/out/codex-image-….png"],
  "refs": [{ "id": "sha16", "missing": false, "path": "/original/path.png" }],
  "parent": "id or null",
  "starred": false,
  "pending": false
}
```

`short` is 6 characters from `abcdefghjkmnpqrstuvwxyz23456789`, unique, stored once and never changed.
`parent`: set by a gallery regenerate; also inferred when one of an image's references has the
same content hash as an indexed image. `mac` is `scutil --get ComputerName` with "Tomer’s " style
owner prefixes removed, or the source host for imported images. Lists are newest first.

## HTTP

The server listens on `127.0.0.1:<port>` (default 8765, next free if taken). Every response is
`Cache-Control: no-store` except thumbnails, files and refs (`immutable`, a year). JSON is gzip'd
when the client accepts it.

| Method and path | Returns |
|---|---|
| `GET /` , `GET /i/<short>` , other non-API paths | `gallery/dist/index.html` (the app routes itself); `/assets/*` from `gallery/dist/assets` |
| `GET /api/images` | `{ "total": n, "items": [Image…] }`, every non-deleted image (about 3,000; small enough to send whole). Optional `since=<iso>` returns only newer ones |
| `GET /api/images/<id>` | `{ "image": Image, "parent": Image or null, "children": [Image…] }` |
| `GET /api/short/<short>` | `{ "id": … }` or 404 |
| `GET /thumb/<id>?w=480` or `w=1600` | WebP, made on demand from the first existing file, cached in `thumbs/` |
| `GET /file/<id>` | the original PNG; `?download=1` adds `Content-Disposition: attachment; filename="<short>.png"` |
| `GET /ref/<refid>` | a reference image from `refs/` or `uploads/` |
| `POST /api/images/<id>/star` | body `{ "starred": true }` → Image |
| `DELETE /api/images/<id>` | moves its files under `~/.codex/generated_images` to `trash/` (other copies stay), hides it → `{ "ok": true }` |
| `POST /api/images/<id>/restore` | Undo: puts them back → Image |
| `POST /api/uploads` | raw image body, `Content-Type: image/*` → `{ "id": refid }` |
| `POST /api/regenerate` | body below → `{ "jobs": [jobId…] }`; each job adds a `pending` image right away |
| `GET /api/events` | Server-Sent Events, below |
| `GET /api/server` | the server popover, below |
| `POST /api/server/stop` | stops the server and its tailscale serve → `{ "ok": true }` |
| `GET /api/hosts` | `[{ "name": "MoltBook", "ssh": "tomerdanan@m1", "last_import": iso or null, "new": n or null }]` |
| `POST /api/import` | body `{ "host": "MoltBook" }` → `{ "job": id }` |

Regenerate body:

```json
{
  "from": "id",
  "prompt": "…",
  "strength": "subtle | balanced | strong | new",
  "count": 1,
  "size": "1024x1024 | 1024x1536 | 1536x1024 | 1672x941 | 941x1672",
  "scale": 1,
  "refs": ["refid of the original's refs to keep", "uploaded refid"]
}
```

`count` 1 to 10 starts that many runs of `scripts/generate.py` (at most 2 at once, the rest queue).
Unless `strength` is `new`, the source image is the first `--input`, and the prompt gets one line:
subtle "Stay very close to reference 1: same composition, change only what the prompt asks.",
balanced "Keep reference 1's subject and layout; details may change.", strong "Keep the idea of
reference 1 but reinvent the look." `scale` 2 or 4 runs Real-ESRGAN (`realesrgan-x4plus`, then
resized to 2x when asked) on each result before it is indexed; download
`realesrgan-ncnn-vulkan-20220424-macos.zip` from GitHub release v0.2.5.0 of xinntao/Real-ESRGAN
into `bin/` on first use.

Events (`event: <name>`, `data: <json>`), plus a `: ping` comment every 20 s:

| Event | Data |
|---|---|
| `image` | Image, new or changed (also when a pending one finishes) |
| `deleted` | `{ "id": … }` |
| `job` | `{ "id", "kind": "regenerate or import", "status": "queued or running or done or failed", "message", "image": id or null }` |

Server popover (`GET /api/server`):

```json
{
  "host": "Mac Studio",
  "url": "https://studio.tail08ad7c.ts.net:8445/",
  "local": "http://127.0.0.1:8765/",
  "tailnet_only": true,
  "idle_limit_s": 14400, "idle_left_s": 11520,
  "watching": true,
  "sources": [
    { "kind": "codex", "path": "~/.codex/generated_images", "images": 2948, "bytes": 2469606400 },
    { "kind": "skill", "label": "Skill runs (codex-image)", "runs": 784 },
    { "kind": "own", "path": "~/.codex-image", "bytes": 327155712 }
  ],
  "imports": [{ "name": "MoltBook", "last": "2026-11-05T…" }]
}
```

## CLI (`python3 gallery/server/cli.py`)

- `start [--port N] [--idle 4h | --forever] [--no-tailscale] [--foreground]`: indexes, starts in
  the background, runs `tailscale serve --bg --https=8445 http://127.0.0.1:<port>`, prints the
  tailnet and local links. A running server is reused.
- `stop`: stops it and runs `tailscale serve --https=8445 off`. Never touch other serve entries.
- `status`: running or not, links, idle time left.
- `index [--full]`: reindex without serving.
- `import <host>`: copy images and session files from another Mac over SSH into
  `~/.codex-image/imported/<host>/`, indexed with `mac` set to that host.

The idle limit counts from the last HTTP request; an open page's event stream does not count as use.
