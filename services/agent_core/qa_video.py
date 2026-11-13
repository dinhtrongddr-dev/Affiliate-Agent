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


def visual_prompt(timeline: dict, n_frames: int, has_product_ref: bool) -> str:
    cast = [{k: c.get(k) for k in ("id", "name", "gender", "age", "wardrobe")}
            for c in timeline.get("characters") or []]
    product_note = (f"Ảnh cuối cùng (ảnh {n_frames + 1}) là ảnh sản phẩm thật; so sản phẩm trong video với ảnh đó."
                    if has_product_ref else "Không có ảnh sản phẩm tham chiếu: product_match = null.")
    return f"""Bạn là QA video ngắn khó tính. Ảnh 1 là khung hook (giây 0.8), các ảnh sau là giữa từng cảnh theo thứ tự.
Nhân vật dự kiến: {json.dumps(cast, ensure_ascii=False)}
{product_note}
Chấm 0-10 (10 = tốt nhất), null nếu không áp dụng:
- face_consistency: cùng một nhân vật có giữ nguyên khuôn mặt giữa các cảnh không (null nếu không có người)
- wardrobe_consistency: trang phục có giữ nguyên không
- product_match: sản phẩm trong video có đúng sản phẩm thật không
- artifacts: 10 = không có tay/mặt méo, chữ rác, logo lạ, vật thể biến dạng
- subtitle_readable: phụ đề đọc được, không chồng dòng, không che mặt
- phone_realism: trông như clip người thật quay bằng điện thoại (10) hay quảng cáo AI bóng bẩy (0)
- hook_stop_power: khung hook có khiến người lướt dừng lại không
Trả DUY NHẤT JSON: {{"face_consistency":8,"wardrobe_consistency":8,"product_match":null,"artifacts":9,
"subtitle_readable":9,"phone_realism":7,"hook_stop_power":6,"issues":["lỗi cụ thể bằng tiếng Việt"]}}"""


def visual_check(settings, video: Path, timeline: dict, scene_report: list[dict],
                 product_ref: Path | None = None, ask=None) -> dict | None:
    """One vision call; None when it could not run (never counted as a pass)."""
    if not getattr(settings, "visual_qa", True):
        return None
    frames = sample_frames(settings, video, scene_report, Path(video).parent / "qa_frames")
    if len(frames) < 2:
        return None
    images = frames + ([product_ref] if product_ref and Path(product_ref).is_file() else [])
    prompt = visual_prompt(timeline, len(frames), len(images) > len(frames))
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
    return result


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
    return len(styles)


def score_video(timeline: dict, scene_report: list[dict], duration: float, music: bool,
                visual: dict | None, ai_review: float | None, ass_path: Path | None,
                tech_passed: bool = True) -> dict:
    branch = timeline.get("creation_branch") or ""
    low, high = DURATION_TARGETS.get(branch, (12.0, 45.0))
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
    parts = {"facts": (facts, 30.0)}

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
            "visual": visual, "gates": gates, "improvements": issues, "strengths": [],
            "subtitle_overlaps": overlaps, "text_layers": layers}
