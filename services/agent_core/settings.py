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
    ai_model: str = "gpt-6-astra-wm"
    ai_thinking_effort: str = "extended"
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
    tts_voice: str = "vi-VN-HoaiMyNeural"
    tts_rate: str = "+8%"
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
    variants: int = 1

    # Stock footage and AI video generation (optional)
    pexels_api_key: SecretStr = SecretStr("")
    videogen_provider: str = "none"  # none | http
    videogen_base_url: str = ""
    videogen_api_key: SecretStr = SecretStr("")
    videogen_model: str = ""
    videogen_timeout_seconds: int = 600
    videogen_max_clips: int = 3

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
    trend_min_purchase_intent: float = 30.0
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
