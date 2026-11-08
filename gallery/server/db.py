"""SQLite storage. Facts tables are rebuilt by the indexer; `state` holds what users chose."""
from __future__ import annotations

import secrets
import sqlite3
import threading
from pathlib import Path

SHORT_ALPHABET = "abcdefghjkmnpqrstuvwxyz23456789"

SCHEMA = """
CREATE TABLE IF NOT EXISTS files (
  path TEXT PRIMARY KEY,
  root TEXT NOT NULL,          -- codex | own | published | imported
  host TEXT,                   -- imported host name, else NULL
  sha256 TEXT,
  size INTEGER, mtime REAL, w INTEGER, h INTEGER,
  present INTEGER NOT NULL DEFAULT 1
);
CREATE INDEX IF NOT EXISTS files_sha ON files(sha256);
CREATE TABLE IF NOT EXISTS sessions (
  path TEXT PRIMARY KEY,
  size INTEGER, mtime REAL, offset INTEGER,
  thread TEXT, cwd TEXT, first_user TEXT, creative TEXT, host TEXT
);
CREATE TABLE IF NOT EXISTS gens (
  saved_path TEXT PRIMARY KEY,
  exec_id TEXT, thread TEXT, ts TEXT, revised TEXT,
  session_path TEXT, inputs TEXT
);
CREATE TABLE IF NOT EXISTS runs (
  run_dir TEXT PRIMARY KEY,
  prompt TEXT, size TEXT, quality TEXT, inputs TEXT, outputs TEXT, host TEXT
);
CREATE TABLE IF NOT EXISTS refs (
  id TEXT PRIMARY KEY, sha256 TEXT, ext TEXT, stored TEXT, kind TEXT
);
CREATE TABLE IF NOT EXISTS made (
  id TEXT PRIMARY KEY, prompt TEXT, quality TEXT, created TEXT, refs TEXT
);
CREATE TABLE IF NOT EXISTS images (
  id TEXT PRIMARY KEY, sha256 TEXT,
  w INTEGER, h INTEGER, bytes INTEGER, created TEXT,
  prompt TEXT, context TEXT, source TEXT, mac TEXT, quality TEXT, session TEXT,
  files TEXT, refs TEXT, parent_inferred TEXT, run_dir TEXT,
  present INTEGER NOT NULL DEFAULT 1
);
CREATE INDEX IF NOT EXISTS images_created ON images(created);
CREATE TABLE IF NOT EXISTS state (
  id TEXT PRIMARY KEY,
  short TEXT UNIQUE,
  starred INTEGER NOT NULL DEFAULT 0,
  deleted_at REAL,
  trash TEXT,
  parent TEXT
);
CREATE TABLE IF NOT EXISTS kv (k TEXT PRIMARY KEY, v TEXT);
"""


class Database:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._local = threading.local()
        self.write_lock = threading.RLock()
        with self.write_lock:
            c = self.conn()
            c.executescript(SCHEMA)
            self._migrate(c)
            c.commit()

    @staticmethod
    def _migrate(c: sqlite3.Connection) -> None:
        cols = lambda t: {r[1] for r in c.execute(f"PRAGMA table_info({t})")}
        added = False
        if "request" not in cols("gens"):
            c.execute("ALTER TABLE gens ADD COLUMN request TEXT")
            added = True
        if "last_user" not in cols("sessions"):
            c.execute("ALTER TABLE sessions ADD COLUMN last_user TEXT")
            added = True
        if added:
            # sessions read before these columns existed must be read again to fill them
            c.execute("DELETE FROM sessions")

    def conn(self) -> sqlite3.Connection:
        c = getattr(self._local, "conn", None)
        if c is None:
            c = sqlite3.connect(self.path, timeout=30, check_same_thread=False)
            c.row_factory = sqlite3.Row
            c.execute("PRAGMA journal_mode=WAL")
            c.execute("PRAGMA synchronous=NORMAL")
            self._local.conn = c
        return c

    def close(self):
        c = getattr(self._local, "conn", None)
        if c is not None:
            c.close()
            self._local.conn = None

    def get_kv(self, key, default=None):
        row = self.conn().execute("SELECT v FROM kv WHERE k=?", (key,)).fetchone()
        return row[0] if row else default

    def set_kv(self, key, value):
        with self.write_lock:
            c = self.conn()
            c.execute("INSERT OR REPLACE INTO kv(k, v) VALUES (?, ?)", (key, value))
            c.commit()

    def ensure_state(self, c: sqlite3.Connection, image_id: str) -> None:
        if c.execute("SELECT 1 FROM state WHERE id=?", (image_id,)).fetchone():
            return
        while True:
            short = "".join(secrets.choice(SHORT_ALPHABET) for _ in range(6))
            try:
                c.execute("INSERT INTO state(id, short) VALUES (?, ?)", (image_id, short))
                return
            except sqlite3.IntegrityError:
                if c.execute("SELECT 1 FROM state WHERE id=?", (image_id,)).fetchone():
                    return
