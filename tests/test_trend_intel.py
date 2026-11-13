import json
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx
from pydantic import SecretStr

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "services"))

from agent_core import catalog, db  # noqa: E402
from agent_core.settings import get_settings  # noqa: E402
from agent_core.trend_intel import (analyzer, collectors, matcher, pipeline, safety,  # noqa: E402
                                    scoring, store)

NOW = datetime(2026, 11, 12, 12, 0, tzinfo=timezone.utc)

TRENDS_RSS = """<?xml version="1.0" encoding="UTF-8"?>
<rss xmlns:ht="https://trends.google.com/trending/rss" version="2.0"><channel>
<item><title>nồi chiên không dầu</title><ht:approx_traffic>20000+</ht:approx_traffic>
<pubDate>Thu, 12 Nov 2026 01:20:00 -0800</pubDate>
<ht:news_item><ht:news_item_title>Mẹo dùng nồi chiên</ht:news_item_title>
<ht:news_item_url>https://vnexpress.net/a</ht:news_item_url><ht:news_item_source>VnExpress</ht:news_item_source></ht:news_item>
</item>
<item><title>xổ số miền nam ngày 12 tháng 11</title><ht:approx_traffic>200000+</ht:approx_traffic></item>
</channel></rss>"""


def news_rss(ages_hours: list[float]) -> str:
    items = "".join(
        f"<item><title>Bài {i}</title><pubDate>"
        f"{(NOW - timedelta(hours=h)).strftime('%a, %d %b %Y %H:%M:%S GMT')}</pubDate></item>"
        for i, h in enumerate(ages_hours))
    return f'<?xml version="1.0"?><rss version="2.0"><channel>{items}</channel></rss>'


CSV = (
    "Mã sản phẩm,Tên sản phẩm,Giá,Doanh thu,Tên cửa hàng,Tỉ lệ hoa hồng,Hoa hồng,"
    "Link sản phẩm,Link ưu đãi\n"
    '1,Nồi chiên không dầu Lock&Lock 5L,"1.290,0k",120000000,Shop A,8%,₫103.200,'
    "https://shopee.vn/product/1/1,https://s.shopee.vn/a1\n"
    '2,Bình giữ nhiệt inox 500ml,"99,0k",5000000,Shop B,12%,₫11.880,'
    "https://shopee.vn/product/1/2,https://s.shopee.vn/b2\n"
)


def temp_settings(root: Path):
    settings = get_settings()
    settings.project_root = root
    settings.media_root = root / "media"
    settings.database_path = root / "state.sqlite3"
    settings.shopee_skill_home = root / "shopee"
    settings.ai_api_key = SecretStr("")
    settings.youtube_api_key = SecretStr("")
    settings.trendsmcp_api_key = SecretStr("")
    return settings


def mock(handler):
    return httpx.MockTransport(handler)


class CollectorTests(unittest.TestCase):
    def test_parse_count(self):
        for raw, want in [("200000+", 200000), ("2K+", 2000), ("1,2tr", 1_200_000),
                          ("3.5M", 3_500_000), ("12.400", 12400), ("", None), ("abc", None)]:
            self.assertEqual(collectors.parse_count(raw), want, raw)

    def test_google_trends_rss(self):
        transport = mock(lambda r: httpx.Response(200, text=TRENDS_RSS))
        signals = collectors.google_trends(None, transport=transport)
        self.assertEqual(len(signals), 2)
        self.assertEqual(signals[0]["keyword"], "nồi chiên không dầu")
        self.assertEqual(signals[0]["value"], 20000)
        self.assertEqual(signals[0]["url"], "https://vnexpress.net/a")
        self.assertEqual(signals[1]["rank"], 2)

    def test_google_news_windows(self):
        seen = {}

        def handler(request):
            seen.update(dict(request.url.params))
            return httpx.Response(200, text=news_rss([2, 5, 30, 50, 100, 200, 250, 400]))

        sig = collectors.google_news(None, "nồi chiên", transport=mock(handler), now=NOW)
        self.assertEqual(seen["gl"], "VN")
        self.assertEqual(sig["meta"]["last24h"], 2)
        self.assertEqual(sig["meta"]["last7d"], 5)
        self.assertEqual(sig["meta"]["prev7d"], 2)
        self.assertEqual(sig["value"], 5.0)

    def test_youtube_uses_key_and_sums_stats(self):
        settings = temp_settings(Path(tempfile.gettempdir()))
        settings.youtube_api_key = SecretStr("k")

        def handler(request):
            if request.url.path.endswith("/search"):
                self.assertEqual(request.url.params["regionCode"], "VN")
                return httpx.Response(200, json={"items": [{"id": {"videoId": "a"}},
                                                           {"id": {"videoId": "b"}}]})
            return httpx.Response(200, json={"items": [
                {"id": "a", "snippet": {"title": "A"},
                 "statistics": {"viewCount": "1000", "likeCount": "50", "commentCount": "10"}},
                {"id": "b", "snippet": {"title": "B"}, "statistics": {"viewCount": "3000"}}]})

        sig = collectors.youtube(settings, "máy xay", transport=mock(handler), now=NOW)
        self.assertEqual(sig["meta"]["views"], 4000)
        self.assertEqual(sig["meta"]["engagement_rate"], 0.015)
        self.assertTrue(sig["url"].endswith("v=b"))

    def test_youtube_error_is_reported(self):
        settings = temp_settings(Path(tempfile.gettempdir()))
        settings.youtube_api_key = SecretStr("k")
        transport = mock(lambda r: httpx.Response(403, json={"error": {"message": "quota"}}))
        with self.assertRaises(collectors.CollectorError):
            collectors.youtube(settings, "x", transport=transport)

    def test_trendsmcp_rows_are_parsed_defensively(self):
        # Response shape is not documented in the README; the parser accepts common layouts.
        settings = temp_settings(Path(tempfile.gettempdir()))
        settings.trendsmcp_api_key = SecretStr("k")
        payload = {"data": [{"name": "#bepnhaminh", "rank": 1, "views": 12500000},
                            {"hashtag": "reviewdogiadung"}]}
        transport = mock(lambda r: httpx.Response(200, json=payload))
        sigs = collectors.trendsmcp_board(settings, transport=transport)
        self.assertEqual([s["keyword"] for s in sigs], ["bepnhaminh", "reviewdogiadung"])
        self.assertEqual(sigs[0]["value"], 12500000)
        self.assertEqual(sigs[1]["rank"], 2)

    def test_manual_paste(self):
        sigs = collectors.parse_manual("#bepnhaminh | 12,5M | https://www.tiktok.com/tag/x\n\n"
                                       "nồi chiên không dầu\n")
        self.assertEqual(len(sigs), 2)
        self.assertEqual(sigs[0]["value"], 12_500_000)
        self.assertEqual(sigs[0]["url"], "https://www.tiktok.com/tag/x")
        self.assertIsNone(sigs[1]["value"])


class AnalyserScoringTests(unittest.TestCase):
    def obs(self, values, source="google_trends", start_hours=60):
        out = []
        for i, v in enumerate(values):
            at = (NOW - timedelta(hours=start_hours - i * 6)).isoformat(timespec="seconds")
            out.append({"source": source, "observed_at": at, "value": v, "rank": None, "meta": {}})
        return out

    def topic(self, hours_first=60):
        stamp = (NOW - timedelta(hours=hours_first)).isoformat(timespec="seconds")
        return {"keyword": "nồi chiên không dầu", "first_seen": stamp,
                "last_seen": NOW.isoformat(timespec="seconds"), "intent": None}

    def test_series_growth_acceleration_z(self):
        a = analyzer.analyse(self.topic(), self.obs([100, 100, 110, 200]), NOW)
        self.assertAlmostEqual(a["growth"], 200 / 110 - 1, places=3)
        self.assertGreater(a["acceleration"], 0)
        self.assertGreater(a["z_score"], 2)
        self.assertEqual(analyzer.lifecycle(a), "RISING")

    def test_news_window_growth(self):
        obs = [{"source": "google_news", "observed_at": NOW.isoformat(), "value": 6.0, "rank": None,
                "meta": {"last24h": 3, "last7d": 6, "prev7d": 2, "capped": False}}]
        a = analyzer.analyse(self.topic(5), obs, NOW)
        self.assertEqual(a["growth"], 2.0)
        self.assertEqual(a["growth_method"], "news_window")
        capped = [{**obs[0], "meta": {**obs[0]["meta"], "capped": True}}]
        self.assertIsNone(analyzer.analyse(self.topic(5), capped, NOW)["growth"])

    def test_single_observation_is_provisional(self):
        result = scoring.score_topic(self.topic(2), self.obs([20000], start_hours=1), now=NOW)
        self.assertEqual(result["status"], "PROVISIONAL")
        self.assertIsNone(result["components"]["growth"])
        self.assertIsNone(result["components"]["affiliate"])
        self.assertIsNotNone(result["total"])
        self.assertEqual(result["lifecycle"], "NEW")

    def test_declining_and_weights(self):
        t = self.topic()
        res = scoring.score_topic(t, self.obs([500, 400, 200]), now=NOW)
        self.assertEqual(res["lifecycle"], "DECLINING")
        self.assertLess(res["components"]["growth"], 50)

        class S:
            trend_weights = json.dumps({"affiliate": 0, "growth": 1})

        self.assertEqual(scoring.weights(S())["growth"], 1.0)
        self.assertNotIn("affiliate", scoring.weights(S()))
        self.assertEqual(res["affiliate_factor"], scoring.UNKNOWN_AFFILIATE_FACTOR)
        self.assertAlmostEqual(res["total"], res["trend_score"] * 0.65, places=0)

    def test_affiliate_component(self):
        self.assertEqual(scoring.affiliate_score({"commercial": False, "purchase_intent": 90}, 80), 0)
        self.assertEqual(scoring.affiliate_score({"commercial": True, "purchase_intent": 80}, None), 48)
        self.assertEqual(scoring.affiliate_score({"commercial": True, "purchase_intent": 80}, 50), 68)


class SafetyTests(unittest.TestCase):
    def test_blocks_and_allows(self):
        for text in ["xổ số miền nam ngày 12", "XSMN hom nay", "tai nạn liên hoàn", "ca sĩ qua đời",
                     "bị bắt vì lừa đảo", "siêu bão số 13"]:
            self.assertTrue(safety.check(text)[0], text)
        for text in ["bát đĩa sứ", "bão giá 11.11", "nồi chiên không dầu", "bật mí cách nấu"]:
            self.assertFalse(safety.check(text)[0], text)
        self.assertTrue(safety.check("Hà Anh", "Tai nạn giao thông")[0])


class MatcherTests(unittest.TestCase):
    def test_relevance_and_scoring(self):
        products = [
            {"id": "shopee:1", "item_id": "1", "name": "Nồi chiên không dầu Lock&Lock 5L",
             "affiliate_link": "https://s.shopee.vn/a", "commission_rate": 8, "price": 1290000,
             "revenue": 129000000, "updated_at": NOW.isoformat()},
            {"id": "shopee:2", "item_id": "2", "name": "Bình giữ nhiệt inox",
             "affiliate_link": "https://s.shopee.vn/b", "commission_rate": 12},
            {"id": "shopee:3", "item_id": "3", "name": "Nồi chiên mini", "affiliate_link": ""},
        ]
        topic = {"keyword": "mẹo nấu ăn nồi chiên", "intent": {
            "product_keywords": ["nồi chiên không dầu"], "categories": ["Đồ gia dụng"]}}
        found = matcher.match_products(None, topic, products=products)
        self.assertEqual([m["product_id"] for m in found], ["shopee:1"])
        self.assertGreater(found[0]["relevance"], 90)

    def test_filler_words_do_not_match(self):
        products = [
            {"id": "a", "name": "Cáp sạc USB mini Micro", "affiliate_link": "https://s.shopee.vn/a"},
            {"id": "b", "name": "Áo giữ nhiệt lót nỉ nữ", "affiliate_link": "https://s.shopee.vn/b"},
            {"id": "c", "name": "Máy xay sinh tố cầm tay mini sạc USB",
             "affiliate_link": "https://s.shopee.vn/c"},
            {"id": "d", "name": "Son Bóng 3CE Glazy Lip", "affiliate_link": "https://s.shopee.vn/d"},
        ]
        fryer = {"keyword": "máy xay mini", "intent": {
            "product_keywords": ["máy xay sạc USB", "bình giữ nhiệt mini"]}}
        self.assertEqual([m["product_id"] for m in matcher.match_products(None, fryer, products=products)],
                         ["c"])
        broad = {"keyword": "bình giữ nhiệt", "intent": {"product_keywords": ["bình giữ nhiệt"],
                                                         "categories": ["Đồ gia dụng"]}}
        stand = [{"id": "e", "name": "Giá đỡ đứng điện thoại", "affiliate_link": "https://s.shopee.vn/e"}]
        self.assertEqual(matcher.match_products(None, broad, products=stand), [])
        lips = {"keyword": "son môi", "intent": {"product_keywords": ["son tint bóng"]}}
        self.assertEqual([m["product_id"] for m in matcher.match_products(None, lips, products=products)],
                         ["d"])

    def test_rerank_veto(self):
        matches = [{"product_id": "a", "name": "A", "score": 80, "reason": ""},
                   {"product_id": "b", "name": "B", "score": 70, "reason": ""}]
        reply = {"ranking": [{"product_id": "a", "fit": 10, "reason": "không liên quan"},
                             {"product_id": "b", "fit": 90}]}
        out = matcher.rerank(None, {"keyword": "x"}, matches, ask=lambda p: reply)
        self.assertEqual([m["product_id"] for m in out], ["b"])

    def test_intents_are_validated(self):
        topics = [{"key": "a", "keyword": "A"}, {"key": "b", "keyword": "B"}]
        reply = {"topics": [
            {"key": "a", "commercial": True, "purchase_intent": 150, "product_keywords": ["x"]},
            {"key": "zzz", "commercial": True},
        ]}
        out = matcher.analyse_intents(None, topics, ask=lambda p: reply)
        self.assertEqual(list(out), ["a"])
        self.assertEqual(out["a"]["purchase_intent"], 100)


class PipelineTests(unittest.TestCase):
    def test_end_to_end_without_network(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            settings = temp_settings(Path(tmp))
            path = settings.database_path
            catalog.import_products(path, catalog.parse_csv(CSV.encode()))
            store.record_signal(path, {"source": "google_trends", "keyword": "nồi chiên không dầu",
                                       "value": 20000, "rank": 1})
            store.record_signal(path, {"source": "google_trends", "keyword": "xổ số miền nam",
                                       "value": 200000, "rank": 2})
            store.record_signal(path, {"source": "google_trends", "keyword": "Real Madrid",
                                       "value": 50000, "rank": 3})
            calls = []

            def ask(prompt):
                calls.append(prompt)
                if "kiểm duyệt ghép trend" in prompt:   # every pair is verified now
                    return {"pairs": [{"pair": 0, "fit": 90, "reason": "đúng"}]}
                return {"topics": [
                    {"key": "noi chien khong dau", "commercial": True, "purchase_intent": 85,
                     "product_keywords": ["nồi chiên không dầu"], "angle": "mẹo nấu nhanh"},
                    {"key": "real madrid", "commercial": False, "purchase_intent": 10,
                     "reason": "tin thể thao"}]}

            summary = pipeline.run(settings, collect=False, ask=ask)
            self.assertEqual(summary["blocked_by_rules"], 1)
            self.assertEqual(summary["ai_analysed"], 2)
            self.assertNotIn("xo so", "".join(calls))  # blocked topics never reach the AI
            fryer = store.get_topic(path, "noi chien khong dau")
            self.assertEqual(fryer["status"], "MATCHED")
            self.assertEqual(store.matches_for(path, fryer["key"])[0]["product_id"], "shopee:1")
            self.assertIsNotNone(fryer["scores"]["components"]["affiliate"])
            self.assertEqual(store.get_topic(path, "real madrid")["status"], "NOT_COMMERCIAL")
            lottery = store.get_topic(path, "xo so mien nam")
            self.assertEqual(lottery["safety"], "BLOCKED")
            self.assertIsNone(lottery["total_score"])
            sport = store.get_topic(path, "real madrid")
            self.assertLess(sport["total_score"], fryer["total_score"])  # no buying intent ranks low
            # second run: intents are cached, no new AI intent call
            before = len(calls)
            pipeline.run(settings, collect=False, ask=ask)
            self.assertFalse(any("Trend:" in c for c in calls[before:]))

    def test_budget(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            path = Path(tmp) / "s.sqlite3"
            self.assertTrue(store.consume(path, "x", 2))
            self.assertTrue(store.consume(path, "x", 2))
            self.assertFalse(store.consume(path, "x", 2))
            self.assertEqual(store.usage(path, "x"), 2)


class WebTests(unittest.TestCase):
    def test_trend_page_and_job_from_trend(self):
        from fastapi.testclient import TestClient

        from agent_core.web.app import create_app

        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            app = create_app(start_worker=False)
            settings = temp_settings(Path(tmp))
            app.state.ctx["settings"] = settings
            path = settings.database_path
            catalog.import_products(path, catalog.parse_csv(CSV.encode()))
            key = store.record_signal(path, {"source": "google_trends",
                                             "keyword": "nồi chiên không dầu", "value": 20000})
            store.update_topic(path, key, safety="OK", status="MATCHED",
                               intent_json={"commercial": True, "purchase_intent": 80,
                                            "angle": "mẹo", "product_keywords": ["nồi chiên"]})
            store.save_matches(path, key, [{"product_id": "shopee:1", "score": 80}])
            pipeline.rescore(settings, key)
            h = {"X-Requested-With": "affiliate-agent"}
            with TestClient(app) as client:
                for view in ("products", "all", "skipped"):
                    page = client.get(f"/trends?view={view}")
                    self.assertEqual(page.status_code, 200)
                self.assertIn("Nồi chiên không dầu Lock", client.get("/trends").text)
                res = client.post("/api/intel/create-job", json={"key": key,
                                                                 "product_id": "shopee:1"}, headers=h)
                self.assertEqual(res.status_code, 200, res.text)
                job = db.get_job(path, res.json()["job_id"])
                self.assertFalse(job["affiliate_link"])
                self.assertEqual(json.loads(job["brief_json"])["creation_branch"], "viral_creator")
                self.assertEqual(store.get_topic(path, key)["status"], "USED")
                manual = client.post("/api/intel/manual", json={"text": "#bepnhaminh | 2M"},
                                     headers=h)
                self.assertEqual(manual.json()["added"], 1)
                self.assertEqual(client.post("/api/intel/manual", json={"text": " "},
                                             headers=h).status_code, 400)


if __name__ == "__main__":
    unittest.main()
