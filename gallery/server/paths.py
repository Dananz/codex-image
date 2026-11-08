"""Where the gallery and Codex keep their files."""
from __future__ import annotations

import functools
import os
import re
import subprocess
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
DIST = REPO / "gallery" / "dist"
GENERATE_SCRIPT = REPO / "scripts" / "generate.py"
THUMB_WIDTHS = (480, 1600)
TAILSCALE_PORT = 8445
DEFAULT_PORT = 8765


def image_home() -> Path:
    return Path(os.environ.get("CODEX_IMAGE_HOME") or "~/.codex-image").expanduser()


def codex_home() -> Path:
    return Path(os.environ.get("CODEX_HOME") or "~/.codex").expanduser()


def generate_script() -> Path:
    return Path(os.environ.get("CODEX_IMAGE_GENERATE") or GENERATE_SCRIPT)


class Home:
    def __init__(self, root: Path | None = None, codex: Path | None = None):
        self.root = Path(root) if root else image_home()
        self.codex = Path(codex) if codex else codex_home()

    @property
    def db(self): return self.root / "gallery.db"
    @property
    def thumbs(self): return self.root / "thumbs"
    @property
    def refs(self): return self.root / "refs"
    @property
    def uploads(self): return self.root / "uploads"
    @property
    def generated(self): return self.root / "generated"
    @property
    def trash(self): return self.root / "trash"
    @property
    def bin(self): return self.root / "bin"
    @property
    def imported(self): return self.root / "imported"
    @property
    def jobs(self): return self.root / "jobs"
    @property
    def server_json(self): return self.root / "server.json"
    @property
    def config_json(self): return self.root / "config.json"
    @property
    def log(self): return self.root / "server.log"

    @property
    def codex_images(self): return self.codex / "generated_images"

    def session_roots(self):
        return [self.codex / "sessions", self.codex / "archived_sessions"]

    def ensure(self):
        for d in (self.root, self.thumbs, self.refs, self.uploads, self.generated, self.trash):
            d.mkdir(parents=True, exist_ok=True)


@functools.cache
def mac_name() -> str:
    name = os.environ.get("CODEX_IMAGE_MAC")
    if not name:
        try:
            name = subprocess.run(["scutil", "--get", "ComputerName"], capture_output=True,
                                  text=True, timeout=5).stdout.strip()
        except (OSError, subprocess.TimeoutExpired):
            name = ""
        name = name or os.uname().nodename.split(".")[0]
    return re.sub(r"^\S+[’']s\s+", "", name).strip() or name


def is_within(path: Path, root: Path) -> bool:
    try:
        Path(path).resolve().relative_to(Path(root).resolve())
        return True
    except (ValueError, OSError):
        return False
