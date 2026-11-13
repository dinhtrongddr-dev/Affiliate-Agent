"""Integration tests for Continuity and Frame-Chaining pipeline."""
from types import SimpleNamespace

from agent_core.editor.ffmpeg import extract_continuity_frame, optimize_reference_image, probe
from agent_core.videogen.engine import (
    ensure_schema,
    plan_job,
    _submit,
    get_task,
    list_tasks,
    _insert,
)
from agent_core.videogen.providers import VideoProvider, GenStatus


class MockProvider(VideoProvider):
    name = "mock"
    capabilities = frozenset({"connection", "image", "video", "references"})

    def __init__(self):
        self.submitted = []

    def check_connection(self):
        return {"ok": True}

    def submit_video(self, prompt, refs, duration, idem_key):
        self.submitted.append({"prompt": prompt, "refs": refs, "duration": duration, "idem": idem_key})
        return f"mock_vid_{len(self.submitted)}"

    def submit_image(self, prompt, refs, idem_key):
        self.submitted.append({"prompt": prompt, "refs": refs, "kind": "image"})
        return "mock_img_0"

    def status(self, kind, remote_id):
        return GenStatus("succeeded", "http://fake.test/clip.mp4", 100, None, {})

    def download(self, url, dest):
        dest.parent.mkdir(parents=True, exist_ok=True)
        # Create a tiny 1-second test MP4 via ffmpeg
        import subprocess
        subprocess.run([
            "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
            "-f", "lavfi", "-i", "testsrc=duration=1:size=320x240:rate=10",
            "-c:v", "libx264", "-pix_fmt", "yuv420p", str(dest)
        ], check=True)


def test_extract_continuity_frame_and_optimize(tmp_path):
    video = tmp_path / "sample.mp4"
    import subprocess
    subprocess.run([
        "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
        "-f", "lavfi", "-i", "testsrc=duration=2:size=320x240:rate=10",
        "-c:v", "libx264", "-pix_fmt", "yuv420p", str(video)
    ], check=True)

    frame = tmp_path / "frame.jpg"
    extract_continuity_frame(video, frame, offset_seconds=0.3)
    assert frame.is_file()
    assert frame.stat().st_size > 500

    optimized = tmp_path / "opt.jpg"
    optimize_reference_image(frame, optimized, target_width=360, target_height=640)
    assert optimized.is_file()
    meta = probe("ffprobe", optimized)
    assert meta["width"] == 360
    assert meta["height"] == 640


def test_plan_job_creates_continuity_chain(tmp_path):
    db_path = tmp_path / "test.sqlite3"
    ensure_schema(db_path)

    settings = SimpleNamespace(
        database_path=db_path,
        work_dir=tmp_path / "work",
        videogen_max_clips=5,
        videogen_clip_seconds=6,
        videogen_character=False,
        videogen_continuity_chain=True,
        videogen_daily_limit=100,
        videogen_task_timeout_minutes=30,
        ffmpeg_path="ffmpeg",
        ffprobe_path="ffprobe",
    )

    plan = {
        "scenes": [
            {"visual": "generate", "gen_prompt": "First scene showing a girl walking"},
            {"visual": "generate", "gen_prompt": "Second scene showing her reaching for a cup"},
        ]
    }
    provider = MockProvider()

    count = plan_job(settings, "job_chain", plan, None, provider)
    assert count == 2

    tasks = sorted(list_tasks(db_path, "job_chain"), key=lambda t: t["scene_index"])
    assert len(tasks) == 2
    assert tasks[0]["depends_on"] is None
    # Scene 1 must depend on Scene 0 to form a continuity chain!
    assert tasks[1]["depends_on"] == tasks[0]["id"]


def test_submit_injects_continuity_frame_and_prompt(tmp_path):
    db_path = tmp_path / "test.sqlite3"
    ensure_schema(db_path)
    work_dir = tmp_path / "work"
    gen_dir = work_dir / "job_cont" / "generated"
    gen_dir.mkdir(parents=True, exist_ok=True)

    settings = SimpleNamespace(
        database_path=db_path,
        work_dir=work_dir,
        videogen_max_clips=5,
        videogen_clip_seconds=6,
        videogen_character=False,
        videogen_continuity_chain=True,
        videogen_daily_limit=100,
        videogen_task_timeout_minutes=30,
        ffmpeg_path="ffmpeg",
        ffprobe_path="ffprobe",
    )

    provider = MockProvider()

    # Tạo task 0 đã hoàn thành
    t0_id = _insert(db_path, job_id="job_cont", scene_index=0, kind="video",
                    provider=provider.name, prompt="Scene 0", refs=[])
    dummy_video = gen_dir / f"{t0_id}.mp4"
    dummy_video.write_bytes(b"dummy")
    cont_frame = gen_dir / f"continuity_{t0_id}.jpg"
    cont_frame.write_bytes(b"dummy_frame")

    from agent_core.videogen.engine import _update
    _update(db_path, t0_id, status="DOWNLOADED", result_path=str(dummy_video))

    # Tạo task 1 phụ thuộc vào task 0
    t1_id = _insert(db_path, job_id="job_cont", scene_index=1, kind="video",
                    provider=provider.name, prompt="Scene 1 continuing", refs=[],
                    depends_on=t0_id)
    t1 = get_task(db_path, t1_id)

    _submit(settings, provider, t1)

    assert len(provider.submitted) == 1
    call = provider.submitted[0]
    # Attachment 1 must be the continuity frame!
    assert len(call["refs"]) == 1
    assert call["refs"][0] == cont_frame
    # Prompt must contain continuity instruction!
    assert "Attachment 1 is the seamless starting frame" in call["prompt"]
