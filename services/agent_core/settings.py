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

    ai_protocol: str = "openai"
    ai_base_url: str = "http://127.0.0.1:13000/v1"
    ai_api_key: SecretStr = SecretStr("")
    ai_model: str = "gpt-6-astra-wm"
    ai_thinking_effort: str = "extended"

    shopee_skill_home: Path = PROJECT_ROOT / "config" / "shopee-skill"
    shopee_affiliate_enabled: bool = False

    meta_graph_version: str = ""
    meta_page_id: str = ""
    meta_page_access_token: SecretStr = SecretStr("")
    meta_enabled: bool = False

    auto_publish: bool = False
    stable_file_seconds: float = 3.0
    file_stable_timeout_seconds: int = 300
    facebook_min_duration_seconds: float = 4.0
    facebook_max_duration_seconds: float = 60.0
    facebook_min_width: int = 540
    facebook_min_height: int = 960

    @property
    def inbox(self) -> Path:
        return self.inbox_dir or self.media_root / "inbox"

    @property
    def assets(self) -> Path:
        return self.assets_dir or self.media_root / "assets"

    @property
    def shopee_output(self) -> Path:
        return self.output_shopee_dir or self.media_root / "outbox" / "shopee"

    @property
    def facebook_output(self) -> Path:
        return self.output_facebook_dir or self.media_root / "outbox" / "facebook"

    @property
    def quarantine(self) -> Path:
        return self.quarantine_dir or self.media_root / "quarantine"


def get_settings() -> Settings:
    return Settings()
