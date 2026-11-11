import json
import tempfile
import unittest
from pathlib import Path

import httpx
from pydantic import SecretStr

from agent_core import db
from agent_core.affiliate_client import ShopeeAffiliateClient, ShopeeAffiliateError
from agent_core.creative import normalise_plan
from agent_core.editor.render import chain_graph
from agent_core.editor.scenes import build_segments
from agent_core.editor.subtitles import build_ass, chunk_words
from agent_core.editor.tts import Word, evenly_timed_words
from agent_core.env_file import read_env, update_env
from agent_core.settings import get_settings
from agent_core.tiktok import chunk_plan
from agent_core.workflow import (
    WorkflowError,
    approve_job,
    create_trend_job,
    finalise_content,
    offer_to_product,
)


def temp_settings(root: Path):
    settings = get_settings()
    settings.project_root = root
    settings.media_root = root / "media"
    settings.database_path = root / "state.sqlite3"
    settings.shopee_skill_home = root / "shopee"
    return settings


class EditorTests(unittest.TestCase):
    def test_chain_offsets_account_for_transitions(self):
        parts, v, a, total, starts = chain_graph([3.0, 2.0, 4.0], 0.5)
        self.assertEqual(starts, [0.0, 2.5, 4.0])
        self.assertAlmostEqual(total, 8.0)
        self.assertEqual((v, a), ("xv2", "xa2"))
        self.assertEqual(len(parts), 4)

    def test_single_clip_chain(self):
        parts, v, a, total, starts = chain_graph([3.0], 0.5)
        self.assertEqual((parts, v, total, starts), ([], "0:v", 3.0, [0.0]))

    def test_caption_chunks_break_on_punctuation(self):
        words = [Word("Xin", 0, .2), Word("chào,", .2, .4), Word("các", .5, .6), Word("bạn", .6, .8)]
        chunks = chunk_words(words, max_words=4)
        self.assertEqual([[w.text for w in c] for c in chunks], [["Xin", "chào,"], ["các", "bạn"]])

    def test_ass_file_contains_hook_karaoke_and_cta(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = build_ass(Path(tmp) / "s.ass", 1080, 1920, "Arial",
                             [(0.0, evenly_timed_words("một hai ba", 0, 1.5))],
                             "Hook {bad}", 2.0, [(2.0, 4.0, "Nhãn")], "Mua ngay", 4.0, 6.0)
            text = path.read_text(encoding="utf-8-sig")
        self.assertIn("HOOK BAD", text)
        self.assertIn(r"\kf", text)
        self.assertIn("Mua ngay", text)
        self.assertIn("PlayResY: 1920", text)

    def test_segments_split_long_scenes(self):
        segs = build_segments([5.0], 12.0, max_len=3.6)
        self.assertTrue(all(s["duration"] <= 4.2 for s in segs))
        self.assertEqual(segs[0]["start"], 0.0)
        self.assertEqual([s["index"] for s in segs], list(range(len(segs))))


class CreativeTests(unittest.TestCase):
    def test_base_plan_drops_unknown_segments_and_clamps(self):
        segments = [{"index": 0, "start": 0.0, "duration": 3.0}, {"index": 1, "start": 3.0, "duration": 3.0}]
        plan = normalise_plan({"hook": "x" * 200, "scenes": [
            {"segment": 1, "start_offset": 9, "duration": 30, "voiceover": "a"},
            {"segment": 7, "voiceover": "ignored"},
            {"segment": "bad"},
        ]}, "base", segments)
        self.assertEqual(len(plan["scenes"]), 1)
        scene = plan["scenes"][0]
        self.assertEqual(scene["duration"], 6.0)
        self.assertEqual(scene["start"], 5.5)
        self.assertLessEqual(len(plan["hook"]), 70)

    def test_trend_plan_defaults_visual(self):
        plan = normalise_plan({"scenes": [{"visual": "hack", "voiceover": "x"}]}, "trend")
        self.assertEqual(plan["scenes"][0]["visual"], "product_image")

    def test_empty_plan_rejected(self):
        with self.assertRaises(ValueError):
            normalise_plan({"scenes": []}, "trend")

    def test_finalise_content_adds_link_and_disclosure(self):
        content = finalise_content({"caption": "Hay", "hashtags": ["#a"]}, "https://s.shopee.vn/x")
        self.assertIn("https://s.shopee.vn/x", content["shopee_caption"])
        self.assertIn("tiếp thị liên kết", content["shopee_caption"])
        self.assertIn("https://s.shopee.vn/x", content["facebook_post"])

    def test_offer_to_product(self):
        product = offer_to_product({"itemId": 5, "productName": "A", "imageUrl": "u", "offerLink": "l"})
        self.assertEqual((product["item_id"], product["name"], product["image_url"]), ("5", "A", "u"))


class QueueTests(unittest.TestCase):
    def test_enqueue_dedupe_claim_retry_and_fail(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "q.sqlite3"
            first = db.enqueue_task(path, "process", "job1")
            self.assertIsNotNone(first)
            self.assertIsNone(db.enqueue_task(path, "process", "job1"))
            task = db.claim_task(path)
            self.assertEqual(task["attempts"], 1)
            self.assertIsNone(db.claim_task(path))
            db.fail_task(path, task, "boom", retry_in=0)
            task = db.claim_task(path)
            self.assertEqual(task["attempts"], 2)
            db.fail_task(path, {**task, "attempts": 3}, "boom", retry_in=0)
            self.assertEqual(db.list_tasks(path)[0]["status"], "FAILED")

    def test_running_publish_is_not_requeued_after_crash(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "q.sqlite3"
            db.enqueue_task(path, "publish", "j", {"platform": "shopee"})
            db.claim_task(path)
            db.reset_stale_tasks(path)
            self.assertEqual(db.list_tasks(path)[0]["status"], "FAILED")


class AffiliateClientTests(unittest.TestCase):
    def test_signed_request_and_search(self):
        seen = {}

        def handler(request: httpx.Request):
            seen["auth"] = request.headers["Authorization"]
            seen["body"] = json.loads(request.content)
            return httpx.Response(200, json={"data": {"productOfferV2": {"nodes": [{"itemId": 1}]}}})

        with tempfile.TemporaryDirectory() as tmp:
            settings = temp_settings(Path(tmp))
            settings.shopee_affiliate_enabled = True
            client = ShopeeAffiliateClient(settings, transport=httpx.MockTransport(handler))
            client.save_credentials("123", "secret")
            self.assertTrue(client.credentials_status()["sections"]["shopee_affiliate"]["configured"])
            offers = client.search_offers("máy xay", limit=5, sort_type=2)
        self.assertEqual(offers, [{"itemId": 1}])
        self.assertTrue(seen["auth"].startswith("SHA256 Credential=123, Timestamp="))
        self.assertEqual(seen["body"]["variables"]["sortType"], 2)

    def test_short_link_must_stay_on_shopee(self):
        def handler(request):
            return httpx.Response(200, json={"data": {"generateShortLink": {"shortLink": "https://evil.example/x"}}})

        with tempfile.TemporaryDirectory() as tmp:
            settings = temp_settings(Path(tmp))
            settings.shopee_affiliate_enabled = True
            client = ShopeeAffiliateClient(settings, transport=httpx.MockTransport(handler))
            client.save_credentials("1", "s")
            with self.assertRaises(ShopeeAffiliateError):
                client.create_short_link("https://shopee.vn/product/1/2")


class MiscTests(unittest.TestCase):
    def test_tiktok_chunk_plan(self):
        self.assertEqual(chunk_plan(10_000_000), (10_000_000, 1))
        size, total = chunk_plan(100 * 1024 * 1024)
        self.assertEqual((size, total), (10 * 1024 * 1024, 10))

    def test_env_file_update_preserves_other_lines(self):
        with tempfile.TemporaryDirectory() as tmp:
            env = Path(tmp) / ".env"
            env.write_text("# c\nA=1\nB=2\n", encoding="utf-8")
            update_env(env, {"B": "3\nX", "C": "4"})
            self.assertEqual(read_env(env), {"A": "1", "B": "3 X", "C": "4"})
            self.assertTrue(env.read_text().startswith("# c"))

    def test_trend_job_and_approval_blockers(self):
        with tempfile.TemporaryDirectory() as tmp:
            settings = temp_settings(Path(tmp))
            job_id = create_trend_job(settings, "POV mở hộp", "notes", ["máy xay"])
            job = db.get_job(settings.database_path, job_id)
            self.assertEqual(job["mode"], "trend")
            self.assertEqual(json.loads(job["brief_json"])["keywords"], ["máy xay"])
            with self.assertRaises(WorkflowError):
                approve_job(settings, job_id, {"shopee": "draft"})
            db.update_job(settings.database_path, job_id, status="READY_FOR_REVIEW", shopee_ready=1)
            settings.auto_publish = False
            with self.assertRaises(WorkflowError) as ctx:
                approve_job(settings, job_id, {"facebook_reels": "publish"})
            self.assertIn("AA_AUTO_PUBLISH", str(ctx.exception))
            (Path(tmp) / "profiles" / "shopee").mkdir(parents=True)
            result = approve_job(settings, job_id, {"shopee": "draft"}, "2030-01-01T09:00")
            self.assertEqual(result["scheduled_at_utc"], "2030-01-01T02:00:00+00:00")
            tasks = db.list_tasks(settings.database_path)
            self.assertEqual(tasks[0]["kind"], "publish")
            self.assertEqual(db.list_platform_posts(settings.database_path, job_id)[0]["status"], "SCHEDULED")


class WebTests(unittest.TestCase):
    def test_pages_render_and_api_requires_header(self):
        from fastapi.testclient import TestClient

        from agent_core.web.app import create_app

        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            app = create_app(start_worker=False)
            settings = temp_settings(Path(tmp))
            settings.ai_api_key = SecretStr("")
            app.state.ctx["settings"] = settings
            with TestClient(app) as client:
                job_id = create_trend_job(settings, "Trend thử")
                for path in ["/", "/jobs", f"/jobs/{job_id}", "/trends", "/tasks",
                             "/settings", "/logs", "/analytics", "/products"]:
                    response = client.get(path)
                    self.assertEqual(response.status_code, 200, path)
                self.assertEqual(client.post("/api/scan").status_code, 403)
                ok = client.post("/api/trends", json={"title": "Trend A"},
                                 headers={"X-Requested-With": "affiliate-agent"})
                self.assertEqual(ok.status_code, 200)
                bad = client.post("/api/trends", json={"title": "x"}, headers={
                    "X-Requested-With": "affiliate-agent", "Origin": "https://evil.example"})
                self.assertEqual(bad.status_code, 403)
                self.assertEqual(client.get("/files/x/../../.env").status_code, 404)


if __name__ == "__main__":
    unittest.main()


class MetaTokenTests(unittest.TestCase):
    def test_exchange_returns_matching_page_token(self):
        from agent_core.meta_reels import MetaReelsError, exchange_for_page_token

        def handler(request: httpx.Request):
            if request.url.path.endswith("/oauth/access_token"):
                return httpx.Response(200, json={"access_token": "LONG"})
            assert request.headers["Authorization"] == "Bearer LONG"
            if request.url.path.endswith("/me"):
                return httpx.Response(200, json={"id": "user"})
            return httpx.Response(200, json={"data": [
                {"id": "1", "access_token": "P1"}, {"id": "2", "access_token": "P2"}]})

        transport = httpx.MockTransport(handler)
        self.assertEqual(exchange_for_page_token("v27.0", "a", "s", "short", "2", transport), "P2")
        with self.assertRaises(MetaReelsError):
            exchange_for_page_token("v27.0", "a", "s", "short", "9", transport)

    def test_exchange_accepts_page_token(self):
        from agent_core.meta_reels import exchange_for_page_token

        def handler(request: httpx.Request):
            if request.url.path.endswith("/oauth/access_token"):
                return httpx.Response(200, json={"access_token": "LONGPAGE"})
            return httpx.Response(200, json={"id": "2"})

        self.assertEqual(exchange_for_page_token("v27.0", "a", "s", "short", "2",
                                                 httpx.MockTransport(handler)), "LONGPAGE")


class ClipLibraryTests(unittest.TestCase):
    def _settings(self, root):
        settings = temp_settings(root)
        settings.assets_dir = root / "assets"
        return settings

    def _make_clip(self, root, name="c.mp4", seconds=3):
        import shutil
        import subprocess
        path = root / name
        subprocess.run([shutil.which("ffmpeg"), "-hide_banner", "-loglevel", "error", "-y",
                        "-f", "lavfi", "-i", "testsrc2=size=320x568:rate=24", "-t", str(seconds),
                        "-c:v", "libx264", "-pix_fmt", "yuv420p", str(path)], check=True)
        return path

    def test_add_list_delete_and_reject_bad_files(self):
        import io

        from agent_core.clips import ClipError, add_clip, delete_clip, list_clips

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            settings = self._settings(root)
            src = self._make_clip(root)
            with src.open("rb") as f:
                clip = add_clip(settings, "job12345", "Muse clip.mp4", f, ai_generated=True, note="xay")
            self.assertEqual(clip["kind"], "video")
            self.assertTrue(clip["ai_generated"])
            self.assertAlmostEqual(clip["duration"], 3, delta=0.2)
            self.assertEqual(len(list_clips(settings, "job12345")), 1)
            with self.assertRaises(ClipError):
                add_clip(settings, "job12345", "x.exe", io.BytesIO(b"MZ"))
            with self.assertRaises(ClipError):
                add_clip(settings, "job12345", "broken.mp4", io.BytesIO(b"not a video"))
            with self.assertRaises(ClipError):
                add_clip(settings, "../evil", "a.mp4", io.BytesIO(b""))
            delete_clip(settings, "job12345", clip["id"])
            self.assertEqual(list_clips(settings, "job12345"), [])

    def test_script_can_use_clip_and_rejects_foreign_paths(self):
        from agent_core.clips import add_clip
        from agent_core.workflow import update_script

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            settings = self._settings(root)
            job_id = create_trend_job(settings, "Clip test")
            src = self._make_clip(root)
            with src.open("rb") as f:
                clip = add_clip(settings, job_id, "real.mp4", f, ai_generated=True)
            update_script(settings, job_id, {"hook": "h", "scenes": [
                {"clip_id": clip["id"], "start": 99, "duration": 2, "voiceover": "a"}]})
            scene = json.loads(db.get_job(settings.database_path, job_id)["script_json"])["scenes"][0]
            self.assertEqual(scene["kind"], "video")
            self.assertTrue(scene["generated"])
            self.assertLessEqual(scene["start"], 3)
            self.assertTrue(scene["source"].endswith(clip["file"]))
            with self.assertRaises(WorkflowError):
                update_script(settings, job_id, {"scenes": [
                    {"kind": "image", "source": str(root / "state.sqlite3")}]})
            with self.assertRaises(WorkflowError):
                update_script(settings, job_id, {"scenes": [{"clip_id": "nope"}]})

    def test_plan_clip_field_and_timeline(self):
        from agent_core.assets import VideoGenerator
        from agent_core.workflow import build_timeline

        plan = normalise_plan({"scenes": [
            {"clip": 0, "start_offset": 1, "voiceover": "x"},
            {"visual": "stock", "voiceover": "y"},
            {"clip": 5, "visual": "card", "voiceover": "z"},
        ]}, "trend", None, n_clips=2)
        self.assertEqual(plan["scenes"][0]["clip"], 0)
        self.assertNotIn("clip", plan["scenes"][2])
        with tempfile.TemporaryDirectory() as tmp:
            settings = self._settings(Path(tmp))
            clips = [{"id": "a", "file": "a.mp4", "kind": "video", "duration": 4},
                     {"id": "b", "file": "b.jpg", "kind": "image", "duration": None,
                      "ai_generated": True}]
            job = {"id": "job12345", "mode": "trend", "source_path": ""}
            product = Path(tmp) / "p.jpg"
            product.write_bytes(b"x")
            tl = build_timeline(settings, job, plan, product, VideoGenerator(settings),
                                Path(tmp), clips)
        kinds = [(s["kind"], s.get("clip_id")) for s in tl["scenes"]]
        self.assertEqual(kinds, [("video", "a"), ("image", "b"), ("card", None)])
        self.assertEqual(tl["scenes"][0]["start"], 1.0)
        self.assertTrue(tl["scenes"][1]["generated"])
