from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import date
from typing import Any

import psycopg

from app.reservation_lab.db import fetch_all, fetch_one
from app.reservation_lab.reservations import _load_turns
from app.reservation_lab.utils import parse_time_value


@dataclass
class VoiceRestaurant:
    """Minimal restaurant view for OpenAI Realtime prompt (loaded from Supabase)."""

    id: str
    name: str
    timezone: str = "Europe/Rome"
    address: str = ""
    whatsapp_phone: str | None = None
    twilio_phone: str | None = None
    custom_greeting: str | None = None
    agent_style_notes: str | None = None
    voice_settings: dict[str, Any] = field(default_factory=dict)
    turni: list[dict[str, Any]] = field(default_factory=list)
    opening_hours: dict[str, str] = field(default_factory=dict)
    weekly_closures: list[str] = field(default_factory=list)
    closure_dates: list[str] = field(default_factory=list)
    booking_rules: dict[str, int] = field(default_factory=dict)
    openai_prompt_override: str | None = None
    openai_realtime_settings: dict[str, Any] = field(default_factory=dict)
    escalation_phone: str | None = None
    voice_provider: str = "openai_realtime"


def load_voice_restaurant(conn: psycopg.Connection[Any], restaurant_id: str) -> VoiceRestaurant | None:
    row = fetch_one(conn, "SELECT * FROM restaurants WHERE restaurant_id = %s", (restaurant_id,))
    if not row:
        return None

    turni: list[dict[str, Any]] = []
    opening_hours: dict[str, str] = {}
    rows = fetch_all(
        conn,
        """
        SELECT day_of_week, meal_period, turn_index, starts_at, ends_at
        FROM restaurant_opening_times
        WHERE restaurant_id = %s AND active = true
        ORDER BY day_of_week, meal_period, turn_index
        """,
        (restaurant_id,),
    )
    for item in rows:
        turni.append(
            {
                "name": f"{item['meal_period']} {item['turn_index']}",
                "start": parse_time_value(item["starts_at"]).strftime("%H:%M"),
                "end": parse_time_value(item["ends_at"]).strftime("%H:%M"),
                "meal_period": item["meal_period"],
                "turn_index": item["turn_index"],
            }
        )
    if turni:
        opening_hours["service"] = f"{turni[0]['start']}-{turni[-1]['end']}"

    voice_settings = row.get("voice_settings") or {}
    if isinstance(voice_settings, str):
        voice_settings = json.loads(voice_settings)

    whatsapp = row.get("whatsapp_phone_number")
    return VoiceRestaurant(
        id=restaurant_id,
        name=str(row.get("restaurant_name") or restaurant_id),
        timezone=str(row.get("timezone") or "Europe/Rome"),
        address=" ".join(
            p
            for p in [
                row.get("street"),
                row.get("street_number"),
                row.get("postal_code"),
                row.get("city"),
            ]
            if p
        ),
        whatsapp_phone=whatsapp,
        twilio_phone=row.get("twilio_phone"),
        custom_greeting=row.get("custom_greeting"),
        agent_style_notes=row.get("agent_style_notes"),
        voice_settings=voice_settings,
        turni=turni,
        opening_hours=opening_hours,
        booking_rules={
            "min_party": int(row.get("min_party") or 1),
            "max_party": int(row.get("max_party") or 20),
            "large_group_threshold": int(row.get("large_group_threshold") or 8),
            "max_advance_days": int(row.get("max_advance_days") or 60),
            "min_lead_hours": int(row.get("min_lead_hours") or 2),
        },
        openai_prompt_override=voice_settings.get("openai_prompt_override"),
        openai_realtime_settings=voice_settings.get("openai_realtime_settings") or {},
        escalation_phone=whatsapp,
        voice_provider="openai_realtime",
    )
