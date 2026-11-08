"""Image JSON, user actions (star, delete, restore, uploads) and thumbnails."""
from __future__ import annotations

import hashlib
import io
import json
import os
import shutil
import threading
import time
from pathlib import Path

from PIL import Image

import db as dbmod
from index import iso_from_ts
from paths import Home, THUMB_WIDTHS, is_within, mac_name

TRASH_KEEP_S = 24 * 3600
UPLOAD_LIMIT = 64 * 1024 * 1024


class NotFound(Exception):
    pass


class BadRequest(Exception):
    pass


IMAGE_SQL = ("SELECT i.*, s.short, s.starred, s.parent AS parent_explicit, s.deleted_at "
             "FROM images i JOIN state s ON s.id = i.id ")


class Library:
    def __init__(self, home: Home, database: dbmod.Database):
        self.home = home
        self.db = database
        self.pending: dict[str, dict] = {}
        self.pending_lock = threading.Lock()
        self.thumb_lock = threading.Lock()

    # ---------- JSON ----------

    @staticmethod
    def to_json(row) -> dict:
        return {
            "id": row["id"], "short": row["short"],
            "w": row["w"], "h": row["h"], "bytes": row["bytes"],
            "created": row["created"], "prompt": row["prompt"] or "", "context": row["context"] or "",
            "source": row["source"], "mac": row["mac"], "quality": row["quality"],
            "session": row["session"],
            "files": json.loads(row["files"] or "[]"),
            "refs": json.loads(row["refs"] or "[]"),
            "parent": row["parent_explicit"] or row["parent_inferred"],
            "starred": bool(row["starred"]), "pending": False,
        }

    def _row(self, image_id):
        return self.db.conn().execute(IMAGE_SQL + "WHERE i.id=?", (image_id,)).fetchone()

    def get(self, image_id, include_deleted=False) -> dict:
        with self.pending_lock:
            if image_id in self.pending:
                return dict(self.pending[image_id])
        row = self._row(image_id)
        if row is None or not row["present"] or (row["deleted_at"] is not None and not include_deleted):
            raise NotFound(image_id)
        return self.to_json(row)

    def list(self, since: str | None = None) -> list[dict]:
        sql = IMAGE_SQL + "WHERE i.present=1 AND s.deleted_at IS NULL "
        args = ()
        if since:
            sql += "AND i.created > ? "
            args = (since,)
        rows = self.db.conn().execute(sql + "ORDER BY i.created DESC, i.id", args).fetchall()
        with self.pending_lock:
            pend = sorted(self.pending.values(), key=lambda p: p["created"], reverse=True)
        if since:
            pend = [p for p in pend if p["created"] > since]
        return [dict(p) for p in pend] + [self.to_json(r) for r in rows]

    def detail(self, image_id) -> dict:
        image = self.get(image_id)
        parent = None
        if image["parent"]:
            try:
                parent = self.get(image["parent"])
            except NotFound:
                parent = None
        rows = self.db.conn().execute(
            IMAGE_SQL + "WHERE i.present=1 AND s.deleted_at IS NULL AND "
            "COALESCE(s.parent, i.parent_inferred)=? ORDER BY i.created DESC", (image_id,)).fetchall()
        children = [self.to_json(r) for r in rows]
        with self.pending_lock:
            children = [dict(p) for p in self.pending.values() if p["parent"] == image_id] + children
        return {"image": image, "parent": parent, "children": children}

    def by_short(self, short) -> str:
        row = self.db.conn().execute(
            "SELECT s.id FROM state s JOIN images i ON i.id=s.id "
            "WHERE s.short=? AND s.deleted_at IS NULL AND i.present=1", (short,)).fetchone()
        if row is None:
            raise NotFound(short)
        return row["id"]

    # ---------- actions ----------

    def star(self, image_id, starred: bool) -> dict:
        self.get(image_id)
        with self.db.write_lock:
            c = self.db.conn()
            c.execute("UPDATE state SET starred=? WHERE id=?", (1 if starred else 0, image_id))
            c.commit()
        return self.get(image_id)

    def delete(self, image_id) -> None:
        image = self.get(image_id)
        moved = []
        codex_images = self.home.codex_images
        dest_dir = self.home.trash / image_id
        with self.db.write_lock:
            c = self.db.conn()
            for i, path in enumerate(image["files"]):
                # Only Codex's own copies move; published copies elsewhere stay where the user put them.
                if not is_within(path, codex_images) or not os.path.isfile(path):
                    continue
                dest_dir.mkdir(parents=True, exist_ok=True)
                dest = dest_dir / f"{i}-{Path(path).name}"
                shutil.move(path, dest)
                moved.append({"from": path, "to": str(dest)})
                c.execute("UPDATE files SET present=0 WHERE path=?", (path,))
            c.execute("UPDATE state SET deleted_at=?, trash=? WHERE id=?",
                      (time.time(), json.dumps(moved), image_id))
            c.commit()

    def restore(self, image_id) -> list[str]:
        row = self.db.conn().execute("SELECT * FROM state WHERE id=?", (image_id,)).fetchone()
        if row is None or row["deleted_at"] is None:
            raise NotFound(image_id)
        restored = []
        with self.db.write_lock:
            c = self.db.conn()
            for item in json.loads(row["trash"] or "[]"):
                src, dest = item["to"], item["from"]
                if os.path.isfile(src) and not os.path.exists(dest):
                    Path(dest).parent.mkdir(parents=True, exist_ok=True)
                    shutil.move(src, dest)
                    restored.append(dest)
            c.execute("UPDATE state SET deleted_at=NULL, trash=NULL WHERE id=?", (image_id,))
            c.commit()
        try:
            (self.home.trash / image_id).rmdir()
        except OSError:
            pass
        return restored

    def purge_trash(self) -> None:
        cutoff = time.time() - TRASH_KEEP_S
        rows = self.db.conn().execute(
            "SELECT id FROM state WHERE deleted_at IS NOT NULL AND deleted_at < ? AND trash IS NOT NULL",
            (cutoff,)).fetchall()
        for r in rows:
            shutil.rmtree(self.home.trash / r["id"], ignore_errors=True)
            with self.db.write_lock:
                c = self.db.conn()
                c.execute("UPDATE state SET trash=NULL WHERE id=?", (r["id"],))
                c.commit()

    def add_upload(self, data: bytes) -> str:
        if not data:
            raise BadRequest("empty body")
        if len(data) > UPLOAD_LIMIT:
            raise BadRequest("upload too large")
        try:
            with Image.open(io.BytesIO(data)) as im:
                im.verify()
                fmt = (im.format or "").lower()
        except Exception as exc:
            raise BadRequest("not an image") from exc
        ext = {"jpeg": "jpg"}.get(fmt, fmt or "bin")
        digest = hashlib.sha256(data).hexdigest()
        dest = self.home.uploads / f"{digest}.{ext}"
        if not dest.exists():
            self.home.uploads.mkdir(parents=True, exist_ok=True)
            tmp = dest.with_name(dest.name + f".{os.getpid()}.{threading.get_ident()}.tmp")
            tmp.write_bytes(data)
            os.replace(tmp, dest)
        ref_id = digest[:16]
        with self.db.write_lock:
            c = self.db.conn()
            if not c.execute("SELECT 1 FROM refs WHERE id=?", (ref_id,)).fetchone():
                c.execute("INSERT INTO refs(id, sha256, ext, stored, kind) VALUES (?,?,?,?,?)",
                          (ref_id, digest, ext, str(dest), "upload"))
                c.commit()
        return ref_id

    def ref_path(self, ref_id) -> Path:
        row = self.db.conn().execute("SELECT stored FROM refs WHERE id=?", (ref_id,)).fetchone()
        if row is None or not os.path.isfile(row["stored"]):
            raise NotFound(ref_id)
        path = Path(row["stored"])
        if not (is_within(path, self.home.refs) or is_within(path, self.home.uploads)):
            raise NotFound(ref_id)
        return path

    def ref_sha(self, ref_id) -> str | None:
        row = self.db.conn().execute("SELECT sha256 FROM refs WHERE id=?", (ref_id,)).fetchone()
        return row["sha256"] if row else None

    # ---------- files and thumbnails ----------

    def file_path(self, image_id, include_deleted=False) -> Path:
        row = self._row(image_id)
        if row is None or (row["deleted_at"] is not None and not include_deleted):
            raise NotFound(image_id)
        for p in json.loads(row["files"] or "[]"):
            if os.path.isfile(p):
                return Path(p)
        raise NotFound(image_id)

    def short_of(self, image_id) -> str:
        row = self.db.conn().execute("SELECT short FROM state WHERE id=?", (image_id,)).fetchone()
        return row["short"] if row else image_id

    def thumb(self, image_id, width: int) -> Path:
        if width not in THUMB_WIDTHS:
            raise BadRequest("w must be one of %s" % (THUMB_WIDTHS,))
        dest = self.home.thumbs / f"{image_id}-{width}.webp"
        if dest.exists():
            return dest
        src = self.file_path(image_id)
        self.home.thumbs.mkdir(parents=True, exist_ok=True)
        with Image.open(src) as im:
            im.load()
            if im.mode not in ("RGB", "RGBA"):
                im = im.convert("RGBA" if "A" in im.getbands() else "RGB")
            if im.width > width:
                im = im.resize((width, max(1, round(im.height * width / im.width))), Image.LANCZOS)
            tmp = dest.with_name(f"{dest.stem}.{os.getpid()}.{threading.get_ident()}.tmp")
            im.save(tmp, "WEBP", quality=82, method=4)
        os.replace(tmp, dest)
        return dest

    # ---------- pending (regenerate in progress) ----------

    def add_pending(self, job_id, prompt, parent, w, h) -> dict:
        image = {"id": job_id, "short": "", "w": w, "h": h, "bytes": 0,
                 "created": iso_from_ts(time.time()), "prompt": prompt, "context": "",
                 "source": "skill", "mac": mac_name(), "quality": "auto", "session": None,
                 "files": [], "refs": [], "parent": parent, "starred": False, "pending": True}
        with self.pending_lock:
            self.pending[job_id] = image
        return dict(image)

    def drop_pending(self, job_id) -> None:
        with self.pending_lock:
            self.pending.pop(job_id, None)
