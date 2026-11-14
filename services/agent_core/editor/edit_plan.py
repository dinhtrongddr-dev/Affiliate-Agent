"""Voice-led editing (S1) and sequence continuity (S3).

The narration is the spine: every beat's voice is laid end to end with natural pauses, and the
pictures are cut to it (J-cut: the picture changes LEAD seconds before the next sentence).
Takes of one sequence keep their real continuity: a take that continues from the previous
take's last frame starts at its head, a take that feeds the next one ends at its tail, and
when both apply the take is played slightly faster/slower (0.8-1.3x) so both seams line up.
"""
import subprocess
from pathlib import Path

HEAD = 0.25          # silence before the first word
LEAD = 0.2           # picture changes this much before the sentence (J-cut)
GAP_SAME = 0.25      # pause between sentences inside a sequence
GAP_NEW = 0.45       # pause when the story moves to another sequence
PAUSE_EXTRA = 0.6    # beat marked "pause": a deliberate breath for emotion
TAIL = 0.7           # after the last word
MIN_SHOT = 0.9
CUT_T, DISSOLVE_T = 0.08, 0.4
SPEED_MIN, SPEED_MAX = 0.8, 1.3
CONTINUITY_OFFSET = 0.3   # continuity frame is taken this far before a take's end


def same_sequence(a: dict, b: dict) -> bool:
    return bool(a.get("group")) and a.get("group") == b.get("group")


def layout(scenes: list[dict], voice_lens: list[float]) -> dict:
    """Voice start per beat, cut times, seam transitions and xfade input lengths."""
    n = len(scenes)
    lens = [v if v > 0 else max(1.2, min(3.0, float(s.get("duration") or 2.0)))
            for s, v in zip(scenes, voice_lens, strict=True)]
    starts, v = [], HEAD
    for i, scene in enumerate(scenes):
        starts.append(round(v, 3))
        if i < n - 1:
            gap = GAP_SAME if same_sequence(scene, scenes[i + 1]) else GAP_NEW
            v += lens[i] + gap + (PAUSE_EXTRA if scene.get("pause") else 0.0)
    end = starts[-1] + lens[-1] + TAIL
    cuts = [0.0]
    for i in range(1, n):
        cuts.append(round(max(cuts[-1] + MIN_SHOT, starts[i] - LEAD), 3))
    visible = [cuts[i + 1] - cuts[i] for i in range(n - 1)] + [max(MIN_SHOT, end - cuts[-1])]
    seams = [0.0]
    for i in range(1, n):
        t = CUT_T if same_sequence(scenes[i - 1], scenes[i]) else DISSOLVE_T
        seams.append(round(min(t, 0.4 * visible[i - 1], 0.4 * visible[i]), 3))
    durations = [round(visible[i] + (seams[i + 1] if i + 1 < n else 0.0), 3) for i in range(n)]
    return {"voice_starts": starts, "cuts": cuts, "seams": seams, "durations": durations,
            "total": round(cuts[-1] + visible[-1], 3)}


def takes(scenes: list[dict]) -> list[list[int]]:
    """Consecutive beats that cut the same generated clip, in story order."""
    out: list[list[int]] = []
    for i, scene in enumerate(scenes):
        if scene.get("kind", "video") != "video" or not scene.get("generated"):
            continue
        if out and scenes[out[-1][-1]].get("source") == scene.get("source") and out[-1][-1] == i - 1:
            out[-1].append(i)
        else:
            out.append([i])
    return out


def window(src_len: float, need: float, chain_in: bool, chain_out: bool,
           best_start: float | None = None) -> tuple[float, float]:
    """(start, speed) for a take that must fill `need` seconds of the edit."""
    tail_end = max(0.5, src_len - CONTINUITY_OFFSET)
    if chain_in and chain_out:
        speed = tail_end / max(0.1, need)
        if SPEED_MIN <= speed <= SPEED_MAX:
            return 0.0, round(speed, 3)
        return 0.0, round(min(SPEED_MAX, max(SPEED_MIN, speed)), 3)
    if chain_in:
        return 0.0, 1.0
    if chain_out:
        return round(max(0.0, tail_end - need), 3), 1.0
    if best_start is not None:
        return round(max(0.0, min(best_start, src_len - need)), 3), 1.0
    return 0.0, 1.0


def frame_stats(ffmpeg: str, source: Path, at: float) -> tuple[float, float] | None:
    """(mean luma 0-255, mean saturation 0-1) of one small frame."""
    try:
        import numpy as np
        raw = subprocess.run([ffmpeg, "-hide_banner", "-loglevel", "error", "-ss", f"{max(0.0, at):.2f}",
                              "-i", str(source), "-frames:v", "1", "-vf", "scale=48:84", "-f", "rawvideo",
                              "-pix_fmt", "rgb24", "-"], capture_output=True, timeout=60).stdout
        px = np.frombuffer(raw, dtype=np.uint8).reshape(-1, 3).astype(np.float32) / 255.0
    except Exception:  # noqa: BLE001
        return None
    if not len(px):
        return None
    mx, mn = px.max(axis=1), px.min(axis=1)
    luma = (0.299 * px[:, 0] + 0.587 * px[:, 1] + 0.114 * px[:, 2]).mean() * 255
    sat = float(((mx - mn) / np.maximum(mx, 1e-3)).mean())
    return float(luma), sat


def grade_to(ref: tuple[float, float], own: tuple[float, float]) -> dict:
    """Small eq adjustment that brings a take's brightness/saturation toward the sequence's
    first take (clamped: it evens out drift, it does not repaint a shot)."""
    brightness = max(-0.08, min(0.08, (ref[0] - own[0]) / 255.0))
    saturation = max(0.85, min(1.15, ref[1] / max(0.05, own[1])))
    return {"brightness": round(brightness, 3), "saturation": round(saturation, 3)}
