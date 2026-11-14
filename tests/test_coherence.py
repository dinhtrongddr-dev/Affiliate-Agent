"""S1-S5: voice-led edit, script coherence, sequence continuity, flow QA."""
from agent_core import creative
from agent_core.editor import edit_plan
from agent_core.qa_video import score_video, voice_flow
from agent_core.videogen.engine import chain_map


def test_layout_lays_voice_end_to_end_with_j_cuts():
    scenes = [{"group": "a"}, {"group": "a"}, {"group": "b", "pause": False}, {"group": "b"}]
    out = edit_plan.layout(scenes, [3.0, 2.5, 4.0, 2.0])
    v = out["voice_starts"]
    assert v[0] == edit_plan.HEAD
    assert round(v[1] - (v[0] + 3.0), 2) == edit_plan.GAP_SAME          # same sequence: short breath
    assert round(v[2] - (v[1] + 2.5), 2) == edit_plan.GAP_NEW           # new sequence: longer breath
    assert all(round(v[i] - out["cuts"][i], 2) == edit_plan.LEAD for i in range(1, 4))   # J-cut
    assert out["seams"][1] == edit_plan.CUT_T and out["seams"][2] == edit_plan.DISSOLVE_T
    # xfade inputs overlap by the seam, so each picture starts exactly at its cut
    start = 0.0
    for i in range(1, 4):
        start = start + out["durations"][i - 1] - out["seams"][i]
        assert abs(start - out["cuts"][i]) < 1e-6
    assert abs(out["total"] - (v[-1] + 2.0 + edit_plan.TAIL)) < 1e-6


def test_silent_beats_and_pauses():
    out = edit_plan.layout([{"group": "a", "pause": True}, {"group": "a", "duration": 9}], [2.0, 0.0])
    assert round(out["voice_starts"][1] - (edit_plan.HEAD + 2.0), 2) == edit_plan.GAP_SAME + edit_plan.PAUSE_EXTRA
    assert out["total"] - out["cuts"][1] <= 3.0 + edit_plan.TAIL + edit_plan.LEAD + 0.01   # silent beat capped


def test_take_windows_keep_the_matched_frames():
    # continues from previous take: start at the head
    assert edit_plan.window(10, 5, chain_in=True, chain_out=False) == (0.0, 1.0)
    # feeds the next take: end where the continuity frame was taken
    start, speed = edit_plan.window(10, 5, chain_in=False, chain_out=True)
    assert speed == 1.0 and abs(start + 5 - (10 - edit_plan.CONTINUITY_OFFSET)) < 1e-6
    # both: play the whole take a bit faster so both seams line up
    start, speed = edit_plan.window(10, 8, chain_in=True, chain_out=True)
    assert start == 0.0 and abs(speed - 9.7 / 8) < 0.01
    assert edit_plan.window(10, 4, False, False, best_start=7.5) == (6.0, 1.0)


def test_takes_group_consecutive_cuts_of_one_clip():
    scenes = [{"generated": True, "source": "a"}, {"generated": True, "source": "a"},
              {"generated": True, "source": "b"}, {"kind": "image", "source": "p"}, {"generated": True, "source": "a"}]
    assert edit_plan.takes(scenes) == [[0, 1], [2], [4]]


def test_grade_is_gentle():
    g = edit_plan.grade_to((120.0, 0.4), (60.0, 0.1))
    assert g == {"brightness": 0.08, "saturation": 1.15}


def test_chain_map_links_takes_inside_a_sequence_only():
    scenes = [{"visual": "generate", "group": "s1"}, {"visual": "reuse", "group": "s1", "reuse_of": 0},
              {"visual": "generate", "group": "s1"}, {"visual": "generate", "group": "s2"},
              {"visual": "generate", "group": "s2"}, {"visual": "product_image", "group": "s2"},
              {"visual": "generate", "group": "s2"}]
    assert chain_map(scenes) == {2: 0, 4: 3}


def test_voice_flow_measures_coverage_and_silence():
    flow = voice_flow([(0.25, 3.25), (3.5, 6.0), (8.5, 10.0)], 11.0)
    assert flow["coverage"] == round(7.0 / 11.0, 3) and flow["max_gap"] == 2.5
    assert voice_flow([(0.25, 3.25), (3.5, 6.0), (8.5, 10.0)], 11.0, pauses=1)["max_gap"] == 0.25


def test_flow_enters_the_score():
    timeline = {"creation_branch": "viral_creator", "viewer_test": {"flow": 9, "hook_answered": True}}
    good = score_video(timeline, [], 25, True, None, 8, None, voice_segments=[(0.2, 24.0)], seam_ssim=[0.8])
    bad = score_video(timeline, [], 25, True, None, 8, None,
                      voice_segments=[(0.2, 3), (6, 8), (12, 14)], seam_ssim=[0.2])
    assert good["voice_coverage"] > 0.9 and bad["voice_coverage"] < 0.6
    assert "voice_gaps" in bad["gates"] and bad["total_score"] < good["total_score"]
    assert any("Khoảng lặng" in i for i in bad["improvements"])


def _plan(lines, takeaway="Rễ thở giúp cây mắm sống trong bùn", **extra):
    return {"creation_branch": "viral_creator", "format": "explainer", "takeaway": takeaway,
            "scenes": [{"lines": [{"speaker": "narrator", "text": t}]} for t in lines], **extra}


def test_coherence_flags_fragments_thin_scripts_and_missing_takeaway():
    fragments = _plan(["Rễ mọc ngược.", "Rất lạ.", "Ở bùn.", "Có lỗ nhỏ."], takeaway="")
    issues = " | ".join(creative.coherence_issues(fragments))
    assert "takeaway" in issues and "Lời quá ít" in issues and "câu cụt" in issues
    story = _plan(["Rễ cây sao lại mọc ngược lên trời giữa bãi bùn Cà Mau?",
                   "Vì bùn ở đây gần như không có không khí nên rễ dưới đất rất dễ ngạt.",
                   "Thế là cây mắm mọc thêm hàng nghìn chiếc rễ nhô lên như những ống thở.",
                   "Nhờ những lỗ li ti trên rễ, không khí đi xuống nuôi cả bộ rễ bên dưới.",
                   "Hoá ra cả cánh rừng vẫn thở được, kể cả khi thuỷ triều lên ngập gốc.",
                   "Lần sau đi Cà Mau, nhìn xuống bùn là bạn biết rừng đang thở đấy."])
    assert creative.coherence_issues(story) == []


def test_viewer_test_feeds_the_fix():
    plan = _plan(["Rễ cây sao lại mọc ngược lên trời giữa bãi bùn Cà Mau?"] * 6,
                 viewer_test={"hook_answered": False, "summary": "nói về bùn", "unclear_lines": ["Rất lạ."]})
    issues = " | ".join(creative.coherence_issues(plan))
    assert "Người xem lạ" in issues and "thiếu ngữ cảnh" in issues
    assert "Hook trên màn hình" in creative.viewer_test_prompt(plan)


def test_one_narrator_for_explainers(tmp_path):
    from types import SimpleNamespace

    from agent_core import workflow
    plan = {"creation_branch": "viral_creator", "format": "explainer",
            "characters": [{"id": "lan", "role": "creator", "gender": "female"}],
            "scenes": [{"lines": [{"speaker": "lan", "text": "a"}]}, {"lines": [{"speaker": "narrator", "text": "b"}]}]}
    workflow._cast_plan_voices(SimpleNamespace(tts_voice="Mai Anh", style_profile=""), plan, {})
    assert list(plan["voice_cast"]) == ["lan"]
    story = {"creation_branch": "viral_creator", "format": "story",
             "characters": [{"id": "bo", "role": "father", "gender": "male"}],
             "scenes": [{"lines": [{"speaker": "bo", "text": "a"}]}, {"lines": [{"speaker": "narrator", "text": "b"}]}]}
    workflow._cast_plan_voices(SimpleNamespace(tts_voice="Mai Anh", style_profile=""), story, {})
    assert set(story["voice_cast"]) == {"bo", "narrator"}


def test_prompt_asks_for_script_first_and_sequences():
    from agent_core.creation_branches import normalise_brief
    prompt = creative.branch_plan_prompt(normalise_brief("viral_creator"), {}, True, False)
    assert '"script"' in prompt and '"takeaway"' in prompt and "CHUỖI CẢNH" in prompt
    assert "MỘT TAKE CHO 2 NHỊP" in prompt and "2,6 từ/giây" in prompt
