"""Timeline -> finished 9:16 short video.

A timeline is a plain dict (stored as script_json) so it can be edited in the UI:

    {
      "hook": "Câu hook 0-3 giây",
      "cta": "Link sản phẩm ở giỏ hàng",
      "music_mood": "upbeat",
      "scenes": [
        {"kind": "video", "source": "C:/in.mp4", "start": 3.2, "duration": 2.5,
         "voiceover": "...", "text": "nhãn trên màn hình", "motion": "zoom_in"},
        {"kind": "image", "source": "C:/product.jpg", "duration": 3,
         "voiceover": "...", "text": "", "motion": "pan_left"}
      ]
    }

Each scene becomes a normalised clip (blurred-fill 9:16, Ken Burns/zoom
motion, exact length matched to its voice-over), clips are chained with
varied xfade transitions, then voice, licensed music (ducked under the
voice) and karaoke subtitles are mixed and loudness-normalised.
"""
import logging
import shutil
from dataclasses import dataclass
from pathlib import Path

from .ffmpeg import FFmpegError, probe, run_ffmpeg
from .music import pick_track
from .subtitles import build_ass
from .tts import Speech, synthesize

log = logging.getLogger("affiliate_agent.render")

TRANSITIONS = ["smoothleft", "fade", "slideup", "circleopen", "smoothup",
               "wipeleft", "zoomin", "radial", "slideleft", "dissolve"]
MOTIONS = ["zoom_in", "pan_right", "zoom_out", "pan_left"]
MIN_SCENE, MAX_SCENE = 1.4, 7.0
CARD_PALETTES = [("0x141E30", "0xEE0979"), ("0x0F2027", "0x2C9E8F"),
                 ("0x232526", "0xFF8008"), ("0x1D2671", "0xC33764")]


class RenderError(RuntimeError):
    pass


@dataclass
class RenderResult:
    path: Path
    duration: float
    scenes: list[dict]
    music: str | None
    voice: bool
    subtitles: Path


def _motion_filter(motion: str, frames: int, w: int, h: int, fps: int) -> str:
    n = max(1, frames - 1)
    if motion == "zoom_in":
        z, x, y = f"1+0.10*on/{n}", "iw/2-(iw/zoom/2)", "ih/2-(ih/zoom/2)"
    elif motion == "zoom_out":
        z, x, y = f"1.10-0.10*on/{n}", "iw/2-(iw/zoom/2)", "ih/2-(ih/zoom/2)"
    elif motion == "pan_left":
        z, x, y = "1.08", f"(iw-iw/zoom)*(1-on/{n})", "ih/2-(ih/zoom/2)"
    elif motion == "pan_right":
        z, x, y = "1.08", f"(iw-iw/zoom)*on/{n}", "ih/2-(ih/zoom/2)"
    else:
        return f"scale={w}:{h},fps={fps}"
    return f"zoompan=z='{z}':x='{x}':y='{y}':d=1:s={w}x{h}:fps={fps}"


def _fill_916(label_in: str, w: int, h: int, prescale: float) -> str:
    """Blurred background fill + fitted foreground, no black bars."""
    pw, ph = int(w * prescale) // 2 * 2, int(h * prescale) // 2 * 2
    return (
        f"[{label_in}]split=2[bgsrc][fgsrc];"
        f"[bgsrc]scale={w // 4}:{h // 4}:force_original_aspect_ratio=increase,"
        f"crop={w // 4}:{h // 4},boxblur=10:2,eq=brightness=-0.06:saturation=1.15,"
        f"scale={pw}:{ph}[bg];"
        f"[fgsrc]scale={pw}:{ph}:force_original_aspect_ratio=decrease,setsar=1[fg];"
        f"[bg][fg]overlay=(W-w)/2:(H-h)/2,format=yuv420p"
    )


def render_scene(settings, scene: dict, duration: float, out: Path, index: int) -> Path:
    w, h, fps = settings.render_width, settings.render_height, settings.render_fps
    frames = max(2, int(round(duration * fps)))
    motion = scene.get("motion") or MOTIONS[index % len(MOTIONS)]
    kind = scene.get("kind", "video")
    source = Path(scene["source"])
    if not source.is_file():
        raise RenderError(f"Scene {index} source is missing: {source}")

    keep_audio = False
    if kind == "card":
        c0, c1 = CARD_PALETTES[index % len(CARD_PALETTES)]
        inputs = ["-f", "lavfi", "-i",
                  f"gradients=s={w}x{h}:r={fps}:d={duration + 0.5:.3f}:c0={c0}:c1={c1}"
                  f":nb_colors=2:speed=0.012",
                  "-loop", "1", "-framerate", str(fps), "-t", f"{duration + 0.5:.3f}",
                  "-i", str(source)]
        fg_w = int(w * 0.8) // 2 * 2
        graph = (
            f"[1:v]fps={fps},scale={fg_w}:{int(h * 0.55)}:force_original_aspect_ratio=decrease,"
            f"pad=iw+24:ih+24:12:12:color=white,setsar=1[card];"
            f"[0:v][card]overlay=(W-w)/2:H*0.40-h/2,format=yuv420p,"
            + _motion_filter(motion if motion != "pan_left" else "zoom_in", frames, w, h, fps)
            + f",trim=end_frame={frames},setpts=PTS-STARTPTS,setsar=1,format=yuv420p[v]"
        )
        inputs += ["-f", "lavfi", "-t", f"{duration:.3f}", "-i",
                   "anullsrc=channel_layout=stereo:sample_rate=44100"]
        run_ffmpeg(settings.ffmpeg_path, [
            *inputs, "-filter_complex", graph, "-map", "[v]", "-map", "2:a",
            "-t", f"{duration:.3f}", "-r", str(fps),
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "16", "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-b:a", "160k", "-ar", "44100", str(out),
        ])
        return out
    if kind == "image":
        inputs = ["-loop", "1", "-framerate", str(fps), "-t", f"{duration + 0.5:.3f}",
                  "-i", str(source)]
        pre = f"[0:v]fps={fps}[src];"
        prescale = 1.25
    else:
        meta = probe(settings.ffprobe_path, source)
        start = max(0.0, min(float(scene.get("start") or 0), max(0.0, meta["duration_sec"] - 0.5)))
        inputs = ["-ss", f"{start:.3f}", "-t", f"{duration + 0.5:.3f}", "-i", str(source)]
        pre = (f"[0:v]fps={fps},tpad=stop_mode=clone:stop_duration={duration + 0.5:.3f}"
               f"[src];")
        prescale = 1.0
        keep_audio = settings.keep_source_audio and meta["has_audio"]

    graph = (
        pre + _fill_916("src", w, h, prescale) + ","
        + _motion_filter(motion, frames, w, h, fps)
        + f",trim=end_frame={frames},setpts=PTS-STARTPTS,setsar=1,format=yuv420p[v]"
    )
    if keep_audio:
        graph += (f";[0:a]aresample=44100,aformat=channel_layouts=stereo,"
                  f"volume={settings.source_audio_volume},apad,"
                  f"atrim=duration={duration:.3f},asetpts=PTS-STARTPTS[a]")
        audio_map = ["-map", "[a]"]
    else:
        inputs += ["-f", "lavfi", "-t", f"{duration:.3f}", "-i",
                   "anullsrc=channel_layout=stereo:sample_rate=44100"]
        audio_map = ["-map", "1:a"]
    run_ffmpeg(settings.ffmpeg_path, [
        *inputs, "-filter_complex", graph, "-map", "[v]", *audio_map,
        "-t", f"{duration:.3f}", "-r", str(fps),
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "16", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "160k", "-ar", "44100", str(out),
    ])
    return out


def chain_graph(durations: list[float], t: float, seed: int = 0
                ) -> tuple[list[str], str, str, float, list[float]]:
    """xfade/acrossfade chain over inputs 0..n-1.

    Returns filter parts, final video/audio labels, total length and the start
    time of every scene in the final timeline.
    """
    if len(durations) == 1:
        return [], "0:v", "0:a", durations[0], [0.0]
    parts, starts = [], [0.0]
    length = durations[0]
    vprev, aprev = "0:v", "0:a"
    for i in range(1, len(durations)):
        offset = length - t
        trans = TRANSITIONS[(i + seed) % len(TRANSITIONS)]
        vout, aout = f"xv{i}", f"xa{i}"
        parts.append(f"[{vprev}][{i}:v]xfade=transition={trans}:duration={t}:offset={offset:.3f}[{vout}]")
        parts.append(f"[{aprev}][{i}:a]acrossfade=d={t}:c1=tri:c2=tri[{aout}]")
        starts.append(offset)
        length = length + durations[i] - t
        vprev, aprev = vout, aout
    return parts, vprev, aprev, length, starts


def render_timeline(settings, timeline: dict, work_dir: Path, output: Path,
                    seed_text: str = "") -> RenderResult:
    work_dir = Path(work_dir).resolve()
    if work_dir.exists():
        shutil.rmtree(work_dir, ignore_errors=True)
    work_dir.mkdir(parents=True, exist_ok=True)
    scenes = [s for s in timeline.get("scenes") or [] if s.get("source")]
    if not scenes:
        raise RenderError("Timeline has no scenes with a visual source")
    seed = sum(map(ord, seed_text)) % 7

    # 1) voice per scene decides each scene's length
    speeches: list[Speech] = []
    durations: list[float] = []
    for i, scene in enumerate(scenes):
        text = scene.get("voiceover") or ""
        if settings.tts_enabled and text:
            speech = synthesize(text, work_dir / f"voice_{i:02d}.mp3",
                                settings.tts_voice, settings.tts_rate, settings.ffprobe_path)
        else:
            from .tts import estimate_duration, evenly_timed_words
            est = estimate_duration(text) if text else 0.0
            speech = Speech(None, est, evenly_timed_words(text, 0.0, est))
        speeches.append(speech)
        wanted = float(scene.get("duration") or 2.5)
        needed = speech.duration + 0.35 + (settings.transition_seconds if i else 0.15)
        durations.append(round(min(MAX_SCENE + 4, max(MIN_SCENE, wanted, needed)), 3))

    # 2) normalised clips
    clips = []
    for i, scene in enumerate(scenes):
        clips.append(render_scene(settings, scene, durations[i], work_dir / f"scene_{i:02d}.mp4", i))

    # 3) transitions (built into the final single-pass graph)
    chain_parts, vchain, achain, total, starts = chain_graph(
        durations, settings.transition_seconds, seed)

    # 4) subtitles
    lead = settings.transition_seconds / 2
    voice_offsets = [starts[i] + (lead if i else 0.1) for i in range(len(scenes))]
    captions = [(voice_offsets[i], sp.words) for i, sp in enumerate(speeches)]
    labels = []
    for i, scene in enumerate(scenes):
        end = starts[i + 1] if i + 1 < len(scenes) else total
        start = max(starts[i], 2.8) if i == 0 and timeline.get("hook") else starts[i]
        labels.append((start, end, scene.get("text") or ""))
    cta = timeline.get("cta") or settings.cta_text
    cta_start = max(total - 2.6, total * 0.75)
    hook_end = min(2.8, durations[0] if len(durations) == 1 else starts[1] + 0.3)
    subs = build_ass(work_dir / "subs.ass", settings.render_width, settings.render_height,
                     settings.font_name, captions, timeline.get("hook") or "", hook_end,
                     labels, cta, cta_start, total)

    # 5) audio mix + subtitle burn
    inputs: list[str] = []
    for clip in clips:
        inputs += ["-i", clip.name]
    filters = list(chain_parts)
    voice_labels = []
    idx = len(clips)
    for i, sp in enumerate(speeches):
        if sp.audio_path:
            inputs += ["-i", sp.audio_path.name]
            ms = int(voice_offsets[i] * 1000)
            filters.append(f"[{idx}:a]aresample=44100,aformat=channel_layouts=stereo,"
                           f"adelay={ms}|{ms}[vo{i}]")
            voice_labels.append(f"[vo{i}]")
            idx += 1
    music = pick_track(settings.music, timeline.get("music_mood", ""), seed_text)
    has_voice = bool(voice_labels)
    if has_voice:
        filters.append("".join(voice_labels)
                       + f"amix=inputs={len(voice_labels)}:normalize=0:dropout_transition=0,"
                       f"apad,atrim=duration={total:.3f}[voice]")
    if music:
        inputs += ["-stream_loop", "-1", "-i", str(Path(music).resolve())]
        filters.append(f"[{idx}:a]aresample=44100,aformat=channel_layouts=stereo,"
                       f"atrim=duration={total:.3f},volume={settings.music_volume},"
                       f"afade=t=in:d=0.4,afade=t=out:st={max(0, total - 1.2):.3f}:d=1.2[mus]")
        if has_voice:
            filters.append("[voice]asplit=2[voice_mix][voice_sc]")
            filters.append("[mus][voice_sc]sidechaincompress=threshold=0.04:ratio=8:"
                           "attack=15:release=350[duck]")
            mix = [f"[{achain}]", "[voice_mix]", "[duck]"]
        else:
            mix = [f"[{achain}]", "[mus]"]
    else:
        mix = [f"[{achain}]", "[voice]"] if has_voice else [f"[{achain}]"]
    # loudnorm turns pure digital silence into NaN, so only normalise real audio.
    has_sound = has_voice or bool(music) or settings.keep_source_audio
    norm = "loudnorm=I=-14:TP=-1.5:LRA=11," if has_sound else ""
    filters.append("".join(mix) + f"amix=inputs={len(mix)}:normalize=0:dropout_transition=0,"
                   f"{norm}aresample=44100[aout]")
    filters.append(f"[{vchain}]ass=subs.ass[vout]")
    output = Path(output).resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    tmp_out = work_dir / "final.mp4"
    try:
        run_ffmpeg(settings.ffmpeg_path, [
            *inputs, "-filter_complex", ";".join(filters),
            "-map", "[vout]", "-map", "[aout]", "-t", f"{total:.3f}",
            "-c:v", "libx264", "-preset", "fast", "-crf", "20", "-pix_fmt", "yuv420p",
            "-profile:v", "high", "-movflags", "+faststart",
            "-c:a", "aac", "-b:a", "192k", "-ar", "44100", "final.mp4",
        ], cwd=work_dir)
    except FFmpegError as exc:
        raise RenderError(f"Final mix failed: {exc}") from exc
    shutil.move(str(tmp_out), output)
    scene_report = [
        {**scene, "rendered_duration": durations[i], "start_at": round(starts[i], 3),
         "voice": bool(speeches[i].audio_path)}
        for i, scene in enumerate(scenes)
    ]
    shutil.copyfile(subs, output.with_suffix(".ass"))
    return RenderResult(output, round(total, 3), scene_report,
                        str(music) if music else None, has_voice, output.with_suffix(".ass"))


def extract_cover(settings, video: Path, at: float, out: Path) -> Path:
    run_ffmpeg(settings.ffmpeg_path, ["-ss", f"{max(0.0, at):.3f}", "-i", str(video),
                                      "-frames:v", "1", "-q:v", "3", str(out)])
    return out
