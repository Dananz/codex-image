"""Background work: regenerate (scripts/generate.py), Real-ESRGAN upscale, SSH import."""
from __future__ import annotations

import json
import os
import queue
import re
import secrets
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request
import zipfile
from datetime import datetime, timezone
from pathlib import Path

from PIL import Image

from index import iso_from_ts, sha256_file
from library import BadRequest, NotFound

SIZES = {"1024x1024", "1024x1536", "1536x1024", "1672x941", "941x1672"}
STRENGTH_LINES = {
    "subtle": "Stay very close to reference 1: same composition, change only what the prompt asks.",
    "balanced": "Keep reference 1's subject and layout; details may change.",
    "strong": "Keep the idea of reference 1 but reinvent the look.",
    "new": None,
}
ESRGAN_URL = ("https://github.com/xinntao/Real-ESRGAN/releases/download/v0.2.5.0/"
              "realesrgan-ncnn-vulkan-20220424-macos.zip")
ESRGAN_NAME = "realesrgan-ncnn-vulkan"
GENERATE_TIMEOUT = 1800
UUID_RE = re.compile(r"([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})\.jsonl")


def now_iso():
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


class Jobs:
    def __init__(self, app, generate_script: Path, workers: int = 2):
        self.app = app
        self.generate_script = Path(generate_script)
        self.queue: queue.Queue = queue.Queue()
        self.jobs: dict[str, dict] = {}
        self.lock = threading.Lock()
        self.import_lock = threading.Lock()
        self.run_cmd = subprocess.run
        self.esrgan_exe: Path | None = None
        self.threads = [threading.Thread(target=self._worker, daemon=True, name=f"regen-{i}")
                        for i in range(workers)]
        for t in self.threads:
            t.start()

    # ---------- bookkeeping ----------

    def _set(self, job_id, **fields):
        with self.lock:
            job = self.jobs[job_id]
            job.update(fields)
            public = {k: job[k] for k in ("id", "kind", "status", "message", "image")}
        self.app.bus.publish("job", public)
        return public

    def get(self, job_id):
        with self.lock:
            job = self.jobs.get(job_id)
            return None if job is None else {k: job[k] for k in ("id", "kind", "status", "message", "image")}

    def stop(self):
        for _ in self.threads:
            self.queue.put(None)

    # ---------- regenerate ----------

    def regenerate(self, body: dict) -> list[str]:
        if not isinstance(body, dict):
            raise BadRequest("body must be an object")
        lib = self.app.library
        source_id = body.get("from")
        strength = body.get("strength", "balanced")
        if strength not in STRENGTH_LINES:
            raise BadRequest("strength must be subtle, balanced, strong or new")
        count = body.get("count", 1)
        if not isinstance(count, int) or isinstance(count, bool) or not 1 <= count <= 10:
            raise BadRequest("count must be 1 to 10")
        size = body.get("size", "1024x1024")
        if size not in SIZES:
            raise BadRequest("unsupported size")
        scale = body.get("scale", 1)
        if scale not in (1, 2, 4):
            raise BadRequest("scale must be 1, 2 or 4")
        refs = body.get("refs") or []
        if not isinstance(refs, list) or not all(isinstance(r, str) for r in refs):
            raise BadRequest("refs must be a list of ids")
        source = None
        if source_id:
            try:
                source = lib.get(source_id)
            except NotFound:
                raise BadRequest("unknown source image") from None
        prompt = body.get("prompt")
        if not isinstance(prompt, str) or not prompt.strip():
            prompt = source["prompt"] if source else ""
        if not prompt.strip():
            raise BadRequest("prompt is required")
        inputs, ref_meta = [], []
        if strength != "new":
            if source is None:
                raise BadRequest("from is required unless strength is new")
            src_path = lib.file_path(source_id)
            inputs.append(str(src_path))
            ref_meta.append({"id": source_id, "path": str(src_path), "sha256": None})
        for ref_id in refs:
            try:
                path = lib.ref_path(ref_id)
                sha = lib.ref_sha(ref_id)
            except NotFound:
                try:
                    path, sha = lib.file_path(ref_id), None
                except NotFound:
                    raise BadRequest(f"unknown ref {ref_id}") from None
            if str(path) not in inputs:
                inputs.append(str(path))
                ref_meta.append({"id": ref_id, "path": str(path), "sha256": sha})
        line = STRENGTH_LINES[strength]
        final_prompt = prompt.rstrip() + ("\n" + line if line else "")
        w, h = (int(v) for v in size.split("x"))
        ids = []
        for _ in range(count):
            job_id = secrets.token_hex(8)
            job = {"id": job_id, "kind": "regenerate", "status": "queued", "message": "", "image": None,
                   "from": source_id, "prompt": prompt, "final_prompt": final_prompt, "size": size,
                   "scale": scale, "inputs": list(inputs), "ref_meta": ref_meta,
                   "quality": source["quality"] if source else "auto"}
            with self.lock:
                self.jobs[job_id] = job
            pending = lib.add_pending(job_id, prompt, source_id, w * scale, h * scale)
            self.app.bus.publish("image", pending)
            self._set(job_id)
            self.queue.put(job_id)
            ids.append(job_id)
        return ids

    def _worker(self):
        while True:
            job_id = self.queue.get()
            if job_id is None:
                return
            try:
                self._run_regenerate(job_id)
            except Exception as exc:  # report every failure on the job instead of killing the worker
                self._finish_failed(job_id, f"{type(exc).__name__}: {exc}")

    def _finish_failed(self, job_id, message):
        self.app.library.drop_pending(job_id)
        self.app.bus.publish("deleted", {"id": job_id})
        self._set(job_id, status="failed", message=message[:2000])

    def _run_regenerate(self, job_id):
        with self.lock:
            job = dict(self.jobs[job_id])
        self._set(job_id, status="running", message="Generating")
        home = self.app.home
        out_dir = home.generated
        out_dir.mkdir(parents=True, exist_ok=True)
        work = home.jobs / job_id
        work.mkdir(parents=True, exist_ok=True)
        prompt_file = work / "prompt.txt"
        prompt_file.write_text(job["final_prompt"], encoding="utf-8")
        cmd = [sys.executable, str(self.generate_script), "--prompt-file", str(prompt_file),
               "--size", job["size"], "--project", str(out_dir), "--out", str(out_dir)]
        if job["quality"] in ("low", "medium", "high"):
            cmd += ["--quality", job["quality"]]
        for path in job["inputs"]:
            cmd += ["--input", path]
        proc = self.run_cmd(cmd, capture_output=True, text=True, timeout=GENERATE_TIMEOUT, cwd=str(out_dir))
        (work / "result.json").write_text(proc.stdout or "", encoding="utf-8")
        try:
            result = json.loads(proc.stdout)
        except ValueError:
            return self._finish_failed(job_id, (proc.stderr or proc.stdout or "generate.py printed no result")[-2000:])
        paths = [o["path"] for o in result.get("outputs") or [] if o.get("path") and os.path.isfile(o["path"])]
        if not paths:
            errors = "; ".join(result.get("errors") or []) or f"status {result.get('status')}"
            return self._finish_failed(job_id, errors)
        if job["scale"] > 1:
            self._set(job_id, message=f"Upscaling x{job['scale']}")
            paths = [str(self.upscale(Path(p), job["scale"])) for p in paths]
        ids = []
        with self.app.db.write_lock:
            c = self.app.db.conn()
            for p in paths:
                image_id = sha256_file(p)[:16]
                ids.append(image_id)
                self.app.db.ensure_state(c, image_id)
                if job["from"]:
                    c.execute("UPDATE state SET parent=? WHERE id=?", (job["from"], image_id))
                c.execute("INSERT OR REPLACE INTO made(id, prompt, quality, created, refs) VALUES (?,?,?,?,?)",
                          (image_id, job["prompt"], job["quality"], iso_from_ts(time.time()),
                           json.dumps(job["ref_meta"])))
            c.commit()
        self.app.indexer.run()
        self.app.library.drop_pending(job_id)
        self.app.bus.publish("deleted", {"id": job_id})
        for image_id in ids:
            try:
                self.app.bus.publish("image", self.app.library.get(image_id))
            except NotFound:
                pass
        self._set(job_id, status="done", message=result.get("status") or "done", image=ids[0])

    # ---------- upscale ----------

    def ensure_esrgan(self) -> Path:
        if self.esrgan_exe:
            return self.esrgan_exe
        bindir = self.app.home.bin
        found = list(bindir.rglob(ESRGAN_NAME)) if bindir.is_dir() else []
        if not found:
            bindir.mkdir(parents=True, exist_ok=True)
            with tempfile.NamedTemporaryFile(dir=bindir, suffix=".zip", delete=False) as tmp:
                with urllib.request.urlopen(ESRGAN_URL, timeout=300) as resp:
                    shutil.copyfileobj(resp, tmp)
            try:
                with zipfile.ZipFile(tmp.name) as z:
                    for member in z.namelist():
                        target = (bindir / member).resolve()
                        if not str(target).startswith(str(bindir.resolve()) + os.sep):
                            raise RuntimeError("unsafe path in Real-ESRGAN zip")
                    z.extractall(bindir)
            finally:
                os.unlink(tmp.name)
            found = list(bindir.rglob(ESRGAN_NAME))
            if not found:
                raise RuntimeError("Real-ESRGAN binary not found in the download")
        exe = found[0]
        exe.chmod(0o755)
        self.esrgan_exe = exe
        return exe

    def upscale(self, src: Path, scale: int) -> Path:
        exe = self.ensure_esrgan()
        dest = src.with_name(f"{src.stem}-x{scale}.png")
        x4 = src.with_name(f"{src.stem}-x4.png") if scale == 4 else src.with_name(f"{src.stem}-x4-tmp.png")
        models = exe.parent / "models"
        cmd = [str(exe), "-i", str(src), "-o", str(x4), "-n", "realesrgan-x4plus"]
        if models.is_dir():
            cmd += ["-m", str(models)]
        proc = self.run_cmd(cmd, capture_output=True, text=True, timeout=900)
        if proc.returncode or not x4.is_file():
            raise RuntimeError("Real-ESRGAN failed: " + (proc.stderr or "")[-500:])
        if scale == 2:
            with Image.open(src) as orig, Image.open(x4) as big:
                big.resize((orig.width * 2, orig.height * 2), Image.LANCZOS).save(dest, "PNG")
            x4.unlink()
        return dest

    # ---------- import ----------

    def hosts(self) -> list[dict]:
        cfg = self.app.config()
        out = []
        for h in cfg.get("hosts") or []:
            if not isinstance(h, dict) or not h.get("name"):
                continue
            out.append({"name": h["name"], "ssh": h.get("ssh") or h["name"],
                        "last_import": self.app.db.get_kv("import:" + h["name"]), "new": None})
        return out

    def start_import(self, name: str) -> str:
        host = next((h for h in self.hosts() if h["name"] == name), None)
        if host is None:
            raise BadRequest("unknown host")
        job_id = secrets.token_hex(8)
        with self.lock:
            self.jobs[job_id] = {"id": job_id, "kind": "import", "status": "queued", "message": "",
                                 "image": None}
        self._set(job_id)

        def run():
            self._set(job_id, status="running", message=f"Copying from {name}")
            try:
                stats = self.import_host(host["name"], host["ssh"])
                self._set(job_id, status="done", message=f"{stats['images']} images, {stats['sessions']} sessions")
            except Exception as exc:
                self._set(job_id, status="failed", message=str(exc)[:2000])

        threading.Thread(target=run, daemon=True, name=f"import-{job_id}").start()
        return job_id

    def import_host(self, name: str, ssh: str) -> dict:
        if not re.fullmatch(r"[A-Za-z0-9._ -]+", name) or name.strip(". ") != name or not name:
            raise BadRequest("bad host name")
        with self.import_lock:
            dest = self.app.home.imported / name
            (dest / "generated_images").mkdir(parents=True, exist_ok=True)
            ssh_cmd = "ssh -o BatchMode=yes -o ConnectTimeout=15"
            self._check(self.run_cmd(["rsync", "-a", "--partial", "-e", ssh_cmd,
                                      f"{ssh}:.codex/generated_images/", f"{dest}/generated_images/"],
                                     capture_output=True, text=True, timeout=7200), "rsync images")
            threads = {p.name for p in (dest / "generated_images").iterdir() if p.is_dir()}
            listing = self.run_cmd(["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=15", ssh,
                                    "cd ~/.codex && find sessions archived_sessions -type f -name 'rollout-*' 2>/dev/null; true"],
                                   capture_output=True, text=True, timeout=300)
            self._check(listing, "list sessions")
            wanted = []
            for line in listing.stdout.splitlines():
                m = UUID_RE.search(line)
                if m and m.group(1) in threads and not line.startswith("/") and ".." not in line:
                    wanted.append(line.strip())
            if wanted:
                with tempfile.NamedTemporaryFile("w", delete=False, suffix=".txt") as f:
                    f.write("\n".join(wanted) + "\n")
                try:
                    self._check(self.run_cmd(["rsync", "-a", "--partial", "-e", ssh_cmd, f"--files-from={f.name}",
                                              f"{ssh}:.codex/", f"{dest}/"],
                                             capture_output=True, text=True, timeout=7200), "rsync sessions")
                finally:
                    os.unlink(f.name)
            self.app.db.set_kv("import:" + name, now_iso())
        self.app.indexer.run()
        images = sum(1 for d in (dest / "generated_images").iterdir() if d.is_dir() for _ in d.iterdir())
        return {"images": images, "sessions": len(wanted)}

    @staticmethod
    def _check(proc, what):
        if proc.returncode:
            raise RuntimeError(f"{what} failed: {(proc.stderr or '').strip()[-500:]}")
