from __future__ import annotations

from datetime import date, datetime, time
from uuid import uuid4


def iso_weekday_name(service_date: date) -> str:
    return service_date.strftime("%A").lower()


def now_utc_iso() -> str:
    return datetime.utcnow().isoformat()


def generate_id(prefix: str) -> str:
    return f"{prefix}_{uuid4().hex[:10]}"


def parse_time_value(raw: str | time) -> time:
    if isinstance(raw, time):
        return raw
    normalized = raw.strip()
    if len(normalized) == 5:
        normalized = f"{normalized}:00"
    return time.fromisoformat(normalized)

