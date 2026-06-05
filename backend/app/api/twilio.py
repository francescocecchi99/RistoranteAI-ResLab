from __future__ import annotations

from datetime import UTC, datetime
from html import escape
from urllib.parse import urlparse, urlunparse

import jwt
from fastapi import APIRouter, Depends, Request, WebSocket, WebSocketDisconnect
from sqlalchemy import select
from sqlalchemy.orm import Session
from starlette.responses import Response

from app.api.deps import get_db
from app.core.config import settings
from app.core.database import SessionLocal
from app.core.observability import json_log
from app.models import CallLog, Restaurant
from app.services.openai_realtime import bridge_twilio_media_stream

router = APIRouter(prefix="/twilio", tags=["twilio"])


def _twiml_response(body: str) -> Response:
    return Response(
        content=f'<?xml version="1.0" encoding="UTF-8"?><Response>{body}</Response>',
        media_type="application/xml",
    )


def _phone_lookup_candidates(phone_number: str) -> list[str]:
    normalized = "".join(str(phone_number or "").strip().split())
    if not normalized:
        return []

    candidates = [normalized]
    if normalized.startswith("+"):
        candidates.append(normalized[1:])
    else:
        candidates.append(f"+{normalized}")
    return list(dict.fromkeys(candidates))


def _restaurant_by_twilio_phone(db: Session, phone_number: str) -> Restaurant | None:
    candidates = _phone_lookup_candidates(phone_number)
    if not candidates:
        return None
    return db.scalar(select(Restaurant).where(Restaurant.twilio_phone.in_(candidates)))


def _voice_fallback_response(
    *,
    db: Session,
    called_number: str,
    caller_number: str,
    request_id: str | None,
    digits: str = "",
) -> Response:
    restaurant = _restaurant_by_twilio_phone(db, called_number)

    json_log(
        "app.twilio",
        {
            "event": "twilio_voice_fallback_invoked",
            "request_id": request_id,
            "called_number": called_number or None,
            "caller_number_present": bool(caller_number),
            "restaurant_id": restaurant.id if restaurant else None,
        },
    )

    if digits == "1" and restaurant and restaurant.escalation_phone:
        return _twiml_response(
            f"<Say language=\"it-IT\" voice=\"alice\">"
            f"La metto subito in contatto con il ristorante."
            f"</Say>"
            f"<Dial>{escape(restaurant.escalation_phone)}</Dial>"
        )

    if restaurant and restaurant.escalation_phone:
        restaurant_name = escape(restaurant.name)
        escalation_phone = escape(restaurant.escalation_phone)
        return _twiml_response(
            f"<Say language=\"it-IT\" voice=\"alice\">"
            f"Ci scusi, stiamo avendo un problema tecnico con il centralino automatico di {restaurant_name}. "
            f"La metto subito in contatto con il ristorante."
            f"</Say>"
            f"<Dial>{escalation_phone}</Dial>"
        )

    return _twiml_response(
        "<Say language=\"it-IT\" voice=\"alice\">"
        "Ci scusi, stiamo avendo un problema tecnico con il centralino automatico. "
        "La invitiamo a richiamare tra qualche minuto."
        "</Say>"
        "<Hangup/>"
    )


def _stream_token(
    *,
    restaurant: Restaurant,
    from_number: str,
    to_number: str,
    call_sid: str,
) -> str:
    payload = {
        "sub": "twilio-media-stream",
        "restaurant_id": restaurant.id,
        "call_sid": call_sid,
        "from_number": from_number,
        "to_number": to_number,
        "iat": int(datetime.now(UTC).timestamp()),
    }
    return jwt.encode(payload, settings.jwt_secret, algorithm="HS256")


def _stream_url(request: Request) -> str:
    base_url = settings.public_base_url or str(request.base_url).rstrip("/")
    parsed = urlparse(base_url)
    # Ensure we use 'wss' for https origins and 'ws' for http
    scheme = "wss" if parsed.scheme in ("https", "wss") else "ws"
    # Reconstruct netloc without prefix if it was passed with one
    netloc = parsed.netloc or parsed.path.split("/")[0]
    path = f"{settings.api_prefix}/twilio/media-stream"
    return urlunparse(
        (
            scheme,
            netloc,
            path,
            "",
            "",
            "",
        )
    )


def _public_http_url(request: Request, path: str) -> str:
    base_url = settings.public_base_url or str(request.base_url).rstrip("/")
    parsed = urlparse(base_url)
    scheme = "https" if parsed.scheme == "wss" else (parsed.scheme or "https")
    return urlunparse((scheme, parsed.netloc, path, "", "", ""))


def _ensure_call_log(
    *,
    db: Session,
    restaurant: Restaurant,
    call_sid: str,
    caller_phone_hash: str | None = None,
) -> CallLog:
    call = db.scalar(select(CallLog).where(CallLog.twilio_call_sid == call_sid))
    if call:
        return call
    call = CallLog(
        restaurant_id=restaurant.id,
        voice_provider=restaurant.voice_provider,
        provider_call_id=call_sid,
        twilio_call_sid=call_sid,
        caller_phone_hash=caller_phone_hash,
        started_at=datetime.now(UTC),
        duration_seconds=0,
        outcome="info_provided",
        call_status="unknown",
        summary="Chiamata avviata.",
        transcript_preview="",
        extra_data={},
    )
    db.add(call)
    db.commit()
    db.refresh(call)
    return call


@router.post("/inbound")
async def inbound_call(request: Request, db: Session = Depends(get_db)) -> Response:
    form = await request.form()
    from_number = str(form.get("From") or "").strip()
    to_number = str(form.get("To") or form.get("Called") or "").strip()
    call_sid = str(form.get("CallSid") or "").strip() or "missing-call-sid"

    if settings.reservation_lab_booking_enabled:
        from app.services.reservation_lab_voice import (
            default_restaurant_id,
            get_restaurant_for_voice,
            log_voice_call,
        )

        restaurant_id = default_restaurant_id()
        voice_rest = get_restaurant_for_voice(restaurant_id)
        log_voice_call(
            restaurant_id=restaurant_id,
            twilio_call_sid=call_sid,
            caller_phone=from_number,
        )

        class _TokenRestaurant:
            def __init__(self, rid: str) -> None:
                self.id = rid

        token = _stream_token(
            restaurant=_TokenRestaurant(restaurant_id),
            from_number=from_number,
            to_number=to_number,
            call_sid=call_sid,
        )
        stream_url = escape(_stream_url(request))
        status_url = escape(_public_http_url(request, f"{settings.api_prefix}/twilio/status"))
        twiml = (
            f"<Connect><Stream url=\"{stream_url}\" statusCallback=\"{status_url}\">"
            f"<Parameter name=\"token\" value=\"{escape(token)}\" />"
            f"</Stream></Connect>"
        )
        json_log(
            "app.twilio",
            {
                "event": "twilio_inbound_stream_created",
                "request_id": getattr(request.state, "request_id", None),
                "restaurant_id": restaurant_id,
                "call_sid": call_sid,
                "voice_provider": voice_rest.voice_provider,
                "stream_url": stream_url,
            },
        )
        return Response(
            content=f'<?xml version="1.0" encoding="UTF-8"?><Response>{twiml}</Response>',
            media_type="application/xml",
        )

    restaurant = _restaurant_by_twilio_phone(db, to_number)
    if not restaurant:
        json_log(
            "app.twilio",
            {
                "event": "twilio_inbound_restaurant_not_found",
                "request_id": getattr(request.state, "request_id", None),
                "from_number": from_number or None,
                "to_number": to_number or None,
            },
        )
        return _voice_fallback_response(
            db=db,
            called_number=to_number,
            caller_number=from_number,
            request_id=getattr(request.state, "request_id", None),
        )

    _ensure_call_log(db=db, restaurant=restaurant, call_sid=call_sid)
    token = _stream_token(
        restaurant=restaurant,
        from_number=from_number,
        to_number=to_number,
        call_sid=call_sid,
    )
    stream_url = escape(_stream_url(request))
    status_url = escape(_public_http_url(request, f"{settings.api_prefix}/twilio/status"))

    twiml = (
        f"<Connect><Stream url=\"{stream_url}\" statusCallback=\"{status_url}\">"
        f"<Parameter name=\"token\" value=\"{escape(token)}\" />"
        f"</Stream></Connect>"
    )
    json_log(
        "app.twilio",
        {
            "event": "twilio_inbound_stream_created",
            "request_id": getattr(request.state, "request_id", None),
            "restaurant_id": restaurant.id,
            "call_sid": call_sid,
            "voice_provider": restaurant.voice_provider,
            "stream_url": stream_url,
        },
    )
    return Response(
        content=f'<?xml version="1.0" encoding="UTF-8"?><Response>{twiml}</Response>',
        media_type="application/xml",
    )


@router.post("/status", name="twilio_status_callback")
async def status_callback(request: Request, db: Session = Depends(get_db)) -> dict[str, bool]:
    form = await request.form()
    call_sid = str(form.get("CallSid") or "").strip()
    call_status = str(form.get("CallStatus") or "").strip().lower()
    duration_raw = str(form.get("CallDuration") or "").strip()
    stream_event = str(form.get("StreamEvent") or "").strip().lower()
    stream_error = str(form.get("StreamError") or "").strip()
    stream_sid = str(form.get("StreamSid") or "").strip()

    call = db.scalar(select(CallLog).where(CallLog.twilio_call_sid == call_sid))
    if call:
        if duration_raw.isdigit():
            call.duration_seconds = int(duration_raw)
        if call_status:
            call.call_status = "successful" if call_status == "completed" else call_status
        if stream_event:
            extra_data = dict(call.extra_data or {})
            extra_data["twilio_stream_event"] = stream_event
            if stream_sid:
                extra_data["twilio_stream_sid"] = stream_sid
            if stream_error:
                extra_data["twilio_stream_error"] = stream_error
                call.call_status = "failed"
                if call.outcome == "info_provided":
                    call.outcome = "tool_error"
            call.extra_data = extra_data
        db.add(call)
        db.commit()
    return {"ok": True}


@router.post("/voice-fallback")
async def voice_fallback(request: Request, db: Session = Depends(get_db)) -> Response:
    form = await request.form()
    called_number = str(form.get("Called") or form.get("To") or "").strip()
    caller_number = str(form.get("From") or "").strip()
    digits = str(form.get("Digits") or "").strip()
    return _voice_fallback_response(
        db=db,
        called_number=called_number,
        caller_number=caller_number,
        request_id=getattr(request.state, "request_id", None),
        digits=digits,
    )


@router.websocket("/media-stream")
async def media_stream(websocket: WebSocket) -> None:
    await websocket.accept()
    try:
        await bridge_twilio_media_stream(
            websocket=websocket,
            db_factory=SessionLocal,
        )
    except RuntimeError as exc:
        json_log(
            "app.twilio",
            {
                "event": "twilio_media_stream_rejected",
                "error": str(exc),
            },
        )
        await websocket.close(code=1008)
        raise
    except WebSocketDisconnect:
        json_log(
            "app.twilio",
            {
                "event": "twilio_media_stream_disconnected",
                "reason": "client_disconnected",
            },
        )
    except Exception as exc:
        json_log(
            "app.twilio",
            {
                "event": "twilio_media_stream_error",
                "error": str(exc),
                "error_type": type(exc).__name__,
            },
        )
        raise
