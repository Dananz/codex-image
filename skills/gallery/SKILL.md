---
name: gallery
description: >-
  Open a private gallery of every image Codex generated on this Mac: masonry
  grid with a month timeline, prompt search, stars, image pages with the
  prompt, references and lineage, regenerate, delete with undo, and import from
  other Macs over Tailscale. Use for /codex-image:gallery, "open the gallery",
  "show my Codex images", "send me a link to that image", or to stop, check or
  reindex the gallery server.
---

# Codex image gallery

A local server (Python standard library plus Pillow) indexes `~/.codex` read-only
into `~/.codex-image/` and serves a prebuilt web app. With Tailscale it is shared
to the user's own devices only (`tailscale serve`, never funnel).

The plugin root is two folders up from this `SKILL.md`. Run the CLI from there:

```bash
python3 <root>/gallery/server/cli.py start            # index, serve, print links (reuses a running server)
python3 <root>/gallery/server/cli.py status           # running?, links, idle time left
python3 <root>/gallery/server/cli.py stop             # stop it and remove its tailscale serve entry
python3 <root>/gallery/server/cli.py index --full     # rebuild the index without serving
python3 <root>/gallery/server/cli.py import MoltBook  # copy another Mac's images over SSH
```

`start` options: `--port N` (default 8765), `--idle 4h` (default) or `--forever`,
`--no-tailscale` (this Mac only), `--foreground`.

1. Run `start` and give the user the tailnet link it prints (`https://<mac>.<tailnet>.ts.net:8445/`).
   Without Tailscale give the local link and say it only opens on this Mac.
2. A link to one image is `<link>/i/<short>`; find `short` with
   `curl -s http://127.0.0.1:8765/api/images` (the `short` field). Back on that page
   returns to the gallery at that image.
3. The first index of a large `~/.codex` takes about a minute; the page fills in live.
4. Delete moves only the Codex copy (`~/.codex/generated_images`) to
   `~/.codex-image/trash/` for 24 hours; published copies elsewhere stay.
5. Hosts for import come from `~/.codex-image/config.json`:
   `{ "hosts": [{ "name": "MoltBook", "ssh": "user@host" }] }`.

The API and data layout are in [docs/gallery-api.md](../../docs/gallery-api.md).
Rebuild the web app after editing `gallery/web/`: `cd gallery/web && pnpm install && pnpm build`.
