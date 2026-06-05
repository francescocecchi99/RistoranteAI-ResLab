from __future__ import annotations

from datetime import date, datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo


def turno_label(meal_period: str, turn_index: int) -> str:
    return f"{meal_period}-{turn_index}"


def validate_booking_rules(
    restaurant: dict[str, Any],
    *,
    booking_date: date,
    requested_time: time | None,
    party_size: int,
    timezone: str = "Europe/Rome",
) -> tuple[bool, str | None]:
    min_party = int(restaurant.get("min_party") or 1)
    max_party = int(restaurant.get("max_party") or 20)
    large_threshold = int(restaurant.get("large_group_threshold") or 8)
    max_advance = int(restaurant.get("max_advance_days") or 60)
    min_lead_hours = int(restaurant.get("min_lead_hours") or 2)

    if party_size < min_party:
        return False, f"La prenotazione minima è per {min_party} persone."
    if party_size > max_party:
        return False, f"Accettiamo prenotazioni fino a {max_party} persone."
    if party_size > large_threshold:
        return (
            False,
            f"Per gruppi superiori a {large_threshold} persone la prenotazione "
            "automatica non è permessa. Trasferisci al ristorante.",
        )

    tz = ZoneInfo(timezone or restaurant.get("timezone") or "Europe/Rome")
    local_now = datetime.now(tz)
    if booking_date > local_now.date() + timedelta(days=max_advance):
        return False, f"Le prenotazioni sono disponibili fino a {max_advance} giorni in anticipo."
    if requested_time and booking_date == local_now.date():
        requested_dt = datetime.combine(booking_date, requested_time, tzinfo=tz)
        if requested_dt < local_now + timedelta(hours=min_lead_hours):
            return (
                False,
                f"Per prenotazioni in giornata serve almeno {min_lead_hours} ore di anticipo.",
            )
    return True, None
