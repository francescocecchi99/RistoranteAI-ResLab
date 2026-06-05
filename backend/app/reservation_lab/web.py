from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from typing import Any

from flask import Flask, jsonify, redirect, render_template, request, url_for

from app.reservation_lab.capacity_page import load_capacity_context
from app.reservation_lab.db import get_connection
from app.reservation_lab.models import BookingRequest
from app.reservation_lab.reservations import confirm_pending_reservation, create_pending_reservation
from app.reservation_lab.restaurant_crud import default_new_bundle, list_restaurants, load_bundle, save_bundle

_BACKEND_ROOT = Path(__file__).resolve().parents[2]

app = Flask(
    __name__,
    template_folder=str(_BACKEND_ROOT / "reservation_lab_templates"),
    static_folder=str(_BACKEND_ROOT / "reservation_lab_static"),
    static_url_path="/static",
)
app.secret_key = "reservation-lab-demo-dev"


DEFAULT_REQUEST = {
    "restaurant_id": "r1",
    "date": "2026-04-04",
    "time": "12:30",
    "phone_number": "333 1234567",
    "party_size": 4,
    "high_chairs_requested": 0,
    "preferences": {"preferred_area": "Outdoor", "special_requests": None},
    "original_agent_text": "Customer asked for a table for four outside at 12:30.",
}


def _render_home(setup_message: str = ""):
    return render_template(
        "home.html",
        page_frame="marketing",
        setup_message=setup_message,
    )


def _render_agent(
    payload: dict[str, Any] | None = None,
    result: dict[str, Any] | None = None,
    setup_message: str = "",
):
    payload_obj = payload or DEFAULT_REQUEST
    step2_payload_obj = dict(payload_obj)
    if not str(step2_payload_obj.get("customer_name", "")).strip():
        step2_payload_obj["customer_name"] = "Mario Rossi"
    pending_reservation_id = result.get("pending_reservation_id") if isinstance(result, dict) else None
    return render_template(
        "agent_lab.html",
        page_frame="pick",
        payload=json.dumps(payload_obj, indent=2),
        payload_step2=json.dumps(step2_payload_obj, indent=2),
        pending_reservation_id=pending_reservation_id,
        result=result,
        setup_message=setup_message,
    )


@app.get("/")
def index():
    return _render_home()


@app.get("/agent")
def agent_lab():
    return _render_agent()


@app.post("/setup/schema")
def run_schema():
    schema_path = _BACKEND_ROOT / "sql" / "schema.sql"
    sql_text = schema_path.read_text(encoding="utf-8-sig")
    with get_connection() as conn:
        with conn.transaction():
            with conn.cursor() as cur:
                cur.execute(sql_text)
    return _render_home(setup_message="Schema created successfully.")


@app.post("/setup/seed")
def load_seed():
    return _render_home(setup_message="Seed disabilitato: usa i dati già presenti su Supabase.")


@app.get("/add-restaurant-details")
def add_restaurant_pick():
    restaurants: list[dict[str, Any]] = []
    db_error: str | None = None
    try:
        with get_connection() as conn:
            restaurants = list_restaurants(conn)
    except Exception as exc:
        db_error = str(exc)
    return render_template(
        "pick_restaurant.html",
        page_frame="pick",
        restaurants=restaurants,
        db_error=db_error,
    )


@app.get("/add-restaurant-details/<restaurant_id>/capacity")
def restaurant_capacity(restaurant_id: str):
    sd_raw = (request.args.get("service_date") or "").strip()
    try:
        service_date = date.fromisoformat(sd_raw) if sd_raw else date.today()
    except ValueError:
        service_date = date.today()

    meal_period: str | None = (request.args.get("meal_period") or "").strip() or None
    turn_index: int | None = request.args.get("turn_index", type=int)
    turn_key = (request.args.get("turn_key") or "").strip()
    if turn_key and "|" in turn_key:
        mp, _, ti = turn_key.partition("|")
        meal_period = mp.strip() or None
        try:
            turn_index = int(ti.strip())
        except ValueError:
            turn_index = None

    try:
        with get_connection() as conn:
            capacity = load_capacity_context(conn, restaurant_id, service_date, meal_period, turn_index)
    except Exception as exc:
        return render_template(
            "restaurant_capacity.html",
            page_frame="pick",
            capacity=None,
            db_error=str(exc),
            restaurant_id=restaurant_id,
            service_date=service_date.isoformat(),
        )

    if capacity.get("error") == "not_found":
        return redirect(url_for("add_restaurant_pick", nf=1))

    return render_template(
        "restaurant_capacity.html",
        page_frame="pick",
        capacity=capacity,
        db_error=None,
        restaurant_id=restaurant_id,
        service_date=service_date.isoformat(),
    )


@app.get("/add-restaurant-details/setup")
def add_restaurant_setup():
    rid = (request.args.get("restaurant_id") or "").strip()
    is_new = request.args.get("new") == "1"
    if is_new:
        return render_template(
            "restaurant_setup.html",
            page_frame="setup",
            bundle_json=default_new_bundle(),
            load_error=None,
        )
    if rid:
        try:
            with get_connection() as conn:
                bundle = load_bundle(conn, rid)
        except Exception as exc:
            return redirect(url_for("add_restaurant_pick", err=str(exc)[:200]))
        if not bundle:
            return redirect(url_for("add_restaurant_pick", nf=1))
        return render_template(
            "restaurant_setup.html",
            page_frame="setup",
            bundle_json=bundle,
            load_error=None,
        )
    return redirect(url_for("add_restaurant_pick"))


@app.get("/api/restaurants")
def api_restaurants():
    try:
        with get_connection() as conn:
            rows = list_restaurants(conn)
        return jsonify({"ok": True, "restaurants": rows})
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 500


@app.get("/api/restaurant/<restaurant_id>/bundle")
def api_restaurant_bundle(restaurant_id: str):
    try:
        with get_connection() as conn:
            bundle = load_bundle(conn, restaurant_id)
        if not bundle:
            return jsonify({"ok": False, "error": "not_found"}), 404
        return jsonify({"ok": True, "bundle": bundle})
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 500


@app.post("/api/restaurant/save")
def api_restaurant_save():
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return jsonify({"ok": False, "error": "Expected JSON object"}), 400
    try:
        with get_connection() as conn:
            ok, msg = save_bundle(conn, data)
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 500
    if not ok:
        return jsonify({"ok": False, "error": msg}), 400
    return jsonify({"ok": True, "message": msg})


def _parse_request_from_form(require_customer_name: bool) -> tuple[BookingRequest | None, dict[str, Any] | None, str | None]:
    raw_payload = request.form.get("payload", "")
    try:
        payload = json.loads(raw_payload)
    except json.JSONDecodeError as exc:
        return None, None, f"Invalid JSON payload: {exc}"
    if not isinstance(payload, dict):
        return None, None, "Payload must be a JSON object."

    customer_name_override = (request.form.get("customer_name_override") or "").strip()
    if customer_name_override:
        payload["customer_name"] = customer_name_override
    elif payload.get("customer_name") is None:
        payload["customer_name"] = ""

    if require_customer_name and not str(payload.get("customer_name", "")).strip():
        return None, payload, "Validation error: customer_name is required to create a reservation."

    try:
        booking_request = BookingRequest.model_validate(payload)
    except Exception as exc:
        return None, payload, f"Validation error: {exc}"
    return booking_request, payload, None


@app.post("/check")
def check():
    booking_request, payload, error = _parse_request_from_form(require_customer_name=False)
    if error:
        return _render_agent(
            payload=payload or DEFAULT_REQUEST,
            result={"agent_response": "error", "reason": error},
        )
    with get_connection() as conn:
        result = create_pending_reservation(conn, booking_request)
    return _render_agent(payload=payload, result=_jsonify(result))


@app.post("/create")
def create():
    booking_request, payload, error = _parse_request_from_form(require_customer_name=True)
    if error:
        return _render_agent(
            payload=payload or DEFAULT_REQUEST,
            result={"agent_response": "error", "reason": error},
        )
    pending_reservation_id = (request.form.get("pending_reservation_id") or "").strip()
    with get_connection() as conn:
        result = confirm_pending_reservation(conn, pending_reservation_id, booking_request)
    return _render_agent(payload=payload, result=_jsonify(result))


def _jsonify(result: dict[str, Any]) -> dict[str, Any]:
    agent_response = result.get("agent_response") or {}
    is_available = bool(agent_response.get("available")) if isinstance(agent_response, dict) else False
    db_saved_obj = result.get("db_saved") if isinstance(result.get("db_saved"), dict) else None
    return {
        "agent_response": json.dumps(agent_response, indent=2, default=str),
        "db_preview": json.dumps(result.get("db_preview"), indent=2, default=str) if result.get("db_preview") else None,
        "db_saved": json.dumps(db_saved_obj, indent=2, default=str) if db_saved_obj else None,
        "reason": result.get("reason"),
        "debug": json.dumps(result.get("debug"), indent=2, default=str),
        "available": is_available,
        "pending_reservation_id": db_saved_obj.get("reservation_id") if db_saved_obj else None,
        "reservation_status": db_saved_obj.get("status") if db_saved_obj else None,
    }


@app.post("/reset")
def reset():
    return redirect(url_for("index"))
