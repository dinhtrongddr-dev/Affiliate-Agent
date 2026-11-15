"""Timeline -> finished 9:16 short video.

A timeline is a plain dict (stored as script_json) so it can be edited in the UI:

    {
      "hook": "Câu hook 0-3 giây",
      "cta": "Link sản phẩm ở giỏ hàng",
      "music_mood": "upbeat",
      "content_mode": "viral|value|affiliate",
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
import math
import shutil
from dataclasses import dataclass
from pathlib import Path

from .encoder import video_args
from .ffmpeg import FFmpegError, probe, run_ffmpeg
from .music import pick_track
from .subtitles import build_ass
from .tts import Speech

log = logging.getLogger("affiliate_agent.render")

TRANSITIONS = ["smoothleft", "fade", "slideup", "circleopen", "smoothup",
               "wipeleft", "zoomin", "radial", "slideleft", "dissolve"]
MOTIONS = ["zoom_in", "pan_right", "zoom_out", "pan_left"]
MIN_SCENE, MAX_SCENE = 1.2, 8.0
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
    music_metadata: dict | None = None
    variants: list | None = None
    voice_segments: list | None = None
    seams: list | None = None


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


def best_cut(settings, source: Path, length: float, duration: float,
             avoid: list[tuple[float, float]] | None = None) -> float:
    """Start time of the most lively `duration`-second stretch of an AI clip (the take is
    ~10 s; the edit keeps 2-6 s). Skips the first 0.3 s and the last 0.5 s where generated
    clips often hold still. Returns 0.0 when the clip cannot be read."""
    try:
        import subprocess

        import numpy as np
        fps = 4
        raw = subprocess.run([settings.ffmpeg_path, "-hide_banner", "-loglevel", "error", "-i", str(source),
                              "-vf", f"fps={fps},scale=32:56,format=gray", "-f", "rawvideo", "-"],
                             capture_output=True, timeout=120).stdout
        frames = np.frombuffer(raw, dtype=np.uint8)
        frames = frames[: len(frames) // (32 * 56) * (32 * 56)].reshape(-1, 32 * 56).astype(np.float32)
    except Exception:  # noqa: BLE001
        return 0.0
    if len(frames) < 4:
        return 0.0
    motion = np.abs(np.diff(frames, axis=0)).mean(axis=1)          # motion between frames
    win = max(1, int(round(duration * fps)))
    first, last = int(0.3 * fps), len(motion) - int(0.5 * fps) - win
    if last <= first:
        return 0.0
    avoid = avoid or []
    sums = []
    for k in range(first, last + 1):
        s0, s1 = k / fps, (k + win) / fps
        overlap = any(s0 < b and a < s1 for a, b in avoid)   # a second cut of the same clip
        sums.append(-1.0 if overlap else float(motion[k:k + win].sum()))
    if max(sums) < 0:
        return round(first / fps, 2)
    best = first + int(np.argmax(sums))
    return round(best / fps, 2)


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
            *video_args(settings, "intermediate"),
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
        speed = float(scene.get("speed") or 1.0)
        inputs = ["-ss", f"{start:.3f}", "-t", f"{(duration + 0.5) * speed:.3f}", "-i", str(source)]
        retime = f"setpts=PTS/{speed:.4f}," if abs(speed - 1.0) > 0.01 else ""
        pre = (f"[0:v]{retime}fps={fps},tpad=stop_mode=clone:stop_duration={duration + 0.5:.3f}"
               f"[src];")
        prescale = 1.0
        keep_audio = (settings.keep_source_audio or scene.get("native_audio")) and meta["has_audio"]
        if meta.get("width") and meta.get("height") and \
                abs(meta["width"] / meta["height"] - w / h) < 0.02:
            prescale = 0.0   # already 9:16: plain scale, no blurred background pass

    fill = (f"[src]scale={w}:{h}:flags=bicubic,setsar=1,format=yuv420p" if prescale == 0.0
            else _fill_916("src", w, h, prescale))
    grade = scene.get("grade") or {}
    if grade:   # even out brightness/saturation drift inside a sequence (S3.6)
        fill += (f",eq=brightness={float(grade.get('brightness', 0)):.3f}"
                 f":saturation={float(grade.get('saturation', 1)):.3f}")
    graph = (
        pre + fill + ","
        + _motion_filter(motion, frames, w, h, fps)
        + f",trim=end_frame={frames},setpts=PTS-STARTPTS,setsar=1,format=yuv420p[v]"
    )
    if keep_audio:
        graph += (f";[0:a]aresample=44100,aformat=channel_layouts=stereo,"
                  f"volume={1.0 if scene.get('native_audio') else settings.source_audio_volume},apad,"
                  f"atrim=duration={duration:.3f},asetpts=PTS-STARTPTS[a]")
        audio_map = ["-map", "[a]"]
    else:
        inputs += ["-f", "lavfi", "-t", f"{duration:.3f}", "-i",
                   "anullsrc=channel_layout=stereo:sample_rate=44100"]
        audio_map = ["-map", "1:a"]
    run_ffmpeg(settings.ffmpeg_path, [
        *inputs, "-filter_complex", graph, "-map", "[v]", *audio_map,
        "-t", f"{duration:.3f}", "-r", str(fps),
        *video_args(settings, "intermediate"),
        "-c:a", "aac", "-b:a", "160k", "-ar", "44100", str(out),
    ])
    return out


def chain_graph(durations: list[float], t, seed: int = 0, only: str | None = None
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
        ti = t[i] if isinstance(t, (list, tuple)) else t   # one length per seam (S3.5)
        ti = max(0.04, ti)
        offset = length - ti
        trans = only or TRANSITIONS[(i + seed) % len(TRANSITIONS)]
        vout, aout = f"xv{i}", f"xa{i}"
        parts.append(f"[{vprev}][{i}:v]xfade=transition={trans}:duration={ti}:offset={offset:.3f}[{vout}]")
        parts.append(f"[{aprev}][{i}:a]acrossfade=d={ti}:c1=tri:c2=tri[{aout}]")
        starts.append(offset)
        length = length + durations[i] - ti
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

    cast = timeline.get("voice_cast") or {}
    style = str(timeline.get("style_profile") or "")
    hard_cuts = style in {"ugc_phone", "sitcom_bright", "pro_social", "documentary", "chibi_3d"}
    trans = 0.08 if hard_cuts else settings.transition_seconds

    voice_led = bool(getattr(settings, "voice_led", True) and timeline.get("creation_branch"))
    if timeline.get("voice_source") == "native":   # dialogue series: characters keep their Muse voices
        scenes = [{**s, "native_voice": True} if s.get("talking") and s.get("generated") else s
                  for s in scenes]
    edit = None
    if voice_led:
        edit = _voice_led_edit(settings, scenes, cast, work_dir)
        scenes, voices, durations = edit["scenes"], edit["voices"], edit["durations"]
    else:
        voices, durations = _scene_led_durations(settings, scenes, cast, work_dir, trans)
    speeches = [Speech(None, v.end, v.words) for v in voices]

    # 2) Normalised scene clips
    clips = []
    for i, scene in enumerate(scenes):
        if scene.get("generated") and (hard_cuts or voice_led):
            scene = {**scene, "motion": "none"}   # no fake zoom on real-looking footage
        clips.append(render_scene(settings, scene, durations[i], work_dir / f"scene_{i:02d}.mp4", i))

    # 3) Transitions: voice-led edits cut inside a sequence and dissolve between sequences
    if edit:
        chain_parts, vchain, achain, total, starts = chain_graph(durations, edit["seams"], seed, "fade")
        voice_offsets = edit["voice_offsets"]
    else:
        chain_parts, vchain, achain, total, starts = chain_graph(durations, trans, seed,
                                                                 "fade" if hard_cuts else None)
        voice_offsets = [starts[i] for i in range(len(scenes))]

    # 4) Subtitles & Voice offsets
    captions = [(voice_offsets[i], sp.words) for i, sp in enumerate(speeches)]
    labels = []
    if not timeline.get("creation_branch"):   # storyboards keep a single text layer
        for i, scene in enumerate(scenes):
            end = starts[i + 1] if i + 1 < len(scenes) else total
            start = max(starts[i], 2.8) if i == 0 and timeline.get("hook") else starts[i]
            labels.append((start, end, scene.get("text") or ""))

    # Track exact voice end timestamp to never cut off speech or ending dialogue
    last_voice_end = 0.0
    for i, sp in enumerate(speeches):
        if sp.duration > 0:
            last_voice_end = max(last_voice_end, voice_offsets[i] + sp.duration)

    # CTA resolution: never force sales CTA on emotional viral stories
    raw_cta = timeline.get("cta")
    content_mode = str(timeline.get("content_mode") or "").lower()
    if raw_cta is not None:
        cta = str(raw_cta).strip()
    elif content_mode == "viral":
        cta = ""
    else:
        cta = (settings.cta_text or "").strip()

    if cta:
        # CTA only starts AFTER the voice finishes completely, with a buffer
        cta_start = max(last_voice_end + 0.2, total - 2.2)
        if cta_start >= total - 0.3:
            # If dialogue extends to the end of the video, omit CTA popup to keep ending intact
            cta = ""
            cta_start = total
    else:
        cta = ""
        cta_start = total

    single_layer = bool(timeline.get("creation_branch"))
    hook_end = min(2.0 if single_layer else 2.8,
                   durations[0] if len(durations) == 1 else starts[1] + 0.3)
    if timeline.get("no_hook"):
        timeline = {**timeline, "hook": ""}
    subs = build_ass(work_dir / "subs.ass", settings.render_width, settings.render_height,
                     settings.font_name, captions, timeline.get("hook") or "", hook_end,
                     labels, cta, cta_start, total, single_layer=single_layer)

    # 5) Audio mix + Subtitle burn
    inputs: list[str] = []
    for clip in clips:
        inputs += ["-i", clip.name]
    filters = list(chain_parts)
    voice_labels = []
    idx = len(clips)
    for i, voice in enumerate(voices):
        for k, part in enumerate(voice.parts):
            if not part.audio:
                continue
            inputs += ["-i", str(Path(part.audio).resolve())]
            ms = int((voice_offsets[i] + part.offset) * 1000)
            filters.append(f"[{idx}:a]aresample=44100,aformat=channel_layouts=stereo,"
                           f"adelay={ms}|{ms}[vo{i}_{k}]")
            voice_labels.append(f"[vo{i}_{k}]")
            idx += 1
    music = None if timeline.get("no_music") else pick_track(   # long-video parts: music added once
        settings.music,
        timeline.get("music_mood", ""),
        seed=seed_text,
        timeline=timeline,
        settings=settings,
        job_id=seed_text,
    )
    music_meta = music.to_dict() if hasattr(music, "to_dict") else None
    has_voice = bool(voice_labels)
    native = any(sc.get("native_audio") for sc in scenes)   # character speech lives in the scene audio
    if has_voice:
        filters.append("".join(voice_labels)
                       + f"amix=inputs={len(voice_labels)}:normalize=0:dropout_transition=0,"
                       f"apad,atrim=duration={total:.3f}[voice]")

    if music:
        inputs += ["-stream_loop", "-1", "-i", str(Path(music).resolve())]
        filters.append(f"[{idx}:a]aresample=44100,aformat=channel_layouts=stereo,"
                       f"atrim=duration={total:.3f},volume={settings.music_volume},"
                       f"afade=t=in:d=0.4,afade=t=out:st={max(0, total - 1.2):.3f}:d=1.2[mus]")
        keys, scene_audio = [], f"[{achain}]"
        if native:
            filters.append(f"[{achain}]asplit=2[src_mix][src_sc]")
            keys.append("[src_sc]")
            scene_audio = "[src_mix]"
        if has_voice:
            filters.append("[voice]asplit=2[voice_mix][voice_sc]")
            keys.append("[voice_sc]")
        if keys:
            if len(keys) == 2:
                filters.append("[voice_sc][src_sc]amix=inputs=2:normalize=0[duck_key]")
                key = "[duck_key]"
            else:
                key = keys[0]
            # Smooth ducking: natural breathing without sudden drops
            filters.append(f"[mus]{key}sidechaincompress=threshold=0.06:ratio=4:"
                           "attack=20:release=400[duck]")
            mix = [scene_audio] + (["[voice_mix]"] if has_voice else []) + ["[duck]"]
        else:
            mix = [f"[{achain}]", "[mus]"]
    else:
        # Subtle acoustic room tone prevents digital dead silence when music track is absent
        filters.append(f"anoisesrc=d={total:.3f}:color=pink:amplitude=0.0008,lowpass=f=600,"
                       f"aformat=channel_layouts=stereo[amb]")
        if has_voice:
            mix = [f"[{achain}]", "[voice]", "[amb]"]
        else:
            mix = [f"[{achain}]", "[amb]"]

    # Continuous audio: normalize to standard broadcast loudness (-14 LUFS)
    has_sound = has_voice or bool(music) or settings.keep_source_audio or True
    norm = "loudnorm=I=-14:TP=-1.5:LRA=11," if has_sound else ""
    filters.append("".join(mix) + f"amix=inputs={len(mix)}:normalize=0:dropout_transition=0,"
                   f"{norm}aresample=44100[aout]")
    alt_hooks = [] if (timeline.get("fast_mode") or timeline.get("no_variants")) else [h for h in (timeline.get("hook_variants") or [])
                 if h and h != timeline.get("hook")][: max(0, int(getattr(settings, "variants", 1)) - 1)]
    if alt_hooks:   # one decode, two encodes: the hook-free picture is reused for A/B hooks
        filters.append(f"[{vchain}]split=2[vsub][vclean]")
        filters.append("[vsub]ass=subs.ass[vout]")
    else:
        filters.append(f"[{vchain}]ass=subs.ass[vout]")

    fps = settings.render_fps
    cut = math.ceil((hook_end + 0.4) * fps) / fps   # hook variants differ only before this
    if alt_hooks and cut > total - 1.0:
        cut = None
    output = Path(output).resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    tmp_out = work_dir / "final.mp4"
    try:
        run_ffmpeg(settings.ffmpeg_path, [
            *inputs, "-filter_complex", ";".join(filters),
            "-map", "[vout]", "-map", "[aout]", "-t", f"{total:.3f}",
            *video_args(settings, "final", keyframe_at=cut if alt_hooks else None),
            "-movflags", "+faststart",
            "-c:a", "aac", "-b:a", "192k", "-ar", "44100", "final.mp4",
            *(["-map", "[vclean]",
               "-t", f"{(cut or total):.3f}", *video_args(settings, "intermediate"), "-an",
               "clean.mp4"] if alt_hooks else []),
        ], cwd=work_dir)
    except FFmpegError as exc:
        raise RenderError(f"Final mix failed: {exc}") from exc
    finally:
        # Zero-waste disk hygiene: purge temporary downloaded music & audio cache immediately
        if hasattr(music, "cleanup"):
            music.cleanup()
    shutil.move(str(tmp_out), output)
    variants = []
    for k, hook in enumerate(alt_hooks, start=2):
        try:   # same body, only the first seconds' hook text differs
            build_ass(work_dir / f"subs_{k}.ass", settings.render_width, settings.render_height,
                      settings.font_name, captions, hook, hook_end, labels, cta, cta_start, total,
                      single_layer=single_layer)
            target = output.with_name(f"{output.stem}_hook{k}{output.suffix}")
            hook_variant(settings, work_dir, output, f"subs_{k}.ass", cut, total, target)
            variants.append({"index": k, "hook": hook, "path": str(target)})
        except FFmpegError as exc:
            log.warning("hook variant %s failed: %s", k, exc)
    voice_segments = []
    for i, voice in enumerate(voices):
        for part in voice.parts:
            if part.duration > 0:
                voice_segments.append((round(voice_offsets[i] + part.offset, 3),
                                       round(voice_offsets[i] + part.offset + part.duration, 3)))
    seams_report = []
    for i in range(1, len(scenes)):
        seams_report.append({"at": round(starts[i] + (edit["seams"][i] if edit else 0) / 2, 3),
                             "same_sequence": bool(scenes[i].get("group")) and
                             scenes[i].get("group") == scenes[i - 1].get("group"),
                             "chained": bool(scenes[i].get("chain_in"))})
    scene_report = [
        {**scene, "rendered_duration": durations[i], "start_at": round(starts[i], 3),
         "voice": any(p.audio for p in voices[i].parts), "revoice": voices[i].revoice,
         "voice_end": round(voices[i].end, 3)}
        for i, scene in enumerate(scenes)
    ]
    shutil.copyfile(subs, output.with_suffix(".ass"))
    return RenderResult(output, round(total, 3), scene_report,
                        str(music) if music else None, has_voice or native, output.with_suffix(".ass"),
                        music_metadata=music_meta, variants=variants,
                        voice_segments=voice_segments, seams=seams_report)


def hook_variant(settings, work_dir: Path, main: Path, ass: str, cut: float | None,
                 total: float, target: Path) -> Path:
    """Same video with another hook: only the first `cut` seconds are encoded again (from the
    subtitle-free picture); the rest is copied from the main video, which has a keyframe at
    `cut`. The audio track is the main video's, untouched."""
    ff = settings.ffmpeg_path
    if cut is None:   # very short video: encode the whole head
        cut = total
    run_ffmpeg(ff, ["-i", "clean.mp4", "-t", f"{cut:.3f}", "-vf", f"ass={ass}",
                    *video_args(settings, "final"), "-an", f"head_{target.stem}.mp4"], cwd=work_dir)
    parts = [f"head_{target.stem}.mp4"]
    if cut < total - 0.05:
        run_ffmpeg(ff, ["-ss", f"{cut:.3f}", "-i", str(main), "-an", "-c:v", "copy",
                        f"tail_{target.stem}.mp4"], cwd=work_dir)
        parts.append(f"tail_{target.stem}.mp4")
    listing = work_dir / f"parts_{target.stem}.txt"
    listing.write_text("".join(f"file '{p}'\n" for p in parts), encoding="utf-8")
    run_ffmpeg(ff, ["-f", "concat", "-safe", "0", "-i", listing.name, "-i", str(main),
                    "-map", "0:v", "-map", "1:a", "-c", "copy", "-movflags", "+faststart",
                    "-t", f"{total:.3f}", str(target)], cwd=work_dir)
    return target


def _scene_led_durations(settings, scenes, cast, work_dir, trans):
    """Legacy edit (base videos): each scene lasts as long as its picture wants, voice inside."""
    from .voice_track import scene_voice
    voices, durations = [], []
    used_windows: dict[str, list[tuple[float, float]]] = {}
    for i, scene in enumerate(scenes):
        available = None
        if scene.get("kind", "video") == "video" and Path(scene["source"]).is_file():
            try:
                src_len = probe(settings.ffprobe_path, Path(scene["source"]))["duration_sec"]
                available = max(0.5, src_len - float(scene.get("start") or 0))
                if (scene.get("generated") and not float(scene.get("start") or 0)
                        and src_len > float(scene.get("duration") or 4) + 1):
                    shift = best_cut(settings, Path(scene["source"]), src_len,
                                     float(scene.get("duration") or 4), used_windows.get(scene["source"]))
                    if shift:
                        scene = scenes[i] = {**scene, "start": shift}
                        available = max(0.5, src_len - shift)
            except Exception:  # noqa: BLE001
                available = None
        voice = scene_voice(settings, scene, cast, work_dir, i, None)
        voices.append(voice)
        wanted = float(scene.get("duration") or 2.5)
        needed = voice.end + 0.3 + (trans if i else 0.15)
        duration = max(MIN_SCENE, wanted, needed)
        if available is not None:
            duration = min(duration, available + 0.3) if needed <= available + 0.3 else duration
        durations.append(round(min(MAX_SCENE + 4.5, duration), 3))
        if scene.get("kind", "video") == "video":
            start = float(scene.get("start") or 0)
            used_windows.setdefault(scene["source"], []).append((start, start + durations[-1]))
    return voices, durations


def _voice_led_edit(settings, scenes, cast, work_dir) -> dict:
    """S1 + S3: narration laid end to end, pictures cut to it, sequence continuity kept."""
    from . import edit_plan
    from .voice_track import SceneVoice, scene_voice, speech_windows
    ffmpeg = getattr(settings, "ffmpeg_path", "ffmpeg")
    scenes = [dict(sc) for sc in scenes]
    lengths: dict[str, float] = {}

    def src_len(path: str) -> float:
        if path not in lengths:
            try:
                lengths[path] = float(probe(settings.ffprobe_path, Path(path))["duration_sec"])
            except Exception:  # noqa: BLE001
                lengths[path] = 0.0
        return lengths[path]

    groups = edit_plan.takes(scenes)
    member_of = {i: g for g in groups for i in g}
    voices = []
    for i, scene in enumerate(scenes):
        clip_window = None
        alone = len(member_of.get(i, [i])) == 1 and not scene.get("chain_in") and not scene.get("chain_out")
        if scene.get("generated") and scene.get("talking") and alone and src_len(scene["source"]):
            found = speech_windows(ffmpeg, Path(scene["source"]), 0.0, src_len(scene["source"]))
            if found and found[0][0] > 0.4:
                scene["start"] = round(found[0][0] - edit_plan.LEAD, 3)
            scene["fixed_start"] = True
            start = float(scene.get("start") or 0)
            clip_window = (Path(scene["source"]), start, max(0.5, src_len(scene["source"]) - start))
        voice = scene_voice(settings, scene, cast, work_dir, i, clip_window)
        if voice.native:
            scene["native_audio"] = True   # the clip's own speech is the sound of this scene
        first = min((p.offset for p in voice.parts), default=0.0)
        for p in voice.parts:   # relative to the first word: the layout decides where it starts
            p.offset = round(p.offset - first, 3)
        voices.append(voice if voice.parts else SceneVoice())
    plan = edit_plan.layout(scenes, [v.end for v in voices])
    durations = plan["durations"]
    # windows: which part of each take is shown, at what speed (S3.4)
    for g in groups:
        first, last = scenes[g[0]], scenes[g[-1]]
        if any(scenes[i].get("fixed_start") for i in g):
            continue
        length = src_len(first["source"])
        if not length:
            continue
        need = sum(durations[i] for i in g)
        best = None
        if not first.get("chain_in") and not last.get("chain_out"):
            best = best_cut(settings, Path(first["source"]), length, min(need, length - 0.5))
        start, speed = edit_plan.window(length, need, bool(first.get("chain_in")),
                                        bool(last.get("chain_out")), best)
        for i in g:
            scenes[i]["start"], scenes[i]["speed"] = round(start, 3), speed
            start += durations[i] * speed
    # colour: each take toward the first take of its sequence (S3.6)
    refs: dict[str, tuple[float, float]] = {}
    for g in groups:
        sc = scenes[g[0]]
        stats = edit_plan.frame_stats(ffmpeg, Path(sc["source"]), float(sc.get("start") or 0) + 0.5)
        if not stats:
            continue
        key = sc.get("group") or sc["source"]
        if key not in refs:
            refs[key] = stats
            continue
        grade = edit_plan.grade_to(refs[key], stats)
        for i in g:
            scenes[i]["grade"] = grade
    return {"scenes": scenes, "voices": voices, "durations": durations, "seams": plan["seams"],
            "voice_offsets": plan["voice_starts"], "cuts": plan["cuts"]}


def extract_cover(settings, video: Path, at: float, out: Path) -> Path:
    run_ffmpeg(settings.ffmpeg_path, ["-ss", f"{max(0.0, at):.3f}", "-i", str(video),
                                      "-frames:v", "1", "-q:v", "3", str(out)])
    return out
