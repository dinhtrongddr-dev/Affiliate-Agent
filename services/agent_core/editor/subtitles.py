"""ASS subtitle builder: karaoke captions, hook title, scene labels and CTA 2.0."""
import re
from pathlib import Path

from .tts import Word


def _ts(seconds: float) -> str:
    seconds = max(0.0, seconds)
    cs = int(round(seconds * 100))
    h, rem = divmod(cs, 360000)
    m, rem = divmod(rem, 6000)
    s, cs = divmod(rem, 100)
    return f"{h}:{m:02d}:{s:02d}.{cs:02d}"


def _clean(text: str) -> str:
    return re.sub(r"[{}\\]", "", str(text or "")).replace("\n", " ").strip()


def _wrap(text: str, max_chars: int) -> str:
    """Word-boundary wrap preserving readability without ugly splits."""
    words = _clean(text).split()
    if not words:
        return ""
    lines, line = [], ""
    for word in words:
        if line and len(line) + 1 + len(word) > max_chars:
            lines.append(line)
            line = word
        else:
            line = f"{line} {word}".strip()
    if line:
        lines.append(line)
    return r"\N".join(lines)


def chunk_words(words: list[Word], max_words: int = 4, max_gap: float = 0.45) -> list[list[Word]]:
    chunks: list[list[Word]] = []
    current: list[Word] = []
    for word in words:
        if current and (
            len(current) >= max_words
            or word.start - current[-1].end > max_gap
            or re.search(r"[.!?,;:]$", current[-1].text)
        ):
            chunks.append(current)
            current = []
        current.append(word)
    if current:
        chunks.append(current)
    return chunks


def build_ass(
    path: Path,
    width: int,
    height: int,
    font: str,
    captions: list[tuple[float, list[Word]]],
    hook: str,
    hook_end: float,
    labels: list[tuple[float, float, str]],
    cta: str,
    cta_start: float,
    total: float,
) -> Path:
    """Build SSA/ASS subtitle script with safe-zone bounds for Facebook Reels and Shopee Video.

    captions: (scene_offset, words relative to scene start).
    """
    scale = height / 1920
    fs_sub = int(76 * scale)
    fs_hook = int(92 * scale)
    fs_label = int(60 * scale)
    fs_cta = int(68 * scale)

    # Safe margins for 9:16 mobile display (Reels/Shopee UI overlay bounds)
    margin_lr = int(75 * scale)
    margin_sub_v = int(520 * scale)
    margin_hook_v = int(280 * scale)
    margin_label_v = int(440 * scale)

    header = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {width}
PlayResY: {height}
WrapStyle: 2
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Sub,{font},{fs_sub},&H0000E5FF,&H00FFFFFF,&H00000000,&H64000000,-1,0,0,0,100,100,0,0,1,{int(6*scale)},{int(3*scale)},2,{margin_lr},{margin_lr},{margin_sub_v},1
Style: Hook,{font},{fs_hook},&H00FFFFFF,&H00FFFFFF,&H00141414,&H00000000,-1,0,0,0,100,100,0,0,3,{int(18*scale)},0,8,{margin_lr},{margin_lr},{margin_hook_v},1
Style: Label,{font},{fs_label},&H00FFFFFF,&H00FFFFFF,&H00000000,&H50000000,-1,0,0,0,100,100,0,0,1,{int(5*scale)},{int(2*scale)},8,{margin_lr},{margin_lr},{margin_label_v},1
Style: CTA,{font},{fs_cta},&H00141414,&H00141414,&H0000D7FF,&H00000000,-1,0,0,0,100,100,0,0,3,{int(22*scale)},0,5,{margin_lr},{margin_lr},0,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    events: list[str] = []
    pop = r"{\fad(80,120)\fscx70\fscy70\t(0,160,\fscx100\fscy100)}"

    # 1) Hook text in top safe-zone
    clean_hook = _clean(hook)
    if clean_hook:
        events.append(
            f"Dialogue: 3,{_ts(0)},{_ts(hook_end)},Hook,,0,0,0,,{pop}{_wrap(clean_hook.upper(), 18)}"
        )

    # 2) Scene labels (if any)
    for start, end, text in labels:
        clean_text = _clean(text)
        if clean_text and end - start > 0.6:
            events.append(
                f"Dialogue: 2,{_ts(start)},{_ts(end)},Label,,0,0,0,,"
                r"{\fad(120,120)}" + _wrap(clean_text, 22)
            )

    # 3) Karaoke dialogue subtitles - NEVER cut off endings
    for offset, words in captions:
        for chunk in chunk_words(words):
            begin = offset + chunk[0].start
            finish = offset + chunk[-1].end + 0.08
            parts = []
            for word in chunk:
                k = max(1, int(round((word.end - word.start) * 100)))
                parts.append(rf"{{\kf{k}}}{_clean(word.text)}")
            events.append(
                f"Dialogue: 1,{_ts(begin)},{_ts(min(finish, total))},Sub,,0,0,0,,"
                r"{\fad(40,40)}" + " ".join(parts)
            )

    # 4) CTA card - only when explicitly non-empty and within valid bounds
    clean_cta = _clean(cta)
    if clean_cta and cta_start < total - 0.2:
        events.append(
            f"Dialogue: 4,{_ts(cta_start)},{_ts(total)},CTA,,0,0,0,,{pop}{_wrap(clean_cta, 22)}"
        )

    path = Path(path)
    path.write_text(header + "\n".join(events) + "\n", encoding="utf-8-sig")
    return path
