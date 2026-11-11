"""ASS subtitle builder: karaoke captions, hook title, scene labels and CTA."""
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
    words, lines, line = _clean(text).split(), [], ""
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
    """captions: (scene_offset, words relative to scene start)."""
    scale = height / 1920
    fs_sub, fs_hook, fs_label, fs_cta = (int(v * scale) for v in (78, 96, 62, 70))
    header = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {width}
PlayResY: {height}
WrapStyle: 2
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Sub,{font},{fs_sub},&H0000E5FF,&H00FFFFFF,&H00000000,&H64000000,-1,0,0,0,100,100,0,0,1,{int(6*scale)},{int(3*scale)},2,60,60,{int(560*scale)},1
Style: Hook,{font},{fs_hook},&H00FFFFFF,&H00FFFFFF,&H00141414,&H00000000,-1,0,0,0,100,100,0,0,3,{int(18*scale)},0,8,70,70,{int(260*scale)},1
Style: Label,{font},{fs_label},&H00FFFFFF,&H00FFFFFF,&H00000000,&H50000000,-1,0,0,0,100,100,0,0,1,{int(5*scale)},{int(2*scale)},8,70,70,{int(470*scale)},1
Style: CTA,{font},{fs_cta},&H00141414,&H00141414,&H0000D7FF,&H00000000,-1,0,0,0,100,100,0,0,3,{int(22*scale)},0,5,80,80,0,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    events: list[str] = []
    pop = r"{\fad(80,120)\fscx70\fscy70\t(0,160,\fscx100\fscy100)}"

    if hook:
        events.append(
            f"Dialogue: 3,{_ts(0)},{_ts(hook_end)},Hook,,0,0,0,,{pop}{_wrap(hook.upper(), 18)}"
        )
    for start, end, text in labels:
        if text and end - start > 0.6:
            events.append(
                f"Dialogue: 2,{_ts(start)},{_ts(end)},Label,,0,0,0,,"
                r"{\fad(120,120)}" + _wrap(text, 24)
            )
    for offset, words in captions:
        for chunk in chunk_words(words):
            begin = offset + chunk[0].start
            finish = offset + chunk[-1].end + 0.08
            if begin >= cta_start - 0.05 and cta:
                continue
            parts = []
            for word in chunk:
                k = max(1, int(round((word.end - word.start) * 100)))
                parts.append(rf"{{\kf{k}}}{_clean(word.text)}")
            events.append(
                f"Dialogue: 1,{_ts(begin)},{_ts(min(finish, total))},Sub,,0,0,0,,"
                r"{\fad(40,40)}" + " ".join(parts)
            )
    if cta:
        events.append(
            f"Dialogue: 4,{_ts(cta_start)},{_ts(total)},CTA,,0,0,0,,{pop}{_wrap(cta, 22)}"
        )
    path = Path(path)
    path.write_text(header + "\n".join(events) + "\n", encoding="utf-8-sig")
    return path
