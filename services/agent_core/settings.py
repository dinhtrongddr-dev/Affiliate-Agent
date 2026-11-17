from pathlib import Path

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=PROJECT_ROOT / ".env",
        env_prefix="AA_",
        extra="ignore",
        case_sensitive=False,
    )

    project_root: Path = PROJECT_ROOT
    media_root: Path = Path(r"E:\AffiliateAgentMedia")
    inbox_dir: Path | None = None
    assets_dir: Path | None = None
    output_shopee_dir: Path | None = None
    output_facebook_dir: Path | None = None
    quarantine_dir: Path | None = None
    database_path: Path = PROJECT_ROOT / "state" / "affiliate-agent.sqlite3"

    ffmpeg_path: str = "ffmpeg"
    ffprobe_path: str = "ffprobe"
    timezone: str = "Asia/Ho_Chi_Minh"

    # AI gateway (OpenAI-compatible or Anthropic Messages)
    ai_protocol: str = "openai"
    ai_base_url: str = "http://127.0.0.1:13000/v1"
    ai_api_key: SecretStr = SecretStr("")
    # One model for every text/vision task. Measured Nov 2026 on the app's real tasks (storyboard,
    # dialogue episode, visual QA; 3 rounds, blind judged): gpt-6-thinking at the default effort
    # scored highest (6.4/10) and is ~3x faster than the old gpt-6-astra-wm + "extended"
    # (storyboard ~70 s vs ~285 s). "extended" made every model 3-4x slower without a quality gain.
    ai_model: str = "gpt-6-thinking"
    ai_thinking_effort: str = ""
    ai_timeout_seconds: int = 180

    # Shopee
    shopee_skill_home: Path = PROJECT_ROOT / "config" / "shopee-skill"
    shopee_affiliate_enabled: bool = False
    shopee_seed_keywords: str = "máy xay mini,đèn ngủ,ốp lưng,bình giữ nhiệt,son môi"

    # Facebook
    meta_graph_version: str = ""
    meta_page_id: str = ""
    meta_page_access_token: SecretStr = SecretStr("")
    meta_enabled: bool = False
    meta_app_id: str = ""

    # TikTok Content Posting API
    tiktok_enabled: bool = False
    tiktok_access_token: SecretStr = SecretStr("")
    tiktok_mode: str = "inbox"  # inbox = send to TikTok drafts; direct = post publicly
    tiktok_privacy_level: str = "SELF_ONLY"

    # Creative / rendering
    tts_enabled: bool = True
    tts_voice: str = "Mai Anh"
    tts_mode: str = "fast"
    tts_precision: str = "auto"
    tts_rate: str = "+0%"
    music_dir: Path | None = None
    music_volume: float = 0.22
    keep_source_audio: bool = False
    source_audio_volume: float = 0.35
    render_width: int = 1080
    render_height: int = 1920
    render_fps: int = 30
    transition_seconds: float = 0.35
    target_min_seconds: float = 12.0
    target_max_seconds: float = 45.0
    font_name: str = "Arial"
    font_file: str = r"C:\Windows\Fonts\arialbd.ttf"
    cta_text: str = "Link sản phẩm ở giỏ hàng / bình luận"
    viral_review: bool = True
    viral_min_score: float = 7.0
    variants: int = 3   # hook A/B: the main video + up to 2 alternative hooks (same body)

    # Stock footage and AI video generation (optional)
    pexels_api_key: SecretStr = SecretStr("")
    videogen_provider: str = "none"  # none | muse2api | http
    videogen_base_url: str = ""  # embedded Muse gateway sets http://127.0.0.1:18620 itself
    videogen_api_key: SecretStr = SecretStr("")
    videogen_model: str = ""
    videogen_timeout_seconds: int = 600
    videogen_max_clips: int = 3          # AI clips per video
    videogen_daily_limit: int = 15       # clips + reference images per day (Muse weekly quota)
    videogen_clip_seconds: int = 6
    videogen_character: bool = True      # make one character reference image first
    videogen_task_timeout_minutes: int = 15
    videogen_continuity_chain: bool = True   # takes of one sequence continue from the last frame
    voice_led: bool = True                    # narration is the spine: cuts follow the voice track
    videogen_parallel: int = 3               # Muse clips in flight at once, all jobs together (gateway limit)
    style_profile: str = ""                   # pro_social | documentary | chibi_3d | ugc_phone | cinematic; empty = by branch
    use_listing_images: bool = True           # product listing photos (data API) may be used as references
    voice_revoice: bool = True                # fit TTS lines to the mouth movement of talking AI shots
    lipsync_provider: str = "none"            # none | (future) sync.so / fal LatentSync for talking shots
    visual_qa: bool = True                    # one AI vision pass over the rendered frames
    qa_autofix: bool = True                   # quality gate regenerates flagged scenes once
    preflight_enabled: bool = True            # quick AI/Muse/ffmpeg/disk check before a video is started
    clip_qa: bool = True                      # 7-point AI check of each Muse clip; failures are remade
    series_native_voice: bool = True          # dialogue series: keep Muse's own Vietnamese speech when it says the line
    series_vieneu_fallback: bool = True       # a line Muse never says right (3 takes) is voiced by VieNeu
    series_speech_max_wer: float = 0.3        # words wrong / words in the line a take may have
    series_voice_min_sim: float = 0.0         # min speaker similarity to the character voice sample (0 = off)
    cn_trends_enabled: bool = True            # China hot lists (public, no login)
    cn_trend_sources: str = ""                # empty = every supported source
    cn_auto_ideas: int = 3                    # topics per run that get ideas automatically
    autopilot_enabled: bool = False           # create videos from top ideas by itself
    autopilot_daily_videos: int = 3
    autopilot_min_score: float = 70.0
    autopilot_goal_mix: str = "fb_growth:70,affiliate:30"
    video_encoder: str = "auto"               # auto (NVENC if it works) | nvenc | x264
    muse_embedded: bool = True           # app runs the bundled muse2api gateway itself
    muse_gateway_port: int = 18620
    muse_cdp_port: int = 19210

    # Trend Intelligence (all optional keys are the user's own; budgets cap requests)
    youtube_api_key: SecretStr = SecretStr("")
    youtube_daily_units: int = 3000
    trend_youtube_searches_per_run: int = 8
    trendsmcp_api_key: SecretStr = SecretStr("")
    trendsmcp_monthly_budget: int = 90
    trendsmcp_calls_per_run: int = 1
    trendsmcp_boards: str = "TikTok Trending Hashtags"
    trend_news_keywords_per_run: int = 15
    trend_ai_topics_per_run: int = 20
    trend_rerank_topics: int = 5
    trend_min_purchase_intent: float = 40.0
    trend_weights: str = ""  # JSON, e.g. {"affiliate": 0.3, "growth": 0.2}

    # Automation
    auto_process: bool = True
    auto_publish: bool = False
    worker_poll_seconds: float = 3.0
    web_host: str = "127.0.0.1"
    web_port: int = 8765

    stable_file_seconds: float = 3.0
    file_stable_timeout_seconds: int = 300
    facebook_min_duration_seconds: float = 4.0
    facebook_max_duration_seconds: float = 90.0
    facebook_min_width: int = 540
    facebook_min_height: int = 960

    @property
    def inbox(self) -> Path:
        return self.inbox_dir or self.media_root / "inbox"

    @property
    def assets(self) -> Path:
        return self.assets_dir or self.media_root / "assets"

    @property
    def music(self) -> Path:
        return self.music_dir or self.assets / "music"

    @property
    def shopee_output(self) -> Path:
        return self.output_shopee_dir or self.media_root / "outbox" / "shopee"

    @property
    def facebook_output(self) -> Path:
        return self.output_facebook_dir or self.media_root / "outbox" / "facebook"

    @property
    def quarantine(self) -> Path:
        return self.quarantine_dir or self.media_root / "quarantine"

    @property
    def work_dir(self) -> Path:
        return self.project_root / "cache" / "work"

    @property
    def logs_dir(self) -> Path:
        return self.project_root / "logs"

    @property
    def seed_keywords(self) -> list[str]:
        return [k.strip() for k in self.shopee_seed_keywords.split(",") if k.strip()]


def get_settings() -> Settings:
    return Settings()
