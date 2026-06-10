from __future__ import annotations

import json
from datetime import date, time
from pathlib import Path
from typing import Any

from flask import Flask, jsonify, redirect, render_template, request, send_file, url_for

from app.reservation_lab.capacity_page import load_capacity_context
from app.reservation_lab.db import fetch_one, get_connection
from app.reservation_lab.inbound_calls_page import (
    get_voice_call_row,
    link_call_to_reservation,
    load_inbound_calls_context,
)
from app.reservation_lab.models import BookingRequest
from app.reservation_lab.manual_reservation_page import load_manual_reservation_bootstrap, load_manual_reservation_options
from app.reservation_lab.reservations import (
    _load_turns,
    _match_turn,
    confirm_pending_reservation,
    create_owner_manual_reservation,
    create_pending_reservation,
)
from app.reservation_lab.owner_auth import (
    is_logged_in,
    login_owner,
    logout_owner,
    session_restaurant_id,
    verify_credentials,
)
from app.reservation_lab.owner_credentials import get_owner_username, has_owner_credentials_table
from app.reservation_lab.restaurant_crud import default_new_bundle, list_restaurants, load_bundle, save_bundle
from app.reservation_lab.voice_recording import resolve_recording_file

_BACKEND_ROOT = Path(__file__).resolve().parents[2]

app = Flask(
    __name__,
    template_folder=str(_BACKEND_ROOT / "reservation_lab_templates"),
    static_folder=str(_BACKEND_ROOT / "reservation_lab_static"),
    static_url_path="/static",
)
app.secret_key = "reservation-lab-demo-dev"


@app.before_request
def _owner_auth_gate() -> Any:
    path = request.path
    if path in {"/", "/login", "/create-restaurant-profile"} or path.startswith("/static"):
        return None
    if path == "/agent":
        return redirect(url_for("index"))
    if path == "/add-restaurant-details/setup":
        if request.args.get("new") == "1":
            return None
        rid = (request.args.get("restaurant_id") or "").strip()
        if rid and _restaurant_needs_owner_bootstrap(rid):
            return None
    if path == "/api/restaurant/save" and request.method == "POST":
        data = request.get_json(silent=True) or {}
        rid = str(data.get("restaurant_id") or "").strip()
        if rid and _restaurant_needs_owner_bootstrap(rid):
            return None
    if is_logged_in():
        return None
    if path.startswith("/add-restaurant-details") or path == "/owner":
        return redirect(url_for("owner_login", next=path))
    if path.startswith("/api/restaurant") or path.startswith("/api/voice-calls"):
        return jsonify({"ok": False, "error": "unauthorized"}), 401
    return None


def _restaurant_needs_owner_bootstrap(restaurant_id: str) -> bool:
    try:
        with get_connection() as conn:
            if not has_owner_credentials_table(conn):
                return False
            return get_owner_username(conn, restaurant_id) is None
    except Exception:
        return False


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


@app.get("/create-restaurant-profile")
def create_restaurant_profile():
    return redirect(url_for("add_restaurant_setup", new=1))


@app.get("/login")
def owner_login():
    if is_logged_in():
        return redirect(url_for("owner_dashboard"))
    next_url = (request.args.get("next") or "").strip()
    return render_template(
        "owner_login.html",
        page_frame="pick",
        login_error=None,
        next_url=next_url,
    )


@app.post("/login")
def owner_login_submit():
    username = (request.form.get("username") or "").strip()
    password = request.form.get("password") or ""
    next_url = (request.form.get("next") or "").strip()
    restaurant_id = verify_credentials(username, password)
    if not restaurant_id:
        return render_template(
            "owner_login.html",
            page_frame="pick",
            login_error="Invalid username or password.",
            next_url=next_url,
        )
    login_owner(restaurant_id=restaurant_id, username=username)
    if next_url and next_url.startswith("/"):
        return redirect(next_url)
    return redirect(url_for("owner_dashboard"))


@app.get("/owner")
def owner_dashboard():
    restaurant_id = session_restaurant_id()
    try:
        with get_connection() as conn:
            restaurant = fetch_one(
                conn,
                "SELECT restaurant_id, restaurant_name FROM restaurants WHERE restaurant_id = %s",
                (restaurant_id,),
            )
    except Exception as exc:
        return render_template(
            "owner_dashboard.html",
            page_frame="pick",
            restaurant=None,
            db_error=str(exc),
        )
    if not restaurant:
        return render_template(
            "owner_dashboard.html",
            page_frame="pick",
            restaurant={"restaurant_id": restaurant_id, "restaurant_name": restaurant_id},
            db_error=f"Restaurant {restaurant_id} not found in database.",
        )
    return render_template(
        "owner_dashboard.html",
        page_frame="pick",
        restaurant=restaurant,
        db_error=None,
    )


@app.get("/logout")
def owner_logout():
    logout_owner()
    return redirect(url_for("index"))


@app.get("/agent")
def agent_lab():
    return redirect(url_for("index"))


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
    if not is_logged_in():
        return redirect(url_for("owner_login"))
    return redirect(url_for("owner_dashboard"))


@app.get("/add-restaurant-details/_legacy-list")
def add_restaurant_pick_legacy():
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


@app.get("/add-restaurant-details/<restaurant_id>/manual-reservation")
def restaurant_manual_reservation(restaurant_id: str):
    try:
        with get_connection() as conn:
            bootstrap = load_manual_reservation_bootstrap(conn, restaurant_id)
    except Exception as exc:
        return render_template(
            "restaurant_manual_reservation.html",
            page_frame="pick",
            restaurant=None,
            areas=[],
            bootstrap_json="{}",
            db_error=str(exc),
            restaurant_id=restaurant_id,
        )
    if bootstrap.get("error") == "not_found":
        return redirect(url_for("owner_dashboard", nf=1))
    return render_template(
        "restaurant_manual_reservation.html",
        page_frame="pick",
        restaurant=bootstrap["restaurant"],
        areas=bootstrap.get("areas") or [],
        bootstrap_json=json.dumps({"areas": bootstrap.get("areas") or []}),
        db_error=None,
        restaurant_id=restaurant_id,
    )


@app.get("/add-restaurant-details/<restaurant_id>/inbound-calls")
def restaurant_inbound_calls(restaurant_id: str):
    sd_raw = (request.args.get("service_date") or "").strip()
    service_date: date | None = None
    if sd_raw:
        try:
            service_date = date.fromisoformat(sd_raw)
        except ValueError:
            service_date = None
    days = request.args.get("days", type=int) or 30
    days = max(1, min(days, 90))

    try:
        with get_connection() as conn:
            context = load_inbound_calls_context(
                conn, restaurant_id, service_date=service_date, days=days
            )
    except Exception as exc:
        return render_template(
            "restaurant_inbound_calls.html",
            page_frame="pick",
            context=None,
            db_error=str(exc),
            restaurant_id=restaurant_id,
            service_date=sd_raw,
            days=days,
        )

    if context.get("error") == "not_found":
        return redirect(url_for("owner_dashboard", nf=1))

    return render_template(
        "restaurant_inbound_calls.html",
        page_frame="pick",
        context=context,
        db_error=None,
        restaurant_id=restaurant_id,
        service_date=sd_raw,
        days=days,
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

    requested_time_raw = (request.args.get("time") or request.args.get("requested_time") or "").strip()
    if requested_time_raw and meal_period is None and turn_index is None:
        try:
            requested_time = time.fromisoformat(
                requested_time_raw if len(requested_time_raw) == 8 else f"{requested_time_raw}:00"
            )
            with get_connection() as conn:
                matched = _match_turn(requested_time, _load_turns(conn, restaurant_id, service_date))
            if matched:
                meal_period = matched["meal_period"]
                turn_index = matched["turn_index"]
        except ValueError:
            requested_time_raw = ""

    from_manual = request.args.get("from") == "manual"

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
            from_manual=from_manual,
            requested_time=requested_time_raw or None,
        )

    if capacity.get("error") == "not_found":
        return redirect(url_for("owner_dashboard", nf=1))

    return render_template(
        "restaurant_capacity.html",
        page_frame="pick",
        capacity=capacity,
        db_error=None,
        restaurant_id=restaurant_id,
        service_date=service_date.isoformat(),
        from_manual=from_manual,
        requested_time=requested_time_raw or None,
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
            is_new_profile=True,
        )
    if rid:
        try:
            with get_connection() as conn:
                bundle = load_bundle(conn, rid)
        except Exception as exc:
            return redirect(url_for("owner_dashboard", err=str(exc)[:200]))
        if not bundle:
            return redirect(url_for("owner_dashboard", nf=1))
        return render_template(
            "restaurant_setup.html",
            page_frame="setup",
            bundle_json=bundle,
            load_error=None,
            is_new_profile=False,
        )
    return redirect(url_for("owner_dashboard"))


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


@app.get("/api/restaurant/<restaurant_id>/manual-reservation/options")
def api_manual_reservation_options(restaurant_id: str):
    sd_raw = (request.args.get("date") or "").strip()
    if not sd_raw:
        return jsonify({"ok": False, "error": "date is required"}), 400
    try:
        service_date = date.fromisoformat(sd_raw)
    except ValueError:
        return jsonify({"ok": False, "error": "invalid date"}), 400

    requested_time: time | None = None
    time_raw = (request.args.get("time") or "").strip()
    if time_raw:
        try:
            requested_time = time.fromisoformat(time_raw if len(time_raw) == 8 else f"{time_raw}:00")
        except ValueError:
            return jsonify({"ok": False, "error": "invalid time"}), 400

    party_size = request.args.get("party_size", type=int)
    preferred_area = (request.args.get("preferred_area") or "").strip() or None
    high_chairs = request.args.get("high_chairs_requested", type=int) or 0

    try:
        with get_connection() as conn:
            options = load_manual_reservation_options(
                conn,
                restaurant_id=restaurant_id,
                service_date=service_date,
                requested_time=requested_time,
                party_size=party_size,
                preferred_area=preferred_area,
                high_chairs_requested=high_chairs,
            )
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 500

    if options.get("error") == "not_found":
        return jsonify({"ok": False, "error": "not_found"}), 404

    return jsonify({"ok": True, **options})


@app.post("/api/restaurant/<restaurant_id>/manual-reservation")
def api_manual_reservation(restaurant_id: str):
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return jsonify({"ok": False, "error": "Expected JSON object"}), 400

    assigned_tables = data.pop("assigned_tables", None)
    assigned_merge_id = data.pop("assigned_merge_id", None)
    if not isinstance(assigned_tables, list) or not assigned_tables:
        return jsonify({"ok": False, "error": "assigned_tables is required"}), 400

    try:
        data["restaurant_id"] = restaurant_id
        booking_request = BookingRequest.model_validate(data)
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400

    try:
        with get_connection() as conn:
            result = create_owner_manual_reservation(
                conn,
                booking_request,
                assigned_tables=[str(t) for t in assigned_tables],
                assigned_merge_id=str(assigned_merge_id) if assigned_merge_id else None,
            )
            if not result.get("db_saved"):
                return jsonify({"ok": False, "error": result.get("reason") or "unavailable"}), 400
            reservation_id = result["db_saved"]["reservation_id"]
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 500

    return jsonify({"ok": True, "reservation_id": reservation_id})


@app.get("/api/voice-calls/<restaurant_id>/<call_log_id>/transcript")
def api_voice_call_transcript(restaurant_id: str, call_log_id: str):
    try:
        with get_connection() as conn:
            row = get_voice_call_row(conn, call_log_id, restaurant_id)
        if not row:
            return jsonify({"ok": False, "error": "not_found"}), 404
        return jsonify(
            {
                "ok": True,
                "transcript": row.get("transcript") or "",
                "summary": row.get("summary") or "",
            }
        )
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 500


@app.get("/api/voice-calls/<restaurant_id>/<call_log_id>/recording")
def api_voice_call_recording(restaurant_id: str, call_log_id: str):
    try:
        with get_connection() as conn:
            row = get_voice_call_row(conn, call_log_id, restaurant_id)
        if not row or not row.get("audio_storage_path"):
            return jsonify({"ok": False, "error": "not_found"}), 404
        path = row["audio_storage_path"]
        if str(path).startswith("http"):
            return redirect(path)
        file_path = resolve_recording_file(str(path))
        if not file_path:
            return jsonify({"ok": False, "error": "recording_missing"}), 404
        return send_file(file_path, mimetype="audio/wav", conditional=True)
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 500


@app.post("/api/voice-calls/<restaurant_id>/<call_log_id>/book-manually")
def api_voice_call_book_manually(restaurant_id: str, call_log_id: str):
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return jsonify({"ok": False, "error": "Expected JSON object"}), 400
    try:
        data["restaurant_id"] = restaurant_id
        booking_request = BookingRequest.model_validate(data)
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400

    call_row: dict | None = None
    reservation_id: str | None = None
    try:
        with get_connection() as conn:
            call_row = get_voice_call_row(conn, call_log_id, restaurant_id)
            if not call_row:
                return jsonify({"ok": False, "error": "call_not_found"}), 404
            if not booking_request.phone_number and call_row.get("caller_phone"):
                booking_request = booking_request.model_copy(
                    update={"phone_number": call_row.get("caller_phone")}
                )
            pending = create_pending_reservation(conn, booking_request)
            if not pending.get("db_saved"):
                return jsonify({"ok": False, "error": pending.get("reason") or "unavailable"}), 400
            reservation_id = pending["db_saved"]["reservation_id"]
            confirmed = confirm_pending_reservation(conn, reservation_id, booking_request)
            if not confirmed.get("db_saved"):
                return jsonify({"ok": False, "error": confirmed.get("reason") or "confirm_failed"}), 400
            link_call_to_reservation(
                conn,
                call_log_id=call_log_id,
                restaurant_id=restaurant_id,
                reservation_id=reservation_id,
            )
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 500

    return jsonify({"ok": True, "reservation_id": reservation_id})


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
