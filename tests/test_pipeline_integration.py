import hashlib
import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from pydantic import SecretStr

from agent_core.db import get_job, list_platform_posts, register_job
from agent_core.media import probe_video
from agent_core.settings import get_settings
from agent_core.workflow import WorkflowError, process_job, upload_shopee_draft


class FakeAI:
    def __init__(self):
        self.calls = 0

    def chat(self, messages, **kwargs):
        self.calls += 1
        if self.calls == 1:
            return {"content": json.dumps({
                "language": "vi",
                "video_type": "product_demo",
                "topic": "portable blender",
                "visual_summary": "A person shows a portable mini blender cup.",
                "product_category": "portable mini blender",
                "visible_brand": "",
                "product_search_keywords": ["portable mini blender", "mini blender cup"],
                "video_hook": "Một chiếc máy xay mini gọn nhẹ.",
                "observed_features": ["portable cup shape"],
                "uncertainties": ["capacity not visible"],
            })}
        return {"content": json.dumps({
            "title": "Portable mini blender",
            "caption": "Một chiếc máy xay mini nhỏ gọn, tiện mang theo.",
            "hashtags": ["#portable_blender", "#kitchen"],
            "cover_ratio": 0.2,
            "facebook_description": "Video giới thiệu máy xay mini.",
        })}


class FakeAffiliate:
    def credentials_status(self):
        return {
            "sections": {
                "shopee_affiliate": {"configured": True}
            }
        }

    def search_offers(self, keyword, limit=20):
        return [{
            "productName": "Portable mini blender cup",
            "itemId": "123456789",
            "commissionRate": "5%",
            "priceMin": "100000",
            "offerLink": "https://shopee.vn/product/123456789",
            "imageUrl": "",
        }]

    def create_short_link(self, product_url, sub_id=""):
        return "https://s.shopee.vn/test-affiliate-link"


class PipelineIntegrationTests(unittest.TestCase):
    def test_prepare_review_package_and_draft_duplicate_guard(self):
        ffmpeg = shutil.which("ffmpeg")
        self.assertTrue(ffmpeg, "ffmpeg must be available for the integration smoke test")

        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            project = root / "project"
            media = root / "media"
            project.mkdir()
            source = root / "input.mp4"
            command = [
                ffmpeg, "-hide_banner", "-loglevel", "error", "-y",
                "-f", "lavfi", "-i", "testsrc2=size=720x1280:rate=24",
                "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=44100",
                "-t", "6", "-c:v", "libx264", "-pix_fmt", "yuv420p",
                "-c:a", "aac", "-shortest", str(source),
            ]
            subprocess.run(command, check=True, capture_output=True)

            settings = get_settings()
            settings.project_root = project
            settings.media_root = media
            settings.database_path = root / "state" / "test.sqlite3"
            settings.shopee_affiliate_enabled = False
            settings.ai_api_key = SecretStr("test-only")
            settings.facebook_min_duration_seconds = 4
            settings.facebook_max_duration_seconds = 60
            settings.facebook_min_width = 540
            settings.facebook_min_height = 960

            metadata = probe_video(source, settings.ffprobe_path)
            digest = hashlib.sha256(source.read_bytes()).hexdigest()
            job_id = digest[:24]
            register_job(settings.database_path, {
                "id": job_id,
                "source_path": str(source),
                "sha256": digest,
                "file_name": source.name,
                **metadata,
            })

            fake_ai = FakeAI()
            with patch("agent_core.workflow._client", return_value=fake_ai):
                waiting = process_job(settings, job_id)
            self.assertEqual(waiting["status"], "WAITING_FOR_SHOPEE_CREDENTIALS")
            self.assertEqual(fake_ai.calls, 1)

            settings.shopee_affiliate_enabled = True
            with patch("agent_core.workflow._client", return_value=fake_ai), patch(
                "agent_core.workflow.ShopeeAffiliateClient", return_value=FakeAffiliate()
            ):
                result = process_job(settings, job_id)

            self.assertEqual(result["status"], "READY_FOR_REVIEW")
            self.assertTrue(result["shopee_ready"])
            self.assertTrue(result["facebook_ready"])
            self.assertTrue(Path(result["output_path"]).is_file())
            saved_job = get_job(settings.database_path, job_id)
            self.assertTrue(Path(saved_job["facebook_output_path"]).is_file())
            self.assertTrue(
                (media / "outbox" / "shopee" / job_id / "manifest.json").is_file()
            )
            self.assertTrue(
                (media / "outbox" / "shopee" / job_id / "review.html").is_file()
            )
            self.assertEqual(fake_ai.calls, 2)

            fake_upload_result = {
                "status": "DRAFT_SUBMITTED_UNCONFIRMED",
                "result": {"status": "DRAFT_SUBMITTED_UNCONFIRMED"},
            }
            with patch(
                "agent_core.workflow.upload_shopee_video",
                return_value=fake_upload_result,
            ) as upload_mock:
                draft = upload_shopee_draft(settings, job_id, confirmed=True)
                self.assertEqual(draft["status"], "DRAFT_SUBMITTED_UNCONFIRMED")
                self.assertEqual(upload_mock.call_count, 1)
                with self.assertRaises(WorkflowError):
                    upload_shopee_draft(settings, job_id, confirmed=True)
                self.assertEqual(upload_mock.call_count, 1)

            updated = get_job(settings.database_path, job_id)
            posts = list_platform_posts(settings.database_path, job_id)
            self.assertEqual(updated["status"], "DRAFT_SUBMITTED_UNCONFIRMED")
            self.assertEqual(posts[0]["status"], "DRAFT_SUBMITTED_UNCONFIRMED")


if __name__ == "__main__":
    unittest.main()
