from __future__ import annotations

from datetime import date, datetime, time, timedelta
from typing import Any

import psycopg

from app.reservation_lab.allocation import list_allocation_options
from app.reservation_lab.db import fetch_all, fetch_one
from app.reservation_lab.reservations import _load_turns, _match_turn
from app.reservation_lab.utils import parse_time_value


def _slot_times_for_turns(turns: list[dict[str, Any]]) -> list[str]:
    slots: set[str] = set()
    for turn in turns:
        if turn.get("closed"):
            continue
        start = parse_time_value(turn["starts_at"])
        end = parse_time_value(turn["ends_at"])
        cur = datetime.combine(date.today(), start)
        end_dt = datetime.combine(date.today(), end)
        while cur < end_dt:
            slots.add(cur.strftime("%H:%M"))
            cur += timedelta(minutes=15)
    return sorted(slots)


def _option_label(candidate: dict[str, Any], tables_by_id: dict[str, dict[str, Any]]) -> str:
    if candidate["type"] == "single":
        tid = candidate["assigned_tables"][0]
        table = tables_by_id.get(tid) or {}
        name = table.get("table_name") or tid
        cap = table.get("max_capacity", "?")
        return f"Table {name} · {candidate['area']} · max {cap}"
    names = []
    for tid in candidate["assigned_tables"]:
        table = tables_by_id.get(tid) or {}
        names.append(str(table.get("table_name") or tid))
    max_cap = sum((tables_by_id.get(tid) or {}).get("max_capacity", 0) for tid in candidate["assigned_tables"])
    return f"{' + '.join(names)} · {candidate['area']} · merge · max {max_cap}"


def load_manual_reservation_bootstrap(
    conn: psycopg.Connection[Any],
    restaurant_id: str,
) -> dict[str, Any]:
    restaurant = fetch_one(
        conn,
        "SELECT restaurant_id, restaurant_name FROM restaurants WHERE restaurant_id = %s",
        (restaurant_id,),
    )
    if not restaurant:
        return {"error": "not_found"}

    areas = fetch_all(
        conn,
        """
        SELECT area_id, area_name
        FROM restaurant_areas
        WHERE restaurant_id = %s AND active = true
        ORDER BY sort_order, area_name
        """,
        (restaurant_id,),
    )
    return {
        "restaurant": restaurant,
        "areas": [{"area_id": a["area_id"], "area_name": a["area_name"]} for a in areas],
    }


def load_manual_reservation_options(
    conn: psycopg.Connection[Any],
    *,
    restaurant_id: str,
    service_date: date,
    requested_time: time | None = None,
    party_size: int | None = None,
    preferred_area: str | None = None,
    high_chairs_requested: int = 0,
) -> dict[str, Any]:
    restaurant = fetch_one(conn, "SELECT * FROM restaurants WHERE restaurant_id = %s", (restaurant_id,))
    if not restaurant:
        return {"error": "not_found"}

    turns = _load_turns(conn, restaurant_id, service_date)
    if turns and turns[0].get("closed"):
        return {
            "closed": True,
            "reason": turns[0].get("reason") or "Closed",
            "time_slots": [],
            "table_options": [],
        }

    time_slots = _slot_times_for_turns(turns)
    out: dict[str, Any] = {"time_slots": time_slots, "table_options": []}

    if requested_time is None or not party_size or party_size <= 0:
        return out

    matched_turn = _match_turn(requested_time, turns)
    if not matched_turn:
        out["reason"] = "No active service turn for the requested time."
        return out

    areas = fetch_all(
        conn,
        """
        SELECT area_id, area_name, sort_order, active
        FROM restaurant_areas
        WHERE restaurant_id = %s AND active = true
        ORDER BY sort_order
        """,
        (restaurant_id,),
    )
    tables = fetch_all(
        conn,
        """
        SELECT table_id, area_id, table_name, base_capacity, head_seats_max, max_capacity,
               high_chair_allowed, prefer_to_keep_free, fill_priority, active
        FROM restaurant_tables
        WHERE restaurant_id = %s AND active = true
        """,
        (restaurant_id,),
    )
    merges = fetch_all(
        conn,
        """
        SELECT merge_id, merge_tables, active
        FROM restaurant_table_merges
        WHERE restaurant_id = %s AND active = true
        """,
        (restaurant_id,),
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
        (restaurant_id, service_date, matched_turn["meal_period"], matched_turn["turn_index"]),
    )
    occupied_table_ids = {row["table_id"] for row in occupied_rows}
    tables_by_id = {t["table_id"]: t for t in tables}

    candidates = list_allocation_options(
        restaurant=restaurant,
        areas=areas,
        tables=tables,
        merges=merges,
        occupied_table_ids=occupied_table_ids,
        party_size=party_size,
        preferred_area=preferred_area,
        high_chairs_requested=high_chairs_requested,
        restrict_to_preferred_area=bool(preferred_area),
    )

    out["table_options"] = [
        {
            "option_id": (
                f"merge:{c['assigned_merge_id']}"
                if c["type"] == "merge"
                else f"single:{c['assigned_tables'][0]}"
            ),
            "label": _option_label(c, tables_by_id),
            "type": c["type"],
            "assigned_tables": c["assigned_tables"],
            "assigned_merge_id": c["assigned_merge_id"],
            "area": c["area"],
        }
        for c in candidates
    ]
    out["turn"] = matched_turn
    return out
