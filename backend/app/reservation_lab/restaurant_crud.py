from __future__ import annotations

import json
import uuid
from collections import defaultdict
from typing import Any

import psycopg

from app.reservation_lab.db import execute, fetch_all, fetch_one


def has_floor_layout_column(conn: psycopg.Connection[Any]) -> bool:
    row = fetch_one(
        conn,
        """
        SELECT 1 AS ok
        FROM information_schema.columns
        WHERE table_schema = 'public'
          AND table_name = 'restaurants'
          AND column_name = 'floor_layout'
        LIMIT 1
        """,
    )
    return row is not None


def list_restaurants(conn: psycopg.Connection[Any]) -> list[dict[str, Any]]:
    return fetch_all(
        conn,
        """
        SELECT r.restaurant_id, r.restaurant_name, r.business_id, b.business_name
        FROM restaurants r
        JOIN businesses b ON b.business_id = r.business_id
        ORDER BY r.restaurant_name
        """,
    )


def _merge_rows_to_mergeable(merge_rows: list[dict[str, Any]]) -> dict[str, set[str]]:
    adj: dict[str, set[str]] = defaultdict(set)
    for row in merge_rows:
        tables = row.get("merge_tables") or []
        if not isinstance(tables, list) or len(tables) < 2:
            continue
        for i, a in enumerate(tables):
            for b in tables:
                if a != b:
                    adj[str(a)].add(str(b))
    return adj


def load_bundle(conn: psycopg.Connection[Any], restaurant_id: str) -> dict[str, Any] | None:
    rest = fetch_one(
        conn,
        """
        SELECT r.*, b.business_name, b.business_type
        FROM restaurants r
        JOIN businesses b ON b.business_id = r.business_id
        WHERE r.restaurant_id = %s
        """,
        (restaurant_id,),
    )
    if not rest:
        return None

    areas = fetch_all(
        conn,
        """
        SELECT area_id, area_name, sort_order, active
        FROM restaurant_areas
        WHERE restaurant_id = %s
        ORDER BY sort_order, area_name
        """,
        (restaurant_id,),
    )
    area_name_by_id = {a["area_id"]: a["area_name"] for a in areas}

    tables_db = fetch_all(
        conn,
        """
        SELECT table_id, area_id, table_name, base_capacity, head_seats_max, max_capacity,
               movable_between_areas, high_chair_allowed, prefer_to_keep_free, fill_priority,
               allow_if_no_alternative, active
        FROM restaurant_tables
        WHERE restaurant_id = %s AND active = true
        ORDER BY table_name
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
    adj = _merge_rows_to_mergeable(merges)

    tables_ui: list[dict[str, Any]] = []
    floor_tables: dict[str, Any] = {}
    floor_layout = rest.get("floor_layout")
    if isinstance(floor_layout, str):
        try:
            floor_layout = json.loads(floor_layout)
        except json.JSONDecodeError:
            floor_layout = None
    if isinstance(floor_layout, dict):
        ft = floor_layout.get("tables")
        if isinstance(ft, dict):
            floor_tables = {str(k): v for k, v in ft.items() if isinstance(v, dict)}

    for t in tables_db:
        tid = str(t["table_id"])
        loc = area_name_by_id.get(t["area_id"], str(t["area_id"]))
        mw = sorted(adj.get(tid, set()))
        layout = floor_tables.get(tid)
        tables_ui.append(
            {
                "table_id": tid,
                "name": t["table_name"],
                "base_capacity": int(t["base_capacity"]),
                "location": loc,
                "special_attributes": [],
                "max_head_seat_extra_capacity": int(t["head_seats_max"] or 0),
                "movable_to_other_area": bool(t["movable_between_areas"]),
                "high_chair_compatible": bool(t["high_chair_allowed"]),
                "prefer_keep_free": bool(t["prefer_to_keep_free"]),
                "mergeable": len(mw) > 0,
                "merge_group_id": None,
                "mergeable_with": mw,
                "active": bool(t["active"]),
                "layout": layout
                if isinstance(layout, dict)
                else _default_layout_for_index(len(tables_ui)),
            }
        )

    opening = fetch_all(
        conn,
        """
        SELECT opening_time_id, day_of_week, meal_period, turn_index, starts_at::text, ends_at::text, active
        FROM restaurant_opening_times
        WHERE restaurant_id = %s AND active = true
        ORDER BY day_of_week, meal_period, turn_index
        """,
        (restaurant_id,),
    )

    floor_out: dict[str, Any] = {"tables": {}}
    for t in tables_ui:
        lay = t.get("layout")
        if isinstance(lay, dict) and all(k in lay for k in ("xPct", "yPct", "wPct", "hPct")):
            floor_out["tables"][t["table_id"]] = {
                "xPct": float(lay["xPct"]),
                "yPct": float(lay["yPct"]),
                "wPct": float(lay["wPct"]),
                "hPct": float(lay["hPct"]),
            }

    bundle: dict[str, Any] = {
        "restaurant_id": restaurant_id,
        "business": {
            "business_id": rest["business_id"],
            "business_name": rest["business_name"],
            "business_type": rest.get("business_type") or "restaurant",
        },
        "restaurant_name": rest["restaurant_name"],
        "areas": [{"area_id": a["area_id"], "area_name": a["area_name"], "sort_order": int(a["sort_order"])} for a in areas],
        "number_of_high_chairs": int(rest.get("high_chairs_inventory") or 0),
        "shift_definitions": [],
        "merge_allowed": bool(rest.get("table_merge_allowed", True)),
        "max_tables_per_merge": int(rest.get("max_tables_per_merge") or 2),
        "move_between_areas_allowed": bool(rest.get("move_tables_between_areas", False)),
        "max_capacity_equivalent_moves_allowed": int(rest.get("max_capacity_equivalent_moves") or 0),
        "split_group_across_multiple_tables_allowed": True,
        "shared_table_with_strangers_allowed": False,
        "allocation_strategy": rest.get("allocation_strategy") or "prefer_single_table",
        "default_location_fallback_allowed": bool(rest.get("fallback_to_other_areas_by_order", True)),
        "street": rest.get("street") or "",
        "street_number": rest.get("street_number") or "",
        "postal_code": rest.get("postal_code") or "",
        "city": rest.get("city") or "",
        "country": rest.get("country") or "",
        "timezone": rest.get("timezone") or "Europe/Rome",
        "email_address": rest.get("email_address") or "",
        "whatsapp_phone_number": rest.get("whatsapp_phone_number") or "",
        "tables": tables_ui,
        "floor_layout": floor_out,
        "opening_times": opening,
    }
    return bundle


def _default_layout_for_index(i: int) -> dict[str, float]:
    cols = 6
    row, col = divmod(i, cols)
    return {
        "xPct": 2.0 + col * 15.5,
        "yPct": 4.0 + row * 18.0,
        "wPct": 13.0,
        "hPct": 14.0,
    }


def default_new_bundle() -> dict[str, Any]:
    bid = "b_" + uuid.uuid4().hex[:10]
    rid = "r_" + uuid.uuid4().hex[:10]
    area_id = "area_" + uuid.uuid4().hex[:8]
    return {
        "restaurant_id": rid,
        "business": {"business_id": bid, "business_name": "", "business_type": "restaurant"},
        "restaurant_name": "",
        "areas": [{"area_id": area_id, "area_name": "Indoor", "sort_order": 0}],
        "number_of_high_chairs": 0,
        "shift_definitions": [],
        "merge_allowed": True,
        "max_tables_per_merge": 4,
        "move_between_areas_allowed": False,
        "max_capacity_equivalent_moves_allowed": 0,
        "split_group_across_multiple_tables_allowed": True,
        "shared_table_with_strangers_allowed": False,
        "allocation_strategy": "prefer_single_table",
        "default_location_fallback_allowed": True,
        "street": "",
        "street_number": "",
        "postal_code": "",
        "city": "",
        "country": "",
        "timezone": "Europe/Rome",
        "email_address": "",
        "whatsapp_phone_number": "",
        "tables": [
            {
                "table_id": "t1",
                "name": "Table 1",
                "base_capacity": 4,
                "location": "Indoor",
                "special_attributes": [],
                "max_head_seat_extra_capacity": 0,
                "movable_to_other_area": False,
                "high_chair_compatible": True,
                "prefer_keep_free": False,
                "mergeable": False,
                "merge_group_id": None,
                "mergeable_with": [],
                "active": True,
                "layout": _default_layout_for_index(0),
            }
        ],
        "floor_layout": {"tables": {"t1": dict(_default_layout_for_index(0))}},
        "opening_times": [],
    }


def _area_id_by_name(areas: list[dict[str, Any]]) -> dict[str, str]:
    out: dict[str, str] = {}
    for a in areas:
        name = (a.get("area_name") or "").strip()
        aid = (a.get("area_id") or "").strip()
        if name and aid:
            out[name] = aid
    return out


def _pairwise_merges(tables: list[dict[str, Any]]) -> list[frozenset[str]]:
    pairs: set[frozenset[str]] = set()
    for t in tables:
        tid = str(t.get("table_id") or "").strip()
        if not tid:
            continue
        for other in t.get("mergeable_with") or []:
            o = str(other).strip()
            if o and o != tid:
                pairs.add(frozenset({tid, o}))
    return sorted(pairs, key=lambda p: tuple(sorted(p)))


def save_bundle(conn: psycopg.Connection[Any], payload: dict[str, Any]) -> tuple[bool, str]:
    try:
        _save_bundle_tx(conn, payload)
    except Exception as exc:
        return False, str(exc)
    return True, "ok"


def _save_bundle_tx(conn: psycopg.Connection[Any], payload: dict[str, Any]) -> None:
    business = payload.get("business") or {}
    bid = str(business.get("business_id") or "").strip()
    bname = str(business.get("business_name") or "").strip()
    btype = str(business.get("business_type") or "restaurant").strip() or "restaurant"
    rid = str(payload.get("restaurant_id") or "").strip()
    rname = str(payload.get("restaurant_name") or "").strip()
    if not bid or not rid:
        raise ValueError("business_id and restaurant_id are required")
    if not bname or not rname:
        raise ValueError("business_name and restaurant_name are required")

    areas = payload.get("areas") or []
    if not isinstance(areas, list) or not areas:
        raise ValueError("At least one area is required")
    tables = payload.get("tables") or []
    if not isinstance(tables, list) or not tables:
        raise ValueError("At least one table is required")

    name_to_area_id = _area_id_by_name(areas)
    for t in tables:
        loc = str(t.get("location") or "").strip()
        if loc not in name_to_area_id:
            raise ValueError(f"Table {t.get('table_id')}: area/location {loc!r} not found in areas list")

    floor_layout = payload.get("floor_layout") or {"tables": {}}
    if not isinstance(floor_layout, dict):
        floor_layout = {"tables": {}}

    high_chairs = int(payload.get("number_of_high_chairs") or 0)
    merge_allowed = bool(payload.get("merge_allowed", True))
    max_merge = int(payload.get("max_tables_per_merge") or 2)
    move_areas = bool(payload.get("move_between_areas_allowed", False))
    max_moves = int(payload.get("max_capacity_equivalent_moves_allowed") or 0)
    fallback = bool(payload.get("default_location_fallback_allowed", True))
    strategy = str(payload.get("allocation_strategy") or "prefer_single_table").strip()

    with conn.transaction():
        execute(
            conn,
            """
            INSERT INTO businesses (business_id, business_type, business_name)
            VALUES (%s, %s, %s)
            ON CONFLICT (business_id) DO UPDATE SET
              business_name = EXCLUDED.business_name,
              business_type = EXCLUDED.business_type,
              updated_at = now()
            """,
            (bid, btype, bname),
        )

        execute(
            conn,
            """
            INSERT INTO restaurants (
              restaurant_id, business_id, restaurant_name, street, street_number, postal_code,
              city, country, timezone, high_chairs_inventory, allocation_strategy, table_merge_allowed,
              max_tables_per_merge, move_tables_between_areas, max_capacity_equivalent_moves,
              fallback_to_other_areas_by_order, email_address, whatsapp_phone_number
            ) VALUES (
              %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s
            )
            ON CONFLICT (restaurant_id) DO UPDATE SET
              business_id = EXCLUDED.business_id,
              restaurant_name = EXCLUDED.restaurant_name,
              street = EXCLUDED.street,
              street_number = EXCLUDED.street_number,
              postal_code = EXCLUDED.postal_code,
              city = EXCLUDED.city,
              country = EXCLUDED.country,
              timezone = EXCLUDED.timezone,
              high_chairs_inventory = EXCLUDED.high_chairs_inventory,
              allocation_strategy = EXCLUDED.allocation_strategy,
              table_merge_allowed = EXCLUDED.table_merge_allowed,
              max_tables_per_merge = EXCLUDED.max_tables_per_merge,
              move_tables_between_areas = EXCLUDED.move_tables_between_areas,
              max_capacity_equivalent_moves = EXCLUDED.max_capacity_equivalent_moves,
              fallback_to_other_areas_by_order = EXCLUDED.fallback_to_other_areas_by_order,
              email_address = EXCLUDED.email_address,
              whatsapp_phone_number = EXCLUDED.whatsapp_phone_number,
              updated_at = now()
            """,
            (
                rid,
                bid,
                rname,
                payload.get("street") or None,
                payload.get("street_number") or None,
                payload.get("postal_code") or None,
                payload.get("city") or None,
                payload.get("country") or None,
                str(payload.get("timezone") or "Europe/Rome"),
                high_chairs,
                strategy,
                merge_allowed,
                max_merge,
                move_areas,
                max_moves,
                fallback,
                payload.get("email_address") or None,
                payload.get("whatsapp_phone_number") or None,
            ),
        )

        if has_floor_layout_column(conn):
            execute(
                conn,
                "UPDATE restaurants SET floor_layout = %s::jsonb, updated_at = now() WHERE restaurant_id = %s",
                (json.dumps(floor_layout), rid),
            )

        execute(conn, "DELETE FROM restaurant_table_merges WHERE restaurant_id = %s", (rid,))
        execute(conn, "DELETE FROM restaurant_tables WHERE restaurant_id = %s", (rid,))
        execute(conn, "DELETE FROM restaurant_areas WHERE restaurant_id = %s", (rid,))

        for a in sorted(areas, key=lambda x: int(x.get("sort_order") or 0)):
            aid = str(a.get("area_id") or "").strip()
            aname = str(a.get("area_name") or "").strip()
            so = int(a.get("sort_order") or 0)
            if not aid or not aname:
                continue
            execute(
                conn,
                """
                INSERT INTO restaurant_areas (area_id, restaurant_id, area_name, sort_order, active)
                VALUES (%s, %s, %s, %s, true)
                """,
                (aid, rid, aname, so),
            )

        for t in tables:
            tid = str(t.get("table_id") or "").strip()
            if not tid:
                continue
            loc = str(t.get("location") or "").strip()
            area_id = name_to_area_id[loc]
            base_cap = int(t.get("base_capacity") or 1)
            head_max = int(t.get("max_head_seat_extra_capacity") or 0)
            max_cap = base_cap + head_max
            execute(
                conn,
                """
                INSERT INTO restaurant_tables (
                  table_id, restaurant_id, area_id, table_name, base_capacity, head_seats_max, max_capacity,
                  movable_between_areas, high_chair_allowed, prefer_to_keep_free, fill_priority,
                  allow_if_no_alternative, active
                ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,true)
                """,
                (
                    tid,
                    rid,
                    area_id,
                    str(t.get("name") or tid),
                    base_cap,
                    head_max,
                    max_cap,
                    bool(t.get("movable_to_other_area")),
                    bool(t.get("high_chair_compatible", True)),
                    bool(t.get("prefer_keep_free")),
                    "last" if t.get("prefer_keep_free") else "normal",
                    bool(t.get("allow_if_no_alternative", True)),
                ),
            )

        for pair in _pairwise_merges(tables):
            a, b = tuple(sorted(pair))
            merge_id = "mg_" + uuid.uuid4().hex[:16]
            execute(
                conn,
                """
                INSERT INTO restaurant_table_merges (merge_id, restaurant_id, merge_tables, active)
                VALUES (%s, %s, %s, true)
                """,
                (merge_id, rid, [a, b]),
            )
