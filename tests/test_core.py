import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from agent_core.ai_client import AIClient, AIClientError
from agent_core.affiliate_client import ShopeeAffiliateClient
from agent_core.db import get_job, init_db, register_job, update_job
from agent_core.product_matcher import rank_products
from agent_core.quality import validate_shopee_video
from agent_core.workflow import (
    WorkflowError,
    _image_messages,
    _normalise_hashtags,
    _parse_json_reply,
    select_product_for_job,
    publish_facebook_reel_job,
    upload_shopee_draft,
)
from agent_core.settings import get_settings


class WorkflowCoreTests(unittest.TestCase):
    def test_json_response_parser(self):
        self.assertEqual(
            _parse_json_reply({"content": '{"ok":true,"count":2}'}),
            {"ok": True, "count": 2},
        )

    def test_hashtag_normalisation(self):
        self.assertEqual(
            _normalise_hashtags(["#home_decor", "review items", "#home_decor"]),
            ["#home_decor", "#reviewitems"],
        )

    def test_anthropic_image_message_shape(self):
        with tempfile.TemporaryDirectory() as temp:
            frame = Path(temp) / "frame.jpg"
            frame.write_bytes(b"test-image-bytes")
            message = _image_messages("Analyze this frame", [frame], "anthropic-messages")
            self.assertEqual(message[0]["content"][0]["type"], "text")
            self.assertEqual(message[0]["content"][1]["type"], "image")
            self.assertEqual(
                message[0]["content"][1]["source"]["media_type"], "image/jpeg"
            )

    def test_openai_image_message_shape(self):
        with tempfile.TemporaryDirectory() as temp:
            frame = Path(temp) / "frame.jpg"
            frame.write_bytes(b"test-image-bytes")
            message = _image_messages("Analyze this frame", [frame], "openai")
            image = message[0]["content"][1]
            self.assertEqual(image["type"], "image_url")
            self.assertTrue(image["image_url"]["url"].startswith("data:image/jpeg;base64,"))

    def test_product_matcher_requires_clear_match(self):
        offers = [
            {"itemId": "1", "productName": "mini blender portable"},
            {"itemId": "2", "productName": "mens cotton shirt"},
        ]
        result = rank_products(
            "mini blender portable", offers, min_score=68, min_margin=10
        )
        self.assertEqual(result["status"], "MATCHED")
        self.assertEqual(result["product"]["item_id"], "1")

    def test_database_schema_migration_and_update(self):
        with tempfile.TemporaryDirectory() as temp:
            db = Path(temp) / "test.sqlite3"
            init_db(db)
            inserted = register_job(db, {
                "id": "job-1",
                "source_path": "C:/video.mp4",
                "sha256": "a" * 64,
                "file_name": "video.mp4",
                "duration_sec": 6,
                "width": 720,
                "height": 1280,
                "fps": "24/1",
            })
            self.assertTrue(inserted)
            update_job(db, "job-1", status="ANALYZED", analysis_json='{"topic":"demo"}')
            job = get_job(db, "job-1")
            self.assertEqual(job["status"], "ANALYZED")
            self.assertEqual(json.loads(job["analysis_json"])["topic"], "demo")
            self.assertEqual(job["review_status"], "PENDING")
            self.assertIsNone(job["facebook_output_path"])

    def test_ai_client_rejects_empty_key(self):
        with self.assertRaises(AIClientError):
            AIClient("http://127.0.0.1:13000/v1", "", "test-model")

    def test_openai_client_passes_chatgpt2api_thinking_effort(self):
        fake_response = SimpleNamespace(
            status_code=200,
            is_error=False,
            json=lambda: {"choices": [{"message": {"content": "ok"}}]},
        )
        with patch("agent_core.ai_client.httpx.post", return_value=fake_response) as post:
            client = AIClient(
                "http://127.0.0.1:13000/v1",
                "local-test-key",
                "gpt-6-astra-wm",
                protocol="openai",
                thinking_effort="extended",
            )
            result = client.chat([{"role": "user", "content": "test"}])
        self.assertEqual(result["content"], "ok")
        self.assertEqual(post.call_args.kwargs["json"]["thinking_effort"], "extended")
        self.assertEqual(post.call_args.args[0], "http://127.0.0.1:13000/v1/chat/completions")

    def test_affiliate_client_rejects_non_shopee_url_without_network(self):
        client = ShopeeAffiliateClient(get_settings())
        with self.assertRaises(ValueError):
            client.create_short_link("https://example.com/product")

    def test_shopee_draft_requires_confirmation(self):
        settings = SimpleNamespace(database_path=Path("unused.sqlite3"))
        with self.assertRaises(WorkflowError):
            upload_shopee_draft(settings, "job-1", confirmed=False)

    def test_product_selection_requires_confirmation(self):
        settings = SimpleNamespace(database_path=Path("unused.sqlite3"))
        with self.assertRaises(WorkflowError):
            select_product_for_job(settings, "job-1", "123", confirmed=False)

    def test_facebook_publish_requires_confirmation(self):
        settings = SimpleNamespace(auto_publish=False)
        with self.assertRaises(WorkflowError):
            publish_facebook_reel_job(settings, "job-1", confirmed=False)

    def test_facebook_publish_stays_disabled_by_default(self):
        settings = SimpleNamespace(auto_publish=False)
        with self.assertRaises(WorkflowError):
            publish_facebook_reel_job(settings, "job-1", confirmed=True)

    def test_shopee_qa_rejects_missing_file(self):
        result = validate_shopee_video(
            Path("missing-video.mp4"),
            {"duration_sec": 5, "width": 720, "height": 1280},
        )
        self.assertFalse(result.passed)
        self.assertIn("Video file does not exist", result.reasons)


if __name__ == "__main__":
    unittest.main()
