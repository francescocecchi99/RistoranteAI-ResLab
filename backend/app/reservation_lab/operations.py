from __future__ import annotations

from datetime import date, datetime, time, timedelta
from typing import Any

import psycopg

from app.reservation_lab.booking_rules import turno_label, validate_booking_rules
from app.reservation_lab.db import execute, fetch_all, fetch_one
from app.reservation_lab.models import BookingRequest
from app.reservation_lab.phone import normalize_phone_e164, phones_equivalent
from app.reservation_lab.reservations import (
    _load_turns,
    _match_turn,
    cancel_reservation,
    compute_availability,
    confirm_pending_reservation,
    create_pending_reservation,
    list_reservations,
)
from app.reservation_lab.utils import generate_id, parse_time_value

ACTIVE_STATUSES = ("pending", "confirmed")


def expire_stale_pending_reservations(conn: psycopg.Connection[Any], *, restaurant_id: str | None = None) -> int:
    params: list[Any] = []
    where = "status = 'pending' AND pending_expires_at IS NOT NULL AND pending_expires_at < now()"
    if restaurant_id:
        where += " AND restaurant_id = %s"
        params.append(restaurant_id)
    rows = fetch_all(
        conn,
        f"SELECT reservation_id FROM restaurant_reservations WHERE {where}",
        tuple(params),
    )
    for row in rows:
        cancel_reservation(conn, row["reservation_id"])
    return len(rows)


def find_reservations(
    conn: psycopg.Connection[Any],
    *,
    restaurant_id: str,
    caller_phone: str | None = None,
    customer_name: str | None = None,
    service_date: date | None = None,
    requested_time: time | None = None,
) -> list[dict[str, Any]]:
    expire_stale_pending_reservations(conn, restaurant_id=restaurant_id)
    base = """
        SELECT reservation_id, restaurant_id, service_date, requested_time, meal_period, turn_index,
               customer_name, phone_number, party_size, status
        FROM restaurant_reservations
        WHERE restaurant_id = %s AND status IN ('pending', 'confirmed')
    """
    params: list[Any] = [restaurant_id]
    rows = fetch_all(conn, base, tuple(params))

    def _matches(row: dict[str, Any], *, phone: bool, name: bool) -> bool:
        if service_date and row["service_date"] != service_date:
            return False
        if requested_time and parse_time_value(row["requested_time"]) != requested_time:
            return False
        if name and customer_name:
            if str(row["customer_name"]).strip().lower() != customer_name.strip().lower():
                return False
        if phone and caller_phone:
            if not phones_equivalent(row.get("phone_number"), caller_phone):
                return False
        return True

    strict = [
        r
        for r in rows
        if _matches(r, phone=True, name=True)
        and caller_phone
        and customer_name
        and service_date
        and requested_time
    ]
    if strict:
        return strict

    relaxed = [
        r
        for r in rows
        if _matches(r, phone=False, name=True)
        and customer_name
        and service_date
        and requested_time
    ]
    return relaxed


def update_reservation(
    conn: psycopg.Connection[Any],
    *,
    reservation_id: str,
    request: BookingRequest,
) -> dict[str, Any]:
    expire_stale_pending_reservations(conn, restaurant_id=request.restaurant_id)
    row = fetch_one(
        conn,
        """
        SELECT reservation_id, status FROM restaurant_reservations
        WHERE reservation_id = %s AND restaurant_id = %s
        """,
        (reservation_id, request.restaurant_id),
    )
    if not row or row["status"] not in ACTIVE_STATUSES:
        return {"success": False, "reason": "Prenotazione non trovata."}

    cancel_reservation(conn, reservation_id)
    created = create_pending_reservation(conn, request)
    if not created.get("db_saved"):
        return {
            "success": False,
            "reason": created.get("reason") or "Nuovo slot non disponibile.",
            "alternatives": created.get("alternatives") or [],
        }
    new_id = created["db_saved"]["reservation_id"]
    confirmed = confirm_pending_reservation(conn, new_id, request)
    if not confirmed.get("db_saved"):
        return {"success": False, "reason": confirmed.get("reason") or "Conferma fallita."}
    return {"success": True, "reservation_id": new_id, "updated_booking": confirmed["db_saved"]}


def build_ra_availability_result(
    conn: psycopg.Connection[Any],
    request: BookingRequest,
) -> dict[str, Any]:
    """Map Reservation Lab availability to RistoranteAI voice tool shape."""
    restaurant = fetch_one(
        conn,
        "SELECT * FROM restaurants WHERE restaurant_id = %s",
        (request.restaurant_id,),
    )
    if not restaurant:
        return {"open": False, "available": False, "reason": "Ristorante non trovato.", "alternatives": []}

    ok, rule_reason = validate_booking_rules(
        restaurant,
        booking_date=request.date,
        requested_time=request.time,
        party_size=request.party_size,
        timezone=str(restaurant.get("timezone") or "Europe/Rome"),
    )
    if not ok:
        escalate = "Trasferisci al ristorante" in (rule_reason or "")
        return {
            "open": True,
            "available": False,
            "reason": rule_reason,
            "alternatives": [],
            "should_escalate": escalate,
        }

    result = compute_availability(conn, request)
    if result["agent_response"]["available"]:
        preview = result.get("db_preview") or {}
        turno = turno_label(str(preview.get("meal_period", "")), int(preview.get("turn_index") or 0))
        slot_time = parse_time_value(preview.get("requested_time") or request.time)
        return {
            "open": True,
            "available": True,
            "slot": {
                "time": slot_time.strftime("%H:%M"),
                "turno": turno,
            },
            "alternatives": [],
        }

    alternatives = _suggest_alternatives(conn, request)
    return {
        "open": True,
        "available": False,
        "reason": result.get("reason") or "Non disponibile per l'orario richiesto.",
        "alternatives": alternatives[:3],
    }


def _suggest_alternatives(conn: psycopg.Connection[Any], request: BookingRequest) -> list[dict[str, str]]:
    turns = _load_turns(conn, request.restaurant_id, request.date)
    if turns and turns[0].get("closed"):
        return []
    out: list[dict[str, str]] = []
    for turn in turns:
        if turn.get("closed"):
            continue
        starts = parse_time_value(turn["starts_at"])
        probe = BookingRequest(
            restaurant_id=request.restaurant_id,
            date=request.date,
            time=starts,
            customer_name=request.customer_name or "Probe",
            phone_number=request.phone_number,
            party_size=request.party_size,
            high_chairs_requested=request.high_chairs_requested,
            preferences=request.preferences,
        )
        probe_result = compute_availability(conn, probe)
        if probe_result["agent_response"]["available"]:
            preview = probe_result.get("db_preview") or {}
            out.append(
                {
                    "time": starts.strftime("%H:%M"),
                    "turno": turno_label(str(preview.get("meal_period", turn.get("meal_period"))), int(preview.get("turn_index") or turn.get("turn_index") or 0)),
                }
            )
    return out
