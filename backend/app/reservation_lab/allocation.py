from __future__ import annotations

from typing import Any


FILL_PRIORITY_WEIGHT = {"first": 0, "normal": 1, "last": 2}


def _matches_preferred(area: dict[str, Any], preferred_area: str | None) -> bool:
    if not preferred_area:
        return True
    preferred = preferred_area.strip().lower()
    return area["area_name"].strip().lower() == preferred or area["area_id"].strip().lower() == preferred


def _build_area_order(
    restaurant: dict[str, Any], areas: list[dict[str, Any]], preferred_area: str | None
) -> list[dict[str, Any]]:
    if not preferred_area:
        return areas

    preferred = [a for a in areas if _matches_preferred(a, preferred_area)]
    others = [a for a in areas if a not in preferred]
    if preferred:
        if restaurant.get("fallback_to_other_areas_by_order", True):
            return preferred + others
        return preferred
    return areas


def _score_candidate(candidate: dict[str, Any], preferred_area: str | None) -> int:
    score = 100
    score -= candidate["wasted_seats"] * 5
    if candidate["type"] == "merge":
        score -= 20
    score -= candidate["head_seats_used"] * 10
    if candidate["uses_prefer_keep_free"]:
        score -= 25
    if preferred_area and not candidate["is_preferred_area"]:
        score -= 10
    return score


def find_best_allocation(
    restaurant: dict[str, Any],
    areas: list[dict[str, Any]],
    tables: list[dict[str, Any]],
    merges: list[dict[str, Any]],
    occupied_table_ids: set[str],
    party_size: int,
    preferred_area: str | None,
    high_chairs_requested: int,
) -> dict[str, Any]:
    tables_by_id = {t["table_id"]: t for t in tables if t["active"]}
    area_order = _build_area_order(restaurant, areas, preferred_area)
    debug: dict[str, Any] = {
        "area_order": [a["area_name"] for a in area_order],
        "candidate_tables": [],
        "candidate_merges": [],
    }

    for area in area_order:
        area_id = area["area_id"]
        is_preferred_area = _matches_preferred(area, preferred_area) if preferred_area else True
        single_candidates: list[dict[str, Any]] = []

        for table in tables:
            if not table["active"] or table["table_id"] in occupied_table_ids:
                continue
            if table["area_id"] != area_id:
                continue
            if table["max_capacity"] < party_size:
                continue
            if high_chairs_requested > 0 and not table["high_chair_allowed"]:
                continue

            head_seats_used = max(0, party_size - table["base_capacity"])
            if head_seats_used > table["head_seats_max"]:
                continue

            wasted_seats = table["max_capacity"] - party_size
            candidate = {
                "type": "single",
                "assigned_tables": [table["table_id"]],
                "assigned_merge_id": None,
                "area": area["area_name"],
                "head_seats_used": head_seats_used,
                "wasted_seats": wasted_seats,
                "uses_prefer_keep_free": bool(table["prefer_to_keep_free"]),
                "is_preferred_area": is_preferred_area,
                "sort_key": (
                    0 if not table["prefer_to_keep_free"] else 1,
                    FILL_PRIORITY_WEIGHT.get(table["fill_priority"], 1),
                    table["max_capacity"],
                    wasted_seats,
                ),
            }
            candidate["score"] = _score_candidate(candidate, preferred_area)
            single_candidates.append(candidate)

        single_candidates.sort(key=lambda c: c["sort_key"])
        debug["candidate_tables"].extend(single_candidates)
        if single_candidates:
            best = max(single_candidates, key=lambda c: c["score"])
            return {"available": True, **best, "debug": debug}

        if not restaurant.get("table_merge_allowed", True):
            continue

        merge_candidates: list[dict[str, Any]] = []
        for merge in merges:
            if not merge["active"]:
                continue
            merge_table_ids: list[str] = merge["merge_tables"]
            if len(merge_table_ids) > restaurant.get("max_tables_per_merge", 2):
                continue
            if any(table_id in occupied_table_ids for table_id in merge_table_ids):
                continue

            merge_tables = [tables_by_id.get(tid) for tid in merge_table_ids]
            if any(t is None for t in merge_tables):
                continue

            merge_area_ids = {t["area_id"] for t in merge_tables if t}
            if len(merge_area_ids) != 1 or area_id not in merge_area_ids:
                continue

            if high_chairs_requested > 0 and not any(t["high_chair_allowed"] for t in merge_tables if t):
                continue

            merge_capacity = sum(t["base_capacity"] for t in merge_tables if t)
            merge_max_capacity = sum(t["max_capacity"] for t in merge_tables if t)
            total_head_seats_max = sum(t["head_seats_max"] for t in merge_tables if t)
            if merge_max_capacity < party_size:
                continue

            head_seats_used = max(0, party_size - merge_capacity)
            if head_seats_used > total_head_seats_max:
                continue

            wasted_seats = merge_max_capacity - party_size
            uses_prefer_keep_free = any(t["prefer_to_keep_free"] for t in merge_tables if t)
            candidate = {
                "type": "merge",
                "assigned_tables": merge_table_ids,
                "assigned_merge_id": merge["merge_id"],
                "area": area["area_name"],
                "head_seats_used": head_seats_used,
                "wasted_seats": wasted_seats,
                "uses_prefer_keep_free": uses_prefer_keep_free,
                "is_preferred_area": is_preferred_area,
                "sort_key": (
                    merge_max_capacity,
                    len(merge_table_ids),
                    head_seats_used,
                    1 if uses_prefer_keep_free else 0,
                    wasted_seats,
                ),
            }
            candidate["score"] = _score_candidate(candidate, preferred_area)
            merge_candidates.append(candidate)

        merge_candidates.sort(key=lambda c: c["sort_key"])
        debug["candidate_merges"].extend(merge_candidates)
        if merge_candidates:
            best = max(merge_candidates, key=lambda c: c["score"])
            return {"available": True, **best, "debug": debug}

    return {
        "available": False,
        "reason": "No available table or merge for this party size.",
        "debug": debug,
    }


def list_allocation_options(
    restaurant: dict[str, Any],
    areas: list[dict[str, Any]],
    tables: list[dict[str, Any]],
    merges: list[dict[str, Any]],
    occupied_table_ids: set[str],
    party_size: int,
    preferred_area: str | None,
    high_chairs_requested: int,
    *,
    restrict_to_preferred_area: bool = False,
) -> list[dict[str, Any]]:
    tables_by_id = {t["table_id"]: t for t in tables if t["active"]}
    if restrict_to_preferred_area and preferred_area:
        area_order = [a for a in areas if _matches_preferred(a, preferred_area)]
    else:
        area_order = _build_area_order(restaurant, areas, preferred_area)
    all_candidates: list[dict[str, Any]] = []

    for area in area_order:
        area_id = area["area_id"]
        is_preferred_area = _matches_preferred(area, preferred_area) if preferred_area else True

        for table in tables:
            if not table["active"] or table["table_id"] in occupied_table_ids:
                continue
            if table["area_id"] != area_id:
                continue
            if table["max_capacity"] < party_size:
                continue
            if high_chairs_requested > 0 and not table["high_chair_allowed"]:
                continue
            head_seats_used = max(0, party_size - table["base_capacity"])
            if head_seats_used > table["head_seats_max"]:
                continue
            wasted_seats = table["max_capacity"] - party_size
            candidate = {
                "type": "single",
                "assigned_tables": [table["table_id"]],
                "assigned_merge_id": None,
                "area": area["area_name"],
                "head_seats_used": head_seats_used,
                "wasted_seats": wasted_seats,
                "uses_prefer_keep_free": bool(table["prefer_to_keep_free"]),
                "is_preferred_area": is_preferred_area,
                "sort_key": (
                    0 if not table["prefer_to_keep_free"] else 1,
                    FILL_PRIORITY_WEIGHT.get(table["fill_priority"], 1),
                    table["max_capacity"],
                    wasted_seats,
                ),
            }
            candidate["score"] = _score_candidate(candidate, preferred_area)
            all_candidates.append(candidate)

        if not restaurant.get("table_merge_allowed", True):
            continue

        for merge in merges:
            if not merge["active"]:
                continue
            merge_table_ids: list[str] = merge["merge_tables"]
            if len(merge_table_ids) > restaurant.get("max_tables_per_merge", 2):
                continue
            if any(table_id in occupied_table_ids for table_id in merge_table_ids):
                continue
            merge_tables = [tables_by_id.get(tid) for tid in merge_table_ids]
            if any(t is None for t in merge_tables):
                continue
            merge_area_ids = {t["area_id"] for t in merge_tables if t}
            if len(merge_area_ids) != 1 or area_id not in merge_area_ids:
                continue
            if high_chairs_requested > 0 and not any(t["high_chair_allowed"] for t in merge_tables if t):
                continue
            merge_capacity = sum(t["base_capacity"] for t in merge_tables if t)
            merge_max_capacity = sum(t["max_capacity"] for t in merge_tables if t)
            total_head_seats_max = sum(t["head_seats_max"] for t in merge_tables if t)
            if merge_max_capacity < party_size:
                continue
            head_seats_used = max(0, party_size - merge_capacity)
            if head_seats_used > total_head_seats_max:
                continue
            wasted_seats = merge_max_capacity - party_size
            uses_prefer_keep_free = any(t["prefer_to_keep_free"] for t in merge_tables if t)
            candidate = {
                "type": "merge",
                "assigned_tables": merge_table_ids,
                "assigned_merge_id": merge["merge_id"],
                "area": area["area_name"],
                "head_seats_used": head_seats_used,
                "wasted_seats": wasted_seats,
                "uses_prefer_keep_free": uses_prefer_keep_free,
                "is_preferred_area": is_preferred_area,
                "sort_key": (
                    merge_max_capacity,
                    len(merge_table_ids),
                    head_seats_used,
                    1 if uses_prefer_keep_free else 0,
                    wasted_seats,
                ),
            }
            candidate["score"] = _score_candidate(candidate, preferred_area)
            all_candidates.append(candidate)

    all_candidates.sort(key=lambda c: (-c["score"], c["sort_key"]))
    return all_candidates

