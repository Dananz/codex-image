"""Incremental indexer: Codex session logs + image folders -> derived `images` rows."""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

from PIL import Image

import db as dbmod
from paths import Home, mac_name

try:
    from compression import zstd
except ImportError:  # Python < 3.14
    zstd = None

IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".webp"}
QUALITIES = {"auto", "high", "medium", "low"}
RUN_CWD = re.compile(r"^(.*/\.codex-image-runs/[^/]+)/work/?$")
CREATIVE_MARK = "CREATIVE REQUEST (JSON):"
CONTEXT_LIMIT = 400


def iso_from_ts(ts: float) -> str:
    return datetime.fromtimestamp(ts, timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.") + \
        f"{int(ts * 1000) % 1000:03d}Z"


def sha256_file(path) -> str:
    with open(path, "rb") as f:
        return hashlib.file_digest(f, "sha256").hexdigest()


def image_info(path):
    try:
        with Image.open(path) as im:
            return im.size, (im.format or "").lower()
    except Exception:
        return (None, None), None


def parse_creative(text: str):
    if not text or CREATIVE_MARK not in text:
        return None
    tail = text.split(CREATIVE_MARK, 1)[1].lstrip()
    try:
        value, _ = json.JSONDecoder().raw_decode(tail)
    except ValueError:
        return None
    return value if isinstance(value, dict) else None


def user_text(item) -> str:
    parts = item.get("content") or []
    if isinstance(parts, str):
        return parts
    return "\n".join(p.get("text", "") for p in parts
                     if isinstance(p, dict) and p.get("type") == "text").strip()


def trim(text: str, limit: int = CONTEXT_LIMIT) -> str:
    text = (text or "").strip()
    return text if len(text) <= limit else text[:limit - 1].rstrip() + "…"


def read_json(path):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


class Indexer:
    def __init__(self, home: Home, database: dbmod.Database, on_change=None):
        self.home = home
        self.db = database
        self.on_change = on_change
        self.lock = threading.Lock()
        self.last_stats = {}

    # ---------- public ----------

    def run(self, full: bool = False) -> dict:
        with self.lock:
            started = time.monotonic()
            c = self.db.conn()
            with self.db.write_lock:
                sessions_read = self._index_sessions(c, full)
                self._refresh_runs(c)
                files_hashed = self._index_files(c, full)
                changed, removed = self._derive(c)
                c.commit()
            stats = {"sessions_read": sessions_read, "files_hashed": files_hashed,
                     "changed": len(changed), "removed": len(removed),
                     "seconds": round(time.monotonic() - started, 2)}
            self.last_stats = stats
        if self.on_change and (changed or removed):
            self.on_change(changed, removed)
        return stats

    # ---------- sessions ----------

    def _session_files(self):
        roots = [(r, None) for r in self.home.session_roots()]
        if self.home.imported.is_dir():
            for host_dir in sorted(self.home.imported.iterdir()):
                for sub in ("sessions", "archived_sessions"):
                    roots.append((host_dir / sub, host_dir.name))
        for root, host in roots:
            if not root.is_dir():
                continue
            for dirpath, _dirs, names in os.walk(root):
                for name in names:
                    if name.startswith("rollout-") and (name.endswith(".jsonl") or name.endswith(".jsonl.zst")):
                        yield os.path.join(dirpath, name), host

    def _index_sessions(self, c, full):
        known = {r["path"]: r for r in c.execute("SELECT * FROM sessions")}
        count = 0
        for path, host in self._session_files():
            try:
                st = os.stat(path)
            except OSError:
                continue
            row = known.get(path)
            if row and not full and row["size"] == st.st_size and row["mtime"] == st.st_mtime:
                continue
            resume = (row is not None and not full and not path.endswith(".zst")
                      and row["offset"] and st.st_size > row["offset"])
            self._read_session(c, path, host, st, row if resume else None)
            count += 1
        return count

    def _read_session(self, c, path, host, st, resume_row):
        compressed = path.endswith(".zst")
        if compressed and zstd is None:
            return
        state = {"thread": None, "cwd": None, "first_user": None, "creative": None, "last_user": None}
        offset = 0
        if resume_row:
            state = {k: resume_row[k] for k in state}
            offset = resume_row["offset"]
        gens = []
        try:
            fh = zstd.open(path, "rb") if compressed else open(path, "rb")
        except OSError:
            return
        with fh:
            if offset:
                fh.seek(offset)
            pos = offset
            try:
                for line in fh:
                    if not line.endswith(b"\n") and not compressed:
                        break  # partially written last line; read it next time
                    pos += len(line)
                    if state["thread"] is None and b"session_meta" in line[:400]:
                        obj = self._loads(line)
                        if obj and obj.get("type") == "session_meta":
                            p = obj.get("payload") or {}
                            state["thread"] = p.get("id") or p.get("session_id")
                            state["cwd"] = p.get("cwd")
                        continue
                    if b"image_gen.generation" in line:
                        obj = self._loads(line)
                        gen = obj and self._gen_from(obj, path, host)
                        if gen:
                            # the message that asked for this image is the latest one before it
                            gens.append(gen + (state["last_user"],))
                        continue
                    if b'"UserMessage"' in line:
                        obj = self._loads(line)
                        item = ((obj or {}).get("payload") or {}).get("item") or {}
                        if (obj or {}).get("payload", {}).get("type") == "item_completed" \
                                and item.get("type") == "UserMessage":
                            text = user_text(item)
                            if text:
                                state["last_user"] = text
                            if state["first_user"] is None:
                                state["first_user"] = text
                                creative = parse_creative(text)
                                if creative:
                                    state["creative"] = json.dumps(creative, ensure_ascii=False)
            except (OSError, EOFError, ValueError):
                pass  # unreadable tail (truncated .zst or I/O error): keep what was read
        c.execute("INSERT OR REPLACE INTO sessions(path, size, mtime, offset, thread, cwd, first_user,"
                  " creative, host, last_user) VALUES (?,?,?,?,?,?,?,?,?,?)",
                  (path, st.st_size, st.st_mtime, pos, state["thread"], state["cwd"],
                   state["first_user"], state["creative"], host, state["last_user"]))
        for g in gens:
            c.execute("INSERT OR REPLACE INTO gens(saved_path, exec_id, thread, ts, revised, session_path, inputs,"
                      " request) VALUES (?,?,?,?,?,?,?,?)", g)
        if state["cwd"]:
            m = RUN_CWD.match(state["cwd"])
            if m:
                self._store_run(c, m.group(1), host, state["creative"])

    @staticmethod
    def _loads(line):
        try:
            return json.loads(line)
        except ValueError:
            return None

    def _gen_from(self, obj, session_path, host):
        payload = obj.get("payload") or {}
        item = payload.get("item") or {}
        if payload.get("type") != "item_completed" or item.get("kind") != "image_gen.generation":
            return None
        saved = item.get("savedPath")
        if not saved or item.get("status") not in (None, "completed"):
            return None
        if host:
            marker = "/generated_images/"
            if marker in saved:
                saved = str(self.home.imported / host / "generated_images" / saved.split(marker, 1)[1])
        inputs = [{"path": i.get("path"), "sha256": i.get("sha256")}
                  for i in (item.get("inputImages") or []) if isinstance(i, dict)]
        return (saved, item.get("id"), payload.get("thread_id"), obj.get("timestamp"),
                item.get("revisedPrompt") or "", session_path, json.dumps(inputs))

    # ---------- skill runs ----------

    def _store_run(self, c, run_dir, host, creative_json):
        info = self._read_run_folder(run_dir) if not host else None
        if info is None:
            creative = json.loads(creative_json) if creative_json else {}
            existing = c.execute("SELECT 1 FROM runs WHERE run_dir=?", (run_dir,)).fetchone()
            if existing and not creative:
                return
            info = {"prompt": creative.get("prompt") or "", "size": creative.get("size"),
                    "quality": creative.get("quality"), "inputs": [], "outputs": []}
        c.execute("INSERT OR REPLACE INTO runs(run_dir, prompt, size, quality, inputs, outputs, host)"
                  " VALUES (?,?,?,?,?,?,?)",
                  (run_dir, info["prompt"], info["size"], info["quality"],
                   json.dumps(info["inputs"]), json.dumps(info["outputs"]), host))

    @staticmethod
    def _read_run_folder(run_dir):
        run = Path(run_dir)
        job = read_json(run / "job.json")
        result = read_json(run / "result.json")
        if job is None and result is None:
            creative = None
            try:
                creative = parse_creative((run / "request.txt").read_text(encoding="utf-8"))
            except OSError:
                pass
            if creative is None:
                return None
            request = creative
        else:
            request = (job or {}).get("request") or {}
            if not request.get("prompt"):
                try:
                    request = parse_creative((run / "request.txt").read_text(encoding="utf-8")) or request
                except OSError:
                    pass
        inputs = [i.get("path") for i in request.get("inputs") or [] if isinstance(i, dict)]
        outputs = [{"path": o.get("path"), "sha256": o.get("sha256")}
                   for o in ((result or {}).get("outputs") or []) if o.get("path")]
        return {"prompt": request.get("prompt") or "", "size": request.get("size"),
                "quality": request.get("quality"), "inputs": inputs, "outputs": outputs}

    def _refresh_runs(self, c):
        """Re-read local runs that had no result yet, and pick up gallery-made runs."""
        pending = [r["run_dir"] for r in c.execute(
            "SELECT run_dir FROM runs WHERE host IS NULL AND (outputs IS NULL OR outputs='[]')")]
        own = self.home.generated / ".codex-image-runs"
        if own.is_dir():
            known = {r[0] for r in c.execute("SELECT run_dir FROM runs")}
            pending += [str(p) for p in own.iterdir() if p.is_dir() and str(p) not in known]
        for run_dir in pending:
            info = self._read_run_folder(run_dir)
            if info is None:
                continue
            c.execute("INSERT OR REPLACE INTO runs(run_dir, prompt, size, quality, inputs, outputs, host)"
                      " VALUES (?,?,?,?,?,?,NULL)",
                      (run_dir, info["prompt"], info["size"], info["quality"],
                       json.dumps(info["inputs"]), json.dumps(info["outputs"])))

    # ---------- files ----------

    def _image_roots(self):
        yield self.home.codex_images, "codex", None, 1
        yield self.home.generated, "own", None, 0
        if self.home.imported.is_dir():
            for host_dir in sorted(self.home.imported.iterdir()):
                yield host_dir / "generated_images", "imported", host_dir.name, 1

    def _index_files(self, c, full):
        known = {r["path"]: r for r in c.execute("SELECT path, size, mtime, present, root FROM files")}
        seen = set()
        hashed = 0

        def visit(path, root, host):
            nonlocal hashed
            try:
                st = os.stat(path)
            except OSError:
                return
            seen.add(path)
            row = known.get(path)
            if row and not full and row["present"] and row["size"] == st.st_size and row["mtime"] == st.st_mtime:
                return
            try:
                digest = sha256_file(path)
            except OSError:
                seen.discard(path)
                return
            (w, h), _fmt = image_info(path)
            if w is None:
                seen.discard(path)
                return
            hashed += 1
            c.execute("INSERT OR REPLACE INTO files(path, root, host, sha256, size, mtime, w, h, present)"
                      " VALUES (?,?,?,?,?,?,?,?,1)", (path, root, host, digest, st.st_size, st.st_mtime, w, h))

        for root_dir, kind, host, depth in self._image_roots():
            if not root_dir.is_dir():
                continue
            if depth == 1:
                for sub in os.scandir(root_dir):
                    if sub.is_dir():
                        for entry in os.scandir(sub.path):
                            if entry.is_file() and Path(entry.name).suffix.lower() in IMAGE_EXTS:
                                visit(entry.path, kind, host)
            else:
                for entry in os.scandir(root_dir):
                    if entry.is_file() and Path(entry.name).suffix.lower() in IMAGE_EXTS:
                        visit(entry.path, kind, host)
        for r in c.execute("SELECT outputs FROM runs WHERE host IS NULL AND outputs != '[]'").fetchall():
            for o in json.loads(r["outputs"] or "[]"):
                p = o.get("path")
                if p and p not in seen:
                    visit(p, "published", None)
        gone = [p for p, r in known.items() if r["present"] and p not in seen]
        for p in gone:
            c.execute("UPDATE files SET present=0 WHERE path=?", (p,))
        return hashed

    # ---------- derive ----------

    def _derive(self, c):
        files = c.execute("SELECT * FROM files WHERE present=1").fetchall()
        groups: dict[str, list] = {}
        for f in files:
            groups.setdefault(f["sha256"][:16], []).append(f)
        gens = {g["saved_path"]: g for g in c.execute("SELECT * FROM gens")}
        sessions = {s["path"]: s for s in c.execute("SELECT path, thread, cwd, first_user, host FROM sessions")}
        runs = {r["run_dir"]: r for r in c.execute("SELECT * FROM runs")}
        output_run = {}
        for r in runs.values():
            for o in json.loads(r["outputs"] or "[]"):
                if o.get("path"):
                    output_run[o["path"]] = r["run_dir"]
        made = {m["id"]: m for m in c.execute("SELECT * FROM made")}
        refs_have = {r["id"]: r for r in c.execute("SELECT * FROM refs")}
        existing = {r["id"]: r for r in c.execute("SELECT * FROM images")}
        deleted = {r["id"] for r in c.execute("SELECT id FROM state WHERE deleted_at IS NOT NULL")}
        rank = {"codex": 0, "imported": 1, "published": 2, "own": 3}
        local_mac = mac_name()

        changed, removed = [], []
        for image_id, members in groups.items():
            members.sort(key=lambda f: (rank.get(f["root"], 9), f["path"]))
            gen = None
            for f in members:
                g = gens.get(f["path"])
                if g and (gen is None or (g["ts"] or "") < (gen["ts"] or "")):
                    gen = g
            session = sessions.get(gen["session_path"]) if gen else None
            run_dir = None
            if session and session["cwd"]:
                m = RUN_CWD.match(session["cwd"])
                if m:
                    run_dir = m.group(1)
            if not run_dir:
                run_dir = next((output_run[f["path"]] for f in members if f["path"] in output_run), None)
            run = runs.get(run_dir) if run_dir else None
            mk = made.get(image_id)

            if mk:
                prompt, context, source = mk["prompt"] or "", trim(mk["prompt"]), "skill"
            elif run is not None:
                prompt, context, source = run["prompt"] or "", trim(run["prompt"]), "skill"
            elif gen:
                prompt = gen["revised"] or ""
                context = trim(gen["request"] or (session["first_user"] if session else "") or "")
                source = "codex"
            else:
                prompt, context, source = "", "", "codex"
            if gen and gen["ts"]:
                created = gen["ts"]
            elif mk and mk["created"]:
                created = mk["created"]
            else:
                created = iso_from_ts(min(f["mtime"] for f in members))
            quality = (run["quality"] if run else None) or (mk["quality"] if mk else None) or "auto"
            if quality not in QUALITIES:
                quality = "auto"
            host = next((f["host"] for f in members if f["host"]), None)
            first = members[0]
            refs = self._refs_for(c, gen, run, mk, refs_have)
            parent = next((r["id"] for r in refs if r["id"] in groups and r["id"] != image_id), None)
            row = (first["sha256"], first["w"], first["h"], first["size"], created, prompt, context, source,
                   host or local_mac, quality, gen["thread"] if gen else None,
                   json.dumps([f["path"] for f in members]), json.dumps(refs), parent, run_dir, 1)
            old = existing.get(image_id)
            if old is None or tuple(old)[1:] != row:
                c.execute("INSERT OR REPLACE INTO images(id, sha256, w, h, bytes, created, prompt, context, source,"
                          " mac, quality, session, files, refs, parent_inferred, run_dir, present)"
                          " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", (image_id, *row))
                if image_id not in deleted:
                    changed.append(image_id)
            self.db.ensure_state(c, image_id)
        for image_id, old in existing.items():
            if image_id not in groups and old["present"]:
                c.execute("UPDATE images SET present=0 WHERE id=?", (image_id,))
                if image_id not in deleted:
                    removed.append(image_id)
        return changed, removed

    def _refs_for(self, c, gen, run, mk, refs_have):
        entries = []
        originals = json.loads(run["inputs"] or "[]") if run else []
        inputs = json.loads(gen["inputs"] or "[]") if gen else []
        if mk and mk["refs"]:
            for r in json.loads(mk["refs"]):
                entries.append((r.get("path") or "", r.get("sha256"), [r.get("path")], r.get("id")))
        elif inputs:
            for i, inp in enumerate(inputs):
                shown = originals[i] if i < len(originals) and originals[i] else inp.get("path") or ""
                entries.append((shown, inp.get("sha256"), [inp.get("path"), shown], None))
        else:
            for p in originals:
                entries.append((p, None, [p], None))
        out = []
        for shown, sha, candidates, given_id in entries:
            ref_id = given_id or (sha[:16] if sha else None)
            have = refs_have.get(ref_id) if ref_id else None
            if have is None or not os.path.exists(have["stored"] or ""):
                have = self._copy_ref(c, sha, [p for p in candidates if p], refs_have)
                if have:
                    ref_id = have["id"]
            if ref_id is None:
                ref_id = "x" + hashlib.sha256(shown.encode()).hexdigest()[:15]
            out.append({"id": ref_id, "missing": have is None, "path": shown})
        return out

    def _copy_ref(self, c, sha, candidates, refs_have):
        for p in candidates:
            if not os.path.isfile(p):
                continue
            try:
                digest = sha256_file(p)
            except OSError:
                continue
            if sha and digest != sha:
                continue
            _size, fmt = image_info(p)
            ext = {"jpeg": "jpg"}.get(fmt or "", fmt or Path(p).suffix.lstrip(".").lower() or "bin")
            dest = self.home.refs / f"{digest}.{ext}"
            if not dest.exists():
                self.home.refs.mkdir(parents=True, exist_ok=True)
                tmp = dest.with_name(dest.name + ".tmp")
                shutil.copyfile(p, tmp)
                os.replace(tmp, dest)
            row = {"id": digest[:16], "sha256": digest, "ext": ext, "stored": str(dest), "kind": "ref"}
            c.execute("INSERT OR REPLACE INTO refs(id, sha256, ext, stored, kind) VALUES (?,?,?,?,?)",
                      (row["id"], digest, ext, str(dest), "ref"))
            refs_have[row["id"]] = row
            return row
        return None
