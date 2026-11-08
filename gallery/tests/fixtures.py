"""Builds a fake ~/.codex and ~/.codex-image in a temp folder."""
from __future__ import annotations

import hashlib
import json
import os
import sys
import tempfile
from pathlib import Path

from PIL import Image

SERVER = Path(__file__).resolve().parents[1] / "server"
if str(SERVER) not in sys.path:
    sys.path.insert(0, str(SERVER))
sys.dont_write_bytecode = True
os.environ.setdefault("CODEX_IMAGE_MAC", "Test Mac")

from compression import zstd  # noqa: E402

THREAD_A = "019aaaaa-0000-7000-8000-00000000000a"
THREAD_B = "019bbbbb-0000-7000-8000-00000000000b"
THREAD_C = "019ccccc-0000-7000-8000-00000000000c"
THREAD_D = "019ddddd-0000-7000-8000-00000000000d"

FAKE_GENERATE = r'''
import argparse, hashlib, json, os, pathlib, random, sys
from PIL import Image
p = argparse.ArgumentParser()
p.add_argument("--prompt-file"); p.add_argument("--size"); p.add_argument("--quality")
p.add_argument("--out"); p.add_argument("--project"); p.add_argument("--input", action="append", default=[])
a = p.parse_args()
log = pathlib.Path(os.environ["FAKE_GENERATE_LOG"])
with log.open("a") as f:
    f.write(json.dumps({"argv": sys.argv[1:], "prompt": pathlib.Path(a.prompt_file).read_text()}) + "\n")
if os.environ.get("FAKE_GENERATE_FAIL"):
    print(json.dumps({"status": "error", "exit_code": 2, "errors": ["boom"]})); sys.exit(2)
w, h = (int(v) for v in a.size.split("x"))
out = pathlib.Path(a.out); out.mkdir(parents=True, exist_ok=True)
path = out / ("codex-image-%08x.png" % random.getrandbits(32))
Image.new("RGB", (w // 64, h // 64), tuple(random.randrange(256) for _ in range(3))).save(path)
sha = hashlib.sha256(path.read_bytes()).hexdigest()
print(json.dumps({"status": "complete", "exit_code": 0, "outputs": [{"index": 1, "path": str(path), "sha256": sha}]}))
'''


def png(path: Path, color, size=(16, 12)) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", size, color).save(path)
    return hashlib.sha256(path.read_bytes()).hexdigest()


def meta(thread, cwd, ts="2026-11-01T10:00:00.000Z"):
    return {"timestamp": ts, "ordinal": 0, "type": "session_meta",
            "payload": {"id": thread, "session_id": thread, "cwd": cwd, "originator": "codex_exec"}}


def user(thread, text, ts="2026-11-01T10:00:01.000Z"):
    return {"timestamp": ts, "type": "event_msg", "payload": {
        "type": "item_completed", "thread_id": thread,
        "item": {"type": "UserMessage", "id": "u1", "content": [
            {"type": "local_image", "path": "references/input-1.png"},
            {"type": "text", "text": text, "text_elements": []}]}}}


def gen(thread, saved, revised, ts, inputs=None):
    item = {"type": "Extension", "kind": "image_gen.generation", "id": "exec-" + Path(saved).stem,
            "status": "completed", "revisedPrompt": revised, "result": "iVBORw0KGgo" + "A" * 2000,
            "savedPath": str(saved), "generationId": "g"}
    if inputs is not None:
        item["inputImages"] = inputs
    return {"timestamp": ts, "type": "event_msg",
            "payload": {"type": "item_completed", "thread_id": thread, "item": item}}


def filler():
    return {"timestamp": "2026-11-01T10:00:02.000Z", "type": "response_item",
            "payload": {"type": "message", "role": "assistant", "content": [{"type": "output_text", "text": "x" * 500}]}}


def write_jsonl(path: Path, objs, compress=False):
    path.parent.mkdir(parents=True, exist_ok=True)
    data = "".join(json.dumps(o, separators=(",", ":")) + "\n" for o in objs).encode()
    path.write_bytes(zstd.compress(data) if compress else data)


def creative_text(prompt, size="1536x1024", quality="high"):
    return ("Use the native built-in image_gen tool to fulfill the image request below.\n"
            "You are already inside the codex-image runner.\n\nCREATIVE REQUEST (JSON):\n"
            + json.dumps({"prompt": prompt, "size": size, "quality": quality, "count": 1,
                          "references": []}, indent=2) + "\n")


class Fixture:
    """
    Thread A: plain codex session, 2 images; image a2 uses a1 as reference (lineage) and an
              existing ref file plus a vanished one.
    Thread B: skill run (zst session) whose run folder exists; published copy elsewhere (dedupe).
    Thread C: skill run whose run folder is gone (prompt from the session's CREATIVE REQUEST).
    Thread D: no session at all (orphan PNG), plus a byte-identical copy of a1 (dedupe).
    """

    def __init__(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="gallery-tests-")
        root = Path(self.tmp.name)
        self.root = root
        self.codex = root / "codex"
        self.image_home = root / "image-home"
        self.outside = root / "outside"
        gi = self.codex / "generated_images"
        sessions = self.codex / "sessions" / "2026" / "11" / "01"

        self.ref_live = self.outside / "ref-live.png"
        self.ref_live_sha = png(self.ref_live, (1, 200, 3))

        self.a1 = gi / THREAD_A / "exec-a1.png"
        self.a1_sha = png(self.a1, (200, 10, 10), (32, 24))
        self.a2 = gi / THREAD_A / "exec-a2.png"
        self.a2_sha = png(self.a2, (10, 200, 10))
        a1_copy_for_ref = self.outside / "a1-as-ref.png"
        a1_copy_for_ref.write_bytes(self.a1.read_bytes())
        self.session_a = sessions / f"rollout-2026-11-01T10-00-00-{THREAD_A}.jsonl"
        self.user_a = "Make me a red square. " + "Please make it nice. " * 40
        write_jsonl(self.session_a, [
            meta(THREAD_A, "/Users/someone/project"),
            user(THREAD_A, self.user_a),
            filler(),
            gen(THREAD_A, self.a1, "A red square, flat.", "2026-11-01T10:01:00.000Z"),
            user(THREAD_A, "second message is ignored for context"),
            gen(THREAD_A, self.a2, "A green square from the red one.", "2026-11-01T10:02:00.000Z", inputs=[
                {"path": str(a1_copy_for_ref), "sha256": self.a1_sha},
                {"path": str(self.ref_live), "sha256": self.ref_live_sha},
                {"path": str(self.outside / "gone.png"), "sha256": "f" * 64},
            ]),
        ])

        self.run_b = self.outside / "proj" / ".codex-image-runs" / "20261101T110000Z-bbbbbbbbbbbb"
        self.b1 = gi / THREAD_B / "exec-b1.png"
        self.b1_sha = png(self.b1, (10, 10, 200))
        self.b1_published = self.outside / "proj" / "codex-image-20261101T110000Z-bbbbbbbbbbbb.png"
        self.b1_published.parent.mkdir(parents=True, exist_ok=True)
        self.b1_published.write_bytes(self.b1.read_bytes())
        self.prompt_b = "A blue square, product photo.\n"
        work = self.run_b / "work"
        (work / "references").mkdir(parents=True)
        ref_b_src = self.outside / "user-ref-b.png"
        ref_b_sha = png(ref_b_src, (90, 90, 90))
        (work / "references" / "input-1.png").write_bytes(ref_b_src.read_bytes())
        (self.run_b / "request.txt").write_text(creative_text(self.prompt_b))
        (self.run_b / "job.json").write_text(json.dumps({"version": 1, "state": "finished", "request": {
            "prompt": self.prompt_b, "size": "1536x1024", "quality": "high", "count": 1,
            "inputs": [{"path": str(ref_b_src), "sha256": ref_b_sha}]}}))
        (self.run_b / "result.json").write_text(json.dumps({"status": "complete", "outputs": [
            {"index": 1, "path": str(self.b1_published), "sha256": self.b1_sha}]}))
        self.ref_b_sha = ref_b_sha
        self.session_b = sessions / f"rollout-2026-11-01T11-00-00-{THREAD_B}.jsonl.zst"
        write_jsonl(self.session_b, [
            meta(THREAD_B, str(work), "2026-11-01T11:00:00.000Z"),
            user(THREAD_B, creative_text(self.prompt_b), "2026-11-01T11:00:01.000Z"),
            gen(THREAD_B, self.b1, "Codex rewrote: blue square.", "2026-11-01T11:01:00.000Z",
                inputs=[{"path": str(work / "references" / "input-1.png"), "sha256": ref_b_sha}]),
        ], compress=True)

        self.c1 = gi / THREAD_C / "exec-c1.png"
        self.c1_sha = png(self.c1, (200, 200, 10))
        self.prompt_c = "A yellow square, gone run folder."
        self.session_c = self.codex / "archived_sessions" / f"rollout-2026-11-01T12-00-00-{THREAD_C}.jsonl.zst"
        write_jsonl(self.session_c, [
            meta(THREAD_C, "/private/tmp/vanished/.codex-image-runs/20261101T120000Z-cccccccccccc/work",
                 "2026-11-01T12:00:00.000Z"),
            user(THREAD_C, creative_text(self.prompt_c, "1024x1024", "medium"), "2026-11-01T12:00:01.000Z"),
            gen(THREAD_C, self.c1, "rewritten yellow", "2026-11-01T12:01:00.000Z"),
        ], compress=True)

        self.d1 = gi / THREAD_D / "exec-d1.png"
        self.d1_sha = png(self.d1, (5, 5, 5))
        self.d_dup = gi / THREAD_D / "exec-dup-of-a1.png"
        self.d_dup.write_bytes(self.a1.read_bytes())

        self.fake_generate = root / "fake_generate.py"
        self.fake_generate.write_text(FAKE_GENERATE)
        self.fake_log = root / "fake-generate.log"

    def env(self):
        return {"CODEX_HOME": str(self.codex), "CODEX_IMAGE_HOME": str(self.image_home),
                "FAKE_GENERATE_LOG": str(self.fake_log)}

    def home(self):
        from paths import Home
        return Home(self.image_home, self.codex)

    def cleanup(self):
        self.tmp.cleanup()
