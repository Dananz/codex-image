#!/usr/bin/env python3
"""HTTP, SSE and job tests against a real server on a free port with fixture data."""
from __future__ import annotations

import gzip
import io
import json
import subprocess
import os
import socket
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from pathlib import Path

from PIL import Image

import fixtures  # noqa: F401  (puts gallery/server on sys.path)
from fixtures import Fixture

from api import App


class Client:
    def __init__(self, base):
        self.base = base

    def request(self, method, path, body=None, headers=None):
        data = json.dumps(body).encode() if isinstance(body, (dict, list)) else body
        hdrs = {"Content-Type": "application/json"} if isinstance(body, (dict, list)) else {}
        hdrs.update(headers or {})
        req = urllib.request.Request(self.base + path, data=data, method=method, headers=hdrs)
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                return resp.status, dict(resp.headers), resp.read()
        except urllib.error.HTTPError as exc:
            with exc:
                return exc.code, dict(exc.headers), exc.read()

    def json(self, method, path, body=None, status=200):
        code, _h, raw = self.request(method, path, body)
        assert code == status, (code, raw)
        return json.loads(raw)


class SSE:
    """Minimal event-stream reader on a raw socket."""

    def __init__(self, port):
        self.sock = socket.create_connection(("127.0.0.1", port), timeout=10)
        self.sock.sendall(b"GET /api/events HTTP/1.1\r\nHost: x\r\nAccept: text/event-stream\r\n\r\n")
        self.buf = b""
        self.headers = self._read_until(b"\r\n\r\n")

    def _read_until(self, marker):
        while marker not in self.buf:
            chunk = self.sock.recv(65536)
            if not chunk:
                raise EOFError
            self.buf += chunk
        head, self.buf = self.buf.split(marker, 1)
        return head

    def next_event(self, name, timeout=10):
        deadline = time.time() + timeout
        while time.time() < deadline:
            block = self._read_until(b"\n\n").decode()
            fields = dict(line.split(": ", 1) for line in block.splitlines() if not line.startswith(":") and ": " in line)
            if fields.get("event") == name:
                return json.loads(fields["data"])
        raise TimeoutError(name)

    def close(self):
        self.sock.close()


class ApiTests(unittest.TestCase):
    def setUp(self):
        self.fx = Fixture()
        self.old_env = {k: os.environ.get(k) for k in self.fx.env()}
        os.environ.update(self.fx.env())
        self.dist = Path(self.fx.root) / "dist-missing"
        self.app = App(self.fx.home(), dist=self.dist, generate=self.fx.fake_generate, watch=False)
        self.app.indexer.run()
        self.app.bind(0)
        self.app.serve_in_thread()
        self.c = Client(self.app.local_url.rstrip("/"))
        self.a1 = self.fx.a1_sha[:16]
        self.a2 = self.fx.a2_sha[:16]

    def tearDown(self):
        self.app.close()
        for k, v in self.old_env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        self.fx.cleanup()

    def test_images_list_gzip_and_since(self):
        code, headers, raw = self.c.request("GET", "/api/images", headers={"Accept-Encoding": "gzip"})
        self.assertEqual(code, 200)
        self.assertEqual(headers.get("Content-Encoding"), "gzip")
        self.assertEqual(headers.get("Cache-Control"), "no-store")
        data = json.loads(gzip.decompress(raw))
        self.assertEqual(data["total"], 5)
        self.assertEqual(len(data["items"]), 5)
        keys = {"id", "short", "w", "h", "bytes", "created", "prompt", "context", "source", "mac", "quality",
                "session", "files", "refs", "parent", "starred", "pending"}
        self.assertEqual(set(data["items"][0]), keys)
        newer = self.c.json("GET", "/api/images?since=2026-11-01T11:30:00.000Z")
        self.assertIn(self.fx.c1_sha[:16], [i["id"] for i in newer["items"]])
        self.assertNotIn(self.a1, [i["id"] for i in newer["items"]])
        self.assertTrue(all(i["created"] > "2026-11-01T11:30:00.000Z" for i in newer["items"]))

    def test_image_detail_and_short(self):
        d = self.c.json("GET", f"/api/images/{self.a2}")
        self.assertEqual(d["image"]["id"], self.a2)
        self.assertEqual(d["parent"]["id"], self.a1)
        self.assertEqual(d["children"], [])
        d1 = self.c.json("GET", f"/api/images/{self.a1}")
        self.assertIsNone(d1["parent"])
        self.assertEqual([c["id"] for c in d1["children"]], [self.a2])
        short = d1["image"]["short"]
        self.assertEqual(self.c.json("GET", f"/api/short/{short}"), {"id": self.a1})
        self.c.json("GET", "/api/short/zzzzzz", status=404)
        self.c.json("GET", "/api/images/0000000000000000", status=404)

    def test_thumbnail_webp_cached(self):
        code, headers, raw = self.c.request("GET", f"/thumb/{self.a1}?w=480")
        self.assertEqual(code, 200)
        self.assertEqual(headers["Content-Type"], "image/webp")
        self.assertIn("immutable", headers["Cache-Control"])
        with Image.open(io.BytesIO(raw)) as im:
            self.assertEqual(im.format, "WEBP")
            self.assertEqual(im.size, (32, 24))
        cached = self.fx.image_home / "thumbs" / f"{self.a1}-480.webp"
        self.assertTrue(cached.is_file())
        mtime = cached.stat().st_mtime_ns
        time.sleep(0.01)
        self.c.request("GET", f"/thumb/{self.a1}?w=480")
        self.assertEqual(cached.stat().st_mtime_ns, mtime)
        self.assertEqual(self.c.request("GET", f"/thumb/{self.a1}?w=300")[0], 400)

    def test_thumbnail_downscales(self):
        big = self.fx.codex / "generated_images" / fixtures.THREAD_D / "exec-big.png"
        sha = fixtures.png(big, (9, 9, 9), (2000, 1000))
        self.app.indexer.run()
        raw = self.c.request("GET", f"/thumb/{sha[:16]}?w=1600")[2]
        with Image.open(io.BytesIO(raw)) as im:
            self.assertEqual(im.size, (1600, 800))

    def test_file_and_download(self):
        code, headers, raw = self.c.request("GET", f"/file/{self.a1}?download=1")
        self.assertEqual(code, 200)
        self.assertEqual(raw, self.fx.a1.read_bytes())
        self.assertIn("immutable", headers["Cache-Control"])
        short = self.c.json("GET", f"/api/images/{self.a1}")["image"]["short"]
        self.assertEqual(headers["Content-Disposition"], f'attachment; filename="{short}.png"')

    def test_ref_endpoint(self):
        code, _h, raw = self.c.request("GET", f"/ref/{self.fx.ref_live_sha[:16]}")
        self.assertEqual(code, 200)
        self.assertEqual(raw, self.fx.ref_live.read_bytes())
        self.assertEqual(self.c.request("GET", "/ref/" + "f" * 16)[0], 404)

    def test_star(self):
        img = self.c.json("POST", f"/api/images/{self.a1}/star", {"starred": True})
        self.assertTrue(img["starred"])
        listed = {i["id"]: i for i in self.c.json("GET", "/api/images")["items"]}
        self.assertTrue(listed[self.a1]["starred"])
        img = self.c.json("POST", f"/api/images/{self.a1}/star", {"starred": False})
        self.assertFalse(img["starred"])

    def test_delete_and_restore(self):
        sse = SSE(self.app.port)
        try:
            b1 = self.fx.b1_sha[:16]
            self.assertEqual(self.c.json("DELETE", f"/api/images/{b1}"), {"ok": True})
            self.assertEqual(sse.next_event("deleted"), {"id": b1})
            self.assertFalse(self.fx.b1.exists())
            self.assertTrue(self.fx.b1_published.exists(), "copies outside generated_images stay")
            trashed = list((self.fx.image_home / "trash" / b1).iterdir())
            self.assertEqual(len(trashed), 1)
            ids = [i["id"] for i in self.c.json("GET", "/api/images")["items"]]
            self.assertNotIn(b1, ids)
            self.app.indexer.run()
            self.assertNotIn(b1, [i["id"] for i in self.c.json("GET", "/api/images")["items"]])
            self.c.json("GET", f"/api/images/{b1}", status=404)

            img = self.c.json("POST", f"/api/images/{b1}/restore")
            self.assertEqual(img["id"], b1)
            self.assertTrue(self.fx.b1.exists())
            self.assertEqual(sorted(img["files"]), sorted([str(self.fx.b1), str(self.fx.b1_published)]))
            self.assertIn(b1, [i["id"] for i in self.c.json("GET", "/api/images")["items"]])
            self.c.json("POST", f"/api/images/{b1}/restore", status=404)
        finally:
            sse.close()

    def test_uploads(self):
        buf = io.BytesIO()
        Image.new("RGB", (5, 5), (1, 2, 3)).save(buf, "JPEG")
        code, _h, raw = self.c.request("POST", "/api/uploads", buf.getvalue(), {"Content-Type": "image/jpeg"})
        self.assertEqual(code, 200)
        ref_id = json.loads(raw)["id"]
        self.assertEqual(len(ref_id), 16)
        stored = list((self.fx.image_home / "uploads").glob("*.jpg"))
        self.assertEqual(len(stored), 1)
        self.assertEqual(self.c.request("GET", f"/ref/{ref_id}")[2], buf.getvalue())
        self.assertEqual(self.c.request("POST", "/api/uploads", b"nope", {"Content-Type": "image/png"})[0], 400)
        self.assertEqual(self.c.request("POST", "/api/uploads", buf.getvalue(), {"Content-Type": "text/plain"})[0], 400)

    def test_sse_sends_image_event(self):
        sse = SSE(self.app.port)
        try:
            self.assertIn(b"text/event-stream", sse.headers)
            new_png = self.fx.codex / "generated_images" / fixtures.THREAD_D / "exec-new.png"
            sha = fixtures.png(new_png, (3, 30, 90))
            threading.Thread(target=self.app.indexer.run).start()
            event = sse.next_event("image")
            self.assertEqual(event["id"], sha[:16])
            self.assertEqual(event["files"], [str(new_png)])
        finally:
            sse.close()

    def test_regenerate_with_fake_script(self):
        upload = io.BytesIO()
        Image.new("RGB", (4, 4), (250, 0, 250)).save(upload, "PNG")
        ref_id = json.loads(self.c.request("POST", "/api/uploads", upload.getvalue(), {"Content-Type": "image/png"})[2])["id"]
        sse = SSE(self.app.port)
        try:
            body = {"from": self.a1, "prompt": "Make it purple", "strength": "subtle", "count": 2,
                    "size": "1024x1536", "scale": 1, "refs": [ref_id]}
            jobs = self.c.json("POST", "/api/regenerate", body)["jobs"]
            self.assertEqual(len(jobs), 2)
            pending = sse.next_event("image")
            self.assertTrue(pending["pending"])
            self.assertIn(pending["id"], jobs)
            self.assertEqual(pending["parent"], self.a1)
            done = {}
            deadline = time.time() + 20
            while len(done) < 2 and time.time() < deadline:
                ev = sse.next_event("job", timeout=20)
                if ev["status"] in ("done", "failed"):
                    done[ev["id"]] = ev
            self.assertEqual({e["status"] for e in done.values()}, {"done"}, done)
        finally:
            sse.close()
        calls = [json.loads(l) for l in self.fx.fake_log.read_text().splitlines()]
        self.assertEqual(len(calls), 2)
        argv = calls[0]["argv"]
        inputs = [argv[i + 1] for i, a in enumerate(argv) if a == "--input"]
        self.assertEqual(inputs[0], str(self.fx.a1))
        self.assertEqual(len(inputs), 2)
        self.assertEqual(argv[argv.index("--size") + 1], "1024x1536")
        self.assertEqual(argv[argv.index("--out") + 1], str(self.fx.image_home / "generated"))
        self.assertEqual(calls[0]["prompt"], "Make it purple\nStay very close to reference 1: same composition, "
                                             "change only what the prompt asks.")
        for ev in done.values():
            img = self.c.json("GET", f"/api/images/{ev['image']}")["image"]
            self.assertEqual(img["parent"], self.a1)
            self.assertEqual(img["prompt"], "Make it purple")
            self.assertEqual(img["source"], "skill")
            self.assertFalse(img["pending"])
            self.assertEqual(img["refs"][0]["id"], self.a1)
        items = self.c.json("GET", "/api/images")["items"]
        self.assertFalse(any(i["pending"] for i in items))
        self.assertEqual(len(items), 7)
        children = self.c.json("GET", f"/api/images/{self.a1}")["children"]
        self.assertEqual(len(children), 3)

    def test_regenerate_failure_and_validation(self):
        os.environ["FAKE_GENERATE_FAIL"] = "1"
        try:
            sse = SSE(self.app.port)
            try:
                job = self.c.json("POST", "/api/regenerate", {"from": self.a1, "prompt": "x", "strength": "new",
                                                             "count": 1, "size": "1024x1024"})["jobs"][0]
                while True:
                    ev = sse.next_event("job")
                    if ev["id"] == job and ev["status"] in ("done", "failed"):
                        break
                self.assertEqual(ev["status"], "failed")
                self.assertIn("boom", ev["message"])
            finally:
                sse.close()
        finally:
            os.environ.pop("FAKE_GENERATE_FAIL")
        calls = [json.loads(l) for l in self.fx.fake_log.read_text().splitlines()]
        self.assertNotIn("--input", calls[0]["argv"])
        self.assertEqual(calls[0]["prompt"], "x")
        for bad in ({"from": self.a1, "count": 11}, {"from": self.a1, "size": "5x5"},
                    {"from": self.a1, "strength": "wild"}, {"from": "nope", "prompt": "x"},
                    {"from": self.a1, "refs": ["0123456789abcdef"]}):
            self.assertEqual(self.c.request("POST", "/api/regenerate", bad)[0], 400, bad)

    def test_static_app_not_built_and_built(self):
        code, headers, raw = self.c.request("GET", "/i/abcdef")
        self.assertEqual(code, 200)
        self.assertIn(b"App not built", raw)
        self.assertEqual(headers["Cache-Control"], "no-store")
        (self.dist / "assets").mkdir(parents=True)
        (self.dist / "index.html").write_text("<html>app</html>")
        (self.dist / "assets" / "app.js").write_text("console.log(1)")
        self.assertEqual(self.c.request("GET", "/")[2], b"<html>app</html>")
        self.assertEqual(self.c.request("GET", "/i/abcdef")[2], b"<html>app</html>")
        code, headers, raw = self.c.request("GET", "/assets/app.js")
        self.assertEqual((code, raw), (200, b"console.log(1)"))
        self.assertIn("javascript", headers["Content-Type"])
        self.assertEqual(self.c.request("GET", "/assets/missing.js")[0], 404)
        self.assertEqual(self.c.request("GET", "/../../etc/passwd")[2], b"<html>app</html>")

    def test_server_info_hosts_and_idle(self):
        info = self.c.json("GET", "/api/server")
        self.assertEqual(info["local"], self.app.local_url)
        self.assertEqual(info["host"], "Test Mac")
        self.assertTrue(info["tailnet_only"])
        codex = info["sources"][0]
        self.assertEqual((codex["kind"], codex["images"]), ("codex", 6))
        self.assertEqual(info["sources"][1]["runs"], 2)
        self.assertEqual(info["idle_limit_s"], 14400)
        self.assertGreater(info["idle_left_s"], 14000)
        (self.fx.image_home / "config.json").write_text(json.dumps({"hosts": [{"name": "MoltBook", "ssh": "u@m1"}]}))
        calls = []

        def fake_ssh(argv, **kw):
            calls.append(argv)
            return subprocess.CompletedProcess(argv, 0, stdout="7\n", stderr="")
        self.app.jobs.run_cmd = fake_ssh
        self.assertEqual(self.c.json("GET", "/api/hosts"),
                         [{"name": "MoltBook", "ssh": "u@m1", "last_import": None, "new": 7, "online": True}])
        self.assertIn("u@m1", calls[0])
        (self.fx.image_home / "config.json").write_text(json.dumps({"hosts": [{"name": "Asleep", "ssh": "u@m4"}]}))
        self.app.jobs.run_cmd = lambda argv, **kw: subprocess.CompletedProcess(argv, 255, stdout="", stderr="timed out")
        self.assertEqual(self.c.json("GET", "/api/hosts")[0]["online"], False)
        before = self.app.last_request
        sse = SSE(self.app.port)
        time.sleep(0.05)
        sse.close()
        self.assertEqual(self.app.last_request, before, "event stream does not count as use")

    def test_port_taken_moves_to_next(self):
        other = App(self.fx.home(), dist=self.dist, generate=self.fx.fake_generate, watch=False)
        try:
            port = other.bind(self.app.port)
            self.assertNotEqual(port, self.app.port)
        finally:
            other.server.server_close()
            other.jobs.stop()
            other.db.close()

    def test_server_stop(self):
        stopped = threading.Event()
        self.app.on_stop = stopped.set
        self.assertEqual(self.c.json("POST", "/api/server/stop"), {"ok": True})
        self.assertTrue(stopped.wait(5))


class CliTests(unittest.TestCase):
    def test_parse_idle_and_index_command(self):
        import cli
        self.assertEqual(cli.parse_idle("4h"), 14400)
        self.assertEqual(cli.parse_idle("2h30m"), 9000)
        self.assertEqual(cli.parse_idle("90s"), 90)
        fx = Fixture()
        old = {k: os.environ.get(k) for k in fx.env()}
        os.environ.update(fx.env())
        try:
            out = io.StringIO()
            import contextlib
            with contextlib.redirect_stdout(out):
                cli.main(["index"])
            stats = json.loads(out.getvalue())
            self.assertEqual((stats["images"], stats["with_prompt"], stats["skill"]), (5, 4, 2))
            self.assertFalse((fx.image_home / "server.json").exists())
        finally:
            for k, v in old.items():
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v
            fx.cleanup()


if __name__ == "__main__":
    unittest.main()
