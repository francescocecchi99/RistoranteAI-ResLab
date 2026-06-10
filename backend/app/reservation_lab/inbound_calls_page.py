from __future__ import annotations

from datetime import date
from typing import Any

import psycopg

from app.reservation_lab.db import fetch_all, fetch_one


def compute_confidence_level(
    *,
    outcome: str | None,
    call_status: str | None,
    reservation_status: str | None,
    has_reservation: bool,
) -> int:
    outcome = (outcome or "info_provided").strip()
    call_status = (call_status or "unknown").strip()
    reservation_status = (reservation_status or "").strip()

    if call_status == "failed" or outcome in {"tool_error", "escalation_failed"}:
        return 5
    if outcome == "escalated":
        return 25
    if outcome == "abandoned":
        return 15
    if outcome == "booking_created":
        if reservation_status == "confirmed":
            return 95
        if reservation_status == "pending":
            return 75
        if has_reservation:
            return 70
        return 40
    if outcome == "booking_modified":
        return 85
    if outcome == "booking_cancelled":
        return 80
    if has_reservation and reservation_status in {"pending", "confirmed"}:
        return 65
    return 35


def load_inbound_calls_context(
    conn: psycopg.Connection[Any],
    restaurant_id: str,
    *,
    service_date: date | None = None,
    days: int = 30,
) -> dict[str, Any]:
    rest = fetch_one(
        conn,
        """
        SELECT restaurant_id, restaurant_name
        FROM restaurants
        WHERE restaurant_id = %s
        """,
        (restaurant_id,),
    )
    if not rest:
        return {"error": "not_found"}

    params: list[Any] = [restaurant_id]
    date_filter = ""
    if service_date is not None:
        date_filter = " AND (rr.service_date = %s OR (rr.service_date IS NULL AND v.started_at::date = %s))"
        params.extend([service_date, service_date])
    else:
        date_filter = " AND v.started_at >= now() - (%s || ' days')::interval"
        params.append(str(max(1, min(days, 90))))

    rows = fetch_all(
        conn,
        f"""
        SELECT
          v.call_log_id,
          v.restaurant_id,
          v.reservation_id,
          v.twilio_call_sid,
          v.caller_phone,
          v.started_at,
          v.duration_seconds,
          v.outcome,
          v.call_status,
          v.transcript,
          v.summary,
          v.audio_storage_path,
          v.confidence_level,
          v.high_chairs_requested AS call_high_chairs,
          rr.customer_name,
          rr.phone_number,
          rr.requested_time,
          rr.service_date,
          rr.party_size,
          (
            SELECT COALESCE(
              array_agg(a.table_id ORDER BY a.is_primary_table DESC, a.table_id)
                FILTER (WHERE a.table_id IS NOT NULL),
              ARRAY[]::text[]
            )
            FROM reservation_table_assignments a
            WHERE a.reservation_id = rr.reservation_id
          ) AS assigned_tables,
          rr.high_chairs_requested,
          rr.status AS reservation_status
        FROM voice_call_logs v
        LEFT JOIN restaurant_reservations rr
          ON rr.reservation_id = v.reservation_id
        WHERE v.restaurant_id = %s
        {date_filter}
        ORDER BY v.started_at DESC
        LIMIT 200
        """,
        tuple(params),
    )

    calls: list[dict[str, Any]] = []
    for row in rows:
        assigned = row.get("assigned_tables") or []
        if isinstance(assigned, str):
            assigned = [assigned]
        confidence = row.get("confidence_level")
        if confidence is None:
            confidence = compute_confidence_level(
                outcome=row.get("outcome"),
                call_status=row.get("call_status"),
                reservation_status=row.get("reservation_status"),
                has_reservation=bool(row.get("reservation_id")),
            )
        calls.append(
            {
                "call_log_id": row["call_log_id"],
                "twilio_call_sid": row.get("twilio_call_sid"),
                "started_at": row.get("started_at"),
                "duration_seconds": row.get("duration_seconds") or 0,
                "outcome": row.get("outcome") or "info_provided",
                "call_status": row.get("call_status") or "unknown",
                "transcript": row.get("transcript") or "",
                "summary": row.get("summary") or "",
                "audio_storage_path": row.get("audio_storage_path"),
                "confidence_level": int(confidence),
                "reservation_id": row.get("reservation_id"),
                "guest": row.get("customer_name") or "—",
                "phone": row.get("phone_number") or row.get("caller_phone") or "—",
                "time": str(row.get("requested_time") or "—"),
                "service_date": str(row.get("service_date") or ""),
                "covers": row.get("party_size") or "—",
                "tables": ", ".join(str(t) for t in assigned) if assigned else "—",
                "high_chairs": row.get("high_chairs_requested")
                if row.get("high_chairs_requested") is not None
                else (row.get("call_high_chairs") or 0),
                "reservation_status": row.get("reservation_status"),
            }
        )

    return {
        "restaurant": rest,
        "calls": calls,
        "service_date": service_date,
        "days": days,
    }


def link_call_to_reservation(
    conn: psycopg.Connection[Any],
    *,
    call_log_id: str,
    restaurant_id: str,
    reservation_id: str,
) -> None:
    from app.reservation_lab.db import execute

    execute(
        conn,
        """
        UPDATE voice_call_logs
        SET reservation_id = %s,
            outcome = 'booking_created',
            call_status = 'successful',
            confidence_level = 100,
            updated_at = now()
        WHERE call_log_id = %s AND restaurant_id = %s
        """,
        (reservation_id, call_log_id, restaurant_id),
    )


def get_voice_call_row(conn: psycopg.Connection[Any], call_log_id: str, restaurant_id: str) -> dict[str, Any] | None:
    return fetch_one(
        conn,
        """
        SELECT call_log_id, restaurant_id, reservation_id, twilio_call_sid,
               caller_phone, transcript, audio_storage_path
        FROM voice_call_logs
        WHERE call_log_id = %s AND restaurant_id = %s
        """,
        (call_log_id, restaurant_id),
    )
