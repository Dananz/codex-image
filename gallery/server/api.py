"""HTTP API, static app, Server-Sent Events, file watcher and idle shutdown."""
from __future__ import annotations

import gzip
import json
import mimetypes
import os
import queue
import socket
import threading
import time
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlsplit

import db as dbmod
import events
from index import Indexer, iso_from_ts
from jobs import Jobs
from library import BadRequest, Library, NotFound
from paths import DIST, DEFAULT_PORT, Home, generate_script, mac_name

PING_S = 20
WATCH_S = 4
IMMUTABLE = "public, max-age=31536000, immutable"
NOT_BUILT = b"""<!doctype html><html><head><meta charset="utf-8"><title>Gallery</title>
<style>body{font-family:system-ui,-apple-system,sans-serif;background:#111;color:#ddd;display:grid;
place-items:center;height:100vh;margin:0}</style></head><body><div><h1>App not built</h1>
<p>Build gallery/web into gallery/dist, then reload. The API is running at /api/images.</p></div>
</body></html>"""


class App:
    def __init__(self, home: Home | None = None, *, dist: Path = DIST, generate: Path | None = None,
                 idle_limit_s: float | None = 4 * 3600, watch: bool = True, workers: int = 2):
        self.home = home or Home()
        self.home.ensure()
        self.dist = Path(dist)
        self.db = dbmod.Database(self.home.db)
        self.bus = events.Bus()
        self.library = Library(self.home, self.db)
        self.indexer = Indexer(self.home, self.db, on_change=self._on_index_change)
        self.jobs = Jobs(self, generate or generate_script(), workers=workers)
        self.idle_limit_s = idle_limit_s
        self.started = time.time()
        self.last_request = time.time()
        self.watch = watch
        self.tailnet_url: str | None = None
        self.tailscale = False
        self.local_url: str | None = None
        self.port: int | None = None
        self.server: ThreadingHTTPServer | None = None
        self.stopping = threading.Event()
        self.on_stop = None
        self._own_bytes = (0.0, 0)
        self._watcher: threading.Thread | None = None
        self._server_json_written = 0.0

    # ---------- index hooks ----------

    def _on_index_change(self, changed, removed):
        for image_id in changed:
            try:
                self.bus.publish("image", self.library.get(image_id))
            except NotFound:
                pass
        for image_id in removed:
            self.bus.publish("deleted", {"id": image_id})

    def config(self) -> dict:
        try:
            with open(self.home.config_json, encoding="utf-8") as f:
                cfg = json.load(f)
            return cfg if isinstance(cfg, dict) else {}
        except (OSError, ValueError):
            return {}

    # ---------- lifecycle ----------

    def bind(self, port: int = DEFAULT_PORT, tries: int = 50) -> int:
        last = None
        for candidate in range(port, port + tries):
            if candidate and port_answers(candidate):
                continue  # on macOS a 0.0.0.0 listener does not stop a 127.0.0.1 bind
            try:
                server = GalleryServer(("127.0.0.1", candidate), Handler)
            except OSError as exc:
                last = exc
                continue
            server.app = self
            self.server = server
            self.port = server.server_address[1]
            self.local_url = f"http://127.0.0.1:{self.port}/"
            return self.port
        raise OSError(f"no free port from {port}: {last}")

    def start_background(self, initial_index=True):
        threading.Thread(target=self._watch_loop, args=(initial_index,), daemon=True, name="watcher").start()
        threading.Thread(target=self._idle_loop, daemon=True, name="idle").start()

    def serve_forever(self):
        try:
            self.server.serve_forever(poll_interval=0.5)
        finally:
            self.server.server_close()

    def serve_in_thread(self):
        t = threading.Thread(target=self.server.serve_forever, kwargs={"poll_interval": 0.1},
                             daemon=True, name="http")
        t.start()
        return t

    def stop(self):
        if self.stopping.is_set():
            return
        self.stopping.set()
        self.bus.close()
        self.jobs.stop()
        if self.on_stop:
            try:
                self.on_stop()
            except Exception:
                traceback.print_exc()
        if self.server:
            threading.Thread(target=self.server.shutdown, daemon=True).start()

    def close(self):
        self.stop()
        if self.server:
            self.server.shutdown()
            self.server.server_close()
        self.db.close()

    def touch(self):
        self.last_request = time.time()
        if self.last_request - self._server_json_written > 30:
            self.write_server_json()

    def idle_left(self):
        if self.idle_limit_s is None:
            return None
        return max(0, int(self.idle_limit_s - (time.time() - self.last_request)))

    def _idle_loop(self):
        while not self.stopping.wait(15):
            left = self.idle_left()
            if left is not None and left <= 0:
                self.stop()
                return

    def _watch_loop(self, initial_index):
        self._watcher = threading.current_thread()
        first = True
        while not self.stopping.is_set():
            if self.watch or (first and initial_index):
                try:
                    self.indexer.run()
                    self.library.purge_trash()
                except Exception:
                    traceback.print_exc()
            first = False
            if self.stopping.wait(WATCH_S):
                return

    def write_server_json(self):
        self._server_json_written = time.time()
        data = {"pid": os.getpid(), "port": self.port, "local": self.local_url, "tailnet": self.tailnet_url,
                "tailscale": self.tailscale,
                "started": iso_from_ts(self.started), "idle_limit_s": self.idle_limit_s,
                "last_request": iso_from_ts(self.last_request)}
        tmp = self.home.server_json.with_name(f"server.json.{os.getpid()}.tmp")
        tmp.write_text(json.dumps(data, indent=2))
        os.replace(tmp, self.home.server_json)

    # ---------- /api/server ----------

    def own_bytes(self):
        stamp, value = self._own_bytes
        if time.time() - stamp > 60:
            total = 0
            for dirpath, _dirs, names in os.walk(self.home.root):
                for n in names:
                    try:
                        total += os.lstat(os.path.join(dirpath, n)).st_size
                    except OSError:
                        pass
            self._own_bytes = (time.time(), total)
            value = total
        return value

    def server_info(self) -> dict:
        c = self.db.conn()
        codex = c.execute("SELECT COUNT(*), COALESCE(SUM(size), 0) FROM files WHERE root='codex' AND present=1").fetchone()
        runs = c.execute("SELECT COUNT(*) FROM runs WHERE host IS NULL").fetchone()[0]
        imports = [{"name": h["name"], "last": h["last_import"]} for h in self.jobs.hosts() if h["last_import"]]
        home_s = str(Path.home())

        def short(p):
            p = str(p)
            return "~" + p[len(home_s):] if p.startswith(home_s + os.sep) else p

        return {
            "host": mac_name(), "url": self.tailnet_url, "local": self.local_url,
            "tailnet_only": True,
            "idle_limit_s": self.idle_limit_s, "idle_left_s": self.idle_left(),
            "watching": bool(self.watch and self._watcher and self._watcher.is_alive()),
            "sources": [
                {"kind": "codex", "path": short(self.home.codex_images), "images": codex[0], "bytes": codex[1]},
                {"kind": "skill", "label": "Skill runs (codex-image)", "runs": runs},
                {"kind": "own", "path": short(self.home.root), "bytes": self.own_bytes()},
            ],
            "imports": imports,
        }


def port_answers(port: int) -> bool:
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=0.3):
            return True
    except OSError:
        return False


class GalleryServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True  # restart right after stop despite TIME_WAIT sockets
    allow_reuse_port = False
    app: App


class Handler(BaseHTTPRequestHandler):
    server_version = "CodexGallery/1"
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):
        if os.environ.get("CODEX_IMAGE_HTTP_LOG"):
            super().log_message(fmt, *args)

    @property
    def app(self) -> App:
        return self.server.app

    def finish(self):
        try:
            super().finish()
        finally:
            self.app.db.close()  # each connection runs on its own thread

    # ---------- plumbing ----------

    def _send(self, status, body: bytes, ctype, cache="no-store", extra=None):
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Cache-Control", cache)
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _json(self, value, status=200):
        body = json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode()
        extra = {}
        if "gzip" in (self.headers.get("Accept-Encoding") or "") and len(body) > 512:
            body = gzip.compress(body, compresslevel=5)
            extra = {"Content-Encoding": "gzip", "Vary": "Accept-Encoding"}
        self._send(status, body, "application/json; charset=utf-8", extra=extra)

    def _error(self, status, message):
        self._json({"error": message}, status)

    def _file(self, path: Path, ctype=None, cache=IMMUTABLE, extra=None):
        ctype = ctype or mimetypes.guess_type(str(path))[0] or "application/octet-stream"
        data = Path(path).read_bytes()
        self._send(200, data, ctype, cache=cache, extra=extra)

    def _body(self, limit=70 * 1024 * 1024) -> bytes:
        length = int(self.headers.get("Content-Length") or 0)
        if length > limit:
            raise BadRequest("body too large")
        return self.rfile.read(length) if length else b""

    def _json_body(self):
        raw = self._body(1024 * 1024)
        try:
            return json.loads(raw or b"{}")
        except ValueError:
            raise BadRequest("invalid JSON") from None

    def _dispatch(self):
        url = urlsplit(self.path)
        path = unquote(url.path)
        query = {k: v[-1] for k, v in parse_qs(url.query).items()}
        if path != "/api/events":
            self.app.touch()
        try:
            self.route(self.command, path, query)
        except NotFound:
            self._error(404, "not found")
        except BadRequest as exc:
            self._error(400, str(exc))
        except (BrokenPipeError, ConnectionResetError):
            pass
        except Exception as exc:
            traceback.print_exc()
            try:
                self._error(500, f"{type(exc).__name__}: {exc}")
            except OSError:
                pass

    do_GET = do_HEAD = do_POST = do_DELETE = _dispatch

    # ---------- routes ----------

    def route(self, method, path, query):
        lib = self.app.library
        parts = [p for p in path.split("/") if p]
        if parts[:1] == ["api"]:
            return self.api(method, parts[1:], query)
        if method not in ("GET", "HEAD"):
            raise NotFound(path)
        if parts[:1] == ["thumb"] and len(parts) == 2:
            try:
                width = int(query.get("w", "480"))
            except ValueError:
                raise BadRequest("w must be a number") from None
            return self._file(lib.thumb(parts[1], width), "image/webp")
        if parts[:1] == ["file"] and len(parts) == 2:
            src = lib.file_path(parts[1])
            extra = {}
            if query.get("download") == "1":
                extra["Content-Disposition"] = f'attachment; filename="{lib.short_of(parts[1])}.png"'
            return self._file(src, extra=extra)
        if parts[:1] == ["ref"] and len(parts) == 2:
            return self._file(lib.ref_path(parts[1]))
        return self.static(parts)

    def static(self, parts):
        dist = self.app.dist.resolve()
        index = dist / "index.html"
        if parts:
            candidate = (dist / "/".join(parts)).resolve()
            if str(candidate).startswith(str(dist) + os.sep) and candidate.is_file():
                return self._file(candidate, cache="no-store")
            if parts[0] == "assets":
                raise NotFound("/".join(parts))
        if index.is_file():
            return self._file(index, "text/html; charset=utf-8", cache="no-store")
        return self._send(200, NOT_BUILT, "text/html; charset=utf-8")

    def api(self, method, parts, query):
        app, lib = self.app, self.app.library
        if parts == ["images"] and method == "GET":
            items = lib.list(query.get("since"))
            return self._json({"total": len(items), "items": items})
        if len(parts) == 2 and parts[0] == "images":
            if method == "GET":
                return self._json(lib.detail(parts[1]))
            if method == "DELETE":
                lib.delete(parts[1])
                app.bus.publish("deleted", {"id": parts[1]})
                return self._json({"ok": True})
        if len(parts) == 3 and parts[0] == "images" and method == "POST":
            if parts[2] == "star":
                body = self._json_body()
                image = lib.star(parts[1], bool(body.get("starred")))
                app.bus.publish("image", image)
                return self._json(image)
            if parts[2] == "restore":
                lib.restore(parts[1])
                app.indexer.run()
                image = lib.get(parts[1])
                app.bus.publish("image", image)
                return self._json(image)
        if len(parts) == 2 and parts[0] == "short" and method == "GET":
            return self._json({"id": lib.by_short(parts[1])})
        if parts == ["uploads"] and method == "POST":
            if not (self.headers.get("Content-Type") or "").startswith("image/"):
                raise BadRequest("Content-Type must be image/*")
            return self._json({"id": lib.add_upload(self._body())})
        if parts == ["regenerate"] and method == "POST":
            return self._json({"jobs": app.jobs.regenerate(self._json_body())})
        if parts == ["events"] and method == "GET":
            return self.sse()
        if parts == ["server"] and method == "GET":
            return self._json(app.server_info())
        if parts == ["server", "stop"] and method == "POST":
            self._json({"ok": True})
            threading.Timer(0.2, app.stop).start()
            return None
        if parts == ["hosts"] and method == "GET":
            return self._json(app.jobs.hosts(probe=True))
        if parts == ["import"] and method == "POST":
            body = self._json_body()
            return self._json({"job": app.jobs.start_import(str(body.get("host") or ""))})
        if len(parts) == 2 and parts[0] == "jobs" and method == "GET":
            job = app.jobs.get(parts[1])
            if job is None:
                raise NotFound(parts[1])
            return self._json(job)
        raise NotFound("/".join(parts))

    def sse(self):
        q = self.app.bus.subscribe()
        try:
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Connection", "keep-alive")
            self.send_header("X-Accel-Buffering", "no")
            self.end_headers()
            self.wfile.write(b": connected\n\n")
            self.wfile.flush()
            while True:
                try:
                    item = q.get(timeout=PING_S)
                except queue.Empty:
                    self.wfile.write(b": ping\n\n")
                    self.wfile.flush()
                    continue
                if item is events.CLOSE:
                    break
                name, data = item
                payload = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
                self.wfile.write(f"event: {name}\ndata: {payload}\n\n".encode())
                self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError, OSError):
            pass
        finally:
            self.app.bus.unsubscribe(q)
            self.close_connection = True
