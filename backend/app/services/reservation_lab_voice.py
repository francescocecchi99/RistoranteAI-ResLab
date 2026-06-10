from __future__ import annotations

from datetime import date, time
from typing import Any, Callable

from app.core.config import settings
from app.reservation_lab.db import get_connection
from app.reservation_lab.models import BookingRequest, Preferences
from app.reservation_lab.operations import (
    build_ra_availability_result,
    expire_stale_pending_reservations,
    find_reservations,
    update_reservation,
)
from app.reservation_lab.phone import normalize_phone_e164
from app.reservation_lab.reservations import cancel_reservation, confirm_pending_reservation, create_pending_reservation
from app.reservation_lab.utils import generate_id, parse_time_value
from app.reservation_lab.voice_context import VoiceRestaurant, load_voice_restaurant

ACTIVE_STATUSES = ("pending", "confirmed")


def default_restaurant_id() -> str:
    return (settings.default_restaurant_id or "r1").strip()


def get_restaurant_for_voice(restaurant_id: str | None = None) -> VoiceRestaurant:
    rid = (restaurant_id or default_restaurant_id()).strip()
    with get_connection() as conn:
        restaurant = load_voice_restaurant(conn, rid)
    if not restaurant:
        raise RuntimeError(f"Restaurant not found in Supabase: {rid}")
    return restaurant


def check_availability_for_voice(
    *,
    restaurant_id: str,
    booking_date: date,
    requested_time: time | None,
    party_size: int,
    preferred_area: str | None = None,
    high_chairs: int = 0,
) -> dict[str, Any]:
    request = BookingRequest(
        restaurant_id=restaurant_id,
        date=booking_date,
        time=requested_time or time(20, 0),
        customer_name="Voice",
        phone_number=None,
        party_size=party_size,
        high_chairs_requested=high_chairs,
        preferences=Preferences(preferred_area=preferred_area),
    )
    with get_connection() as conn:
        expire_stale_pending_reservations(conn, restaurant_id=restaurant_id)
        return build_ra_availability_result(conn, request)


def create_booking_for_voice(
    *,
    restaurant_id: str,
    booking_date: date,
    booking_time: time,
    party_size: int,
    customer_name: str,
    caller_phone: str | None,
    special_requests: str | None = None,
    preferred_area: str | None = None,
    high_chairs: int = 0,
) -> dict[str, Any]:
    request = BookingRequest(
        restaurant_id=restaurant_id,
        date=booking_date,
        time=booking_time,
        customer_name=customer_name,
        phone_number=normalize_phone_e164(caller_phone),
        party_size=party_size,
        high_chairs_requested=high_chairs,
        preferences=Preferences(preferred_area=preferred_area, special_requests=special_requests),
        original_agent_text="openai_realtime",
    )
    with get_connection() as conn:
        expire_stale_pending_reservations(conn, restaurant_id=restaurant_id)
        existing = find_reservations(
            conn,
            restaurant_id=restaurant_id,
            caller_phone=caller_phone,
            customer_name=customer_name,
            service_date=booking_date,
            requested_time=booking_time,
        )
        if any(r["status"] in ACTIVE_STATUSES for r in existing):
            return {
                "success": False,
                "reason": (
                    "Risulta già una prenotazione attiva con questi dati "
                    "(telefono, nome, data e ora)."
                ),
                "retry_write": False,
            }
        created = create_pending_reservation(conn, request)
        if not created.get("db_saved"):
            return {
                "success": False,
                "reason": created.get("reason") or "Slot non disponibile.",
                "alternatives": [],
            }
        reservation_id = created["db_saved"]["reservation_id"]
        return {
            "success": True,
            "pending": True,
            "reservation_id": reservation_id,
            "assistant_instruction": (
                "Conferma la prenotazione in una frase breve usando il nome del cliente, "
                "poi saluta e chiudi."
            ),
        }


def confirm_booking_for_voice(*, reservation_id: str, request: BookingRequest) -> dict[str, Any]:
    with get_connection() as conn:
        result = confirm_pending_reservation(conn, reservation_id, request)
        if not result.get("db_saved"):
            return {"success": False, "reason": result.get("reason") or "Conferma fallita."}
        return {
            "success": True,
            "reservation_id": reservation_id,
            "booking_id": reservation_id,
        }


def find_bookings_for_voice(
    *,
    restaurant_id: str,
    caller_phone: str | None,
    customer_name: str | None = None,
    service_date: date | None = None,
    requested_time: time | None = None,
) -> dict[str, Any]:
    with get_connection() as conn:
        rows = find_reservations(
            conn,
            restaurant_id=restaurant_id,
            caller_phone=caller_phone,
            customer_name=customer_name,
            service_date=service_date,
            requested_time=requested_time,
        )
    bookings = [
        {
            "id": row["reservation_id"],
            "confirmation_code": row["reservation_id"],
            "date": str(row["service_date"]),
            "time": str(row["requested_time"]),
            "party_size": row["party_size"],
            "customer_name": row["customer_name"],
            "status": row["status"],
        }
        for row in rows
    ]
    return {"found": bool(bookings), "bookings": bookings}


def modify_booking_for_voice(
    *,
    reservation_id: str,
    restaurant_id: str,
    booking_date: date | None = None,
    booking_time: time | None = None,
    party_size: int | None = None,
    customer_name: str | None = None,
    caller_phone: str | None = None,
    special_requests: str | None = None,
) -> dict[str, Any]:
    with get_connection() as conn:
        row = fetch_one_reservation(conn, reservation_id, restaurant_id)
    if not row:
        return {"success": False, "reason": "Prenotazione non trovata."}

    request = BookingRequest(
        restaurant_id=restaurant_id,
        date=booking_date or row["service_date"],
        time=booking_time or parse_time_value(row["requested_time"]),
        customer_name=customer_name or row["customer_name"],
        phone_number=normalize_phone_e164(caller_phone) or row.get("phone_number"),
        party_size=party_size or int(row["party_size"]),
        preferences=Preferences(special_requests=special_requests),
        original_agent_text="openai_realtime modify",
    )
    with get_connection() as conn:
        result = update_reservation(conn, reservation_id=reservation_id, request=request)
    if not result.get("success"):
        return result
    return {"success": True, "updated_booking": result.get("reservation_id")}


def cancel_booking_for_voice(*, reservation_id: str) -> dict[str, Any]:
    with get_connection() as conn:
        cancel_reservation(conn, reservation_id)
    return {"success": True}


def fetch_one_reservation(conn, reservation_id: str, restaurant_id: str) -> dict[str, Any] | None:
    from app.reservation_lab.db import fetch_one

    return fetch_one(
        conn,
        """
        SELECT * FROM restaurant_reservations
        WHERE reservation_id = %s AND restaurant_id = %s
        """,
        (reservation_id, restaurant_id),
    )


def log_voice_call(
    *,
    restaurant_id: str,
    twilio_call_sid: str | None,
    caller_phone: str | None,
    reservation_id: str | None = None,
    outcome: str = "info_provided",
    transcript: str | None = None,
    summary: str | None = None,
) -> str:
    from app.reservation_lab.db import execute, fetch_one

    call_log_id = generate_id("call")
    with get_connection() as conn:
        if twilio_call_sid:
            existing = fetch_one(
                conn,
                """
                SELECT call_log_id FROM voice_call_logs
                WHERE twilio_call_sid = %s
                """,
                (twilio_call_sid,),
            )
            if existing:
                return str(existing["call_log_id"])
        execute(
            conn,
            """
            INSERT INTO voice_call_logs (
              call_log_id, restaurant_id, reservation_id, twilio_call_sid,
              caller_phone, outcome, transcript, summary
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT DO NOTHING
            """,
            (
                call_log_id,
                restaurant_id,
                reservation_id,
                twilio_call_sid,
                normalize_phone_e164(caller_phone),
                outcome,
                transcript,
                summary,
            ),
        )
    return call_log_id


def sync_voice_call_log_update(
    *,
    call_sid: str | None = None,
    call_log_id: str | None = None,
    fields: dict[str, Any],
) -> None:
    """Persist Twilio stream updates into Supabase voice_call_logs (RL mode)."""
    from app.reservation_lab.db import execute, fetch_one
    from app.reservation_lab.inbound_calls_page import compute_confidence_level

    if not call_sid and not call_log_id:
        return

    extra = fields.get("extra_data") if isinstance(fields.get("extra_data"), dict) else {}
    reservation_id = fields.get("booking_id") or fields.get("reservation_id")
    transcript = fields.get("transcript_preview") or fields.get("transcript")
    summary = fields.get("summary")
    outcome = fields.get("outcome")
    call_status = fields.get("call_status")
    duration_seconds = fields.get("duration_seconds")
    audio_storage_path = fields.get("audio_storage_path")
    confidence_level = fields.get("confidence_level")

    with get_connection() as conn:
        if call_sid:
            row = fetch_one(
                conn,
                """
                SELECT v.call_log_id, v.reservation_id, rr.status AS reservation_status
                FROM voice_call_logs v
                LEFT JOIN restaurant_reservations rr ON rr.reservation_id = v.reservation_id
                WHERE v.twilio_call_sid = %s
                """,
                (call_sid,),
            )
        else:
            row = fetch_one(
                conn,
                """
                SELECT v.call_log_id, v.reservation_id, rr.status AS reservation_status
                FROM voice_call_logs v
                LEFT JOIN restaurant_reservations rr ON rr.reservation_id = v.reservation_id
                WHERE v.call_log_id = %s
                """,
                (call_log_id,),
            )
        if not row:
            return

        effective_reservation_id = reservation_id or row.get("reservation_id")
        res_status = row.get("reservation_status")
        if effective_reservation_id and effective_reservation_id != row.get("reservation_id"):
            res_row = fetch_one(
                conn,
                "SELECT status FROM restaurant_reservations WHERE reservation_id = %s",
                (effective_reservation_id,),
            )
            res_status = res_row.get("status") if res_row else None

        if confidence_level is None and (outcome or call_status):
            confidence_level = compute_confidence_level(
                outcome=outcome,
                call_status=call_status,
                reservation_status=res_status,
                has_reservation=bool(effective_reservation_id),
            )

        where_clause = "twilio_call_sid = %s" if call_sid else "call_log_id = %s"
        where_val = call_sid if call_sid else call_log_id
        execute(
            conn,
            f"""
            UPDATE voice_call_logs
            SET
              reservation_id = COALESCE(%s, reservation_id),
              transcript = COALESCE(%s, transcript),
              summary = COALESCE(%s, summary),
              outcome = COALESCE(%s, outcome),
              call_status = COALESCE(%s, call_status),
              duration_seconds = COALESCE(%s, duration_seconds),
              audio_storage_path = COALESCE(%s, audio_storage_path),
              confidence_level = COALESCE(%s, confidence_level),
              updated_at = now()
            WHERE {where_clause}
            """,
            (
                effective_reservation_id,
                transcript,
                summary,
                outcome,
                call_status,
                duration_seconds,
                audio_storage_path,
                confidence_level,
                where_val,
            ),
        )


def finalize_voice_call_log(
    *,
    call_sid: str,
    restaurant_id: str,
    caller_audio_ulaw: bytes | None = None,
    **fields: Any,
) -> None:
    from app.reservation_lab.db import fetch_one
    from app.reservation_lab.voice_recording import save_caller_ulaw_wav

    if caller_audio_ulaw:
        with get_connection() as conn:
            row = fetch_one(
                conn,
                "SELECT call_log_id FROM voice_call_logs WHERE twilio_call_sid = %s",
                (call_sid,),
            )
        if row:
            path = save_caller_ulaw_wav(
                restaurant_id=restaurant_id,
                call_log_id=str(row["call_log_id"]),
                ulaw_payload=caller_audio_ulaw,
            )
            if path:
                fields["audio_storage_path"] = path

    sync_voice_call_log_update(call_sid=call_sid, fields=fields)


def dispatch_reservation_lab_tool(
    *,
    restaurant: Any,
    state: Any,
    tool_name: str,
    arguments: dict[str, Any],
    sync_transfer: Any,
) -> dict[str, Any]:
    """Execute OpenAI Realtime tools against Supabase Reservation Lab logic."""
    from datetime import date, time

    restaurant_id = str(getattr(restaurant, "id", None) or default_restaurant_id())

    if tool_name == "wait_for_user":
        return {
            "success": True,
            "wait": True,
            "assistant_instruction": (
                "Resta in ascolto. Non rispondere a voce finché il cliente "
                "non si rivolge chiaramente a te."
            ),
        }

    if tool_name == "check_availability":
        requested_time = time.fromisoformat(arguments["time"]) if arguments.get("time") else None
        party_size = int(arguments.get("party_size") or state.booking_context.get("party_size") or 0)
        if party_size <= 0:
            return {
                "success": False,
                "available": False,
                "reason": "Non ho il numero di persone. Chiedi al cliente quante persone saranno.",
                "missing_field": "party_size",
            }
        return check_availability_for_voice(
            restaurant_id=restaurant_id,
            booking_date=date.fromisoformat(arguments["date"]),
            requested_time=requested_time,
            party_size=party_size,
            preferred_area=(arguments.get("preferred_area") or state.booking_context.get("preferred_area")),
            high_chairs=int(arguments.get("high_chairs_requested") or 0),
        )

    if tool_name == "find_booking":
        req_date = date.fromisoformat(arguments["date"]) if arguments.get("date") else None
        req_time = time.fromisoformat(arguments["time"]) if arguments.get("time") else None
        return find_bookings_for_voice(
            restaurant_id=restaurant_id,
            caller_phone=state.caller_phone or None,
            customer_name=arguments.get("customer_name"),
            service_date=req_date,
            requested_time=req_time,
        )

    if tool_name == "create_booking":
        customer_name = str(arguments.get("customer_name") or arguments.get("name") or "").strip()
        if not customer_name:
            return {
                "success": False,
                "reason": "Manca il nome cliente per completare la prenotazione.",
                "assistant_instruction": "Chiedi solo il nome per la prenotazione.",
            }
        booking_time = time.fromisoformat(arguments["time"])
        party_size = int(arguments.get("party_size") or state.booking_context.get("party_size") or 0)
        result = create_booking_for_voice(
            restaurant_id=restaurant_id,
            booking_date=date.fromisoformat(arguments["date"]),
            booking_time=booking_time,
            party_size=party_size,
            customer_name=customer_name,
            caller_phone=state.caller_phone,
            special_requests=arguments.get("special_requests"),
            preferred_area=arguments.get("preferred_area"),
        )
        if result.get("success") and result.get("pending"):
            state.pending_reservation_id = result.get("reservation_id")
            state.pending_confirm_payload = {
                "date": arguments["date"],
                "time": arguments["time"],
                "party_size": party_size,
                "customer_name": customer_name,
                "preferred_area": arguments.get("preferred_area"),
                "special_requests": arguments.get("special_requests"),
            }
            state.end_call_after_response = True
            return result
        return result

    if tool_name == "modify_booking":
        reservation_id = str(arguments.get("confirmation_code") or arguments.get("reservation_id") or "")
        if not reservation_id:
            found = find_bookings_for_voice(
                restaurant_id=restaurant_id,
                caller_phone=state.caller_phone,
                customer_name=arguments.get("customer_name"),
                service_date=date.fromisoformat(arguments["date"]) if arguments.get("date") else None,
                requested_time=time.fromisoformat(arguments["time"]) if arguments.get("time") else None,
            )
            if not found.get("bookings"):
                return {"success": False, "reason": "Prenotazione non trovata."}
            reservation_id = found["bookings"][0]["id"]
        changes_date = date.fromisoformat(arguments["date"]) if arguments.get("date") else None
        changes_time = time.fromisoformat(arguments["time"]) if arguments.get("time") else None
        return modify_booking_for_voice(
            reservation_id=reservation_id,
            restaurant_id=restaurant_id,
            booking_date=changes_date,
            booking_time=changes_time,
            party_size=int(arguments["party_size"]) if arguments.get("party_size") else None,
            customer_name=arguments.get("customer_name"),
            caller_phone=state.caller_phone,
            special_requests=arguments.get("special_requests"),
        )

    if tool_name == "cancel_booking":
        reservation_id = str(arguments.get("confirmation_code") or "")
        if not reservation_id:
            found = find_bookings_for_voice(
                restaurant_id=restaurant_id,
                caller_phone=state.caller_phone,
                customer_name=arguments.get("customer_name"),
            )
            if not found.get("bookings"):
                return {"success": False, "reason": "Prenotazione non trovata."}
            reservation_id = found["bookings"][0]["id"]
        cancel_booking_for_voice(reservation_id=reservation_id)
        state.outcome = "booking_cancelled"
        state.terminal_write_success = True
        state.end_call_after_response = True
        return {"success": True}

    if tool_name == "escalate_to_human":
        state.outcome = "escalated"
        if state.twilio_call_sid.startswith("studio-"):
            return {"success": True, "transferred": True, "simulated": True, "reason": arguments.get("reason")}
        transferred = sync_transfer(restaurant=restaurant, call_sid=state.twilio_call_sid)
        return {"success": transferred, "transferred": transferred, "reason": arguments.get("reason")}

    return {"success": False, "reason": f"Tool sconosciuto: {tool_name}"}
