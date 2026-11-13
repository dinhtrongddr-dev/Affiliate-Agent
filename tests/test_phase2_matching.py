"""Phase 2: stricter trend-product matching and link checks."""
from types import SimpleNamespace

from agent_core.trend_intel import matcher


def test_generic_head_words_do_not_match():
    cable = "Cáp Sạc Android 1M Micro USB Sạc Nhanh Cho Realme"
    assert matcher._relevance([("máy xay sạc USB", 1.0)], cable) < matcher.MIN_RELEVANCE
    assert matcher._relevance([("mini", 1.0)], "Móc khoá gấu bông mini") == 0
    blender = "Máy xay sinh tố mini cầm tay sạc USB"
    assert matcher._relevance([("máy xay sinh tố mini", 1.0)], blender) >= matcher.MIN_RELEVANCE


def _settings(tmp_path, floor=30):
    return SimpleNamespace(database_path=tmp_path / "t.sqlite3", trend_min_purchase_intent=floor)


def test_every_pair_is_verified_cached_and_low_intent_needs_an_exact_product(tmp_path):
    settings = _settings(tmp_path)
    hot = {"key": "may-xay", "keyword": "máy xay mini", "intent": {"purchase_intent": 90}}
    news = {"key": "u23", "keyword": "lịch thi đấu u23", "intent": {"purchase_intent": 32}}
    items = [(hot, [{"product_id": "p1", "name": "Máy xay mini", "score": 80, "reason": ""},
                    {"product_id": "p2", "name": "Cáp sạc USB", "score": 70, "reason": ""}]),
             (news, [{"product_id": "p3", "name": "Loa bluetooth", "score": 75, "reason": ""}])]
    calls = []

    def ask(prompt):
        calls.append(prompt)
        return {"pairs": [{"pair": 0, "fit": 95, "reason": "đúng món"},
                          {"pair": 1, "fit": 10, "reason": "sai loại"},
                          {"pair": 2, "fit": 60, "reason": "cổ vũ"}]}

    out = matcher.verify_matches(settings, items, ask=ask)
    assert [m["product_id"] for m in out["may-xay"]] == ["p1"]
    assert out["u23"] == []                       # 60 < 75 required for a low-intent trend
    assert len(calls) == 1                        # all pairs in one batch
    again = matcher.verify_matches(settings, items, ask=lambda p: calls.append(p))
    assert len(calls) == 1 and [m["product_id"] for m in again["may-xay"]] == ["p1"]  # cached


def test_unverified_pairs_are_never_shown(tmp_path):
    def broken(prompt):
        raise RuntimeError("AI down")
    out = matcher.verify_matches(_settings(tmp_path), [({"key": "k", "keyword": "x", "intent": {}},
                                  [{"product_id": "p", "name": "y", "score": 90, "reason": ""}])], ask=broken)
    assert out == {"k": []}


def test_hashtags_drop_internal_and_generic_english():
    from agent_core.workflow import _normalise_hashtags
    tags = _normalise_hashtags(["#ViralCreator", "#DogRunning", "#CountrysideLife", "#HieuMe",
                                "#TinhCamGiaDinh", "#hieume", "#CayLauNha", "#MeoDonNha", "#Extra"])
    assert tags == ["#HieuMe", "#TinhCamGiaDinh", "#CayLauNha", "#MeoDonNha", "#Extra"]


def test_link_check_blocks_wrong_or_missing_review_links():
    import json
    from agent_core.workflow import link_check
    review = {"id": "j1", "brief_json": json.dumps({"creation_branch": "product_review",
                                                    "offer": {"itemId": "111"}})}
    assert link_check(None, {**review, "affiliate_link": ""})["blockers"]
    wrong = link_check(None, {**review, "affiliate_link": "https://s.shopee.vn/x", "product_item_id": "222"})
    assert any("sản phẩm khác" in b for b in wrong["blockers"])
    ok = link_check(None, {**review, "affiliate_link": "https://s.shopee.vn/x", "product_item_id": "111"})
    assert not ok["blockers"] and any("Sub_id" in n for n in ok["notes"])
    tracked = {**review, "brief_json": json.dumps({"creation_branch": "product_review",
                                                   "offer": {"itemId": "111"}, "link_has_sub_id": True})}
    assert not link_check(None, {**tracked, "affiliate_link": "https://s.shopee.vn/x",
                                 "product_item_id": "111"})["notes"]
    assert link_check(None, {**review, "affiliate_link": "https://evil.example/x",
                             "product_item_id": "111"})["blockers"]


def test_hook_variants_are_short_distinct_and_kept_in_the_storyboard():
    from agent_core import creative
    plan = creative.normalise_plan({
        "creation_branch": "viral_creator", "hook": "Ai hiểu mẹ nhất?",
        "hook_variants": ["Ai hiểu mẹ nhất?", "Bố tự chấm 10 điểm, nhưng câu cuối khiến cả nhà im lặng",
                          "Câu hỏi làm cả nhà im"],
        "scenes": [{"visual": "generate", "shot": {"action": "x"}}]}, "trend")
    assert plan["hook_variants"] == ["Bố tự chấm 10 điểm", "Câu hỏi làm cả nhà im"]


def test_use_hook_variant_swaps_files_and_hooks(tmp_path):
    import json
    from agent_core import db, workflow
    from agent_core.settings import Settings
    s = Settings(_env_file=None, project_root=tmp_path, database_path=tmp_path / "d.sqlite3",
                 media_root=tmp_path / "m")
    out_dir = tmp_path / "out"
    out_dir.mkdir()
    main, alt = out_dir / "j.mp4", out_dir / "j_hook2.mp4"
    main.write_bytes(b"A")
    alt.write_bytes(b"B")
    (out_dir / "manifest.json").write_text(json.dumps({"timeline": {"hook": "Hook A"}, "hook_variants": [
        {"index": 1, "hook": "Hook A", "path": str(main)}, {"index": 2, "hook": "Hook B", "path": str(alt)}]}),
        encoding="utf-8")
    db.register_job(s.database_path, {"id": "j", "source_path": "", "sha256": "x", "file_name": "j"})
    db.update_job(s.database_path, "j", status="READY_FOR_REVIEW", output_path=str(main),
                  script_json=json.dumps({"hook": "Hook A", "hook_variants": ["Hook B"], "scenes": []}),
                  content_json="{}")
    assert workflow.use_hook_variant(s, "j", 2) == {"hook": "Hook B"}
    assert main.read_bytes() == b"B" and alt.read_bytes() == b"A"
    workflow.use_hook_variant(s, "j", 2)                      # and back again
    assert main.read_bytes() == b"A" and alt.read_bytes() == b"B"
    job = db.get_job(s.database_path, "j")
    assert json.loads(job["script_json"])["hook"] == "Hook A"
