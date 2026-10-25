from __future__ import annotations

import os
import re
from dataclasses import dataclass
from datetime import datetime, time, tzinfo
from pathlib import Path
from zoneinfo import ZoneInfo

from dotenv import load_dotenv


def _int_set(name: str, value: str) -> frozenset[int]:
    """Parses a list of ids separated by commas, semicolons or spaces, e.g. "123, -100456"."""
    try:
        return frozenset(int(x) for x in re.split(r"[,;\s]+", value.strip()) if x)
    except ValueError:
        raise SystemExit(f"{name} must be a list of numeric ids separated by commas, got {value!r}") from None


def _time(value: str) -> time:
    hours, minutes = value.strip().split(":")
    return time(int(hours), int(minutes))


def _bool(value: str) -> bool:
    return value.strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class Config:
    hik_host: str
    hik_port: int
    hik_user: str
    hik_password: str

    tz: tzinfo
    day_cutoff: time

    db_path: Path
    reconcile_minutes: int
    backfill_days: int

    telegram_token: str | None
    report_chat_ids: frozenset[int]
    admin_ids: frozenset[int]
    daily_report_time: time
    daily_report_for: str
    monthly_report_time: time
    notify_arrivals: bool

    @property
    def device_url(self) -> str:
        return f"http://{self.hik_host}:{self.hik_port}"


def load_config(*, require_device: bool = True) -> Config:
    load_dotenv()
    env = os.environ.get

    if require_device:
        missing = [k for k in ("HIK_HOST", "HIK_PASSWORD") if not env(k)]
        if missing:
            raise SystemExit(
                f"Missing required settings: {', '.join(missing)}. "
                "Copy .env.example to .env and fill them in."
            )

    tz_name = env("TIMEZONE")
    tz = ZoneInfo(tz_name) if tz_name else datetime.now().astimezone().tzinfo

    daily_for = env("DAILY_REPORT_FOR", "today").strip().lower()
    if daily_for not in {"today", "yesterday"}:
        raise SystemExit("DAILY_REPORT_FOR must be 'today' or 'yesterday'")

    return Config(
        hik_host=env("HIK_HOST", ""),
        hik_port=int(env("HIK_PORT", "80")),
        hik_user=env("HIK_USER", "admin"),
        hik_password=env("HIK_PASSWORD", ""),
        tz=tz,
        day_cutoff=_time(env("DAY_CUTOFF", "04:00")),
        db_path=Path(env("DB_PATH", "data/attendance.db")),
        reconcile_minutes=int(env("RECONCILE_MINUTES", "10")),
        backfill_days=int(env("BACKFILL_DAYS", "31")),
        telegram_token=env("TELEGRAM_BOT_TOKEN") or None,
        report_chat_ids=_int_set("TELEGRAM_REPORT_CHAT_IDS", env("TELEGRAM_REPORT_CHAT_IDS", "")),
        admin_ids=_int_set("TELEGRAM_ADMIN_IDS", env("TELEGRAM_ADMIN_IDS", "")),
        daily_report_time=_time(env("DAILY_REPORT_TIME", "20:00")),
        daily_report_for=daily_for,
        monthly_report_time=_time(env("MONTHLY_REPORT_TIME", "09:00")),
        notify_arrivals=_bool(env("NOTIFY_ARRIVALS", "false")),
    )
