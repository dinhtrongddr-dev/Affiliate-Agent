"""Muse Video Engine tests.

These are contract tests against an in-memory fake gateway: they prove the queue logic
(submit once, resume after restart, QC, retry, quota, fallback) and the muse2api request
format. They are NOT a live Muse test - that needs the user's muse.ai account."""
import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import httpx
from pydantic import SecretStr

from agent_core.db import connect, get_job, list_tasks as list_queue
from agent_core.settings import get_settings
from agent_core.videogen import engine, providers

FFMPEG = shutil.which("ffmpeg") or "ffmpeg"


def make_clip(path: Path, black: bool = False, seconds: int = 6) -> Path:
    src = "color=c=black:size=360x640:rate=24" if black else "testsrc2=size=360x640:rate=24"
    subprocess.run([FFMPEG, "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi", "-i", src,
                    "-t", str(seconds), "-c:v", "libx264", "-pix_fmt", "yuv420p", str(path)],
                   check=True, capture_output=True)
    return path


def settings_for(root: Path):
    s = get_settings()
    s.project_root = root / "project"
    s.media_root = root / "media"
    s.database_path = root / "state" / "t.sqlite3"
    s.videogen_provider = "muse2api"
    s.videogen_base_url = "http://127.0.0.1:18610"
    s.videogen_api_key = SecretStr("m2a_test")
    s.videogen_daily_limit = 20
    s.videogen_max_clips = 3
    s.videogen_character = True
    s.ai_api_key = SecretStr("test-only")
    s.tts_enabled = False
    s.shopee_affiliate_enabled = False
    s.render_width, s.render_height = 360, 640
    s.facebook_min_width, s.facebook_min_height = 360, 640
    s.music_dir = root / "no-music"
    return s


class FakeGateway(providers.VideoProvider):
    """In-memory gateway: tasks finish after `ready_after` status calls."""
    name = "muse2api"
    capabilities = frozenset({"connection", "image", "video", "references"})

    def __init__(self, settings, clip: Path, image: Path, ready_after: int = 1,
                 bad_clip: Path | None = None, bad_times: int = 0):
        super().__init__(settings)
        self.clip, self.image, self.bad_clip = clip, image, bad_clip
        self.ready_after, self.bad_times = ready_after, bad_times
        self.submitted: list[dict] = []
        self.polls: dict[str, int] = {}
        self.fail_submit = 0

    def _submit(self, kind, prompt, refs, key, duration=None):
        if self.fail_submit:
            self.fail_submit -= 1
            raise providers.ProviderError("gateway down")
        rid = f"r{len(self.submitted)}"
        self.submitted.append({"id": rid, "kind": kind, "prompt": prompt,
                               "refs": [str(r) for r in refs], "key": key, "duration": duration})
        return rid

    def submit_image(self, prompt, refs, idem_key):
        return self._submit("image", prompt, refs, idem_key)

    def submit_video(self, prompt, refs, duration, idem_key):
        return self._submit("video", prompt, refs, idem_key, duration)

    def status(self, kind, remote_id):
        self.polls[remote_id] = self.polls.get(remote_id, 0) + 1
        if self.polls[remote_id] < self.ready_after:
            return providers.GenStatus("running", progress=50)
        return providers.GenStatus("succeeded", url=f"http://127.0.0.1:18610/v1/media/{remote_id}")

    def download(self, url, out):
        out.parent.mkdir(parents=True, exist_ok=True)
        rid = url.rsplit("/", 1)[-1]
        # A "restarted" gateway object does not know earlier submissions: treat as video.
        sub = next((s for s in self.submitted if s["id"] == rid), {"kind": "video"})
        if sub["kind"] == "image":
            src = self.image
        elif self.bad_clip and self.bad_times > 0:
            self.bad_times -= 1
            src = self.bad_clip
        else:
            src = self.clip
        shutil.copy(src, out)
        return out


PLAN = {
    "character": "Vietnamese woman, 25, long black hair, white t-shirt",
    "scenes": [
        {"visual": "generate", "gen_prompt": "She opens the mini blender box on a kitchen counter",
         "duration": 6, "voiceover": "Mở hộp nè", "text": ""},
        {"visual": "card", "duration": 2, "voiceover": "", "text": ""},
        {"visual": "generate", "gen_prompt": "She blends mango smoothie, close-up",
         "duration": 7, "voiceover": "Xay thử xoài", "text": ""},
    ],
}


class EngineTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        root = Path(self.tmp.name)
        self.s = settings_for(root)
        self.clip = make_clip(root / "ok.mp4")
        self.black = make_clip(root / "black.mp4", black=True)
        self.image = root / "char.png"
        subprocess.run([FFMPEG, "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi", "-i",
                        "color=c=red:size=360x640", "-frames:v", "1", str(self.image)],
                       check=True, capture_output=True)
        self.product = root / "product.jpg"
        shutil.copy(self.image, self.product)
        from agent_core.db import register_job
        register_job(self.s.database_path, {"id": "jobmuse01", "source_path": "", "sha256": "x",
                                            "file_name": "t", "mode": "trend", "brief": {}})

    def tearDown(self):
        self.tmp.cleanup()

    def wait_status(self):
        from agent_core.db import update_job
        update_job(self.s.database_path, "jobmuse01", status=engine.WAIT_STATUS)

    def test_full_cycle_character_first_then_clips(self):
        gw = FakeGateway(self.s, self.clip, self.image)
        n = engine.plan_job(self.s, "jobmuse01", PLAN, self.product, gw)
        self.assertEqual(n, 2)
        self.wait_status()
        engine.poll(self.s, gw)  # character image submitted, videos wait for it
        self.assertEqual([x["kind"] for x in gw.submitted], ["image"])
        engine.poll(self.s, gw)  # image downloaded; videos submitted with both references
        videos = [x for x in gw.submitted if x["kind"] == "video"]
        self.assertEqual(len(videos), 2)
        for v in videos:
            self.assertEqual(len(v["refs"]), 2)  # product photo + character image
            self.assertIn("Same person in every shot", v["prompt"])
            self.assertIn("product reference image", v["prompt"])
        result = engine.poll(self.s, gw)
        self.assertEqual(result["resumed"], ["jobmuse01"])
        clips = engine.results_for_job(self.s.database_path, "jobmuse01")
        self.assertEqual(sorted(clips), [0, 2])
        self.assertEqual(get_job(self.s.database_path, "jobmuse01")["status"], "GENERATION_DONE")
        kinds = [t["kind"] for t in list_queue(self.s.database_path, 20)]
        self.assertIn("render_generated", kinds)
        from agent_core.clips import list_clips
        self.assertEqual(len([c for c in list_clips(self.s, "jobmuse01") if c["ai_generated"]]), 2)

    def test_restart_never_resubmits(self):
        gw = FakeGateway(self.s, self.clip, self.image, ready_after=3)
        self.s.videogen_character = False
        engine.plan_job(self.s, "jobmuse01", PLAN, None, gw)
        engine.poll(self.s, gw)
        self.assertEqual(len(gw.submitted), 2)
        # "restart": a brand-new gateway object and engine pass
        gw2 = FakeGateway(self.s, self.clip, self.image, ready_after=1)
        engine.poll(self.s, gw2)
        self.assertEqual(gw2.submitted, [])  # only polled by remote id
        self.assertEqual(len(engine.results_for_job(self.s.database_path, "jobmuse01")), 2)
        # planning the same script again cancels open work and reuses ids deterministically
        self.assertEqual(engine.task_id("j", 0, "video", "p", ["a"]),
                         engine.task_id("j", 0, "video", "p", ["a"]))

    def test_crash_during_submit_resends_same_key(self):
        gw = FakeGateway(self.s, self.clip, self.image)
        self.s.videogen_character = False
        engine.plan_job(self.s, "jobmuse01", {"scenes": PLAN["scenes"][:1]}, None, gw)
        tid = engine.list_tasks(self.s.database_path, "jobmuse01")[0]["id"]
        with connect(self.s.database_path) as conn:  # simulate crash after SUBMITTING
            conn.execute("UPDATE gen_tasks SET status='SUBMITTING' WHERE id=?", (tid,))
        engine.poll(self.s, gw)
        self.assertEqual([x["key"] for x in gw.submitted], [tid])

    def test_submit_errors_then_fail(self):
        gw = FakeGateway(self.s, self.clip, self.image)
        gw.fail_submit = 10
        self.s.videogen_character = False
        engine.plan_job(self.s, "jobmuse01", {"scenes": PLAN["scenes"][:1]}, None, gw)
        for _ in range(3):
            engine.poll(self.s, gw)
        task = engine.list_tasks(self.s.database_path, "jobmuse01")[0]
        self.assertEqual(task["status"], "FAILED")
        self.assertIn("gateway down", task["error"])
        self.assertFalse(engine.has_active(self.s.database_path))

    def test_black_clip_retried_once_then_falls_back(self):
        gw = FakeGateway(self.s, self.clip, self.image, bad_clip=self.black, bad_times=5)
        self.s.videogen_character = False
        engine.plan_job(self.s, "jobmuse01", {"scenes": PLAN["scenes"][:1]}, None, gw)
        self.wait_status()
        for _ in range(5):
            engine.poll(self.s, gw)
        tasks = engine.list_tasks(self.s.database_path, "jobmuse01")
        self.assertEqual(sorted(t["attempt"] for t in tasks), [1, 2])
        self.assertTrue(all(t["status"] == "FAILED" for t in tasks))
        self.assertIn("khung đen", tasks[0]["error"])
        self.assertEqual(engine.results_for_job(self.s.database_path, "jobmuse01"), {})
        self.assertEqual(get_job(self.s.database_path, "jobmuse01")["status"], "GENERATION_DONE")

    def test_daily_limit(self):
        self.s.videogen_daily_limit = 1
        self.s.videogen_character = False
        gw = FakeGateway(self.s, self.clip, self.image)
        self.assertEqual(engine.plan_job(self.s, "jobmuse01", PLAN, None, gw), 1)
        engine.poll(self.s, gw)
        self.assertEqual(len(gw.submitted), 1)
        self.assertEqual(engine.remaining_today(self.s), 0)
        self.assertEqual(engine.plan_job(self.s, "jobmuse01", PLAN, None, gw), 0)

    def test_retry_and_cancel(self):
        gw = FakeGateway(self.s, self.clip, self.image, ready_after=99)
        self.s.videogen_character = False
        engine.plan_job(self.s, "jobmuse01", {"scenes": PLAN["scenes"][:1]}, None, gw)
        tid = engine.list_tasks(self.s.database_path, "jobmuse01")[0]["id"]
        with patch("agent_core.videogen.engine.providers.get_provider", return_value=gw):
            self.assertTrue(engine.cancel_task(self.s, tid))
            new = engine.retry_task(self.s, tid)
        self.assertNotEqual(new, tid)
        self.assertEqual(engine.get_task(self.s.database_path, new)["attempt"], 2)


class Muse2APIContractTests(unittest.TestCase):
    def test_request_format_and_status_parsing(self):
        with tempfile.TemporaryDirectory() as tmp:
            s = settings_for(Path(tmp))
            ref = Path(tmp) / "p.jpg"
            ref.write_bytes(b"\xff\xd8\xff")
            seen = []

            def handler(request: httpx.Request):
                seen.append(request)
                if request.method == "POST" and request.url.path == "/v1/videos":
                    return httpx.Response(200, json={"id": "task_1", "status": "queued"})
                if request.method == "POST" and request.url.path == "/v1/images/tasks":
                    return httpx.Response(202, json={"id": "img_1"})
                if request.url.path == "/v1/videos/task_1":
                    return httpx.Response(200, json={"id": "task_1", "status": "succeeded",
                                                     "progress": 100,
                                                     "result": {"url": "http://127.0.0.1:18610/v1/media/v.mp4"}})
                if request.url.path == "/v1/images/tasks/img_1":
                    return httpx.Response(200, json={"status": "completed",
                                                     "data": [{"url": "/v1/media/i.png"}]})
                if request.url.path == "/v1/models":
                    return httpx.Response(200, json={"data": [{"id": "muse"}]})
                if request.url.path.startswith("/v1/media/"):
                    return httpx.Response(200, content=b"MEDIA")
                return httpx.Response(404, json={"error": {"message": "nope"}})

            p = providers.Muse2APIProvider(s, transport=httpx.MockTransport(handler))
            self.assertEqual(p.check_connection()["models"], ["muse"])
            self.assertEqual(p.submit_video("a cat", [ref], 7, "key-1"), "task_1")
            body = json.loads(seen[-1].content)
            self.assertEqual(body["size"], "9:16")
            self.assertIn(body["duration"], (6, 8))
            self.assertTrue(body["images"][0]["url"].startswith("data:image/jpeg;base64,"))
            self.assertEqual(seen[-1].headers["Idempotency-Key"], "key-1")
            self.assertEqual(seen[-1].headers["Authorization"], "Bearer m2a_test")
            st = p.status("video", "task_1")
            self.assertEqual((st.state, st.url), ("succeeded", "http://127.0.0.1:18610/v1/media/v.mp4"))
            self.assertEqual(p.submit_image("portrait", [], "key-2"), "img_1")
            self.assertEqual(p.status("image", "img_1").url, "/v1/media/i.png")
            out = p.download("/v1/media/i.png", Path(tmp) / "i.png")
            self.assertEqual(out.read_bytes(), b"MEDIA")
            self.assertEqual(seen[-1].headers["Authorization"], "Bearer m2a_test")
            with self.assertRaises(providers.NotSupported):
                p.check_quota()
            with self.assertRaises(providers.ProviderError):
                p.status("video", "missing")

    def test_key_not_sent_to_other_hosts(self):
        with tempfile.TemporaryDirectory() as tmp:
            s = settings_for(Path(tmp))
            seen = []

            def handler(request):
                seen.append(request)
                return httpx.Response(200, content=b"x")

            p = providers.Muse2APIProvider(s, transport=httpx.MockTransport(handler))
            p.download("https://cdn.example.com/v.mp4", Path(tmp) / "v.mp4")
            self.assertNotIn("Authorization", seen[-1].headers)

    def test_provider_factory(self):
        with tempfile.TemporaryDirectory() as tmp:
            s = settings_for(Path(tmp))
            self.assertIsInstance(providers.get_provider(s), providers.Muse2APIProvider)
            s.videogen_provider = "none"
            self.assertIsNone(providers.get_provider(s))


class TrendFakeAI:
    def chat(self, messages, **kwargs):
        content = messages[0]["content"]
        prompt = content if isinstance(content, str) else content[0]["text"]
        if "biên tập viên" in prompt:
            return {"content": json.dumps({"score": 8.5, "issues": [], "revised": None})}
        if "đạo diễn" in prompt:
            assert "Video AI (Muse)" in prompt and '"character"' in prompt
            return {"content": json.dumps({
                "title": "Máy xay mini", "hook": "Xay sinh tố 30 giây?", "music_mood": "upbeat",
                "character": "Vietnamese woman, 25, white t-shirt",
                "scenes": [
                    {"visual": "generate", "gen_prompt": "woman opens mini blender box",
                     "duration": 6, "voiceover": "Mở hộp máy xay mini nè.", "text": "Mở hộp"},
                    {"visual": "generate", "gen_prompt": "woman blends mango",
                     "duration": 6, "voiceover": "Xay xoài thử luôn.", "text": ""},
                ],
                "cta": "Link ở giỏ hàng", "caption": "Máy xay mini tiện lợi.",
                "hashtags": ["#mayxay"], "facebook_description": "Máy xay mini"})}
        return {"content": json.dumps({"topic": "máy xay mini", "angle": "xay nhanh",
                                       "product_category": "máy xay",
                                       "product_search_keywords": ["máy xay mini"],
                                       "visual_summary": "demo"})}


class TrendJobWithMuseTests(unittest.TestCase):
    def test_trend_job_waits_then_renders_with_ai_clips(self):
        from agent_core import workflow

        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            root = Path(tmp)
            s = settings_for(root)
            clip = make_clip(root / "ok.mp4")
            image = make_clip(root / "img.mp4", seconds=1)
            gw = FakeGateway(s, clip, image)
            offer = {"itemId": "9", "productName": "Máy xay mini", "affiliateLink": "https://s.shopee.vn/x",
                     "offerLink": "https://shopee.vn/product/1/9", "imageUrl": ""}
            job_id = workflow.create_trend_job(s, "máy xay mini", offer=offer)
            with patch("agent_core.workflow._client", return_value=TrendFakeAI()), \
                    patch("agent_core.workflow.get_provider", return_value=gw):
                result = workflow.process_job(s, job_id)
                self.assertEqual(result["status"], engine.WAIT_STATUS, result)
                gw.capabilities = frozenset({"video", "references"})  # no character image
                for _ in range(4):
                    engine.poll(s, gw)
                self.assertEqual(get_job(s.database_path, job_id)["status"], "GENERATION_DONE")
                done = workflow.resume_after_generation(s, job_id)
            job = get_job(s.database_path, job_id)
            self.assertEqual(done["status"], "READY_FOR_REVIEW", job["last_error"])
            scenes = json.loads(job["script_json"])["scenes"]
            self.assertEqual([sc["kind"] for sc in scenes], ["video", "video"])
            self.assertTrue(all(sc["generated"] for sc in scenes))


if __name__ == "__main__":
    unittest.main()
