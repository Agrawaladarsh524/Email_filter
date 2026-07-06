"""Runtime configuration, loaded from environment / .env."""

from __future__ import annotations

from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=PROJECT_ROOT / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # --- Gmail ---------------------------------------------------------
    google_credentials_file: Path = PROJECT_ROOT / "credentials.json"
    google_token_file: Path = PROJECT_ROOT / "token.json"
    gmail_user_id: str = "me"
    # Only messages matching this Gmail search are considered. Narrow it while
    # testing (e.g. "is:unread newer_than:1d label:^smartlabel_personal").
    gmail_query: str = "is:unread newer_than:1d"

    # --- LLM -----------------------------------------------------------
    openai_api_key: str = Field(default="", repr=False)
    llm_model: str = "gpt-4o-mini"
    llm_temperature: float = 0.0
    # Body text beyond this many characters is truncated before classification.
    body_char_limit: int = 4000

    # --- Alerting ------------------------------------------------------
    slack_webhook_url: str = Field(default="", repr=False)
    telegram_bot_token: str = Field(default="", repr=False)
    telegram_chat_id: str = ""

    # --- Poller --------------------------------------------------------
    poll_interval_seconds: int = 300  # 5 minutes
    max_messages_per_poll: int = 25
    seen_db: Path = PROJECT_ROOT / "state" / "seen.sqlite"

    # --- API -----------------------------------------------------------
    api_host: str = "127.0.0.1"
    api_port: int = 8000
    # Off when you want the API purely for manual /poll and /triage calls.
    enable_background_poller: bool = True

    # Log and classify, but never mutate Gmail or send alerts.
    dry_run: bool = False

    # --- Guardrails ----------------------------------------------------
    confidence_floor: float = 0.70
    quiet_hours_start: str = "22:00"
    quiet_hours_end: str = "07:00"
    quiet_hours_timezone: str = "UTC"
    vip_senders: list[str] = ["*@boss.com"] # Default examples
    max_archives_per_day: int = 50
    max_drafts_per_day: int = 10
    max_alerts_per_day: int = 20

    @property
    def slack_enabled(self) -> bool:
        return bool(self.slack_webhook_url)

    @property
    def telegram_enabled(self) -> bool:
        return bool(self.telegram_bot_token and self.telegram_chat_id)


_settings: Settings | None = None


def get_settings() -> Settings:
    """Process-wide singleton so nodes don't each re-read the environment."""
    global _settings
    if _settings is None:
        _settings = Settings()
    return _settings
