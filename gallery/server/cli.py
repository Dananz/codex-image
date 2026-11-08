#!/usr/bin/env python3
"""Gallery command line: start, stop, status, index, import."""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parent))

import db as dbmod  # noqa: E402
from index import Indexer  # noqa: E402
from paths import DEFAULT_PORT, TAILSCALE_PORT, Home  # noqa: E402


def parse_idle(text: str) -> int:
    m = re.fullmatch(r"(?:(\d+)h)?(?:(\d+)m)?(?:(\d+)s)?", text.strip().lower())
    if not m or not any(m.groups()):
        if text.isdigit():
            return int(text)
        raise argparse.ArgumentTypeError("idle looks like 4h, 90m or 2h30m")
    h, mi, s = (int(g or 0) for g in m.groups())
    return h * 3600 + mi * 60 + s


# ---------- tailscale (only ever our own https 8445 entry) ----------

def tailscale_on(port: int) -> tuple[bool, str | None]:
    """Returns (serve entry added, tailnet url)."""
    exe = shutil.which("tailscale")
    if not exe:
        return False, None
    try:
        proc = subprocess.run([exe, "serve", "--bg", f"--https={TAILSCALE_PORT}", f"http://127.0.0.1:{port}"],
                              capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.TimeoutExpired):
        return False, None
    if proc.returncode:
        print("tailscale serve failed: " + (proc.stderr or proc.stdout).strip(), file=sys.stderr)
        return False, None
    try:
        status = subprocess.run([exe, "status", "--json"], capture_output=True, text=True, timeout=15)
        dns = json.loads(status.stdout)["Self"]["DNSName"].rstrip(".")
        return True, f"https://{dns}:{TAILSCALE_PORT}/"
    except (OSError, subprocess.TimeoutExpired, ValueError, KeyError, TypeError, AttributeError):
        return True, None


def tailscale_off() -> None:
    exe = shutil.which("tailscale")
    if exe:
        try:
            subprocess.run([exe, "serve", f"--https={TAILSCALE_PORT}", "off"],
                           capture_output=True, text=True, timeout=30)
        except (OSError, subprocess.TimeoutExpired):
            pass


# ---------- running server discovery ----------

def read_server(home: Home) -> dict | None:
    try:
        info = json.loads(home.server_json.read_text())
    except (OSError, ValueError):
        return None
    try:
        os.kill(int(info["pid"]), 0)
    except (OSError, KeyError, ValueError, TypeError):
        return None
    return info


def fetch(url, method="GET", timeout=3):
    req = urllib.request.Request(url, method=method, data=b"{}" if method == "POST" else None,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read() or b"null")


def running(home: Home) -> dict | None:
    info = read_server(home)
    if not info or not info.get("local"):
        return None
    try:
        info["live"] = fetch(info["local"] + "api/server")
    except OSError:
        return None
    return info


def print_links(info: dict) -> None:
    if info.get("tailnet"):
        print(f"Tailnet: {info['tailnet']}")
    print(f"Local:   {info['local']}")


# ---------- commands ----------

def cmd_start(args) -> int:
    home = Home()
    current = running(home)
    if current:
        print("Gallery already running.")
        print_links(current)
        return 0
    idle = None if args.forever else args.idle
    if args.foreground:
        return serve_foreground(home, args.port, idle, not args.no_tailscale)
    home.ensure()
    cmd = [sys.executable, str(Path(__file__).resolve()), "start", "--foreground", "--port", str(args.port)]
    cmd += ["--forever"] if idle is None else ["--idle", f"{idle}s"]
    if args.no_tailscale:
        cmd.append("--no-tailscale")
    with open(home.log, "ab") as log:
        child = subprocess.Popen(cmd, stdin=subprocess.DEVNULL, stdout=log, stderr=log,
                                 start_new_session=True, close_fds=True)
    deadline = time.time() + 45
    while time.time() < deadline:
        if child.poll() is not None:
            print(f"Gallery failed to start; see {home.log}", file=sys.stderr)
            return 1
        info = read_server(home)
        if info and info.get("pid") == child.pid:
            current = running(home)
            if current:
                print("Gallery started.")
                print_links(current)
                return 0
        time.sleep(0.3)
    print(f"Gallery did not answer in time; see {home.log}", file=sys.stderr)
    return 1


def serve_foreground(home: Home, port: int, idle, use_tailscale: bool) -> int:
    from api import App

    app = App(home, idle_limit_s=idle)
    app.bind(port)
    if use_tailscale:
        enabled, app.tailnet_url = tailscale_on(app.port)
        if enabled:
            app.on_stop = tailscale_off
            app.tailscale = True

    def handle(_sig, _frame):
        app.stop()

    signal.signal(signal.SIGTERM, handle)
    signal.signal(signal.SIGINT, handle)
    app.write_server_json()
    app.start_background()
    print_links({"tailnet": app.tailnet_url, "local": app.local_url}, )
    sys.stdout.flush()
    try:
        app.serve_forever()
    finally:
        app.stop()
        info = read_server(home)
        if info is None or info.get("pid") == os.getpid():
            try:
                home.server_json.unlink()
            except OSError:
                pass
    return 0


def cmd_stop(_args) -> int:
    home = Home()
    info = read_server(home)
    if not info:
        try:
            stale = json.loads(home.server_json.read_text())
        except (OSError, ValueError):
            stale = {}
        if stale.get("tailscale"):
            tailscale_off()  # server died without cleaning up its own serve entry
        try:
            home.server_json.unlink()
        except OSError:
            pass
        print("Gallery is not running.")
        return 0
    try:
        fetch(info["local"] + "api/server/stop", method="POST")
    except OSError:
        os.kill(int(info["pid"]), signal.SIGTERM)
    for _ in range(50):
        try:
            os.kill(int(info["pid"]), 0)
        except OSError:
            break
        time.sleep(0.2)
    else:
        os.kill(int(info["pid"]), signal.SIGTERM)
        time.sleep(1)
    if info.get("tailscale"):
        tailscale_off()
    try:
        if json.loads(home.server_json.read_text()).get("pid") == info["pid"]:
            home.server_json.unlink()
    except (OSError, ValueError):
        pass
    print("Gallery stopped.")
    return 0


def cmd_status(_args) -> int:
    home = Home()
    info = running(home)
    if not info:
        print("Gallery is not running.")
        return 1
    live = info["live"]
    print(f"Gallery running (pid {info['pid']}).")
    print_links(info)
    left = live.get("idle_left_s")
    print("Idle limit: none" if left is None else f"Idle time left: {left // 3600}h {left % 3600 // 60}m")
    return 0


def cmd_index(args) -> int:
    home = Home()
    home.ensure()
    database = dbmod.Database(home.db)
    stats = Indexer(home, database).run(full=args.full)
    c = database.conn()
    totals = c.execute(
        "SELECT COUNT(*), SUM(prompt != ''), SUM(source = 'skill') FROM images i JOIN state s ON s.id=i.id "
        "WHERE i.present=1 AND s.deleted_at IS NULL").fetchone()
    stats.update({"images": totals[0], "with_prompt": totals[1] or 0, "skill": totals[2] or 0})
    print(json.dumps(stats, indent=2))
    return 0


def cmd_import(args) -> int:
    from api import App

    app = App(Home(), watch=False)
    hosts = {h["name"]: h for h in app.jobs.hosts()}
    host = hosts.get(args.host) or {"name": args.host, "ssh": args.host}
    stats = app.jobs.import_host(host["name"], host["ssh"])
    print(json.dumps(stats))
    return 0


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="cli.py", description="Local gallery of every Codex image.")
    sub = p.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("start")
    s.add_argument("--port", type=int, default=DEFAULT_PORT)
    s.add_argument("--idle", type=parse_idle, default=4 * 3600)
    s.add_argument("--forever", action="store_true")
    s.add_argument("--no-tailscale", action="store_true")
    s.add_argument("--foreground", action="store_true")
    sub.add_parser("stop")
    sub.add_parser("status")
    i = sub.add_parser("index")
    i.add_argument("--full", action="store_true")
    im = sub.add_parser("import")
    im.add_argument("host")
    args = p.parse_args(argv)
    return {"start": cmd_start, "stop": cmd_stop, "status": cmd_status,
            "index": cmd_index, "import": cmd_import}[args.cmd](args)


if __name__ == "__main__":
    sys.exit(main())
