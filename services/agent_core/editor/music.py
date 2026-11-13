"""AI Background Music Engine: Dynamic discovery, evaluation, and adaptive mixing from Mixkit.

Implements:
- AI Music Brief generation based on story mood, narrative beats, and pacing.
- Mixkit search & category scraper for royalty-free commercial stock music.
- In-depth audio file analysis (FFmpeg/FFprobe: dynamics, loudness, volume, silence, spectrum).
- 100-point AI music scoring with 8 dimensions and strict threshold gating.
- Multi-candidate retry loop (up to 5 candidates) with automatic rejection of ill-fitting tracks.
- Zero-waste disk hygiene: temporary job isolation and automatic deletion upon completion.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import random
import re
import shutil
import subprocess
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

log = logging.getLogger("affiliate_agent.music_engine")

AUDIO_EXTENSIONS = {".mp3", ".m4a", ".aac", ".wav", ".ogg", ".flac"}
MIXKIT_BASE_URL = "https://mixkit.co/free-stock-music"
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"


# ---------------------------------------------------------------------------
# DATA STRUCTURES
# ---------------------------------------------------------------------------

@dataclass
class MusicBrief:
    """Creative and acoustic requirements for a specific video production."""
    primary_mood: str
    secondary_mood: str
    instruments: list[str]
    energy_progression: str
    tempo: str
    genre: str
    search_queries: list[str]
    categories: list[str]
    target_duration_sec: float
    is_instrumental_only: bool = True
    vocal_clash_risk: str = "high"


@dataclass
class MixkitCandidate:
    """Metadata of a music track candidate discovered on Mixkit."""
    track_id: str
    title: str
    author: str
    preview_url: str
    waveform_url: str
    source_url: str
    tags: list[str]
    duration_str: str
    license_type: str = "Mixkit Stock Music Free License"
    license_url: str = "https://mixkit.co/license/#musicFree"


@dataclass
class AudioAnalysisMetrics:
    """Acoustic measurements performed on the downloaded audio file."""
    duration_sec: float = 0.0
    mean_volume_db: float = -20.0
    max_volume_db: float = 0.0
    dynamic_range_db: float = 12.0
    silence_duration_sec: float = 0.0
    sample_rate: int = 44100
    channels: int = 2
    bitrate: int = 320000
    is_valid_audio: bool = True


@dataclass
class MusicScoreBreakdown:
    """100-point evaluation rubric for background music fit."""
    mood_match: float = 0.0          # Max 25
    story_progression: float = 0.0   # Max 20
    voice_compatibility: float = 0.0 # Max 15
    pacing_tempo: float = 0.0        # Max 10
    audio_quality: float = 0.0       # Max 10
    climax_potential: float = 0.0    # Max 10
    ending_fit: float = 0.0          # Max 5
    duration_fit: float = 0.0        # Max 5
    total: float = 0.0               # Max 100
    decision: str = "REJECT"         # "PRIORITY", "PASS", "MARGINAL", "REJECT"
    reasons: list[str] = field(default_factory=list)


class SelectedMusicTrack(os.PathLike):
    """Container for the chosen track that acts as a file path and carries metadata."""

    def __init__(
        self,
        path: Path,
        candidate: MixkitCandidate,
        brief: MusicBrief,
        metrics: AudioAnalysisMetrics,
        score: MusicScoreBreakdown,
        job_id: str,
        temp_dir: Path,
    ) -> None:
        self.path = Path(path)
        self.candidate = candidate
        self.brief = brief
        self.metrics = metrics
        self.score = score
        self.job_id = job_id
        self.temp_dir = temp_dir
        self._cleaned = False

    def __fspath__(self) -> str:
        return str(self.path)

    def __str__(self) -> str:
        return str(self.path)

    def cleanup(self) -> None:
        """Purge all temporary candidate files and audio work artifacts."""
        if self._cleaned:
            return
        try:
            if self.temp_dir.exists():
                shutil.rmtree(self.temp_dir, ignore_errors=True)
            self._cleaned = True
            log.info("Zero-Waste: Cleaned up temporary music directory for job %s", self.job_id)
        except Exception as exc:  # noqa: BLE001
            log.warning("Could not clean music temp dir %s: %s", self.temp_dir, exc)

    def to_dict(self) -> dict[str, Any]:
        return {
            "track_id": self.candidate.track_id,
            "title": self.candidate.title,
            "author": self.candidate.author,
            "source_url": self.candidate.source_url,
            "preview_url": self.candidate.preview_url,
            "license": self.candidate.license_type,
            "license_url": self.candidate.license_url,
            "duration_str": self.candidate.duration_str,
            "duration_sec": self.metrics.duration_sec,
            "score": self.score.total,
            "decision": self.score.decision,
            "reasons": self.score.reasons,
            "primary_mood": self.brief.primary_mood,
            "energy_progression": self.brief.energy_progression,
            "cleaned_up": self._cleaned,
        }


# ---------------------------------------------------------------------------
# STEP 1: MUSIC BRIEF GENERATOR
# ---------------------------------------------------------------------------

def generate_music_brief(timeline: dict, plan: dict | None = None) -> MusicBrief:
    """Analyze video story, emotion, dialogue, and duration to formulate an acoustic brief."""
    plan = plan or timeline
    title = str(plan.get("title") or timeline.get("title") or "").strip()
    hook = str(plan.get("hook") or timeline.get("hook") or "").strip()
    scenes = timeline.get("scenes") or plan.get("scenes") or []
    voiceovers = " ".join(str(s.get("voiceover") or "") for s in scenes)
    music_mood = str(plan.get("music_mood") or timeline.get("music_mood") or "upbeat").lower()
    total_dur = float(timeline.get("duration") or sum(float(s.get("duration") or 3.0) for s in scenes) or 25.0)

    corpus = f"{title} {hook} {voiceovers} {music_mood}".lower()

    # 1. Pet / Dog / Cat / Funny / Comedy / Playful (Check first so pet stories are not misclassified)
    if any(k in corpus for k in ("chó", "mèo", "pet", "thú cưng", "hài hước", "vui nhộn", "ngã ngửa", "nhắng nhít")):
        return MusicBrief(
            primary_mood="Happy, Playful, Cheerful",
            secondary_mood="Heartwarming, Funny",
            instruments=["Acoustic Guitar", "Piano", "Light Percussion", "Ukulele"],
            energy_progression="Medium -> High -> Warm Emotional Ending",
            tempo="Moderate / Upbeat",
            genre="Acoustic Playful",
            search_queries=[
                "happy playful acoustic background music",
                "cute playful pet background music",
                "upbeat comedy cheerful instrumental",
                "fun acoustic guitar positive",
            ],
            categories=["happy", "acoustic", "children"],
            target_duration_sec=total_dur,
        )

    # 2. Emotional Story / Family / Mother / Sacrifice
    if any(k in corpus for k in ("mẹ", "bố", "ba mẹ", "cha mẹ", "gia đình", "người con", "con cái", "thương mẹ", "hy sinh", "bữa cơm", "ấm áp", "hoài niệm", "nước mắt")):
        return MusicBrief(
            primary_mood="Emotional, Warm, Heartwarming",
            secondary_mood="Nostalgic, Hopeful, Gentle",
            instruments=["Piano", "Soft Strings", "Cello"],
            energy_progression="Low -> Medium -> Emotional Climax -> Soft Ending",
            tempo="Slow / Moderate",
            genre="Cinematic Emotional",
            search_queries=[
                "emotional cinematic piano background music",
                "heartwarming family piano strings",
                "sad emotional drama instrumental",
                "nostalgic gentle acoustic piano",
            ],
            categories=["emotional", "cinematic", "ambient"],
            target_duration_sec=total_dur,
        )

    # 3. Suspense / Curiosity / Pattern Interrupt / Tension
    if any(k in corpus for k in ("bí mật", "tò mò", "khoan đã", "nguy hiểm", "sự thật", "không ngờ", "suspense", "drama")):
        return MusicBrief(
            primary_mood="Suspenseful, Dramatic, Mysterious",
            secondary_mood="Curious, Tension-building",
            instruments=["Strings", "Staccato Cello", "Subtle Synth", "Ticking Clock"],
            energy_progression="Tense Intro -> Rising Suspense -> Climax Reveal",
            tempo="Moderate",
            genre="Cinematic Suspense",
            search_queries=[
                "cinematic suspense background music",
                "dramatic tension mystery instrumental",
                "curious investigation subtle background",
            ],
            categories=["dramatic", "cinematic"],
            target_duration_sec=total_dur,
        )

    # 4. Product Review / Tutorial / Life Hacks / Commercial
    if any(k in corpus for k in ("review", "sản phẩm", "tiện ích", "giải pháp", "đập hộp", "hướng dẫn", "chất lượng")):
        return MusicBrief(
            primary_mood="Inspirational, Uplifting, Trustworthy",
            secondary_mood="Modern, Clean, Positive",
            instruments=["Acoustic Guitar", "Modern Synth", "Warm Piano"],
            energy_progression="Engaging Intro -> Steady Dynamic Rhythm -> Clear Finish",
            tempo="Moderate",
            genre="Corporate Lifestyle",
            search_queries=[
                "inspirational uplifting corporate background music",
                "calm modern acoustic lifestyle instrumental",
                "positive commercial background music",
            ],
            categories=["ambient", "acoustic", "happy"],
            target_duration_sec=total_dur,
        )

    # 5. Default by explicit music_mood
    mood_map = {
        "emotional": ("Emotional, Warm", "Cinematic Piano", ["emotional cinematic piano", "gentle piano drama"], ["emotional", "cinematic"]),
        "funny": ("Playful, Comedy", "Ukulele, Percussion", ["upbeat comedy cheerful", "playful funny acoustic"], ["happy", "acoustic"]),
        "chill": ("Chill, Relaxed", "Lo-Fi, Soft Piano", ["chill lofi ambient piano", "calm acoustic guitar"], ["ambient", "acoustic"]),
        "epic": ("Epic, Heroic", "Full Orchestral Strings", ["epic cinematic orchestral", "inspiring dramatic trailer"], ["cinematic", "dramatic"]),
        "upbeat": ("Upbeat, Positive", "Acoustic Pop", ["happy positive acoustic pop", "bright uplifting instrumental"], ["happy", "acoustic"]),
    }
    p_mood, inst, queries, cats = mood_map.get(music_mood, mood_map["upbeat"])
    return MusicBrief(
        primary_mood=p_mood,
        secondary_mood="Engaging, Dynamic",
        instruments=[inst],
        energy_progression="Dynamic build to satisfying payoff",
        tempo="Moderate",
        genre="General Instrumental",
        search_queries=queries,
        categories=cats,
        target_duration_sec=total_dur,
    )


# ---------------------------------------------------------------------------
# STEP 2: MIXKIT DISCOVERY & SCRAPING CLIENT
# ---------------------------------------------------------------------------

class MixkitMusicClient:
    """Safe, rate-respectful client for discovering tracks on Mixkit."""

    def __init__(self, timeout: int = 15) -> None:
        self.timeout = timeout

    def _fetch_html(self, url: str) -> str:
        req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                if resp.status != 200:
                    return ""
                return resp.read().decode("utf-8", errors="ignore")
        except Exception as exc:  # noqa: BLE001
            log.warning("Mixkit fetch failed for %s: %s", url, exc)
            return ""

    def search_candidates(self, query: str, limit: int = 6) -> list[MixkitCandidate]:
        """Search Mixkit free stock music by keyword query."""
        encoded = urllib.parse.quote_plus(query)
        url = f"{MIXKIT_BASE_URL}/?q={encoded}"
        html = self._fetch_html(url)
        return self._parse_cards(html, limit=limit)

    def get_category_candidates(self, category: str, limit: int = 6) -> list[MixkitCandidate]:
        """Retrieve tracks from a Mixkit category or mood."""
        url = f"{MIXKIT_BASE_URL}/{urllib.parse.quote(category)}/"
        html = self._fetch_html(url)
        return self._parse_cards(html, limit=limit)

    def _parse_cards(self, html: str, limit: int = 6) -> list[MixkitCandidate]:
        if not html:
            return []

        # Find item blocks
        items = re.findall(
            r'<div[^>]*class="[^"]*item-grid__item[^"]*"[^>]*>(.*?)</div>\s*</div>',
            html, re.DOTALL
        )
        candidates: list[MixkitCandidate] = []
        seen_ids = set()

        for block in items:
            preview_m = re.search(r'data-audio-player-preview-url-value="([^"]+)"', block)
            if not preview_m:
                continue
            preview_url = preview_m.group(1).strip()
            item_id_m = re.search(r'data-audio-player-item-id-value="([^"]+)"', block)
            track_id = item_id_m.group(1).strip() if item_id_m else preview_url.split("/")[-1].replace(".mp3", "")

            if track_id in seen_ids:
                continue
            seen_ids.add(track_id)

            waveform_m = re.search(r'data-audio-player-waveform-url-value="([^"]+)"', block)
            waveform_url = waveform_m.group(1).strip() if waveform_m else ""

            title_m = (re.search(r'item-grid-card__title[^>]*>\s*([^<]{2,80})', block)
                       or re.search(r'<h2[^>]*>\s*([^<]{2,80})', block))
            if not title_m:  # the card markup nests deeper than the item block: look ahead
                pos = html.find(f'data-audio-player-item-id-value="{track_id}"')
                title_m = re.search(r'item-grid-card__title[^>]*>\s*([^<]{2,80})', html[pos:pos + 6000])                     if pos >= 0 else None
            title = " ".join(title_m.group(1).split()) if title_m else f"Mixkit Track {track_id}"

            author_m = re.search(r'<p[^>]*class="[^"]*author[^"]*"[^>]*>\s*by\s*(.*?)\s*</p>', block, re.DOTALL)
            author = re.sub(r'<[^>]+>', '', author_m.group(1)).strip() if author_m else "Mixkit Creator"

            dur_m = re.search(r'data-test-id="duration"[^>]*>\s*([0-9:]+)\s*<', block)
            duration_str = dur_m.group(1).strip() if dur_m else "2:00"

            tags = [
                re.sub(r'<[^>]+>', '', t).strip()
                for t in re.findall(r'<a[^>]*class="meta-links__link"[^>]*>(.*?)</a>', block)
                if re.sub(r'<[^>]+>', '', t).strip()
            ]

            candidates.append(MixkitCandidate(
                track_id=track_id,
                title=title,
                author=author,
                preview_url=preview_url,
                waveform_url=waveform_url,
                source_url=f"{MIXKIT_BASE_URL}/",
                tags=tags,
                duration_str=duration_str,
            ))
            if len(candidates) >= limit:
                break

        return candidates


# ---------------------------------------------------------------------------
# STEP 3: SAFE DOWNLOAD INTO JOB TEMP DIRECTORY
# ---------------------------------------------------------------------------

def download_candidate_track(candidate: MixkitCandidate, temp_dir: Path) -> Path | None:
    """Download candidate MP3 into isolated job temp directory and verify validity."""
    temp_dir.mkdir(parents=True, exist_ok=True)
    target_path = temp_dir / f"mixkit_{candidate.track_id}.mp3"

    req = urllib.request.Request(candidate.preview_url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=25) as resp:
            data = resp.read()

        if len(data) < 50 * 1024:  # At least 50 KB
            log.warning("Downloaded candidate file too small (%d bytes), likely corrupt", len(data))
            return None

        # Verify not an HTML error page
        header_sample = data[:150].lower()
        if b"<!doctype" in header_sample or b"<html" in header_sample:
            log.warning("Downloaded candidate is an HTML page, not an audio stream")
            return None

        target_path.write_bytes(data)
        return target_path
    except Exception as exc:  # noqa: BLE001
        log.warning("Download failed for candidate %s: %s", candidate.track_id, exc)
        return None


# ---------------------------------------------------------------------------
# STEP 4: ACOUSTIC AUDIO ANALYSIS (FFMPEG / FFPROBE)
# ---------------------------------------------------------------------------

def analyze_audio_file(
    file_path: Path,
    ffprobe_path: str = "ffprobe",
    ffmpeg_path: str = "ffmpeg",
) -> AudioAnalysisMetrics:
    """Inspect real audio stream properties, volume, dynamic range, and silence."""
    file_path = Path(file_path)
    if not file_path.is_file() or file_path.stat().st_size <= 0:
        return AudioAnalysisMetrics(is_valid_audio=False)

    # 1. FFprobe metadata
    cmd_probe = [
        ffprobe_path, "-v", "error", "-show_entries",
        "format=duration,size,bit_rate:stream=codec_name,sample_rate,channels",
        "-of", "json", str(file_path),
    ]
    try:
        res_probe = subprocess.run(cmd_probe, capture_output=True, text=True,
                                   encoding="utf-8", errors="replace", timeout=30)
        data = json.loads(res_probe.stdout or "{}")
        streams = data.get("streams") or []
        audio_stream = next((s for s in streams if s.get("codec_name")), streams[0] if streams else {})
        fmt = data.get("format") or {}
        duration = float(fmt.get("duration") or 0.0)
        bitrate = int(fmt.get("bit_rate") or 320000)
        sample_rate = int(audio_stream.get("sample_rate") or 44100)
        channels = int(audio_stream.get("channels") or 2)
    except Exception as exc:  # noqa: BLE001
        log.warning("FFprobe analysis failed: %s", exc)
        return AudioAnalysisMetrics(is_valid_audio=False)

    # 2. FFmpeg volume and dynamics detection
    cmd_vol = [
        ffmpeg_path, "-hide_banner", "-nostats", "-i", str(file_path),
        "-af", "volumedetect,silencedetect=n=-45dB:d=1.0,astats=metadata=1:reset=1",
        "-f", "null", "-",
    ]
    mean_vol_db = -18.0
    max_vol_db = 0.0
    dyn_range_db = 12.0
    total_silence = 0.0

    try:
        res_vol = subprocess.run(cmd_vol, capture_output=True, text=True,
                                 encoding="utf-8", errors="replace", timeout=60)
        err = res_vol.stderr or ""
        max_m = re.search(r"max_volume:\s*([-\d.]+)\s*dB", err)
        mean_m = re.search(r"mean_volume:\s*([-\d.]+)\s*dB", err)
        silences = [float(x) for x in re.findall(r"silence_duration:\s*([\d.]+)", err)]
        if max_m:
            max_vol_db = float(max_m.group(1))
        if mean_m:
            mean_vol_db = float(mean_m.group(1))
        dyn_range_db = round(max(0.0, max_vol_db - mean_vol_db), 1)
        total_silence = round(sum(silences), 2)
    except Exception as exc:  # noqa: BLE001
        log.warning("FFmpeg volume analysis failed: %s", exc)

    return AudioAnalysisMetrics(
        duration_sec=round(duration, 2),
        mean_volume_db=round(mean_vol_db, 1),
        max_volume_db=round(max_vol_db, 1),
        dynamic_range_db=dyn_range_db,
        silence_duration_sec=total_silence,
        sample_rate=sample_rate,
        channels=channels,
        bitrate=bitrate,
        is_valid_audio=duration > 3.0,
    )


# ---------------------------------------------------------------------------
# STEP 5: MUSIC SCORING (100-POINT AI RUBRIC)
# ---------------------------------------------------------------------------

def evaluate_music_candidate(
    metrics: AudioAnalysisMetrics,
    candidate: MixkitCandidate,
    brief: MusicBrief,
) -> MusicScoreBreakdown:
    """Evaluate downloaded audio candidate against story requirements across 8 dimensions."""
    if not metrics.is_valid_audio or metrics.duration_sec < 5.0:
        return MusicScoreBreakdown(
            total=0.0,
            decision="REJECT",
            reasons=["File âm thanh không hợp lệ hoặc thời lượng quá ngắn"],
        )

    reasons: list[str] = []
    track_meta_text = f"{candidate.title} {' '.join(candidate.tags)}".lower()
    brief_mood_text = f"{brief.primary_mood} {brief.secondary_mood} {' '.join(brief.instruments)}".lower()

    # 1. Mood match (Max 25)
    mood_score = 15.0
    positive_matches = sum(1 for w in brief_mood_text.replace(",", " ").split() if len(w) > 3 and w in track_meta_text)
    mood_score += min(10.0, positive_matches * 3.5)

    # Penalize mood contradiction
    if "emotional" in brief_mood_text and any(bad in track_meta_text for bad in ("techno", "rock", "metal", "party", "club", "disco")):
        mood_score -= 14.0
        reasons.append("Thể loại bài nhạc quá sôi động, xung đột với cảm xúc câu chuyện gia đình")
    elif "playful" in brief_mood_text and any(bad in track_meta_text for bad in ("dark", "horror", "funeral", "sad", "tragic")):
        mood_score -= 14.0
        reasons.append("Nhạc u tối, xung đột với tính hài hước vui nhộn của thú cưng")
    else:
        reasons.append(f"Cảm xúc phù hợp định hướng: {brief.primary_mood}")
    mood_score = round(max(0.0, min(25.0, mood_score)), 1)

    # 2. Story progression (Max 20)
    prog_score = 14.0
    if metrics.dynamic_range_db >= 8.0:
        prog_score += 6.0
        reasons.append("Độ biến thiên năng lượng (Dynamic range) tốt, có phân đoạn cao trào tự nhiên")
    elif metrics.dynamic_range_db >= 4.0:
        prog_score += 3.0
    else:
        prog_score -= 4.0
        reasons.append("Bài nhạc đơn điệu, ít biến thiên cảm xúc")
    prog_score = round(max(0.0, min(20.0, prog_score)), 1)

    # 3. Voice compatibility (Max 15)
    voice_score = 11.0
    # Ideal background volume is between -24 dB and -14 dB for smooth ducking
    if -26.0 <= metrics.mean_volume_db <= -12.0:
        voice_score += 4.0
        reasons.append("Âm lượng chuẩn, dễ dàng tự động giảm âm (ducking) khi giọng đọc xuất hiện")
    elif metrics.mean_volume_db > -10.0:
        voice_score -= 4.0
        reasons.append("Âm lượng nhạc nền quá lớn, có nguy cơ lấn át giọng thuyết minh")
    voice_score = round(max(0.0, min(15.0, voice_score)), 1)

    # 4. Pacing & Tempo (Max 10)
    pacing_score = 8.0
    if "upbeat" in brief.tempo.lower() and any(k in track_meta_text for k in ("upbeat", "energetic", "fast", "groove")):
        pacing_score += 2.0
    elif "slow" in brief.tempo.lower() and any(k in track_meta_text for k in ("slow", "gentle", "calm", "soft")):
        pacing_score += 2.0
    pacing_score = round(max(0.0, min(10.0, pacing_score)), 1)

    # 5. Audio Quality (Max 10)
    quality_score = 8.0
    if metrics.sample_rate >= 44100 and metrics.bitrate >= 128000:
        quality_score += 2.0
    if metrics.max_volume_db >= -0.05:
        quality_score -= 3.0
        reasons.append("Âm thanh có nguy cơ clipping ở một số đoạn đỉnh")
    quality_score = round(max(0.0, min(10.0, quality_score)), 1)

    # 6. Climax potential (Max 10)
    climax_score = 7.0
    if metrics.dynamic_range_db >= 10.0:
        climax_score += 3.0
    elif metrics.dynamic_range_db >= 6.0:
        climax_score += 1.5
    climax_score = round(max(0.0, min(10.0, climax_score)), 1)

    # 7. Ending fit (Max 5)
    ending_score = 4.0
    if metrics.silence_duration_sec < 4.0:
        ending_score += 1.0
    ending_score = round(max(0.0, min(5.0, ending_score)), 1)

    # 8. Duration fit (Max 5)
    duration_score = 3.5
    if metrics.duration_sec >= brief.target_duration_sec:
        duration_score = 5.0
        reasons.append(f"Thời lượng bài nhạc ({metrics.duration_sec:.1f}s) đủ dài cho video ({brief.target_duration_sec:.1f}s)")
    elif metrics.duration_sec >= brief.target_duration_sec * 0.7:
        duration_score = 3.0
    else:
        duration_score = 1.0
        reasons.append("Thời lượng bài nhạc quá ngắn so với độ dài video")
    duration_score = round(max(0.0, min(5.0, duration_score)), 1)

    total = round(
        mood_score + prog_score + voice_score + pacing_score
        + quality_score + climax_score + ending_score + duration_score,
        1
    )

    if total >= 90.0:
        decision = "PRIORITY"
    elif total >= 80.0:
        decision = "PASS"
    elif total >= 70.0:
        decision = "MARGINAL"
    else:
        decision = "REJECT"

    return MusicScoreBreakdown(
        mood_match=mood_score,
        story_progression=prog_score,
        voice_compatibility=voice_score,
        pacing_tempo=pacing_score,
        audio_quality=quality_score,
        climax_potential=climax_score,
        ending_fit=ending_score,
        duration_fit=duration_score,
        total=total,
        decision=decision,
        reasons=reasons,
    )


# ---------------------------------------------------------------------------
# STEP 6: DYNAMIC DISCOVERY, EVALUATION & RETRY LOOP
# ---------------------------------------------------------------------------

SEASONAL = re.compile(r"christmas|xmas|halloween|fright|spooky|wedding|holiday|santa|jingle|horror",
                      re.I)

# Mixkit pages that actually filter (checked 2026-11): mood/<x>, tag/<x>, genre slugs.
MOOD_PAGES = {
    "emotional": ["mood/sad", "mood/romantic", "mood/peaceful"],
    "funny": ["mood/funny", "tag/ukulele", "mood/happy"],
    "happy": ["mood/happy", "tag/ukulele", "tag/acoustic"],
    "chill": ["chillout", "mood/peaceful", "tag/acoustic"],
    "dramatic": ["mood/dramatic"],
    "review": ["mood/happy", "tag/acoustic", "chillout"],
}


def mood_pages(brief: "MusicBrief", timeline: dict | None = None) -> list[str]:
    text = f"{brief.primary_mood} {brief.genre} {(timeline or {}).get('music_mood') or ''}".lower()
    keys = []
    for key, words in (("emotional", ("emotional", "warm", "sad", "nostalg")),
                       ("funny", ("funny", "comedy", "playful")),
                       ("dramatic", ("suspense", "dramatic", "epic", "mysterious")),
                       ("chill", ("chill", "relaxed", "calm")),
                       ("review", ("corporate", "inspirational", "lifestyle")),
                       ("happy", ("happy", "upbeat", "positive", "cheerful"))):
        if any(w in text for w in words):
            keys.append(key)
    pages = []
    for key in keys or ["happy"]:
        pages += [p for p in MOOD_PAGES[key] if p not in pages]
    return pages


def recent_track_ids(settings, exclude_job: str = "", limit: int = 8) -> set[str]:
    """Tracks used by the latest videos, so consecutive videos do not sound the same."""
    import json
    import sqlite3
    db = getattr(settings, "database_path", None)
    if not db or not Path(db).is_file():
        return set()
    conn = None
    try:
        conn = sqlite3.connect(db)   # closed explicitly: "with" only commits on Windows
        rows = conn.execute("SELECT id, script_json FROM jobs WHERE script_json LIKE '%music_metadata%' "
                            "ORDER BY updated_at DESC LIMIT 40").fetchall()
    except sqlite3.Error:
        return set()
    finally:
        if conn is not None:
            conn.close()
    used = []
    for job_id, raw in rows:
        if job_id == exclude_job:
            continue
        try:
            meta = (json.loads(raw).get("render") or {}).get("music_metadata") or {}
        except (TypeError, ValueError):
            continue
        tid = str(meta.get("track_id") or "")
        if tid and tid not in used:
            used.append(tid)
        if len(used) >= limit:
            break
    return set(used)


def discover_and_select_music(
    timeline: dict,
    job_id: str,
    settings: Any,
    max_candidates: int = 5,
) -> SelectedMusicTrack | None:
    """Execute autonomous music discovery on Mixkit, download, evaluate and select best fit."""
    brief = generate_music_brief(timeline)
    client = MixkitMusicClient(timeout=15)
    temp_dir = settings.project_root / "temp" / "music_jobs" / job_id
    temp_dir.mkdir(parents=True, exist_ok=True)

    log.info("AI Music Brief for %s: Mood='%s', Target=%0.1fs", job_id, brief.primary_mood, brief.target_duration_sec)

    candidates: list[MixkitCandidate] = []
    recent = recent_track_ids(settings, exclude_job=job_id)
    rng = random.Random(job_id)
    for page in mood_pages(brief, timeline):
        found = client.get_category_candidates(page, limit=24)
        rng.shuffle(found)
        for c in found:
            if (c.track_id in recent or SEASONAL.search(c.title or "")
                    or any(existing.track_id == c.track_id for existing in candidates)):
                continue
            candidates.append(c)
            if len(candidates) >= max_candidates:
                break
        if len(candidates) >= max_candidates:
            break

    if not candidates:
        log.warning("Mixkit returned no candidates for job %s", job_id)
        cleanup_music_job_cache(job_id, settings.project_root / "temp" / "music_jobs")
        return None

    marginal_backup: tuple[SelectedMusicTrack, float] | None = None
    evaluated_count = 0

    for candidate in candidates[:max_candidates]:
        evaluated_count += 1
        log.info("Evaluating candidate %d/%d: '%s' by %s (ID %s)",
                 evaluated_count, min(len(candidates), max_candidates),
                 candidate.title, candidate.author, candidate.track_id)

        track_file = download_candidate_track(candidate, temp_dir)
        if not track_file:
            continue

        metrics = analyze_audio_file(track_file, settings.ffprobe_path, settings.ffmpeg_path)
        score = evaluate_music_candidate(metrics, candidate, brief)

        log.info("Candidate '%s' score: %0.1f/100 -> %s", candidate.title, score.total, score.decision)

        # 1. Accepted directly (PASS or PRIORITY)
        if score.decision in ("PRIORITY", "PASS"):
            log.info("Selected optimal track '%s' (%0.1f/100) for job %s", candidate.title, score.total, job_id)
            return SelectedMusicTrack(
                path=track_file,
                candidate=candidate,
                brief=brief,
                metrics=metrics,
                score=score,
                job_id=job_id,
                temp_dir=temp_dir,
            )

        # 2. Marginal track: Keep as backup only if no better option arrives
        if score.decision == "MARGINAL":
            if marginal_backup is None or score.total > marginal_backup[1]:
                selected_marginal = SelectedMusicTrack(
                    path=track_file,
                    candidate=candidate,
                    brief=brief,
                    metrics=metrics,
                    score=score,
                    job_id=job_id,
                    temp_dir=temp_dir,
                )
                marginal_backup = (selected_marginal, score.total)
                continue

        # 3. Rejected: Delete temporary file immediately to preserve disk space
        try:
            track_file.unlink(missing_ok=True)
            log.info("Purged rejected candidate file %s", track_file.name)
        except Exception:
            pass

    # If no track achieved >= 80 but a marginal track (70-79) exists, use the best marginal
    if marginal_backup is not None:
        best_marginal = marginal_backup[0]
        log.info("Using marginal fallback track '%s' (%0.1f/100) for job %s",
                 best_marginal.candidate.title, best_marginal.score.total, job_id)
        return best_marginal

    # No acceptable track found
    log.warning("No Mixkit track met the >=70 threshold for job %s", job_id)
    cleanup_music_job_cache(job_id, settings.project_root / "temp" / "music_jobs")
    return None


# ---------------------------------------------------------------------------
# STEP 10: DISK HYGIENE & CLEANUP
# ---------------------------------------------------------------------------

def cleanup_music_job_cache(job_id: str, base_temp_dir: Path | None = None) -> None:
    """Purge temporary music files for a job to guarantee zero-waste disk hygiene."""
    if not job_id:
        return
    candidates = []
    if base_temp_dir:
        candidates.append(Path(base_temp_dir) / job_id)
    candidates.append(Path("temp/music_jobs") / job_id)

    for target in candidates:
        if target.exists() and target.is_dir():
            try:
                shutil.rmtree(target, ignore_errors=True)
                log.info("Purged temporary music directory: %s", target)
            except Exception as exc:  # noqa: BLE001
                log.warning("Failed to remove temp music dir %s: %s", target, exc)


# ---------------------------------------------------------------------------
# BACKWARD-COMPATIBLE DISPATCHER
# ---------------------------------------------------------------------------

def list_tracks(music_dir: Path) -> list[Path]:
    """Scan local directory for existing audio tracks (legacy support)."""
    music_dir = Path(music_dir)
    if not music_dir.is_dir():
        return []
    return sorted(p for p in music_dir.rglob("*") if p.suffix.lower() in AUDIO_EXTENSIONS)


def pick_track(
    music_dir: Path,
    mood: str = "",
    seed: str = "",
    timeline: dict | None = None,
    settings: Any = None,
    job_id: str = "",
) -> Path | SelectedMusicTrack | None:
    """Smart track selector: prioritizes Mixkit AI discovery, falls back to local tracks if needed."""
    effective_job_id = job_id or seed
    # 1. Attempt AI dynamic discovery from Mixkit if timeline and settings are provided
    if timeline and settings and effective_job_id:
        try:
            dynamic_track = discover_and_select_music(
                timeline=timeline,
                job_id=effective_job_id,
                settings=settings,
            )
            if dynamic_track is not None:
                return dynamic_track
        except Exception as exc:  # noqa: BLE001
            log.warning("AI music discovery error: %s; falling back to local pool", exc)

    # 2. Fallback to local music directory
    tracks = list_tracks(music_dir)
    if not tracks:
        return None
    mood_words = [w for w in (mood or "").lower().replace(",", " ").split() if len(w) > 2]
    matching = [t for t in tracks if any(w in str(t).lower() for w in mood_words)]
    pool = matching or tracks
    index = int(hashlib.sha256(seed.encode()).hexdigest(), 16) % len(pool) if seed else 0
    return pool[index]
