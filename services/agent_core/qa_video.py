"""Real QA for a rendered video: look at the frames, measure the edit, no base points.

qa_score (0-100) = visual check by an AI vision model (50) + measured edit facts (30)
+ the AI editor's script review (20). A part that could not run is left out and the others
are re-weighted, so a missing check never adds points. Hard gates mark a video FAIL whatever
the total is.
"""
import json
import logging
import re
from pathlib import Path

from .creation_branches import DURATION_TARGETS

log = logging.getLogger("affiliate_agent.qa")

VISUAL_FIELDS = ("face_consistency", "wardrobe_consistency", "product_match", "artifacts",
                 "subtitle_readable", "phone_realism", "hook_stop_power")
MAX_FRAMES = 8


def sample_frames(settings, video: Path, scene_report: list[dict], out_dir: Path) -> list[Path]:
    from .editor.ffmpeg import run_ffmpeg
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    times = [0.8] + [float(s.get("start_at") or 0) + float(s.get("rendered_duration") or 2) / 2
                     for s in scene_report]
    frames = []
    for k, t in enumerate(times[:MAX_FRAMES]):
        target = out_dir / f"qa_{k:02d}.jpg"
        try:
            run_ffmpeg(settings.ffmpeg_path, ["-ss", f"{max(0.0, t):.2f}", "-i", str(video),
                                              "-frames:v", "1", "-vf", "scale=360:-2", "-q:v", "4",
                                              str(target)])
            if target.is_file():
                frames.append(target)
        except Exception as exc:  # noqa: BLE001
            log.debug("qa frame %s failed: %s", k, exc)
    return frames


def visual_prompt(timeline: dict, n_frames: int, has_product_ref: bool, spoken: list[str] | None = None) -> str:
    cast = [{k: c.get(k) for k in ("id", "name", "gender", "age", "wardrobe")}
            for c in timeline.get("characters") or []]
    product_note = (f"Ảnh cuối cùng (ảnh {n_frames + 1}) là ảnh sản phẩm thật; so sản phẩm trong video với ảnh đó."
                    if has_product_ref else "Không có ảnh sản phẩm tham chiếu: product_match = null.")
    from .style_profiles import get as _style
    _st = _style(timeline.get("style_profile"), timeline.get("creation_branch"))
    style_note = f"{_st['label']}: {_st['look'][:120]}"
    return f"""Bạn là QA video ngắn khó tính. Ảnh 1 là khung hook (giây 0.8), các ảnh sau là giữa từng cảnh theo thứ tự.
Nhân vật dự kiến: {json.dumps(cast, ensure_ascii=False)}
{product_note}
Chấm 0-10 (10 = tốt nhất), null nếu không áp dụng:
- face_consistency: cùng một nhân vật có giữ nguyên khuôn mặt giữa các cảnh không (null nếu không có người)
- wardrobe_consistency: trang phục có giữ nguyên không
- product_match: sản phẩm trong video có đúng sản phẩm thật không
- artifacts: 10 = không có tay/mặt méo, chữ rác, logo lạ, vật thể biến dạng
- subtitle_readable: phụ đề đọc được, không chồng dòng, không che mặt
- phone_realism: hình có đúng phong cách mong muốn ({style_note}) và trông chuyên nghiệp, không méo,
  không "nhựa" kiểu AI rẻ tiền (10) hay lệch phong cách / rung lắc / kém (0)
- hook_stop_power: khung hook có khiến người lướt dừng lại không
- speech_match: hình mỗi cảnh có minh hoạ đúng câu đang được đọc lúc đó không (null nếu không có lời).
  Câu đọc ở mỗi ảnh (ảnh 1 = hook, ảnh 2.. = các cảnh): {json.dumps(spoken or [], ensure_ascii=False)[:1500]}
- bad_frames: số thứ tự các ảnh (1, 2, ...) có lỗi hình nặng cần làm lại: mặt/tay méo, đổi người,
  sai sản phẩm, vật thể biến dạng. Không liệt kê lỗi nhỏ.
Trả DUY NHẤT JSON: {{"face_consistency":8,"wardrobe_consistency":8,"product_match":null,"artifacts":9,
"subtitle_readable":9,"phone_realism":7,"hook_stop_power":6,"speech_match":8,"bad_frames":[],"issues":["lỗi cụ thể bằng tiếng Việt"]}}"""


def visual_check(settings, video: Path, timeline: dict, scene_report: list[dict],
                 product_ref: Path | None = None, ask=None) -> dict | None:
    """One vision call; None when it could not run (never counted as a pass)."""
    if not getattr(settings, "visual_qa", True):
        return None
    frames = sample_frames(settings, video, scene_report, Path(video).parent / "qa_frames")
    if len(frames) < 2:
        return None
    images = frames + ([product_ref] if product_ref and Path(product_ref).is_file() else [])
    spoken = [" ".join(ln.get("text") or "" for ln in (s.get("lines") or [])) or s.get("voiceover") or ""
              for s in [scene_report[0] if scene_report else {}] + list(scene_report)][:len(frames)]
    prompt = visual_prompt(timeline, len(frames), len(images) > len(frames), spoken)
    try:
        if ask is None:
            from .workflow import _ask_json, _client
            client = _client(settings)
            raw = _ask_json(client, settings, prompt, images, temperature=0.1, max_tokens=900)
        else:
            raw = ask(prompt, images)
    except Exception as exc:  # noqa: BLE001
        log.warning("visual QA skipped: %s", exc)
        return None
    result = {}
    for key in VISUAL_FIELDS:
        value = raw.get(key)
        try:
            result[key] = None if value is None else max(0.0, min(10.0, float(value)))
        except (TypeError, ValueError):
            result[key] = None
    result["issues"] = [str(i)[:200] for i in (raw.get("issues") or [])[:8]]
    try:
        result["speech_match"] = None if raw.get("speech_match") is None else max(0.0, min(10.0, float(raw["speech_match"])))
    except (TypeError, ValueError):
        result["speech_match"] = None
    # image 1 is the hook frame (scene 0 at 0.8 s); image k >= 2 is the middle of scene k-2
    bad = set()
    for value in raw.get("bad_frames") or []:
        try:
            k = int(value)
        except (TypeError, ValueError):
            continue
        if k == 1:
            bad.add(0)
        elif 2 <= k <= len(frames):
            bad.add(k - 2)
    result["bad_scenes"] = sorted(i for i in bad if i < len(scene_report))
    return result


FIX_LIMIT = 1   # automatic repair rounds per video; after that a person decides


def gate_decision(qa: dict, fix_attempts: int) -> tuple[str, list[int], str]:
    """PASS / FIX (regenerate listed scenes once) / REJECT (person must look), with a reason."""
    visual = qa.get("visual") or {}
    bad = list(visual.get("bad_scenes") or [])
    if "wrong_product" in (qa.get("gates") or []) and not bad:
        return "REJECT", [], "Sản phẩm trong video không khớp sản phẩm thật"
    if qa.get("status") in ("PRIORITY_PUBLISH", "CONDITIONAL_PASS") and not bad:
        return "PASS", [], "Đạt QA"
    if bad and fix_attempts < FIX_LIMIT:
        return "FIX", bad, f"Tạo lại {len(bad)} cảnh lỗi hình"
    reasons = "; ".join((qa.get("improvements") or [])[:3]) or "Điểm QA thấp"
    if bad:   # already repaired once and frames are still broken: a person decides
        return "REJECT", [], f"Cảnh {', '.join(str(i + 1) for i in bad)} vẫn lỗi hình sau khi làm lại; " + reasons
    if qa.get("status") == "NEEDS_REVISION" and not (qa.get("gates") or []):
        return "PASS", [], "Đạt có điều kiện: " + reasons
    return "REJECT", [], reasons


def subtitle_overlaps(ass_path: Path | None) -> int:
    if not ass_path or not Path(ass_path).is_file():
        return 0
    def t(v):
        h, m, s = v.split(":")
        return int(h) * 3600 + int(m) * 60 + float(s)
    events = []
    for line in Path(ass_path).read_text(encoding="utf-8-sig").splitlines():
        if line.startswith("Dialogue:"):
            parts = line.split(",", 9)
            if parts[3] == "Sub":
                events.append((t(parts[1]), t(parts[2])))
    events.sort()
    return sum(1 for a, b in zip(events, events[1:], strict=False) if b[0] < a[1] - 0.01)


def text_layers(ass_path: Path | None) -> int:
    if not ass_path or not Path(ass_path).is_file():
        return 0
    styles = set(re.findall(r"^Dialogue: \d+,[^,]+,[^,]+,(\w+),", Path(ass_path).read_text(
        encoding="utf-8-sig"), flags=re.M))
    from .editor.subtitles import DECOR_STYLES
    return len(styles - set(DECOR_STYLES))   # the CTA sticker / AI tag are decor, not reading text


COVERAGE_MIN, MAX_GAP, SEAM_MIN = 0.85, 0.8, 0.5


def voice_flow(segments: list | None, total: float, pauses: int = 0) -> dict:
    """How much of the video is narrated and the longest silence between words (S5)."""
    if not segments or total <= 0:
        return {"coverage": None, "max_gap": None}
    segs = sorted(segments)
    spoken = sum(e - s for s, e in segs)
    gaps = [b[0] - a[1] for a, b in zip(segs, segs[1:], strict=False)]
    # deliberate "pause" beats are allowed one long breath each
    allowed = sorted(gaps, reverse=True)[pauses:] if pauses else gaps
    return {"coverage": round(spoken / total, 3), "max_gap": round(max(allowed, default=0.0), 2)}


def seam_similarity(settings, video: Path, seams: list | None) -> list[float]:
    """SSIM across each cut inside a sequence (frame just before vs just after)."""
    import re
    import subprocess
    out = []
    for seam in seams or []:
        if not seam.get("same_sequence"):
            continue
        at = float(seam["at"])
        try:
            a = subprocess.run([settings.ffmpeg_path, "-v", "info", "-ss", f"{max(0.0, at - 0.12):.2f}", "-i",
                                str(video), "-ss", f"{at + 0.12:.2f}", "-i", str(video), "-lavfi",
                                "[0:v]scale=180:320,trim=end_frame=1[a];[1:v]scale=180:320,trim=end_frame=1[b];[a][b]ssim",
                                "-frames:v", "1", "-f", "null", "-"], capture_output=True, text=True, timeout=60).stderr
            m = re.search(r"All:([\d.]+)", a)
            if m:
                out.append(round(float(m.group(1)), 3))
        except Exception:  # noqa: BLE001
            continue
    return out


def score_video(timeline: dict, scene_report: list[dict], duration: float, music: bool,
                visual: dict | None, ai_review: float | None, ass_path: Path | None,
                tech_passed: bool = True, voice_segments: list | None = None,
                seam_ssim: list | None = None) -> dict:
    branch = timeline.get("creation_branch") or ""
    from .formats import duration_range
    low, high = duration_range(timeline) if branch else DURATION_TARGETS.get(branch, (12.0, 45.0))
    issues, gates = [], []

    # measured facts (30)
    facts = 0.0
    if low <= duration <= high:
        facts += 8
    elif low * 0.7 <= duration <= high * 1.3:
        facts += 4
        issues.append(f"Thời lượng {duration:.1f}s ngoài mục tiêu {low:.0f}-{high:.0f}s")
    else:
        issues.append(f"Thời lượng {duration:.1f}s lệch xa mục tiêu {low:.0f}-{high:.0f}s")
        gates.append("duration")
    layers = text_layers(ass_path)
    facts += 4 if layers <= 2 else 1
    if layers > 2:
        issues.append(f"{layers} lớp chữ cùng lúc")
    overlaps = subtitle_overlaps(ass_path)
    facts += 6 if not overlaps else 0
    if overlaps:
        issues.append(f"{overlaps} chỗ phụ đề chồng nhau")
        gates.append("subtitle_overlap")
    fit = [s for s in scene_report if s.get("voice_end")]
    overflow = [s for s in fit if s["voice_end"] > float(s.get("rendered_duration") or 0) + 0.05]
    unfitted = [s for s in scene_report if s.get("revoice") in {"unfitted", "no_speech_found"}]
    facts += 6 * (1 - (len(overflow) + len(unfitted)) / max(1, len(scene_report)))
    if overflow:
        issues.append(f"{len(overflow)} cảnh lời dài hơn hình")
    if unfitted:
        issues.append(f"{len(unfitted)} cảnh nói trước camera chưa khớp khẩu hình")
    facts += 3 if music else 0
    if not music:
        issues.append("Không có nhạc nền")
    facts += 3 if tech_passed else 0
    pacing = pacing_report(branch, timeline, scene_report)
    if pacing["over"]:
        issues.append(f"Nhịp chậm: {len(pacing['over'])} cảnh dài hơn {pacing['limit']:.0f}s "
                      f"(dài nhất {pacing['longest']:.1f}s); cắt ngắn hoặc thêm góc máy")
    parts = {"facts": (facts, 30.0)}

    # flow (S5): narration coverage, silences, seams inside sequences, comprehension
    flow_points, flow_cap = 0.0, 0.0
    vf = voice_flow(voice_segments, duration, sum(1 for s in scene_report if s.get("pause")))
    if vf["coverage"] is not None:
        flow_cap += 10
        flow_points += 10 * min(1.0, vf["coverage"] / COVERAGE_MIN)
        if vf["coverage"] < COVERAGE_MIN:
            issues.append(f"Giọng chỉ phủ {vf['coverage'] * 100:.0f}% thời lượng (cần ≥ {COVERAGE_MIN * 100:.0f}%)")
        flow_cap += 5
        flow_points += 5 if vf["max_gap"] <= MAX_GAP else max(0.0, 5 - 4 * (vf["max_gap"] - MAX_GAP))
        if vf["max_gap"] > MAX_GAP:
            issues.append(f"Khoảng lặng dài {vf['max_gap']:.1f}s giữa các câu")
        if vf["coverage"] < 0.6:
            gates.append("voice_gaps")
    if seam_ssim:
        smooth = sum(1 for v in seam_ssim if v >= SEAM_MIN) / len(seam_ssim)
        flow_cap += 5
        flow_points += 5 * smooth
        if smooth < 1:
            issues.append(f"{sum(1 for v in seam_ssim if v < SEAM_MIN)} chỗ nối trong chuỗi cảnh bị nhảy hình")
    test = timeline.get("viewer_test") or {}
    if test.get("flow") is not None:
        flow_cap += 10
        flow_points += float(test["flow"])
        if test.get("hook_answered") is False:
            issues.append("Người xem lạ không thấy hook được trả lời")
    if visual and visual.get("speech_match") is not None:
        flow_cap += 10
        flow_points += float(visual["speech_match"])
        if visual["speech_match"] < 6:
            issues.append("Hình chưa minh hoạ đúng điều đang nói")
    if flow_cap:
        parts["flow"] = (flow_points, flow_cap)

    # visual (50)
    if visual:
        values = [visual[k] for k in VISUAL_FIELDS if visual.get(k) is not None]
        if values:
            parts["visual"] = (sum(values) / len(values) * 5.0, 50.0)
        issues += visual.get("issues") or []
        if branch == "product_review" and visual.get("product_match") is not None \
                and visual["product_match"] < 5:
            gates.append("wrong_product")
            issues.append("Sản phẩm trong video không khớp sản phẩm thật")
        if visual.get("face_consistency") is not None and visual["face_consistency"] < 4:
            issues.append("Khuôn mặt nhân vật thay đổi giữa các cảnh")
    # editorial (20)
    if ai_review is not None:
        parts["editorial"] = (max(0.0, min(10.0, float(ai_review))) * 2.0, 20.0)

    got = sum(v for v, _ in parts.values())
    cap = sum(c for _, c in parts.values())
    total = round(got / cap * 100, 1) if cap else 0.0
    if gates:
        status = "FAIL"
    elif total >= 85:
        status = "PRIORITY_PUBLISH"
    elif total >= 75:
        status = "CONDITIONAL_PASS"
    elif total >= 60:
        status = "NEEDS_REVISION"
    else:
        status = "FAIL"
    if visual is None and status == "PRIORITY_PUBLISH":
        status = "CONDITIONAL_PASS"   # without a look at the frames it is never "priority"
    return {"total_score": total, "status": status, "visual_checked": visual is not None,
            "breakdown": {k: round(v, 1) for k, (v, _) in parts.items()},
            "voice_coverage": vf["coverage"], "max_silence": vf["max_gap"], "seam_ssim": seam_ssim or [],
            "visual": visual, "gates": gates, "improvements": issues, "strengths": [],
            "subtitle_overlaps": overlaps, "text_layers": layers, "pacing": pacing}


# Longest shot without a cut, per branch (UGC research: cuts <= 3 s keep viewers; a dialogue
# series needs a little longer for one spoken line). A reuse cut counts as its own shot.
SHOT_LIMITS = {"product_review": 3.5, "viral_creator": 3.5, "template_series": 4.5}


def pacing_report(branch: str, timeline: dict, scene_report: list[dict]) -> dict:
    limit = SHOT_LIMITS.get(branch, 4.0)
    if branch == "template_series" and not timeline.get("dialogue"):
        limit = 4.0
    shots = [(i, float(s.get("rendered_duration") or 0)) for i, s in enumerate(scene_report)]
    over = [i for i, d in shots if d > limit + 0.25 and not scene_report[i].get("pause")]
    longest = max((d for _, d in shots), default=0.0)
    avg = sum(d for _, d in shots) / len(shots) if shots else 0.0
    return {"limit": limit, "longest": round(longest, 2), "average": round(avg, 2), "over": over}


# ---------------------------------------------------------------- per-clip checklist (UGC item 2)
CLIP_CHECKS = {
    "action": "đúng MỘT hành động như mô tả",
    "identity": "nhân vật đúng người trong ảnh tham chiếu (mặt, tóc, trang phục)",
    "anatomy": "không tay/ngón/mặt biến dạng",
    "clean": "không chữ rác, logo lạ, bảng hiệu giả",
    "frame": "đầy khung dọc 9:16, không viền đen, chủ thể không bị cắt",
    "motion": "có chuyển động thật (không đứng hình, không trôi lơ lửng)",
    "style": "đúng phong cách hình yêu cầu",
}


def clip_prompt(shot_prompt: str, n_frames: int, n_refs: int) -> str:
    checks = "\n".join(f"- {k}: {v}" for k, v in CLIP_CHECKS.items())
    refs = (f"Ảnh {n_frames + 1}-{n_frames + n_refs} là ảnh tham chiếu nhân vật/sản phẩm."
            if n_refs else "Không có ảnh tham chiếu: identity = null nếu không xác định được.")
    return f"""Bạn là QA clip video AI. Ảnh 1-{n_frames} là các khung của MỘT clip theo thứ tự thời gian. {refs}
Yêu cầu của clip (prompt đã gửi): {shot_prompt[:1200]}
Chấm từng mục true (đạt) / false (lỗi rõ ràng) / null (không áp dụng):
{checks}
Chỉ đánh false khi lỗi thấy rõ và làm hỏng cảnh; lỗi nhỏ vẫn là true.
Trả DUY NHẤT JSON: {{"action":true,"identity":null,"anatomy":true,"clean":true,"frame":true,"motion":true,
"style":true,"issues":["lỗi cụ thể bằng tiếng Anh ngắn, để đưa vào lần tạo lại"]}}"""


def clip_frames(settings, clip: Path, out_dir: Path, points=(0.2, 0.5, 0.85)) -> list[Path]:
    from .editor.ffmpeg import probe, run_ffmpeg
    out_dir.mkdir(parents=True, exist_ok=True)
    try:
        length = float(probe(settings.ffprobe_path, clip)["duration_sec"]) or 10.0
    except Exception:  # noqa: BLE001
        length = 10.0
    frames = []
    for k, frac in enumerate(points):
        target = out_dir / f"{clip.stem}_c{k}.jpg"
        try:
            run_ffmpeg(settings.ffmpeg_path, ["-ss", f"{length * frac:.2f}", "-i", str(clip), "-frames:v", "1",
                                              "-vf", "scale=360:-2", "-q:v", "4", str(target)])
            if target.is_file():
                frames.append(target)
        except Exception as exc:  # noqa: BLE001
            log.debug("clip frame failed: %s", exc)
    return frames


def clip_check(settings, clip: Path, shot_prompt: str, refs: list[Path] | None = None, ask=None) -> dict | None:
    """7-point check of one Muse clip. None when it could not run (the clip is then kept)."""
    frames = clip_frames(settings, Path(clip), Path(clip).parent / "clip_qa")
    if len(frames) < 2:
        return None
    images = frames + [Path(r) for r in (refs or [])[:3] if Path(r).is_file()
                       and Path(r).suffix.lower() in (".jpg", ".jpeg", ".png", ".webp")]
    prompt = clip_prompt(shot_prompt, len(frames), len(images) - len(frames))
    try:
        if ask is None:
            from .workflow import _ask_json, _client
            raw = _ask_json(_client(settings), settings, prompt, images, temperature=0.1, max_tokens=500)
        else:
            raw = ask(prompt, images)
    except Exception as exc:  # noqa: BLE001
        log.warning("clip QA skipped: %s", exc)
        return None
    checks = {k: raw.get(k) if raw.get(k) in (True, False) else None for k in CLIP_CHECKS}
    failed = [k for k, v in checks.items() if v is False]
    return {"ok": not failed, "checks": checks, "failed": failed,
            "issues": [str(i)[:160] for i in (raw.get("issues") or [])[:4]] or
                      [CLIP_CHECKS[k] for k in failed]}
