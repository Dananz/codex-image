#!/usr/bin/env python3
"""Indexer tests on fixture Codex data. No network, no real ~/.codex."""
from __future__ import annotations

import json
import os
import unittest

import fixtures  # noqa: F401  (puts gallery/server on sys.path)
from fixtures import THREAD_A, THREAD_B, Fixture, gen, write_jsonl

import db as dbmod
from index import Indexer
from library import Library


class IndexTests(unittest.TestCase):
    def setUp(self):
        self.fx = Fixture()
        self.home = self.fx.home()
        self.home.ensure()
        self.db = dbmod.Database(self.home.db)
        self.indexer = Indexer(self.home, self.db)
        self.lib = Library(self.home, self.db)

    def tearDown(self):
        self.db.close()
        self.fx.cleanup()

    def images(self):
        return {i["id"]: i for i in self.lib.list()}

    def test_index_prompts_context_and_sources(self):
        stats = self.indexer.run()
        self.assertEqual(stats["sessions_read"], 3)
        imgs = self.images()
        a1 = imgs[self.fx.a1_sha[:16]]
        self.assertEqual(a1["prompt"], "A red square, flat.")
        self.assertEqual(a1["source"], "codex")
        self.assertEqual(a1["session"], THREAD_A)
        self.assertEqual(a1["created"], "2026-11-01T10:01:00.000Z")
        self.assertEqual(a1["mac"], "Test Mac")
        self.assertEqual(a1["quality"], "auto")
        self.assertEqual((a1["w"], a1["h"]), (32, 24))
        self.assertTrue(a1["context"].startswith("Make me a red square."))
        self.assertEqual(self.images()[self.fx.a2_sha[:16]]["context"], "now make it green")
        self.assertLessEqual(len(a1["context"]), 400)
        self.assertFalse(a1["pending"])

        b1 = imgs[self.fx.b1_sha[:16]]
        self.assertEqual(b1["source"], "skill")
        self.assertEqual(b1["prompt"], self.fx.prompt_b)
        self.assertEqual(b1["context"], self.fx.prompt_b.strip())
        self.assertEqual(b1["quality"], "high")

        c1 = imgs[self.fx.c1_sha[:16]]
        self.assertEqual(c1["source"], "skill")
        self.assertEqual(c1["prompt"], self.fx.prompt_c)
        self.assertEqual(c1["quality"], "medium")

        d1 = imgs[self.fx.d1_sha[:16]]
        self.assertEqual(d1["prompt"], "")
        self.assertEqual(d1["source"], "codex")
        self.assertIsNone(d1["session"])

    def test_dedupe_by_hash_lists_every_path(self):
        self.indexer.run()
        imgs = self.images()
        self.assertEqual(len(imgs), 5)
        a1 = imgs[self.fx.a1_sha[:16]]
        self.assertEqual(sorted(a1["files"]), sorted([str(self.fx.a1), str(self.fx.d_dup)]))
        self.assertEqual(a1["prompt"], "A red square, flat.")
        b1 = imgs[self.fx.b1_sha[:16]]
        self.assertEqual(b1["files"], [str(self.fx.b1), str(self.fx.b1_published)])

    def test_newest_first(self):
        self.indexer.run()
        created = [i["created"] for i in self.lib.list()]
        self.assertEqual(created, sorted(created, reverse=True))

    def test_short_ids_unique_and_stable(self):
        self.indexer.run()
        before = {i["id"]: i["short"] for i in self.lib.list()}
        for short in before.values():
            self.assertEqual(len(short), 6)
            self.assertTrue(set(short) <= set(dbmod.SHORT_ALPHABET))
        self.assertEqual(len(set(before.values())), len(before))
        self.indexer.run(full=True)
        self.indexer.run()
        after = {i["id"]: i["short"] for i in self.lib.list()}
        self.assertEqual(before, after)
        some = next(iter(before))
        self.assertEqual(self.lib.by_short(before[some]), some)

    def test_incremental_reindex(self):
        self.indexer.run()
        again = self.indexer.run()
        self.assertEqual((again["sessions_read"], again["files_hashed"], again["changed"]), (0, 0, 0))

        new_png = self.fx.codex / "generated_images" / THREAD_A / "exec-a3.png"
        sha = fixtures.png(new_png, (1, 2, 3))
        line = json.dumps(gen(THREAD_A, new_png, "Third one.", "2026-11-01T10:03:00.000Z")) + "\n"
        with open(self.fx.session_a, "a") as f:
            f.write(line[:50])  # half-written line must not be consumed yet
        stats = self.indexer.run()
        self.assertEqual(stats["files_hashed"], 1)
        self.assertEqual(self.images()[sha[:16]]["prompt"], "")
        with open(self.fx.session_a, "a") as f:
            f.write(line[50:])
        stats = self.indexer.run()
        self.assertEqual(stats["sessions_read"], 1)
        self.assertEqual(self.images()[sha[:16]]["prompt"], "Third one.")
        # the third image came after "now make it green", so that message is what asked for it
        self.assertEqual(self.images()[sha[:16]]["context"], "now make it green")

    def test_event_after_image_and_removed_file(self):
        self.indexer.run()
        seen = []
        self.indexer.on_change = lambda changed, removed: seen.append((set(changed), set(removed)))
        os.unlink(self.fx.d1)
        self.indexer.run()
        self.assertEqual(seen[-1], (set(), {self.fx.d1_sha[:16]}))
        self.assertNotIn(self.fx.d1_sha[:16], self.images())

    def test_refs_copied_and_missing(self):
        self.indexer.run()
        a2 = self.images()[self.fx.a2_sha[:16]]
        refs = a2["refs"]
        self.assertEqual([r["id"] for r in refs], [self.fx.a1_sha[:16], self.fx.ref_live_sha[:16], "f" * 16])
        self.assertEqual([r["missing"] for r in refs], [False, False, True])
        self.assertEqual(refs[1]["path"], str(self.fx.ref_live))
        copied = self.home.refs / f"{self.fx.ref_live_sha}.png"
        self.assertTrue(copied.is_file())
        os.unlink(self.fx.ref_live)
        self.indexer.run(full=True)
        self.assertFalse(self.images()[self.fx.a2_sha[:16]]["refs"][1]["missing"])
        self.assertEqual(self.lib.ref_path(self.fx.ref_live_sha[:16]), copied)

    def test_skill_ref_shows_original_path(self):
        self.indexer.run()
        b1 = self.images()[self.fx.b1_sha[:16]]
        self.assertEqual(b1["refs"][0]["id"], self.fx.ref_b_sha[:16])
        self.assertTrue(b1["refs"][0]["path"].endswith("user-ref-b.png"))
        self.assertFalse(b1["refs"][0]["missing"])

    def test_lineage_inferred_from_reference_hash(self):
        self.indexer.run()
        self.assertEqual(self.images()[self.fx.a2_sha[:16]]["parent"], self.fx.a1_sha[:16])
        self.assertIsNone(self.images()[self.fx.a1_sha[:16]]["parent"])
        detail = self.lib.detail(self.fx.a1_sha[:16])
        self.assertEqual([c["id"] for c in detail["children"]], [self.fx.a2_sha[:16]])

    def test_imported_host(self):
        host = self.home.imported / "MoltBook"
        thread = "019eeeee-0000-7000-8000-00000000000e"
        img = host / "generated_images" / thread / "exec-e1.png"
        sha = fixtures.png(img, (77, 1, 77))
        write_jsonl(host / "sessions" / f"rollout-2026-11-01T13-00-00-{thread}.jsonl", [
            fixtures.meta(thread, "/Users/other/x"),
            gen(thread, f"/Users/tomerdanan/.codex/generated_images/{thread}/exec-e1.png", "From M1",
                "2026-11-01T13:00:00.000Z"),
        ])
        self.indexer.run()
        e1 = self.images()[sha[:16]]
        self.assertEqual((e1["mac"], e1["prompt"]), ("MoltBook", "From M1"))

    def test_never_writes_codex_home(self):
        before = sorted((p, p.stat().st_mtime) for p in self.fx.codex.rglob("*"))
        self.indexer.run()
        self.indexer.run(full=True)
        after = sorted((p, p.stat().st_mtime) for p in self.fx.codex.rglob("*"))
        self.assertEqual(before, after)


if __name__ == "__main__":
    unittest.main()
