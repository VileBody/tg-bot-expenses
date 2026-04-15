from __future__ import annotations

import os
from dataclasses import dataclass

from dotenv import load_dotenv


def _read_bool(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _read_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    return int(raw.strip())


def _read_chat_ids(raw: str | None) -> set[int]:
    if not raw:
        return set()
    values: set[int] = set()
    for token in raw.split(","):
        token = token.strip()
        if not token:
            continue
        values.add(int(token))
    return values


@dataclass(slots=True)
class Settings:
    telegram_bot_token: str
    outbound_proxy: str | None

    google_sheet_url: str
    google_worksheet_name: str
    google_service_account_file: str
    queue_db_path: str

    gemini_api_key: str
    gemini_model: str

    retry_backoff_base_seconds: int
    gemini_transient_retries: int
    gemini_validation_retries: int
    gemini_timeout_seconds: int
    google_append_retries: int
    pipeline_retry_max_backoff_seconds: int
    queue_poll_interval_seconds: int

    allow_group_chats: bool
    allowed_chat_ids: set[int]
    log_level: str

    @classmethod
    def from_env(cls) -> "Settings":
        load_dotenv(override=False)

        token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()

        missing: list[str] = []
        if not token:
            missing.append("TELEGRAM_BOT_TOKEN")

        sheet_url = os.getenv("GOOGLE_SHEET_URL", "").strip()
        service_file = os.getenv("GOOGLE_SERVICE_ACCOUNT_FILE", "").strip()
        gemini_key = os.getenv("GEMINI_API_KEY", "").strip()

        if not sheet_url:
            missing.append("GOOGLE_SHEET_URL")
        if not service_file:
            missing.append("GOOGLE_SERVICE_ACCOUNT_FILE")
        if not gemini_key:
            missing.append("GEMINI_API_KEY")

        if missing:
            raise ValueError(f"Missing required env vars: {', '.join(missing)}")

        outbound_proxy = os.getenv("OUTBOUND_PROXY", "").strip() or None

        return cls(
            telegram_bot_token=token,
            outbound_proxy=outbound_proxy,
            google_sheet_url=sheet_url,
            google_worksheet_name=os.getenv("GOOGLE_WORKSHEET_NAME", "Расходы").strip(),
            google_service_account_file=service_file,
            queue_db_path=os.getenv("QUEUE_DB_PATH", "data/message_queue.sqlite3").strip(),
            gemini_api_key=gemini_key,
            gemini_model=os.getenv("GEMINI_MODEL", "gemini-2.5-flash").strip(),
            retry_backoff_base_seconds=max(1, _read_int("RETRY_BACKOFF_BASE_SECONDS", 2)),
            gemini_transient_retries=max(0, _read_int("GEMINI_TRANSIENT_RETRIES", 4)),
            gemini_validation_retries=max(0, _read_int("GEMINI_VALIDATION_RETRIES", 2)),
            gemini_timeout_seconds=max(5, _read_int("GEMINI_TIMEOUT_SECONDS", 45)),
            google_append_retries=max(0, _read_int("GOOGLE_APPEND_RETRIES", 4)),
            pipeline_retry_max_backoff_seconds=max(5, _read_int("PIPELINE_RETRY_MAX_BACKOFF_SECONDS", 300)),
            queue_poll_interval_seconds=max(1, _read_int("QUEUE_POLL_INTERVAL_SECONDS", 1)),
            allow_group_chats=_read_bool("ALLOW_GROUP_CHATS", False),
            allowed_chat_ids=_read_chat_ids(os.getenv("ALLOWED_CHAT_IDS")),
            log_level=os.getenv("LOG_LEVEL", "INFO").strip().upper(),
        )
