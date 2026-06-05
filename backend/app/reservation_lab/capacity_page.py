from __future__ import annotations

from datetime import date
from typing import Any

import psycopg

from app.reservation_lab.db import fetch_all, fetch_one
from app.reservation_lab.reservations import _load_turns
from app.reservation_lab.seat_diagram import area_stroke, build_seat_diagram_html
from app.reservation_lab.utils import parse_time_value


def _fmt_time_range(starts: Any, ends: Any) -> str:
    try:
        sa = parse_time_value(starts)
        ea = parse_time_value(ends)
        return f"{sa.strftime('%H:%M')}–{ea.strftime('%H:%M')}"
    except Exception:
        return ""


def turn_choices(turns: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for t in turns:
        if t.get("closed"):
            continue
        mp = str(t["meal_period"])
        ti = int(t["turn_index"])
        rng = _fmt_time_range(t.get("starts_at"), t.get("ends_at"))
        label = f"{mp} · turn {ti}"
        if rng:
            label = f"{mp} ({rng})"
        out.append({"meal_period": mp, "turn_index": ti, "label": label, "key": f"{mp}|{ti}"})
    return out


def load_capacity_context(
    conn: psycopg.Connection[Any],
    restaurant_id: str,
    service_date: date,
    meal_period: str | None,
    turn_index: int | None,
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

    turns = _load_turns(conn, restaurant_id, service_date)
    if turns and turns[0].get("closed"):
        return {
            "restaurant": rest,
            "closed": True,
            "reason": turns[0].get("reason") or "Closed",
            "service_date": service_date,
        }

    choices = turn_choices(turns)
    if not choices:
        return {"restaurant": rest, "no_turns": True, "service_date": service_date}

    sel_mp, sel_ti = meal_period, turn_index
    valid_keys = {c["key"] for c in choices}
    if sel_mp is None or sel_ti is None:
        sel_mp, sel_ti = choices[0]["meal_period"], choices[0]["turn_index"]
    cand_key = f"{sel_mp}|{sel_ti}"
    if cand_key not in valid_keys:
        sel_mp, sel_ti = choices[0]["meal_period"], choices[0]["turn_index"]

    total_row = fetch_one(
        conn,
        """
        SELECT COALESCE(SUM(base_capacity), 0)::int AS total_base
        FROM restaurant_tables
        WHERE restaurant_id = %s AND active = true
        """,
        (restaurant_id,),
    )
    total_base = int(total_row["total_base"] or 0) if total_row else 0

    res_rows = fetch_all(
        conn,
        """
        SELECT r.reservation_id, r.customer_name, r.phone_number, r.requested_time, r.party_size,
               r.high_chairs_requested, r.head_seats_used, r.status,
               COALESCE(array_agg(a.table_id ORDER BY a.is_primary_table DESC, a.table_id)
                 FILTER (WHERE a.table_id IS NOT NULL), ARRAY[]::text[]) AS assigned_tables,
               max(a.merge_id) AS merge_id
        FROM restaurant_reservations r
        LEFT JOIN reservation_table_assignments a
          ON a.reservation_id = r.reservation_id
         AND a.restaurant_id = r.restaurant_id
         AND a.service_date = r.service_date
         AND a.meal_period = r.meal_period
         AND a.turn_index = r.turn_index
        WHERE r.restaurant_id = %s
          AND r.service_date = %s
          AND r.meal_period = %s
          AND r.turn_index = %s
          AND r.status IN ('confirmed', 'pending')
        GROUP BY r.reservation_id
        ORDER BY r.requested_time
        """,
        (restaurant_id, service_date, sel_mp, sel_ti),
    )

    blocking = [r for r in res_rows if r["status"] in ("confirmed", "pending")]
    occupied_seats = sum(int(r["party_size"] or 0) for r in blocking)
    pending_count = sum(1 for r in res_rows if r["status"] == "pending")
    confirmed_rows = [r for r in res_rows if r["status"] == "confirmed"]

    free_base = max(0, total_base - occupied_seats)

    areas = fetch_all(
        conn,
        """
        SELECT area_id, area_name, sort_order
        FROM restaurant_areas
        WHERE restaurant_id = %s AND active = true
        ORDER BY sort_order, area_name
        """,
        (restaurant_id,),
    )
    tables = fetch_all(
        conn,
        """
        SELECT t.table_id, t.area_id, t.table_name, t.base_capacity, t.head_seats_max
        FROM restaurant_tables t
        WHERE t.restaurant_id = %s AND t.active = true
        ORDER BY t.table_name
        """,
        (restaurant_id,),
    )
    res_by_table: dict[str, dict[str, Any]] = {}
    for r in res_rows:
        tids = [str(x) for x in (r.get("assigned_tables") or []) if x]
        for tid in tids:
            rr = dict(r)
            rr["all_tables"] = tids
            res_by_table[tid] = rr

    def build_zone_items(zone_tables: list[dict[str, Any]]) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        consumed: set[str] = set()
        ids_in_zone = {str(z["table_id"]) for z in zone_tables}
        for t in zone_tables:
            tid = str(t["table_id"])
            if tid in consumed:
                continue
            res = res_by_table.get(tid)
            if not res:
                items.append({"kind": "single", "table": t, "res": None})
                consumed.add(tid)
                continue
            grp = [str(x) for x in res["all_tables"] if str(x) in ids_in_zone]
            if len(grp) <= 1:
                items.append({"kind": "single", "table": t, "res": res})
                consumed.add(tid)
            else:
                t_objs = [z for z in zone_tables if str(z["table_id"]) in set(grp)]
                for z in t_objs:
                    consumed.add(str(z["table_id"]))
                items.append({"kind": "merge", "tables": t_objs, "res": res})
        return items

    zones: list[dict[str, Any]] = []
    for a in areas:
        aid = a["area_id"]
        zone_tables = [t for t in tables if t["area_id"] == aid]
        zones.append({"area_name": a["area_name"], "zone_items": build_zone_items(zone_tables)})

    known_area_ids = {a["area_id"] for a in areas}
    orphans = [t for t in tables if t["area_id"] not in known_area_ids]
    if orphans:
        zones.append({"area_name": "__other__", "zone_items": build_zone_items(orphans)})

    area_names_ordered = [a["area_name"] for a in areas]

    def enrich_zone_items(zone_name: str, items: list[dict[str, Any]]) -> None:
        stroke = area_stroke(area_names_ordered, zone_name)
        for it in items:
            if it["kind"] == "single":
                t = it["table"]
                res = it["res"]
                # Seat diagram only when this table has a reservation for this turn.
                # Otherwise the bottom chair row looks like a party is seated (layout mirrors setup UI).
                if not res:
                    it["diagram_html"] = None
                    continue
                head = min(2, int(res.get("head_seats_used") or 0), int(t.get("head_seats_max") or 0))
                it["diagram_html"] = build_seat_diagram_html(int(t["base_capacity"]), stroke, head)
                continue
            res = it["res"]
            at = [str(x) for x in (res.get("assigned_tables") or [])] if res else []
            tables_by_id = {str(x["table_id"]): x for x in it["tables"]}
            ordered_tbls = [tables_by_id[tid] for tid in at if tid in tables_by_id]
            if not ordered_tbls:
                ordered_tbls = list(it["tables"])
            primary_tid = at[0] if at else None
            parts: list[str] = []
            for t in ordered_tbls:
                tid = str(t["table_id"])
                head = 0
                if res and primary_tid is not None and tid == str(primary_tid):
                    head = min(2, int(res.get("head_seats_used") or 0), int(t.get("head_seats_max") or 0))
                parts.append(build_seat_diagram_html(int(t["base_capacity"]), stroke, head))
            it["diagram_html"] = (
                '<div class="lab-capacity-merge-diagrams">'
                + "".join(f'<div class="lab-capacity-mini-diagram">{p}</div>' for p in parts)
                + "</div>"
            )

    for z in zones:
        enrich_zone_items(z["area_name"], z["zone_items"])

    return {
        "restaurant": rest,
        "service_date": service_date,
        "turn_choices": choices,
        "meal_period": sel_mp,
        "turn_index": sel_ti,
        "total_base": total_base,
        "occupied_seats": occupied_seats,
        "free_base": free_base,
        "pending_count": pending_count,
        "reservations_all": res_rows,
        "reservations_confirmed": confirmed_rows,
        "zones": zones,
    }
