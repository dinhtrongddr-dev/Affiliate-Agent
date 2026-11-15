"""Series editor: per-character voice and personality, art style, series tone, desired script."""
import json
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from agent_core import creative, creative_series, db, style_profiles, workflow
from agent_core.creation_branches import normalise_brief
from agent_core.settings import Settings

H = {"X-Requested-With": "affiliate-agent"}
SERIES = {
    "id": "nha-meo", "label": "Nhà mèo Mochi", "style_profile": "felt_craft", "style": "len ấm",
    "setting": "căn nhà len tí hon", "voice_mode": "dialogue",
    "script_notes": "Chuyện chữa lành, mỗi tập một bài học nhỏ",
    "characters": [
        {"id": "mochi", "name": "Mochi", "age": 5, "gender": "female", "role": "daughter",
         "personality": "Hay ngại nhưng tốt bụng", "voice": "Ái Hân", "appearance": "mèo len trắng"},
        {"id": "bo-meo", "name": "Bố Mèo", "age": 35, "gender": "male", "role": "father",
         "personality": "Hay pha trò", "voice": "Mạnh Dũng", "wardrobe": "khăn quàng đỏ"}],
    "continuity_rules": ["Giữ nguyên màu len"]}


@pytest.fixture
def s(tmp_path):
    return Settings(_env_file=None, project_root=tmp_path, database_path=tmp_path / "t.sqlite3",
                    media_root=tmp_path / "media", muse_embedded=False)


def test_trending_styles_are_listed_first_and_drawn_stylised():
    rows = style_profiles.catalog()
    trend = [r for r in rows if r["trend"]]
    assert len(trend) >= 8 and rows[:len(trend)] == trend
    for name in ("feature_3d", "anime_2d", "clay_stopmotion", "toy_figure", "felt_craft", "storybook_2d",
                 "cartoon_2d", "chibi_3d"):
        assert style_profiles.is_animated(name), name
    assert not style_profiles.is_animated("retro_film")      # real people, film look
    from agent_core import muse_prompt
    sheet = muse_prompt.character_sheet_prompt(SERIES["characters"][0], {"style_profile": "felt_craft"})
    assert "needle-felted" in sheet and "big head" not in sheet


def test_template_keeps_voice_personality_style_and_notes(s):
    saved = creative_series.save_template(s, SERIES)
    mochi = saved["characters"][0]
    assert mochi["voice"] == "Ái Hân" and mochi["personality"] == "Hay ngại nhưng tốt bụng"
    assert mochi["gender"] == "female" and mochi["appearance"] == "mèo len trắng"
    assert saved["style_profile"] == "felt_craft" and saved["script_notes"].startswith("Chuyện chữa lành")
    assert saved["version"] == 1 and creative_series.save_template(s, SERIES)["version"] == 2   # versions
    for bad in ({**SERIES, "style_profile": "nope"},
                {**SERIES, "characters": [{**SERIES["characters"][0], "voice": "Siri"}]},
                {**SERIES, "characters": [{"id": "x", "name": "X", "age": 3}]}):        # nothing about X
        with pytest.raises(ValueError):
            creative_series.save_template(s, bad)


def test_old_suri_gets_voices_and_personality(s):
    old = {k: v for k, v in creative_series._BUILTIN.items() if k not in ("style_profile", "script_notes")}
    old["characters"] = [{k: v for k, v in c.items() if k not in ("voice", "personality")}
                         for c in old["characters"]]
    filled = creative_series._with_mode(old)
    assert {c["id"]: c["voice"] for c in filled["characters"]} == {
        "suri": "Ái Hân", "mother": "Mỹ Duyên", "father": "Mạnh Dũng"}
    assert filled["style_profile"] == "chibi_3d"


def test_series_choices_reach_the_storyboard(s):
    tpl = creative_series.save_template(s, SERIES)
    brief = {**normalise_brief("template_series", template=tpl), "desired_script": "Mochi làm vỡ cốc, bố an ủi"}
    prompt = creative.branch_plan_prompt(brief, {}, True, False)
    assert "Chuyện chữa lành" in prompt and "Mochi: Hay ngại nhưng tốt bụng" in prompt
    assert "KỊCH BẢN MONG MUỐN" in prompt and "Mochi làm vỡ cốc" in prompt
    assert workflow._brief_plan_fields(brief)["style_profile"] == "felt_craft"
    from agent_core.voice_cast import cast_voices
    plan = {"creation_branch": "template_series", "characters": tpl["characters"],
            "scenes": [{"lines": [{"speaker": "mochi", "text": "a"}, {"speaker": "bo-meo", "text": "b"}]}]}
    pinned = {c["id"]: c["voice"] for c in tpl["characters"]}
    cast = cast_voices(plan, "", pinned)
    assert cast["mochi"]["voice"] == "Ái Hân" and cast["mochi"]["pitch"] > 1     # child voice lifted
    assert cast["bo-meo"]["voice"] == "Mạnh Dũng"


def test_editor_api_saves_and_create_passes_the_script(s, monkeypatch):
    from agent_core.web import app as web
    monkeypatch.setattr(web, "get_settings", lambda: s)
    with patch.object(web, "setup_logging"), TestClient(web.create_app(start_worker=False)) as c:
        page = c.get("/create?branch=template_series")
        assert page.status_code == 200 and "Phong cách nghệ thuật" in page.text and "felt_craft" in page.text
        r = c.post("/api/creative-templates", headers=H, json=SERIES)
        assert r.status_code == 200 and r.json()["characters"][1]["voice"] == "Mạnh Dũng"
        r = c.post("/api/create", headers=H, json={"creation_branch": "template_series", "template_id": "nha-meo",
                                                    "topic": "Mochi làm vỡ cốc", "script": "Bố an ủi, cả nhà dán lại"})
        brief = json.loads(db.get_job(s.database_path, r.json()["job_id"])["brief_json"])
        assert brief["desired_script"] == "Bố an ủi, cả nhà dán lại"
        assert brief["template"]["style_profile"] == "felt_craft"
        assert brief["template"]["characters"][0]["voice"] == "Ái Hân"


def test_child_voice_preview_is_pitched(s, monkeypatch):
    import io
    import wave
    from agent_core.web import app as web
    monkeypatch.setattr(web, "get_settings", lambda: s)
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1), w.setsampwidth(2), w.setframerate(24000), w.writeframes(b"\x00\x01" * 24000)
    calls = []
    with patch.object(web, "setup_logging"), TestClient(web.create_app(start_worker=False)) as c, \
            patch("agent_core.editor.tts.synthesize_bytes", return_value=(buf.getvalue(), "audio/wav")), \
            patch("agent_core.editor.voice_track._retime", side_effect=lambda ff, a, b, t, p: (
                calls.append(p), b.write_bytes(a.read_bytes()))):
        assert c.post("/api/tts/preview", headers=H, json={"voice": "Ái Hân", "child": True}).status_code == 200
        assert c.post("/api/tts/preview", headers=H, json={"voice": "Ái Hân"}).status_code == 200
    assert len(calls) == 1 and calls[0] > 1
