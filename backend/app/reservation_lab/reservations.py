from __future__ import annotations

from datetime import date
from typing import Any

import psycopg

from app.reservation_lab.allocation import find_best_allocation
from app.reservation_lab.db import execute, fetch_all, fetch_one
from app.reservation_lab.models import AgentResponse, BookingRequest, DBReservationPayload
from app.reservation_lab.utils import generate_id, iso_weekday_name, parse_time_value


def _load_turns(conn: psycopg.Connection[Any], restaurant_id: str, service_date: date) -> list[dict[str, Any]]:
    special_day = fetch_one(
        conn,
        """
        SELECT is_closed, override_opening_times, reason
        FROM restaurant_special_days
        WHERE restaurant_id = %s AND service_date = %s
        """,
        (restaurant_id, service_date),
    )
    if special_day and special_day["is_closed"]:
        return [{"closed": True, "reason": special_day.get("reason") or "Restaurant is closed on this date."}]

    if special_day and special_day.get("override_opening_times"):
        turns = special_day["override_opening_times"]
        if isinstance(turns, list):
            return turns

    day_of_week = iso_weekday_name(service_date)
    return fetch_all(
        conn,
        """
        SELECT meal_period, turn_index, starts_at, ends_at
        FROM restaurant_opening_times
        WHERE restaurant_id = %s AND day_of_week = %s AND active = true
        ORDER BY meal_period, turn_index
        """,
        (restaurant_id, day_of_week),
    )


def _match_turn(request_time, turns: list[dict[str, Any]]) -> dict[str, Any] | None:
    for turn in turns:
        if turn.get("closed"):
            continue
        starts_at = parse_time_value(turn["starts_at"])
        ends_at = parse_time_value(turn["ends_at"])
        if starts_at <= request_time < ends_at:
            return {
                "meal_period": turn["meal_period"],
                "turn_index": int(turn["turn_index"]),
                "starts_at": starts_at.isoformat(),
                "ends_at": ends_at.isoformat(),
            }
    return None


def _build_payload(
    request: BookingRequest, turn: dict[str, Any], allocation: dict[str, Any], reservation_id: str | None
) -> DBReservationPayload:
    return DBReservationPayload(
        reservation_id=reservation_id,
        restaurant_id=request.restaurant_id,
        service_date=request.date,
        requested_time=request.time,
        meal_period=turn["meal_period"],
        turn_index=turn["turn_index"],
        customer_name=request.customer_name,
        phone_number=request.phone_number,
        party_size=request.party_size,
        assigned_tables=allocation["assigned_tables"],
        assigned_merge_id=allocation["assigned_merge_id"],
        head_seats_used=allocation["head_seats_used"],
        preferences=request.preferences.model_dump(),
        status="pending",
    )


def compute_availability(conn: psycopg.Connection[Any], request: BookingRequest) -> dict[str, Any]:
    restaurant = fetch_one(
        conn,
        """
        SELECT *
        FROM restaurants
        WHERE restaurant_id = %s
        """,
        (request.restaurant_id,),
    )
    if not restaurant:
        return {
            "agent_response": AgentResponse(available=False, area=None, head_seats_used=None).model_dump(),
            "db_preview": None,
            "reason": "Restaurant not found.",
            "debug": {},
        }

    turns = _load_turns(conn, request.restaurant_id, request.date)
    if turns and turns[0].get("closed"):
        return {
            "agent_response": AgentResponse(available=False, area=None, head_seats_used=None).model_dump(),
            "db_preview": None,
            "reason": turns[0].get("reason") or "Restaurant is closed on this date.",
            "debug": {"selected_turn": None, "occupied_tables": [], "candidate_tables": [], "chosen_solution": None},
        }

    matched_turn = _match_turn(request.time, turns)
    if not matched_turn:
        return {
            "agent_response": AgentResponse(available=False, area=None, head_seats_used=None).model_dump(),
            "db_preview": None,
            "reason": "No active service turn for the requested time.",
            "debug": {"selected_turn": None, "occupied_tables": [], "candidate_tables": [], "chosen_solution": None},
        }

    areas = fetch_all(
        conn,
        """
        SELECT area_id, area_name, sort_order, active
        FROM restaurant_areas
        WHERE restaurant_id = %s AND active = true
        ORDER BY sort_order
        """,
        (request.restaurant_id,),
    )
    tables = fetch_all(
        conn,
        """
        SELECT table_id, area_id, table_name, base_capacity, head_seats_max, max_capacity,
               high_chair_allowed, prefer_to_keep_free, fill_priority, active
        FROM restaurant_tables
        WHERE restaurant_id = %s AND active = true
        """,
        (request.restaurant_id,),
    )
    merges = fetch_all(
        conn,
        """
        SELECT merge_id, merge_tables, active
        FROM restaurant_table_merges
        WHERE restaurant_id = %s AND active = true
        """,
        (request.restaurant_id,),
    )
    occupied_rows = fetch_all(
        conn,
        """
        SELECT table_id
        FROM reservation_table_assignments
        WHERE restaurant_id = %s
          AND service_date = %s
          AND meal_period = %s
          AND turn_index = %s
        """,
        (request.restaurant_id, request.date, matched_turn["meal_period"], matched_turn["turn_index"]),
    )
    occupied_table_ids = {row["table_id"] for row in occupied_rows}
    allocation = find_best_allocation(
        restaurant=restaurant,
        areas=areas,
        tables=tables,
        merges=merges,
        occupied_table_ids=occupied_table_ids,
        party_size=request.party_size,
        preferred_area=request.preferences.preferred_area,
        high_chairs_requested=request.high_chairs_requested,
    )

    if not allocation["available"]:
        debug = allocation.get("debug", {})
        debug.update(
            {
                "selected_turn": matched_turn,
                "occupied_tables": sorted(list(occupied_table_ids)),
                "chosen_solution": None,
            }
        )
        return {
            "agent_response": AgentResponse(available=False, area=None, head_seats_used=None).model_dump(),
            "db_preview": None,
            "reason": allocation["reason"],
            "debug": debug,
        }

    payload = _build_payload(request, matched_turn, allocation, reservation_id=None)
    debug = allocation.get("debug", {})
    debug.update(
        {
            "selected_turn": matched_turn,
            "occupied_tables": sorted(list(occupied_table_ids)),
            "chosen_solution": {
                "type": allocation["type"],
                "assigned_tables": allocation["assigned_tables"],
                "assigned_merge_id": allocation["assigned_merge_id"],
                "score": allocation["score"],
            },
        }
    )
    return {
        "agent_response": AgentResponse(
            available=True, area=allocation["area"], head_seats_used=allocation["head_seats_used"]
        ).model_dump(),
        "db_preview": payload.model_dump(),
        "debug": debug,
    }


def create_reservation(conn: psycopg.Connection[Any], request: BookingRequest) -> dict[str, Any]:
    for _attempt in range(2):
        result = compute_availability(conn, request)
        if not result["agent_response"]["available"]:
            return {"agent_response": result["agent_response"], "db_saved": None, "reason": result.get("reason"), "debug": result.get("debug")}

        preview = result["db_preview"]
        reservation_id = generate_id("res")
        assignments = preview["assigned_tables"]
        assignment_rows = [
            {
                "assignment_id": generate_id("asg"),
                "table_id": table_id,
                "is_primary_table": idx == 0,
                "seats_assigned": request.party_size if idx == 0 else 0,
            }
            for idx, table_id in enumerate(assignments)
        ]

        try:
            with conn.transaction():
                execute(
                    conn,
                    """
                    INSERT INTO restaurant_reservations (
                      reservation_id, restaurant_id, service_date, requested_time, meal_period, turn_index,
                      customer_name, phone_number, party_size, high_chairs_requested, head_seats_used,
                      preferences, original_agent_text, source_payload, status, pending_expires_at
                    ) VALUES (
                      %s, %s, %s, %s, %s, %s,
                      %s, %s, %s, %s, %s,
                      %s::jsonb, %s, %s::jsonb, %s, now() + interval '5 minutes'
                    )
                    """,
                    (
                        reservation_id,
                        preview["restaurant_id"],
                        preview["service_date"],
                        preview["requested_time"],
                        preview["meal_period"],
                        preview["turn_index"],
                        preview["customer_name"],
                        preview["phone_number"],
                        preview["party_size"],
                        request.high_chairs_requested,
                        preview["head_seats_used"],
                        psycopg.types.json.Json(preview["preferences"]),
                        request.original_agent_text,
                        psycopg.types.json.Json(request.model_dump(mode="json")),
                        "pending",
                    ),
                )
                for row in assignment_rows:
                    execute(
                        conn,
                        """
                        INSERT INTO reservation_table_assignments (
                          assignment_id, reservation_id, restaurant_id, service_date,
                          meal_period, turn_index, table_id, merge_id, seats_assigned, is_primary_table
                        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                        """,
                        (
                            row["assignment_id"],
                            reservation_id,
                            preview["restaurant_id"],
                            preview["service_date"],
                            preview["meal_period"],
                            preview["turn_index"],
                            row["table_id"],
                            preview["assigned_merge_id"],
                            row["seats_assigned"],
                            row["is_primary_table"],
                        ),
                    )

            saved = dict(preview)
            saved["reservation_id"] = reservation_id
            return {
                "agent_response": result["agent_response"],
                "db_saved": saved,
                "debug": result.get("debug"),
            }
        except psycopg.errors.UniqueViolation:
            conn.rollback()
            continue

    return {
        "agent_response": AgentResponse(available=False, area=None, head_seats_used=None).model_dump(),
        "db_saved": None,
        "reason": "No available table or merge for this party size.",
        "debug": None,
    }


def create_pending_reservation(conn: psycopg.Connection[Any], request: BookingRequest) -> dict[str, Any]:
    for _attempt in range(2):
        result = compute_availability(conn, request)
        if not result["agent_response"]["available"]:
            return {"agent_response": result["agent_response"], "db_saved": None, "reason": result.get("reason"), "debug": result.get("debug")}

        preview = result["db_preview"]
        reservation_id = generate_id("res")
        assignments = preview["assigned_tables"]
        assignment_rows = [
            {
                "assignment_id": generate_id("asg"),
                "table_id": table_id,
                "is_primary_table": idx == 0,
                "seats_assigned": request.party_size if idx == 0 else 0,
            }
            for idx, table_id in enumerate(assignments)
        ]

        try:
            with conn.transaction():
                execute(
                    conn,
                    """
                    INSERT INTO restaurant_reservations (
                      reservation_id, restaurant_id, service_date, requested_time, meal_period, turn_index,
                      customer_name, phone_number, party_size, high_chairs_requested, head_seats_used,
                      preferences, original_agent_text, source_payload, status, pending_expires_at
                    ) VALUES (
                      %s, %s, %s, %s, %s, %s,
                      %s, %s, %s, %s, %s,
                      %s::jsonb, %s, %s::jsonb, %s, now() + interval '5 minutes'
                    )
                    """,
                    (
                        reservation_id,
                        preview["restaurant_id"],
                        preview["service_date"],
                        preview["requested_time"],
                        preview["meal_period"],
                        preview["turn_index"],
                        preview["customer_name"],
                        preview["phone_number"],
                        preview["party_size"],
                        request.high_chairs_requested,
                        preview["head_seats_used"],
                        psycopg.types.json.Json(preview["preferences"]),
                        request.original_agent_text,
                        psycopg.types.json.Json(request.model_dump(mode="json")),
                        "pending",
                    ),
                )
                for row in assignment_rows:
                    execute(
                        conn,
                        """
                        INSERT INTO reservation_table_assignments (
                          assignment_id, reservation_id, restaurant_id, service_date,
                          meal_period, turn_index, table_id, merge_id, seats_assigned, is_primary_table
                        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                        """,
                        (
                            row["assignment_id"],
                            reservation_id,
                            preview["restaurant_id"],
                            preview["service_date"],
                            preview["meal_period"],
                            preview["turn_index"],
                            row["table_id"],
                            preview["assigned_merge_id"],
                            row["seats_assigned"],
                            row["is_primary_table"],
                        ),
                    )

            saved = dict(preview)
            saved["reservation_id"] = reservation_id
            saved["status"] = "pending"
            return {
                "agent_response": result["agent_response"],
                "db_saved": saved,
                "debug": result.get("debug"),
            }
        except psycopg.errors.UniqueViolation:
            conn.rollback()
            continue

    return {
        "agent_response": AgentResponse(available=False, area=None, head_seats_used=None).model_dump(),
        "db_saved": None,
        "reason": "No available table or merge for this party size.",
        "debug": None,
    }


def confirm_pending_reservation(conn: psycopg.Connection[Any], reservation_id: str, request: BookingRequest) -> dict[str, Any]:
    if not reservation_id:
        return {
            "agent_response": AgentResponse(available=False, area=None, head_seats_used=None).model_dump(),
            "db_saved": None,
            "reason": "Missing reservation_id for confirmation.",
            "debug": None,
        }

    with conn.transaction():
        row = fetch_one(
            conn,
            """
            SELECT reservation_id, restaurant_id, service_date, requested_time, meal_period, turn_index,
                   party_size, head_seats_used, high_chairs_requested, preferences, status, phone_number
            FROM restaurant_reservations
            WHERE reservation_id = %s
            """,
            (reservation_id,),
        )
        if not row:
            return {
                "agent_response": AgentResponse(available=False, area=None, head_seats_used=None).model_dump(),
                "db_saved": None,
                "reason": "Pending reservation not found.",
                "debug": None,
            }
        if row["status"] != "pending":
            return {
                "agent_response": AgentResponse(available=False, area=None, head_seats_used=None).model_dump(),
                "db_saved": None,
                "reason": f"Reservation is already {row['status']}.",
                "debug": None,
            }

        execute(
            conn,
            """
            UPDATE restaurant_reservations
            SET customer_name = %s,
                phone_number = %s,
                original_agent_text = %s,
                source_payload = %s::jsonb,
                status = 'confirmed',
                updated_at = now()
            WHERE reservation_id = %s
            """,
            (
                request.customer_name,
                request.phone_number,
                request.original_agent_text,
                psycopg.types.json.Json(request.model_dump(mode="json")),
                reservation_id,
            ),
        )

    assigned = fetch_all(
        conn,
        """
        SELECT table_id, merge_id
        FROM reservation_table_assignments
        WHERE reservation_id = %s
        ORDER BY is_primary_table DESC, table_id
        """,
        (reservation_id,),
    )
    assigned_tables = [x["table_id"] for x in assigned]
    merge_id = assigned[0]["merge_id"] if assigned else None
    preferences = row["preferences"] if isinstance(row["preferences"], dict) else {}

    saved = {
        "reservation_id": reservation_id,
        "restaurant_id": row["restaurant_id"],
        "service_date": row["service_date"],
        "requested_time": row["requested_time"],
        "meal_period": row["meal_period"],
        "turn_index": row["turn_index"],
        "customer_name": request.customer_name,
        "phone_number": request.phone_number,
        "party_size": row["party_size"],
        "assigned_tables": assigned_tables,
        "assigned_merge_id": merge_id,
        "head_seats_used": row["head_seats_used"],
        "preferences": preferences,
        "status": "confirmed",
    }
    area = request.preferences.preferred_area if request.preferences.preferred_area else None
    return {
        "agent_response": AgentResponse(available=True, area=area, head_seats_used=row["head_seats_used"]).model_dump(),
        "db_saved": saved,
        "debug": {"confirmed_reservation_id": reservation_id},
    }


def cancel_reservation(conn: psycopg.Connection[Any], reservation_id: str) -> dict[str, Any]:
    with conn.transaction():
        execute(
            conn,
            """
            UPDATE restaurant_reservations
            SET status = 'cancelled', updated_at = now()
            WHERE reservation_id = %s
            """,
            (reservation_id,),
        )
        execute(
            conn,
            """
            DELETE FROM reservation_table_assignments
            WHERE reservation_id = %s
            """,
            (reservation_id,),
        )
    return {"status": "cancelled", "reservation_id": reservation_id}


def list_reservations(conn: psycopg.Connection[Any], restaurant_id: str, service_date: date) -> list[dict[str, Any]]:
    rows = fetch_all(
        conn,
        """
        SELECT r.reservation_id, r.restaurant_id, r.service_date, r.requested_time, r.meal_period,
               r.turn_index, r.customer_name, r.phone_number, r.party_size, r.high_chairs_requested,
               r.head_seats_used, r.preferences, r.status,
               COALESCE(array_agg(a.table_id) FILTER (WHERE a.table_id IS NOT NULL), ARRAY[]::text[]) AS assigned_tables,
               max(a.merge_id) AS assigned_merge_id
        FROM restaurant_reservations r
        LEFT JOIN reservation_table_assignments a ON a.reservation_id = r.reservation_id
        WHERE r.restaurant_id = %s AND r.service_date = %s
        GROUP BY r.reservation_id
        ORDER BY r.requested_time
        """,
        (restaurant_id, service_date),
    )
    return rows

