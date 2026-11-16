"""Per-clip 7-point check (UGC item 2) and fix notes carried into the next take."""
import subprocess
from pathlib import Path
from types import SimpleNamespace

from agent_core import db, qa_video
from agent_core.videogen import engine

FFMPEG = "ffmpeg"


def _clip(path: Path) -> Path:
    subprocess.run([FFMPEG, "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi", "-i",
                    "testsrc2=size=360x640:rate=24", "-t", "3", "-pix_fmt", "yuv420p", str(path)],
                   check=True, capture_output=True)
    return path


def _settings(tmp_path):
    return SimpleNamespace(database_path=tmp_path / "a.sqlite3", work_dir=tmp_path / "work",
                           ffmpeg_path=FFMPEG, ffprobe_path="ffprobe", clip_qa=True)


def test_clip_check_reports_failed_points(tmp_path):
    s = _settings(tmp_path)
    clip = _clip(tmp_path / "c.mp4")
    seen = {}

    def ask(prompt, images):
        seen["n"] = len(images)
        seen["prompt"] = prompt
        return {"action": True, "identity": None, "anatomy": False, "clean": True, "frame": True,
                "motion": True, "style": True, "issues": ["left hand has six fingers"]}

    verdict = qa_video.clip_check(s, clip, "Action: a woman pours tea.", ask=ask)
    assert verdict["ok"] is False and verdict["failed"] == ["anatomy"]
    assert verdict["issues"] == ["left hand has six fingers"]
    assert seen["n"] == 3 and "pours tea" in seen["prompt"]


def test_failed_clip_is_remade_with_the_fix_note(tmp_path, monkeypatch):
    s = _settings(tmp_path)
    db.init_db(s.database_path)
    engine.ensure_schema(s.database_path)
    clip = _clip(tmp_path / "c.mp4")
    tid = engine._insert(s.database_path, job_id="j", scene_index=0, kind="video", provider="muse2api",
                         prompt="Action: pour tea.\n§internal", refs=[], duration=10)
    t = {**engine.get_task(s.database_path, tid), "result_path": str(clip), "qc": {"ok": True}}
    monkeypatch.setattr(qa_video, "clip_check", lambda *a, **k: {
        "ok": False, "checks": {}, "failed": ["anatomy"], "issues": ["six fingers on the left hand"]})
    engine._reviewing.add(tid)
    engine._clip_review(s, t)
    rows = {r["attempt"]: r for r in engine.list_tasks(s.database_path, "j")}
    assert rows[1]["status"] == "FAILED" and "Clip QA" in rows[1]["error"]
    assert rows[2]["status"] == "PENDING" and "six fingers on the left hand" in rows[2]["prompt"]
    assert tid not in engine._reviewing


def test_passed_clip_is_kept(tmp_path, monkeypatch):
    s = _settings(tmp_path)
    db.init_db(s.database_path)
    engine.ensure_schema(s.database_path)
    clip = _clip(tmp_path / "c.mp4")
    tid = engine._insert(s.database_path, job_id="j", scene_index=0, kind="video", provider="muse2api",
                         prompt="Action: pour tea.", refs=[], duration=10)
    monkeypatch.setattr(qa_video, "clip_check", lambda *a, **k: {"ok": True, "checks": {}, "failed": [],
                                                                 "issues": []})
    monkeypatch.setattr(engine, "_add_to_clip_library", lambda *a: None)
    engine._clip_review(s, {**engine.get_task(s.database_path, tid), "result_path": str(clip)})
    assert engine.get_task(s.database_path, tid)["status"] == "DOWNLOADED"


def test_fix_note_replaces_the_old_one_and_keeps_internal_lines_last():
    first = engine._with_fix("Action: x.\n§refs:abc", ["blurry face"])
    second = engine._with_fix(first, ["extra arm"])
    assert "blurry face" not in second and "extra arm" in second
    assert second.splitlines()[-1].startswith("§")
    assert engine._with_fix("Action: x.", None) == "Action: x."


def test_attachment_numbers_shift_when_the_continuity_frame_goes_first():
    p = "People: Mai (looks exactly like attachment 1). Attachments: 1 = c1 (character:mai); 2 = p (product)."
    out = engine._shift_attachments(p, 1)
    assert "attachment 2)" in out and "Attachments: 2 = c1" in out and "; 3 = p" in out


def test_pacing_report_flags_long_shots_per_branch():
    report = [{"rendered_duration": 2.8}, {"rendered_duration": 5.2}, {"rendered_duration": 6.0, "pause": True}]
    viral = qa_video.pacing_report("viral_creator", {}, report)
    assert viral["over"] == [1] and viral["longest"] == 6.0 and viral["limit"] == 3.5
    series = qa_video.pacing_report("template_series", {"dialogue": True}, [{"rendered_duration": 4.6}])
    assert series["over"] == []


def test_review_sticker_and_ai_tag_are_decor_layers(tmp_path):
    from agent_core.editor.subtitles import build_ass
    from agent_core.editor.tts import Word
    ass = build_ass(tmp_path / "s.ass", 1080, 1920, "Arial", [(0.0, [Word("xin", 2.5, 2.9), Word("chào", 2.9, 3.3)])],
                    "Hook ngắn", 2.0, [], "link mình để dưới", 10.0, 20.0, single_layer=True,
                    sticker="Link ở giỏ hàng", sticker_start=14.0, ai_label=True)
    text = ass.read_text(encoding="utf-8-sig")
    assert ",Sticker,,0,0,0,," in text and "Nội dung AI" in text
    assert "link mình để dưới" not in text            # the sticker replaces the CTA subtitle line
    assert qa_video.text_layers(ass) == 2              # HookLite + Sub; decor is not counted


def test_series_take_that_says_the_wrong_words_is_remade(tmp_path, monkeypatch):
    from agent_core.editor import speech_check
    s = _settings(tmp_path)
    s.clip_qa = False
    s.series_speech_max_wer = 0.3
    db.init_db(s.database_path)
    engine.ensure_schema(s.database_path)
    clip = _clip(tmp_path / "c.mp4")
    prompt = 'Spoken line (Vietnamese): "Mẹ ơi sao mặt trăng đi theo con"\nSuri says exactly this line once.'
    tid = engine._insert(s.database_path, job_id="j", scene_index=2, kind="video", provider="muse2api",
                         prompt=prompt, refs=[], duration=10)
    monkeypatch.setattr(speech_check, "check_line", lambda *a, **k: {
        "heard": "chào các bạn hôm nay", "wer": 1.0, "extra_words": 0, "speech_start": 1.0, "speech_end": 2.0})
    engine._clip_review(s, {**engine.get_task(s.database_path, tid), "result_path": str(clip)})
    rows = {r["attempt"]: r for r in engine.list_tasks(s.database_path, "j")}
    assert rows[1]["status"] == "FAILED" and "sai kịch bản" in rows[1]["error"]
    assert 'must say exactly "Mẹ ơi sao mặt trăng đi theo con"' in rows[2]["prompt"]
    # the remake says it right: kept, and its speech window is what the render will use
    monkeypatch.setattr(speech_check, "check_line", lambda *a, **k: {
        "heard": "mẹ ơi sao mặt trăng đi theo con", "wer": 0.0, "extra_words": 0,
        "speech_start": 2.1, "speech_end": 4.3})
    monkeypatch.setattr(engine, "_add_to_clip_library", lambda *a: None)
    engine._clip_review(s, {**rows[2], "result_path": str(clip)})
    speech = engine.speech_results(s.database_path, "j")
    assert speech[2]["ok"] is True and speech[2]["speech_start"] == 2.1
