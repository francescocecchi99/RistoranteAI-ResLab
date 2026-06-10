from __future__ import annotations

import asyncio
import base64
import json
import re
from contextlib import suppress
from dataclasses import dataclass, field, replace
from datetime import UTC, date, datetime, time
from typing import Any, Literal
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
from zoneinfo import ZoneInfo

import httpx
import jwt
from fastapi import WebSocket
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker
from websockets.asyncio.client import connect

from app.core.config import settings
from app.core.observability import json_log
from app.models import Booking, CallLog, Restaurant
from app.schemas.booking import BookingCreate, BookingUpdate
from app.schemas.common import BookingSource, BookingStatus
from app.schemas.tools import ModifyBookingChanges
from app.services.availability import ACTIVE_STATUSES, check_availability
from app.services.bookings import (
    cancel_booking as cancel_booking_service,
)
from app.services.bookings import (
    create_booking,
    find_bookings_for_caller,
    update_booking,
)

SUPPORTED_REALTIME_MODELS = {"gpt-realtime", "gpt-realtime-1.5", "gpt-realtime-2", "gpt-realtime-mini"}
SUPPORTED_REALTIME_VOICES = {
    "alloy",
    "ash",
    "ballad",
    "cedar",
    "coral",
    "echo",
    "marin",
    "sage",
    "shimmer",
    "verse",
}
RECOMMENDED_REALTIME_VOICES = {"marin", "cedar"}
INITIAL_TOOL_NAMES = ("wait_for_user", "check_availability", "find_booking", "create_booking", "escalate_to_human")
FULL_TOOL_NAMES = (
    "wait_for_user",
    "check_availability",
    "find_booking",
    "create_booking",
    "modify_booking",
    "cancel_booking",
    "escalate_to_human",
)
INPUT_AUDIO_BUFFER_PACKET_THRESHOLD = 2
SILENT_RESPONSE_WATCHDOG_SECONDS = 1.8
MAX_SILENT_RESPONSE_RETRIES = 1
CALLER_INACTIVITY_TIMEOUT_SECONDS = 30.0
SUMMARY_TRIGGER_TURNS = 10
SUMMARY_KEEP_LAST_TURNS = 4
SUMMARY_MODEL = "gpt-4o-mini"
SUPPORTED_RUNTIME_OVERRIDE_FIELDS = {
    "model",
    "voice",
    "speed",
    "reasoning_effort",
    "tool_choice",
    "turn_detection_type",
    "vad_threshold",
    "vad_prefix_padding_ms",
    "vad_silence_duration_ms",
    "vad_idle_timeout_ms",
    "semantic_vad_eagerness",
    "interrupt_response",
    "create_response",
    "noise_reduction_type",
    "transcription_model",
    "input_language",
    "tracing_enabled",
    "truncation_mode",
    "truncation_retention_ratio",
}
PRACTICAL_STUDIO_FIELDS = {
    "model",
    "voice",
    "speed",
    "reasoning_effort",
    "turn_detection_type",
    "vad_threshold",
    "vad_silence_duration_ms",
    "vad_idle_timeout_ms",
    "noise_reduction_type",
    "input_language",
    "tracing_enabled",
    "truncation_mode",
    "truncation_retention_ratio",
}
STUDIO_PRESETS = [
    {
        "id": "balanced",
        "label": "Balanced",
        "description": (
            "Recommended default for phone bookings: stable turn detection, "
            "traceability, and concise replies."
        ),
        "session_overrides": {
            "model": "gpt-realtime-2",
            "voice": "cedar",
            "reasoning_effort": "low",
            "tool_choice": "auto",
            "turn_detection_type": "server_vad",
            "vad_threshold": 0.58,
            "vad_silence_duration_ms": 600,
            "vad_idle_timeout_ms": 7000,
            "noise_reduction_type": "far_field",
            "tracing_enabled": True,
            "truncation_mode": "retention_ratio",
            "truncation_retention_ratio": 0.75,
        },
    },
    {
        "id": "noisy_room",
        "label": "Noisy room",
        "description": (
            "For busy dining rooms and speakerphone use. Slightly more patient "
            "turn-taking and stronger noise handling."
        ),
        "session_overrides": {
            "model": "gpt-realtime-2",
            "voice": "cedar",
            "reasoning_effort": "low",
            "tool_choice": "auto",
            "turn_detection_type": "server_vad",
            "vad_threshold": 0.62,
            "vad_silence_duration_ms": 1100,
            "vad_idle_timeout_ms": 8000,
            "noise_reduction_type": "far_field",
            "tracing_enabled": True,
            "truncation_mode": "retention_ratio",
            "truncation_retention_ratio": 0.72,
        },
    },
    {
        "id": "natural_pauses",
        "label": "Natural pauses",
        "description": (
            "Test preset for callers who pause, self-correct, or think aloud. "
            "Uses semantic turn detection before promoting it to the live default."
        ),
        "session_overrides": {
            "model": "gpt-realtime-2",
            "voice": "cedar",
            "reasoning_effort": "low",
            "tool_choice": "auto",
            "turn_detection_type": "semantic_vad",
            "semantic_vad_eagerness": "medium",
            "noise_reduction_type": "far_field",
            "tracing_enabled": True,
            "truncation_mode": "retention_ratio",
            "truncation_retention_ratio": 0.75,
        },
    },
]
STUDIO_SCENARIOS = [
    {
        "id": "new_booking",
        "label": "New booking",
        "message": "Vorrei prenotare un tavolo per domani sera alle 20 per due persone.",
        "goal": "Should confirm intent, collect only missing data, and verify availability before writing.",
    },
    {
        "id": "modify_booking",
        "label": "Modify booking",
        "message": "Vorrei spostare la mia prenotazione di domani dalle 20 alle 21.",
        "goal": "Should find the booking first, then re-check availability before modifying.",
    },
    {
        "id": "cancel_booking",
        "label": "Cancel booking",
        "message": "Vorrei cancellare la mia prenotazione per venerdì.",
        "goal": "Should identify the booking, ask for confirmation, and cancel only after a separate yes.",
    },
    {
        "id": "info_request",
        "label": "Info request",
        "message": "Siete aperti lunedì a pranzo e dove siete esattamente?",
        "goal": "Should answer from context only, without forcing a booking flow.",
    },
    {
        "id": "unclear_audio",
        "label": "Unclear caller",
        "message": "Pronto? Mi sente? Vorrei per sabato... cioè forse domenica... non so.",
        "goal": "Should clarify only the missing part, not guess, and stay concise.",
    },
    {
        "id": "human_handoff",
        "label": "Escalation",
        "message": "Siamo in dodici con allergie importanti, preferisco parlare con il ristorante.",
        "goal": "Should use the escape hatch quickly instead of over-handling a risky case.",
    },
]
PROMPT_SECTIONS = [
    "Role & Objective",
    "Personality & Tone",
    "Language",
    "Context",
    "CRITICAL RULES",
    "Tools",
    "Conversation Flow",
    "Write Action Rules",
    "Duplicate Prevention",
    "Safety & Escalation",
    "Unclear Audio",
]
GREETING_DISCIPLINE_BLOCK = """

Greeting Discipline
- Usa "Buongiorno" o "Buonasera" solo nel primo turno della chiamata.
- Dopo il saluto iniziale, non iniziare più le risposte con "Buongiorno", "Buonasera", "Salve" o il nome del ristorante.
- Nei turni successivi vai diretto al dato mancante, al preambolo del tool o alla conferma.
- Se il cliente saluta di nuovo, rispondi brevemente ma non riaprire la chiamata.
""".strip()
REPEATED_GREETING_PATTERNS = (
    re.compile(
        r"(?im)^\s*-?\s*inizia\s+(?:la\s+frase|ogni\s+(?:frase|risposta)|ciascuna\s+(?:frase|risposta))\s+con\s+"
        r"(?:buongiorno|buonasera|salve)\b[^\n]*(?:\n|$)"
    ),
    re.compile(
        r"(?im)^\s*-?\s*(?:ogni|ciascuna)\s+(?:frase|risposta)\s+"
        r"(?:deve\s+)?(?:iniziare|aprire)\s+con\s+(?:buongiorno|buonasera|salve)\b[^\n]*(?:\n|$)"
    ),
    re.compile(
        r"(?i)\s+inizia\s+(?:la\s+frase|ogni\s+(?:frase|risposta)|ciascuna\s+(?:frase|risposta))\s+con\s+"
        r"(?:buongiorno|buonasera|salve)\b[^.\n]*(?:\.\s*)?"
    ),
)
CREATE_BOOKING_CONFIRMATION_PATTERNS = (
    re.compile(
        r"(?im)^\s*-\s*Usa gli strumenti di scrittura solo dopo una conferma esplicita del cliente\s*\n"
        r"\s*e solo nel turno successivo alla domanda finale di conferma\.\s*$"
    ),
    re.compile(
        r"(?im)^\s*-\s*Prima di create_booking, modify_booking o cancel_booking: "
        r"riassumi i dettagli in una frase e chiedi conferma\.\s*$"
    ),
    re.compile(
        r"(?im)^\s*-\s*Esegui il tool solo nel turno successivo a una conferma chiara del cliente\.\s*$"
    ),
    re.compile(r"(?im)^\s*-\s*fai una sola conferma finale\s*$"),
)
CREATE_BOOKING_DIRECT_BLOCK = """

Create Booking Discipline
- Per create_booking: dopo check_availability positivo e dopo che il cliente fornisce il nome,
  esegui subito create_booking.
- Non chiedere "conferma?", "procedo?" o una seconda autorizzazione per una nuova prenotazione.
- La richiesta del nome deve già contenere i dettagli: "Ho disponibilità per domani alle 21. A che nome prenoto?"
- La conferma esplicita separata resta obbligatoria solo per modify_booking e cancel_booking.
""".strip()
OVERRIDE_FIELD_LABELS = {
    "model": "Model",
    "voice": "Voice",
    "tool_choice": "Tool choice",
    "reasoning_effort": "Reasoning effort",
    "temperature": "Temperature",
    "speed": "Speed",
    "max_response_output_tokens": "Max output tokens",
    "turn_detection_type": "Turn detection type",
    "vad_threshold": "VAD threshold",
    "vad_prefix_padding_ms": "Prefix padding ms",
    "vad_silence_duration_ms": "Silence duration ms",
    "vad_idle_timeout_ms": "Idle timeout ms",
    "semantic_vad_eagerness": "Semantic VAD eagerness",
    "interrupt_response": "Interrupt response",
    "create_response": "Create response",
    "noise_reduction_type": "Noise reduction",
    "transcription_model": "Transcription model",
    "input_language": "Input language",
    "tracing_enabled": "Tracing",
    "truncation_mode": "Truncation mode",
    "truncation_retention_ratio": "Retention ratio",
    "truncation_post_instructions_tokens": "Post-instructions tokens",
}
NUMBER_WORDS = {
    "zero": 0,
    "uno": 1,
    "una": 1,
    "un": 1,
    "one": 1,
    "unu": 1,
    "due": 2,
    "two": 2,
    "dos": 2,
    "deux": 2,
    "tre": 3,
    "three": 3,
    "tres": 3,
    "trois": 3,
    "quattro": 4,
    "four": 4,
    "cuatro": 4,
    "quatre": 4,
    "cinque": 5,
    "five": 5,
    "cinco": 5,
    "cinq": 5,
    "sei": 6,
    "six": 6,
    "siete": 7,
    "seven": 7,
    "sept": 7,
    "otto": 8,
    "eight": 8,
    "ocho": 8,
    "huit": 8,
    "nove": 9,
    "nine": 9,
    "nueve": 9,
    "neuf": 9,
    "dieci": 10,
    "ten": 10,
    "diez": 10,
    "dix": 10,
    "undici": 11,
    "eleven": 11,
    "once": 11,
    "onze": 11,
    "dodici": 12,
    "twelve": 12,
    "doce": 12,
    "douze": 12,
}
NEGATIVE_WORDS = {
    "no",
    "non",
    "not",
    "nope",
    "annulla",
    "cancel",
    "cancela",
    "annule",
}
INFO_REQUEST_KEYWORDS = {
    "vegani",
    "vegano",
    "vegetariani",
    "vegetariano",
    "glutine",
    "menu",
    "indirizzo",
    "dove siete",
    "where are you",
}
AVAILABILITY_ONLY_KEYWORDS = {
    "opzioni",
    "options",
    "availability",
    "disponibilita",
    "disponibilità",
    "avete posto",
    "table available",
}
BOOKING_KEYWORDS = {
    "prenot",
    "reservation",
    "reserve",
    "reserva",
    "reservar",
    "book a table",
}
MODIFICATION_KEYWORDS = {"modific", "spost", "change my booking", "cambiar"}
CANCELLATION_KEYWORDS = {"cancel", "cancell", "annull", "supprimer"}
SLOT_19_MARKERS = {"19", "19:00", "7", "7:00", "7pm", "7 p.m", "dalle 19", "from 7", "19 alle 21"}
SLOT_21_MARKERS = {"21", "21:00", "9", "9:00", "9pm", "9 p.m", "dalle 21", "from 9", "21 in poi"}


@dataclass(slots=True)
class RealtimeSessionOverrides:
    model: str | None = None
    voice: str | None = None
    reasoning_effort: Literal["minimal", "low", "medium", "high", "xhigh"] = "low"
    tool_choice: Literal["auto", "none", "required"] = "auto"
    temperature: float = 0.8
    speed: float = 1.1
    max_response_output_tokens: int = 300
    turn_detection_type: Literal["server_vad", "semantic_vad"] = "server_vad"
    vad_threshold: float = 0.58
    vad_prefix_padding_ms: int = 300
    vad_silence_duration_ms: int = 600
    vad_idle_timeout_ms: int = 7000
    semantic_vad_eagerness: Literal["low", "medium", "high", "auto"] = "medium"
    interrupt_response: bool = True
    create_response: bool = True
    noise_reduction_type: Literal["near_field", "far_field", "off"] = "far_field"
    transcription_model: str = "gpt-4o-mini-transcribe"
    input_language: str | None = None
    tracing_enabled: bool = True
    truncation_mode: Literal["retention_ratio", "disabled"] = "retention_ratio"
    truncation_retention_ratio: float = 0.75
    truncation_post_instructions_tokens: int = 1800


@dataclass(slots=True)
class RealtimeCallState:
    started_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    stream_sid: str | None = None
    openai_session_id: str | None = None
    last_user_transcript: str = ""
    last_assistant_transcript: str = ""
    transcript_lines: list[str] = field(default_factory=list)
    assistant_audio_transcript_item_ids: set[str] = field(default_factory=set)
    assistant_audio_transcript_texts: list[str] = field(default_factory=list)
    tool_events: list[dict[str, Any]] = field(default_factory=list)
    outcome: str = "info_provided"
    call_status: str = "unknown"
    caller_phone: str = ""
    twilio_call_sid: str = ""
    technical_tool_failures: int = 0
    response_in_progress: bool = False
    last_audio_item_id: str | None = None
    last_audio_content_index: int = 0
    last_audio_bytes_sent: int = 0
    last_response_usage: dict[str, Any] | None = None
    transcription_events: list[dict[str, Any]] = field(default_factory=list)
    pending_assistant_audio_transcript: str = ""
    pending_tool_followup: bool = False
    pending_user_audio_transcripts: dict[str, str] = field(default_factory=dict)
    end_call_after_response: bool = False
    terminal_write_success: bool = False
    assistant_is_speaking: bool = False
    response_watchdog_generation: int = 0
    response_audio_started: bool = False
    silent_response_retries: int = 0
    dropped_input_audio_packets: int = 0
    recorded_caller_audio_ulaw: bytearray = field(default_factory=bytearray)
    initial_greeting_in_progress: bool = True
    initial_greeting_grace_until: datetime | None = None
    caller_speech_detected: bool = False
    last_caller_activity_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    interruption_events: list[dict[str, Any]] = field(default_factory=list)
    conversation_turns: list[dict[str, str]] = field(default_factory=list)
    conversation_item_ids: list[str] = field(default_factory=list)
    latest_summary: str | None = None
    intent: Literal["unknown", "info", "availability_only", "new_booking", "modify", "cancel"] = "unknown"
    last_requested_field: str | None = None
    last_user_reply_valid: bool = True
    invalid_field_attempts: dict[str, int] = field(default_factory=dict)
    should_escalate: bool = False
    escalation_reason: str | None = None
    booking_context: dict[str, Any] = field(default_factory=dict)
    current_booking_id: str | None = None
    pending_reservation_id: str | None = None
    pending_confirm_payload: dict[str, Any] | None = None


def default_session_overrides() -> RealtimeSessionOverrides:
    return RealtimeSessionOverrides(
        model=settings.openai_realtime_model,
        voice=settings.openai_realtime_voice,
    )


def _override_values(overrides: RealtimeSessionOverrides) -> dict[str, Any]:
    return {
        key: getattr(overrides, key)
        for key in RealtimeSessionOverrides.__dataclass_fields__.keys()
        if getattr(overrides, key) is not None
    }


def _format_override_value(value: Any) -> str:
    if value is None:
        return "default"
    if isinstance(value, bool):
        return "on" if value else "off"
    if isinstance(value, float):
        return f"{value:.2f}".rstrip("0").rstrip(".")
    return str(value)


def restaurant_session_overrides(restaurant: Restaurant) -> RealtimeSessionOverrides:
    raw = restaurant.openai_realtime_settings or {}
    allowed_keys = SUPPORTED_RUNTIME_OVERRIDE_FIELDS
    cleaned = {key: value for key, value in raw.items() if key in allowed_keys and value is not None}
    return replace(default_session_overrides(), **cleaned)


def merged_session_overrides(
    restaurant: Restaurant,
    overrides: RealtimeSessionOverrides | None = None,
) -> RealtimeSessionOverrides:
    base = restaurant_session_overrides(restaurant)
    if overrides is None:
        return base
    return replace(base, **_override_values(overrides))


def _supports_realtime_reasoning(model: str | None) -> bool:
    """Whether the given Realtime model accepts a session.reasoning.effort field.

    Only the GA `gpt-realtime-2` family is reasoning-capable. Older models
    (`gpt-realtime`, `gpt-realtime-1.5`, `gpt-4o-realtime-*`) reject the field.
    """
    return (model or "").strip().startswith("gpt-realtime-2")


def realtime_ws_url(model: str | None) -> str:
    selected_model = (model or settings.openai_realtime_model).strip() or settings.openai_realtime_model
    parts = urlsplit(settings.openai_realtime_base_url)
    query = dict(parse_qsl(parts.query, keep_blank_values=True))
    query["model"] = selected_model
    return urlunsplit(parts._replace(query=urlencode(query)))


def realtime_headers() -> dict[str, str]:
    if not settings.openai_api_key:
        raise RuntimeError("OPENAI_API_KEY non configurata.")
    return {"Authorization": f"Bearer {settings.openai_api_key}"}


def _restaurant_now_strings(restaurant: Restaurant) -> tuple[str, str, str]:
    local_now = datetime.now(UTC).astimezone(ZoneInfo(restaurant.timezone))
    return (
        local_now.strftime("%Y-%m-%d"),
        local_now.strftime("%H:%M"),
        local_now.strftime("%A"),
    )


def _runtime_context_message(restaurant: Restaurant, *, caller_phone: str) -> str:
    current_date, current_time, current_day = _restaurant_now_strings(restaurant)
    return (
        "Contesto runtime della chiamata. "
        f"Numero chiamante: {caller_phone or 'non disponibile'}. "
        f"Data locale: {current_date}. "
        f"Ora locale: {current_time}. "
        f"Giorno locale: {current_day}. "
        "Usa questi dati come riferimento corrente della chiamata."
    )


# Canonical transcription prompt — used both for the Whisper config and for
# filtering echo artifacts from the resulting transcript.
_TRANSCRIPTION_PROMPT_TEXT = "Prenotazione telefonica. Trascrivi quello che dice il cliente."

# Substrings of the transcription prompt that Whisper tends to echo verbatim
# when audio is unclear.  Used by _is_transcription_prompt_echo() below.
_TRANSCRIPTION_PROMPT_ECHO_MARKERS = (
    "trascrivi quello che dice il cliente",
    "prenotazione telefonica. trascrivi",
)


def _transcription_prompt(restaurant: Restaurant) -> str:
    # IMPORTANT: Do NOT include restaurant name, address or other contextual data
    # in the transcription prompt. Whisper-family models echo prompt text verbatim
    # when audio is unclear, causing garbage transcriptions that confuse the agent.
    return _TRANSCRIPTION_PROMPT_TEXT


def _is_transcription_prompt_echo(text: str) -> bool:
    """Return True if *text* looks like Whisper echoing the transcription prompt."""
    lowered = text.strip().lower()
    return any(marker in lowered for marker in _TRANSCRIPTION_PROMPT_ECHO_MARKERS)


def _extract_message_text(output: dict[str, Any]) -> str:
    texts: list[str] = []
    for item in output.get("content", []):
        if item.get("type") in {"text", "output_text"} and item.get("text"):
            texts.append(str(item["text"]).strip())
        if item.get("type") in {"audio", "output_audio"} and item.get("transcript"):
            texts.append(str(item["transcript"]).strip())
    return " ".join(part for part in texts if part).strip()


def _append_transcript_line(state: RealtimeCallState, speaker: str, text: str) -> bool:
    cleaned = text.strip()
    if not cleaned:
        return False
    # Drop lines that are just the transcription prompt echoed by Whisper.
    if _is_transcription_prompt_echo(cleaned):
        return False
    line = f"{speaker}: {cleaned}"
    if state.transcript_lines and state.transcript_lines[-1] == line:
        return False
    state.transcript_lines.append(line)
    return True


def _remember_assistant_audio_transcript(
    state: RealtimeCallState,
    *,
    item_id: str | None,
    text: str,
) -> None:
    if item_id:
        state.assistant_audio_transcript_item_ids.add(item_id)
    normalized = _normalized_text(text)
    if normalized:
        state.assistant_audio_transcript_texts.append(normalized)


def _assistant_message_already_captured_from_audio(
    state: RealtimeCallState,
    *,
    item_id: str | None,
    text: str,
) -> bool:
    if item_id and item_id in state.assistant_audio_transcript_item_ids:
        return True
    normalized = _normalized_text(text)
    if not normalized:
        return False
    for audio_text in state.assistant_audio_transcript_texts:
        if normalized == audio_text or normalized in audio_text or audio_text in normalized:
            return True
    return False


def _record_conversation_turn(
    state: RealtimeCallState,
    *,
    role: Literal["user", "assistant", "system"],
    text: str,
    item_id: str | None = None,
) -> None:
    cleaned = text.strip()
    if not cleaned:
        return
    if item_id:
        state.conversation_item_ids.append(item_id)
    state.conversation_turns.append({"role": role, "text": cleaned, "item_id": item_id or ""})


def _recent_conversation_excerpt(state: RealtimeCallState, *, keep_last_turns: int = SUMMARY_KEEP_LAST_TURNS) -> str:
    excerpt = state.conversation_turns[-keep_last_turns:]
    return "\n".join(f"{turn['role']}: {turn['text']}" for turn in excerpt if turn.get("text"))


def _format_tool_event_for_summary(tool_name: str, result: dict[str, Any]) -> str:
    details: list[str] = []
    if "success" in result:
        details.append(f"success={str(bool(result.get('success'))).lower()}")
    if "available" in result:
        details.append(f"available={str(bool(result.get('available'))).lower()}")
    if "open" in result:
        details.append(f"open={str(bool(result.get('open'))).lower()}")
    if result.get("technical_error"):
        details.append("technical_error=true")
    if result.get("should_escalate") is True:
        details.append("should_escalate=true")
    reason = str(result.get("reason") or "").strip()
    if reason:
        details.append(f"reason={reason}")
    duplicate = result.get("duplicate_booking") if isinstance(result.get("duplicate_booking"), dict) else None
    if duplicate:
        details.append(
            "duplicate_booking="
            f"{duplicate.get('date')} {duplicate.get('time')} "
            f"({duplicate.get('confirmation_code')})"
        )
    if not details:
        details.append("result=recorded")
    return f"TOOL {tool_name} | " + " | ".join(details)


def _normalized_text(text: str) -> str:
    return " ".join(text.lower().strip().split())


def _extract_number_token(token: str) -> int | None:
    cleaned = re.sub(r"[^\w:+]", "", token.lower())
    if cleaned.isdigit():
        return int(cleaned)
    return NUMBER_WORDS.get(cleaned)


def _extract_party_details(text: str) -> dict[str, int]:
    lowered = _normalized_text(text)
    combined_children_match = re.search(
        r"\b(\w+)\s+(?:con|with)\s+(\w+)\s+(?:bambini|bambino|children|kids|enfants|ninos|niños)\b",
        lowered,
    )
    if combined_children_match:
        first_count = _extract_number_token(combined_children_match.group(1))
        second_count = _extract_number_token(combined_children_match.group(2))
        if first_count is not None and second_count is not None:
            return {"total": first_count + second_count, "children": second_count}
    tokens = re.findall(r"\b[\w:']+\b", lowered)
    counts: dict[str, int] = {}
    for index, token in enumerate(tokens):
        number = _extract_number_token(token)
        if number is None:
            continue
        next_token = tokens[index + 1] if index + 1 < len(tokens) else ""
        previous_token = tokens[index - 1] if index > 0 else ""
        if next_token in {"bambini", "bambino", "kids", "children", "enfants", "ninos", "niños"}:
            counts["children"] = number
        elif next_token in {"adulti", "adulti,", "adults", "adultes"}:
            counts["adults"] = number
        elif previous_token == "siamo" or (previous_token == "for" and next_token == "people"):
            counts.setdefault("total", number)
    if "total" not in counts and "adults" in counts and "children" in counts:
        counts["total"] = counts["adults"] + counts["children"]
    return counts


def _extract_time_candidate(text: str) -> time | None:
    lowered = _normalized_text(text)
    match = re.search(r"\b(\d{1,2})(?::(\d{2}))?\s*(a\.?m\.?|p\.?m\.?)?\b", lowered)
    if not match:
        return None
    hour = int(match.group(1))
    minute = int(match.group(2) or 0)
    meridiem = (match.group(3) or "").replace(".", "")
    if meridiem == "pm" and hour < 12:
        hour += 12
    if meridiem == "am" and hour == 12:
        hour = 0
    if hour > 23 or minute > 59:
        return None
    return time(hour, minute)


def _service_period_from_time(requested_time: time | None) -> str | None:
    if requested_time is None:
        return None
    if 12 <= requested_time.hour < 15:
        return "lunch"
    if requested_time.hour >= 19:
        return "dinner"
    return None


def _detect_intent(text: str, current_intent: str) -> str:
    lowered = _normalized_text(text)
    if any(keyword in lowered for keyword in CANCELLATION_KEYWORDS):
        return "cancel"
    if any(keyword in lowered for keyword in MODIFICATION_KEYWORDS):
        return "modify"
    if any(keyword in lowered for keyword in BOOKING_KEYWORDS):
        return "new_booking"
    if any(keyword in lowered for keyword in AVAILABILITY_ONLY_KEYWORDS):
        return "availability_only"
    if any(keyword in lowered for keyword in INFO_REQUEST_KEYWORDS):
        return "info"
    return current_intent


def _detect_requested_field(transcript: str) -> str | None:
    lowered = _normalized_text(transcript)
    if _looks_like_name_confirmation_request(lowered):
        return "customer_name"
    if "seggiolon" in lowered or "high chair" in lowered:
        return "high_chairs"
    if "nome completo" in lowered or "name" in lowered:
        return "customer_name"
    if ("19" in lowered or "21" in lowered or "7 p.m" in lowered or "9 p.m" in lowered) and any(
        cue in lowered for cue in {"quale", "preferisce", "works better", "prefiere", "preferez"}
    ):
        return "service_slot"
    if "giorno" in lowered and "orario" in lowered and ("persone" in lowered or "people" in lowered):
        return "booking_details"
    if "orario" in lowered or "what time" in lowered:
        return "time"
    if "quante persone" in lowered or "how many people" in lowered:
        return "party_size"
    return None


def _text_is_negative(text: str) -> bool:
    lowered = _normalized_text(text)
    return any(re.search(rf"\b{re.escape(word)}\b", lowered) for word in NEGATIVE_WORDS)


def _slot_choice_from_text(text: str) -> str | None:
    lowered = _normalized_text(text)
    if any(marker in lowered for marker in SLOT_19_MARKERS):
        return "slot_19"
    if any(marker in lowered for marker in SLOT_21_MARKERS):
        return "slot_21"
    candidate_time = _extract_time_candidate(lowered)
    if candidate_time and candidate_time.hour in {19, 20}:
        return "slot_19"
    if candidate_time and candidate_time.hour >= 21:
        return "slot_21"
    return None


def _reply_matches_field(text: str, field_name: str | None) -> bool:
    if not field_name:
        return True
    lowered = _normalized_text(text)
    if field_name == "service_slot":
        return _slot_choice_from_text(lowered) is not None
    if field_name == "booking_details":
        return bool(_extract_time_candidate(lowered) or _extract_party_details(lowered))
    if field_name == "party_size":
        return bool(_extract_party_details(lowered))
    if field_name == "time":
        return _extract_time_candidate(lowered) is not None
    if field_name == "customer_name":
        # Reject question marks, very short fragments, and transcription echoes.
        if "?" in text:
            return False
        if _is_transcription_prompt_echo(text):
            return False
        # Must contain at least one word of 2+ alpha chars that isn't a common filler.
        name_tokens = re.findall(r"[a-zA-ZÀ-ÿ]{2,}", text)
        filler_words = {"si", "sì", "ok", "no", "eh", "ah", "grazie", "yes", "yeah", "oui"}
        meaningful = [t for t in name_tokens if t.lower() not in filler_words]
        return len(meaningful) >= 1
    return True


def _update_booking_context(state: RealtimeCallState, transcript: str) -> None:
    party_details = _extract_party_details(transcript)
    if party_details.get("total"):
        state.booking_context["party_size"] = party_details["total"]
    if party_details.get("children") is not None:
        state.booking_context["children"] = party_details["children"]
    if party_details.get("adults") is not None:
        state.booking_context["adults"] = party_details["adults"]

    requested_time = _extract_time_candidate(transcript)
    if requested_time is not None:
        state.booking_context["requested_time"] = requested_time.isoformat()
        service_period = _service_period_from_time(requested_time)
        if service_period:
            state.booking_context["service_period"] = service_period

    slot_choice = _slot_choice_from_text(transcript)
    if slot_choice == "slot_19":
        state.booking_context["requested_time"] = "19:00:00"
        state.booking_context["service_period"] = "dinner"
        state.booking_context["service_slot"] = slot_choice
    elif slot_choice == "slot_21":
        state.booking_context["requested_time"] = "21:00:00"
        state.booking_context["service_period"] = "dinner"
        state.booking_context["service_slot"] = slot_choice

    lowered = _normalized_text(transcript)
    if any(word in lowered for word in {"pranzo", "lunch", "dejeuner", "déjeuner"}):
        state.booking_context["service_period"] = "lunch"
    if any(word in lowered for word in {"cena", "dinner", "diner"}):
        state.booking_context["service_period"] = "dinner"


def _mark_invalid_field_attempt(state: RealtimeCallState, field_name: str) -> None:
    attempts = state.invalid_field_attempts.get(field_name, 0) + 1
    state.invalid_field_attempts[field_name] = attempts
    state.last_user_reply_valid = False
    if attempts >= 2:
        state.should_escalate = True
        state.escalation_reason = field_name


def _clear_invalid_field_attempt(state: RealtimeCallState, field_name: str) -> None:
    state.invalid_field_attempts[field_name] = 0
    state.last_user_reply_valid = True


def _guard_failed_result(state: RealtimeCallState, *, reason: str, assistant_instruction: str) -> dict[str, Any]:
    should_escalate = state.should_escalate
    return {
        "success": False,
        "reason": reason,
        "retry_write": False,
        "should_escalate": should_escalate,
        "assistant_instruction": assistant_instruction,
    }




def _booking_facts_from_state(state: RealtimeCallState) -> dict[str, Any]:
    facts: dict[str, Any] = {}
    for event in state.tool_events:
        if event.get("tool") == "create_booking" and isinstance(event.get("arguments"), dict):
            facts.update(event["arguments"])
            if isinstance(event.get("result"), dict):
                facts.update({key: value for key, value in event["result"].items() if key in {"booking_id"}})
    for key in ("party_size", "requested_time", "customer_name", "date"):
        if key in state.booking_context and key not in facts:
            facts[key] = state.booking_context[key]
    return facts


def _build_final_call_summary(state: RealtimeCallState) -> str:
    facts = _booking_facts_from_state(state)
    if state.outcome == "booking_created":
        party_size = facts.get("party_size", "?")
        booked_time = str(facts.get("time") or facts.get("requested_time") or "?")[:5]
        customer_name = facts.get("customer_name") or "cliente"
        booked_date = facts.get("date") or "data richiesta"
        return (
            f"Prenotazione confermata per {party_size} persone il {booked_date} "
            f"alle {booked_time} a nome {customer_name}."
        )
    if state.outcome == "booking_cancelled":
        # Build a richer summary for cancellations using the tool event data.
        for event in reversed(state.tool_events):
            if event.get("tool") == "cancel_booking" and isinstance(event.get("result"), dict):
                code = (event.get("arguments") or {}).get("confirmation_code", "")
                if code:
                    return f"Prenotazione {code} cancellata su richiesta del cliente."
                return "Prenotazione cancellata su richiesta del cliente."
        return "Prenotazione cancellata su richiesta del cliente."
    if state.outcome == "booking_modified":
        return "Prenotazione modificata su richiesta del cliente."
    if state.outcome == "escalated":
        return "Chiamata trasferita al ristorante."
    if state.outcome == "tool_error":
        return "Chiamata non completata: servono chiarimenti o intervento del ristorante."
    if state.intent == "availability_only" and state.tool_events:
        last_event = state.tool_events[-1]
        if last_event.get("tool") == "check_availability" and isinstance(last_event.get("result"), dict):
            result = last_event["result"]
            requested_time = str(
                (last_event.get("arguments") or {}).get("time")
                or state.booking_context.get("requested_time")
                or "orario richiesto"
            )[:5]
            requested_date = (last_event.get("arguments") or {}).get("date") or "data richiesta"
            if result.get("available") is True:
                return f"Disponibilita fornita per {requested_date} alle {requested_time}."
            return f"Disponibilita non confermata per {requested_date} alle {requested_time}."
    if state.intent == "info":
        return "Informazioni fornite al cliente."
    return state.transcript_lines[-1][:500] if state.transcript_lines else "Chiamata completata."


def _build_final_conversation_summary(state: RealtimeCallState) -> str:
    summary = _build_final_call_summary(state)
    tool_bits = []
    for event in state.tool_events:
        result = event.get("result") if isinstance(event.get("result"), dict) else {}
        if event.get("tool") == "check_availability":
            tool_bits.append(
                f"check_availability available={str(bool(result.get('available'))).lower()}"
            )
        if event.get("tool") == "create_booking":
            tool_bits.append(f"create_booking success={str(bool(result.get('success'))).lower()}")
    tool_line = f"Tool: {', '.join(tool_bits)}." if tool_bits else "Tool: nessun tool rilevante."
    intent_line = f"Intento: {state.intent}."
    return "\n".join([intent_line, f"Esito: {summary}", tool_line])


def _build_conversation_summary_prompt(turns: list[dict[str, str]]) -> str:
    transcript = "\n".join(f"{turn['role']}: {turn['text']}" for turn in turns if turn.get("text"))
    return (
        "Riassumi questa telefonata per mantenere il contesto operativo del ristorante. "
        "Mantieni solo fatti utili: intento, data/orario richiesti, numero persone, nome cliente, "
        "vincoli, conferme ottenute, errori tecnici, decisioni e follow-up. "
        "Scrivi in italiano, massimo 6 righe brevi, senza inventare nulla.\n"
        "- I turni di sistema TOOL sono fatti autorevoli e prevalgono su frasi dell'assistente se c'è conflitto.\n"
        "- Le trascrizioni possono contenere errori: se manca certezza, segnala l'incertezza invece di indovinare.\n"
        "- NON dichiarare una prenotazione confermata se create_booking non ha success=true.\n"
        "- Se un tool di scrittura fallisce, dillo esplicitamente nel riassunto.\n\n"
        f"{transcript}"
    )


def _successful_call_outcome(state: RealtimeCallState) -> str:
    if state.outcome != "info_provided":
        return state.outcome

    assistant_text = state.last_assistant_transcript.lower()
    if "la metto in contatto con il ristorante" in assistant_text:
        escalate_event = next(
            (
                event
                for event in state.tool_events
                if event.get("tool") == "escalate_to_human"
            ),
            None,
        )
        if escalate_event is not None:
            result = escalate_event.get("result")
            if isinstance(result, dict) and (
                result.get("transferred") is True or result.get("success") is True
            ):
                return "escalated"
            if isinstance(result, dict) and (
                result.get("transferred") is False or result.get("success") is False
            ):
                return "escalation_failed"
        return "escalated"

    if not state.tool_events and not state.transcription_events:
        return "abandoned"

    failed_write_attempt = any(
        event.get("tool") in {"create_booking", "modify_booking", "cancel_booking"}
        and isinstance(event.get("result"), dict)
        and event["result"].get("success") is False
        for event in state.tool_events
    )
    if failed_write_attempt:
        return "tool_error"

    return state.outcome


def build_realtime_instructions(
    restaurant: Restaurant,
    *,
    caller_phone: str,
    prompt_override: str | None = None,
) -> str:
    stored_prompt = (restaurant.openai_prompt_override or "").strip()
    if prompt_override and prompt_override.strip():
        return stabilize_realtime_prompt(prompt_override.strip(), restaurant=restaurant)
    if stored_prompt:
        return stabilize_realtime_prompt(stored_prompt, restaurant=restaurant)

    booking_rules = restaurant.booking_rules or {}
    large_group_threshold = booking_rules.get("large_group_threshold", 8)
    prompt = f"""
Role & Objective
- Sei il receptionist telefonico di {restaurant.name}.
- Gestisci nuove prenotazioni, modifiche, cancellazioni, richieste informative e passaggio a un umano quando serve.
- Obiettivo primario: chiudere la richiesta nel minor numero di turni possibile, con chiarezza e affidabilità.
- Quando la richiesta è completata davvero e il cliente non aggiunge altro, chiudi subito.
- Il passaggio al ristorante è una ultima risorsa: usalo solo nei casi esplicitamente previsti sotto.

Personality & Tone
- Parla in italiano naturale, caldo, conciso e orientato all'azione.
- Parla con intonazione italiana naturale, con ritmo melodico e pause espressive tipiche del parlato italiano.
- Massimo due frasi brevi per turno. Se basta una frase, usane una sola.
- Tono: ospitale, elegante, diretto, mai robotico.
- Velocità: rispondi rapidamente ma senza sembrare di fretta.
- Nessun gergo tecnico, nessun riepilogo lungo, nessuna spiegazione di sistema.
- Non aprire ogni risposta con un nuovo saluto o con il nome del ristorante.
- Se un'informazione è già chiara, non richiederla. Se il cliente la ripete, accettala e vai avanti.
- Nomi clienti: un solo nome o cognome è sufficiente. Se il cliente dice "Manuel" o "Bueno",
  accettalo subito senza chiedere il nome completo.
- Se il nome è insolito o l'audio è sporco, usa la parola sentita senza bloccarti a chiedere lo spelling
  o ulteriori conferme.
- Se il cliente chiede il tuo nome: "Mi chiamo Edoardo."
- Se chiede se sei un'AI: "Sono l'assistente digitale del ristorante. Mi dica pure."
- Stile: {restaurant.agent_style_notes or "Tono ospitale, elegante e diretto."}
- Varia leggermente le frasi brevi di cortesia e i preamboli, ma non cambiare la struttura del flusso.
- Puoi usare raramente parole naturali come "Ecco" o "Allora" se aiutano il ritmo, ma senza esagerare.

Greeting Discipline
- Usa "Buongiorno" o "Buonasera" solo nel primo turno della chiamata.
- Dopo il saluto iniziale, non iniziare più le risposte con "Buongiorno", "Buonasera", "Salve" o il nome del ristorante.
- Nei turni successivi vai diretto al dato mancante, al preambolo del tool o alla conferma.
- Se il cliente saluta di nuovo, rispondi brevemente ma non riaprire la chiamata.

Language
- Se il cliente parla chiaramente un'altra lingua che sai gestire, segui la lingua del cliente.
- Apri in italiano solo come default iniziale quando il chiamante non ha ancora mostrato un'altra lingua.
- Per impostazione predefinita, seguire la lingua del cliente funziona meglio che forzare sempre l'italiano.
- Se cambi lingua, cambia SOLO la lingua della risposta:
  non cambiare il flusso, le regole di conferma, i tool, i criteri di sicurezza o il livello di servizio.
- Non cambiare lingua inutilmente a metà conversazione.
- Se l'audio è poco chiaro, chiedi una sola volta di ripetere lentamente nella lingua che il cliente sta usando.
- Non trasferire solo perché non sta parlando italiano.
- Trasferisci solo se la lingua resta incomprensibile
  o se davvero non riesci a gestire bene la richiesta in quella lingua.

Context
- Ristorante: {restaurant.name}
- Indirizzo: {restaurant.address}
- Timezone: {restaurant.timezone}
- Orari: {json.dumps(restaurant.opening_hours or {{}}, ensure_ascii=False)}
- Turni: {json.dumps(restaurant.turni or [], ensure_ascii=False)}
- Chiusure settimanali: {json.dumps(restaurant.weekly_closures or [], ensure_ascii=False)}
- Chiusure straordinarie: {json.dumps(restaurant.closure_dates or [], ensure_ascii=False)}
- Soglia grandi gruppi: {large_group_threshold}
- Saluto iniziale solo per apertura della chiamata:
  {restaurant.custom_greeting or "Buongiorno, come posso aiutarla?"}

CRITICAL RULES — NEVER VIOLATE
- MAI INVENTARE DATI: disponibilità, dettagli, nomi, date, orari o risultati.
- ASPETTA L'INTENTO DEL CLIENTE. Se è già chiaro, vai subito al punto.
- Se ti manca un dato obbligatorio, chiedi solo il dato mancante.
- Non fare più di una domanda per turno, salvo quando raccogli insieme
  giorno, ora e numero persone all'inizio di una nuova prenotazione.
- Non ripetere la stessa domanda, la stessa conferma o lo stesso riepilogo più di una volta.
- Non chiedere mai il numero di telefono.
- Non dire mai l'anno nelle date.
- Non promettere SMS, WhatsApp o email di conferma.
- Non lasciare la conversazione aperta.
- Non trasferire al ristorante per normali chiarimenti su giorno, orario, numero persone o nome.
- SE UNA TRASCRIZIONE È SENZA SENSO, non trattarla come dato valido.
  Esempi: "DX", "casi persone", lettere isolate, rumori o parole incompatibili con la domanda.

Tools
- Strumento di attesa silenziosa: wait_for_user.
- Strumenti di lettura: check_availability, find_booking.
- Strumenti di scrittura: create_booking, modify_booking, cancel_booking.
- Se l'ultimo audio è silenzio, rumore di fondo, musica d'attesa, TV, conversazione laterale
  o parole non rivolte a te, chiama wait_for_user e non rispondere a voce.
- Dopo wait_for_user resta in ascolto. Non dire "sono qui", "non ho capito", "faccia pure"
  o frasi simili. Riprendi solo quando il cliente si rivolge chiaramente a te.
- Usa wait_for_user solo per audio non rivolto all'assistente, non per richieste chiare ma incomprensibili:
  in quel caso chiedi una chiarificazione mirata.
- Usa gli strumenti di lettura in modo proattivo quando servono.
- Usa gli strumenti di scrittura rispettando le regole di conferma specifiche per l'azione
  (creazione vs modifica/cancellazione) spiegate nella sezione Write Action Rules.
- Prima di un tool di lettura (check_availability, find_booking) usa al massimo una frase breve:
  "Controllo subito." oppure "Verifico."
- Per gli strumenti di scrittura (create_booking, modify_booking, cancel_booking) NON usare un preambolo vocale.
  Chiama direttamente il tool nella stessa risposta e poi comunica l'esito in una sola frase breve.
  NON dire "Un momento", "Procedo", "Controllo" prima di uno strumento di scrittura.
- Chiama il tool subito dopo il preambolo (per gli strumenti di lettura)
  o nella stessa risposta (per gli strumenti di scrittura).
- Se il tool restituisce retry_write=false o should_escalate=true, non ripeterlo nello stesso turno.
- Se un tool di scrittura riesce, comunica l'esito in una sola frase breve, saluta e chiudi la chiamata.
- Nel saluto finale, quando possibile, usa il nome del cliente
  (es. "Grazie Marco, a presto!" o "A presto Juan, buona serata!").
  Se non conosci il nome, usa un saluto cortese generico.

Conversation Flow
- Se l'intento è già chiaro, vai subito nel flusso corretto. Non chiedere "Come posso aiutarla?" se non serve.
- Se la conversazione è già avviata, rispondi direttamente senza ripetere un saluto iniziale.
- Se il cliente chiede genericamente disponibilità senza giorno/ora/persone,
  fai UNA SOLA domanda breve che raccolga i dati mancanti
  (es. "Per che giorno, ora e quante persone?").
  Non aggiungere una seconda formulazione della stessa domanda nello stesso turno.
- Nuova prenotazione:
  - raccogli giorno, ora e numero persone
  - se manca un dato, chiedi solo quel dato
  - usa check_availability prima di confermare
  - se c'è disponibilità, raccogli il nome
  - usa la richiesta del nome come recita dei dettagli (es. "Ho disponibilità per stasera alle 21. A che nome prenoto?")
  - appena il cliente fornisce un nome, esegui subito create_booking senza chiedere altre conferme o permessi
- Se l'orario non è disponibile, proponi SOLO orari presenti nel campo "alternatives"
  restituito da check_availability. Se "alternatives" è vuoto, dì che per quell'orario
  non c'è disponibilità e chiedi al cliente un altro giorno od orario.
  Non inventare mai orari non restituiti dal tool.
- Se il nome non è chiaro dopo due tentativi mirati, passa a un umano.
- Modifica e cancellazione:
  - trova prima la prenotazione
  - se serve distinguere più risultati, chiedi il minimo indispensabile
  - fai una sola conferma finale
  - esegui solo nel turno successivo a un sì esplicito
- Richieste informative:
  - rispondi solo con dati presenti nel contesto
  - se il dato non è nel contesto, dì che non puoi confermarlo con certezza
  - trasferisci solo se il cliente chiede una verifica umana o serve una decisione operativa
  - per domande sul menu rispondi solo:
    "Offriamo cucina tradizionale milanese: risotti, cotoletta,
    ossobuco e altre specialità lombarde."
  - Dopo una risposta informativa completa, fai una sola breve apertura naturale, per esempio:
    "Se vuole, posso anche aiutarla con una prenotazione."
  - Se il cliente non aggiunge altro o resta in silenzio, chiudi con cortesia.
- Non comunicare mai il codice di conferma.
- Se devi leggere numeri, codici, iniziali o lettere, scandiscili elemento per elemento con piccole pause naturali.
- Per orari e numeri di telefono letti ad alta voce, pronuncia chiaramente cifra per cifra quando serve evitare errori.
- Chiusura:
  - quando la richiesta è risolta, fai una sola frase finale breve
  - non aggiungere altre domande
  - non attendere altro se hai già dato conferma o risposta completa

Phone Rules
- Il numero del chiamante viene fornito dal sistema nel contesto runtime.
- Il numero del chiamante è registrato automaticamente dal sistema. Non includerlo nei parametri del tool.
- Se il cliente chiede di usare un numero diverso, non prometterlo e passa a un umano.
- Se un nome proprio è straniero o non italiano, non tradurlo e non accorciarlo.
- Ripetilo con la pronuncia più naturale possibile senza cambiare il comportamento del servizio.

Write Action Rules
- Per create_booking: la fornitura del nome da parte del cliente dopo aver suggerito i dettagli equivale a una
  conferma tacita. Esegui il tool in quel preciso turno senza dire "Conferma?".
- Per modify_booking o cancel_booking: sii più cauto, chiedi una conferma veloce
  ("Cencello per domani alle 21, giusto?") ed esegui al prossimo sì.
- Se cambiano data, ora o numero persone dopo check_availability, rifai check_availability prima di scrivere.
- Sii elastico: se l'audio è sporco o la risposta è un "ok"/"va bene" debole, per la creazione consideralo
  pienamente affermativo. Non bloccarti in loop di rassicurazioni.
- Non dire mai al cliente quali parole esatte deve usare per rispondere.
- Se una conferma per cancellazione/modifica resta totalmente incomprensibile dopo un tentativo mirato,
  passa a un umano.
- Non chiamare modify_booking o cancel_booking se l'ultima risposta del cliente è incomprensibile,
  fuori tema, un saluto, un riempitivo o in una lingua che non stai gestendo: chiedi ancora conferma
  una sola volta, poi escala se non arriva.

Duplicate Prevention
- Non creare mai prenotazioni duplicate.
- Se risulta già una prenotazione attiva dello stesso chiamante nello stesso giorno e orario:
  - dillo chiaramente
  - offri di lasciarla così, modificarla o cancellarla
  - trasferisci solo se il cliente chiede il ristorante o il caso esce dal perimetro
  - formula breve possibile:
    "Risulta già una prenotazione da questo numero. Posso lasciarla così, modificarla o cancellarla."

Safety & Escalation
- Escala solo quando serve davvero. Non usare il trasferimento come scorciatoia.
- Escala subito se il cliente lo chiede.
- Escala subito se il gruppo supera {large_group_threshold} persone.
- Escala subito se emergono allergie o richieste fuori policy.
- Escala se l'audio resta poco chiaro dopo due tentativi mirati sulla stessa informazione obbligatoria.
- Escala se due tentativi di tool sulla stessa richiesta falliscono.
- Escala se la chiamata supera circa 90 secondi o 8 turni dell'assistente senza chiusura chiara.
- Non escalare se puoi ancora risolvere con una sola domanda mirata.
- Prima di pronunciare la formula "La metto in contatto con il ristorante",
  chiama SEMPRE lo strumento escalate_to_human.
  Non dirlo mai senza aver già eseguito il tool nel turno corrente.
- Dopo un'azione completata con successo, chiudi subito con cortesia.

Unclear Audio
- Se non capisci qualcosa, chiedi solo la parte mancante.
- Non indovinare mai.
- Se non hai sentito bene nome, orario, numero persone, data o conferma, chiedi di ripetere solo quel pezzo.
- Se la risposta sembra incompatibile con la domanda, correggi subito:
  "Mi scusi, non ho capito bene quel dettaglio. Può ripetere solo l'orario?"
- Non dire "perfetto" dopo una risposta poco chiara o senza senso.
- Se c'è rumore e l'interazione è chiaramente orientata a completare la prenotazione, fidati dell'intento
  e procedi. Non interrompere il flusso magico per incomprensioni vocali minori.
- Usa tentativi mirati solo se manca un dato obbligatorio (es. data o ora). Al massimo due tentativi, poi escala.
""".strip()
    return stabilize_realtime_prompt(prompt, restaurant=restaurant)


def stabilize_realtime_prompt(prompt: str, *, restaurant: Restaurant | None = None) -> str:
    cleaned = prompt.strip()
    removed_repeated_greeting_rule = False
    for pattern in REPEATED_GREETING_PATTERNS:
        cleaned, replacements = pattern.subn("", cleaned)
        removed_repeated_greeting_rule = removed_repeated_greeting_rule or replacements > 0
    removed_create_confirmation_rule = False
    for pattern in CREATE_BOOKING_CONFIRMATION_PATTERNS:
        cleaned, replacements = pattern.subn("", cleaned)
        removed_create_confirmation_rule = removed_create_confirmation_rule or replacements > 0
    if restaurant is not None:
        cleaned = refresh_realtime_prompt_context(cleaned, restaurant)
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned).strip()
    if removed_repeated_greeting_rule and "Greeting Discipline" not in cleaned:
        cleaned = f"{cleaned}\n\n{GREETING_DISCIPLINE_BLOCK}"
    if removed_create_confirmation_rule and "Create Booking Discipline" not in cleaned:
        cleaned = f"{cleaned}\n\n{CREATE_BOOKING_DIRECT_BLOCK}"
    return cleaned


def refresh_realtime_prompt_context(prompt: str, restaurant: Restaurant) -> str:
    booking_rules = restaurant.booking_rules or {}
    large_group_threshold = booking_rules.get("large_group_threshold", 8)
    replacements = {
        "Ristorante": restaurant.name,
        "Indirizzo": restaurant.address,
        "Timezone": restaurant.timezone,
        "Orari": json.dumps(restaurant.opening_hours or {}, ensure_ascii=False),
        "Turni": json.dumps(restaurant.turni or [], ensure_ascii=False),
        "Chiusure settimanali": json.dumps(restaurant.weekly_closures or [], ensure_ascii=False),
        "Chiusure straordinarie": json.dumps(restaurant.closure_dates or [], ensure_ascii=False),
        "Soglia grandi gruppi": str(large_group_threshold),
    }
    refreshed = prompt
    for label, value in replacements.items():
        refreshed = re.sub(rf"(?m)^-\s*{re.escape(label)}:\s.*$", f"- {label}: {value}", refreshed)
    return refreshed


def build_realtime_tools(
    *,
    tool_names: tuple[str, ...] = FULL_TOOL_NAMES,
) -> list[dict[str, Any]]:
    tools = [
        {
            "type": "function",
            "name": "wait_for_user",
            "description": (
                "Call this when the latest audio does not need a spoken response, "
                "such as silence, background noise, hold music, TV audio, side "
                "conversation, or speech not addressed to the assistant. This tool "
                "helps end the turn without a spoken reply."
            ),
            "parameters": {
                "type": "object",
                "properties": {},
                "required": [],
                "additionalProperties": False,
            },
        },
        {
            "type": "function",
            "name": "check_availability",
            "description": (
                "Verifica la disponibilità per una richiesta di prenotazione. "
                "Usalo SOLO quando il cliente ha esplicitamente detto data E numero di persone. "
                "Conta adulti e bambini nel totale del numero di persone. "
                "Se il cliente chiede solo opzioni o disponibilita, puoi usare questo tool "
                "ma NON trasformare la richiesta in prenotazione. "
                "NON chiamare con dati inventati o assunti."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "date": {
                        "type": "string",
                        "description": (
                            "Data in formato YYYY-MM-DD. Converti riferimenti relativi "
                            "(domani, dopodomani) in data assoluta. "
                            "DEVE provenire da ciò che il cliente ha detto."
                        ),
                    },
                    "party_size": {
                        "type": "integer",
                        "description": (
                            "Numero di persone. DEVE essere un numero esplicitamente "
                            "detto dal cliente. Non inventare."
                        ),
                    },
                    "time": {
                        "type": "string",
                        "description": (
                            "Orario richiesto in formato HH:MM:SS (es. 'stasera' -> 19:30 o 20:00). "
                            "DEVE essere fornito per controllare eventuali disponibilità."
                        ),
                    },
                },
                "required": ["date", "time"],
                "additionalProperties": False,
            },
        },
        {
            "type": "function",
            "name": "find_booking",
            "description": (
                "Cerca prenotazioni esistenti del chiamante o tramite codice. "
                "Usalo per verifica, modifica, cancellazione e controllo duplicati."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "confirmation_code": {
                        "type": "string",
                        "description": "Codice prenotazione se il cliente lo fornisce.",
                    },
                },
                "required": [],
                "additionalProperties": False,
            },
        },
        {
            "type": "function",
            "name": "create_booking",
            "description": (
                "Crea una nuova prenotazione confermata. "
                "REQUISITI: 1) check_availability già chiamato con esito positivo, "
                "2) tutti i dati raccolti ESPLICITAMENTE dal cliente "
                "(data, ora, persone, nome). "
                "La fornitura del nome da parte del cliente equivale a confermare. NON chiedere ulteriori consensi. "
                "Se il cliente ha chiesto solo opzioni o disponibilita, NON usare questo tool "
                "finché non decide esplicitamente di prenotare (es. fornendo un nome). "
                "NON usare dati inventati o assunti. "
                "Se il cliente cambia data, ora o numero persone dopo la verifica, "
                "rifai check_availability prima di usare questo tool. "
                "Non includere il numero di telefono, viene catturato automaticamente."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "date": {
                        "type": "string",
                        "description": (
                            "Data in formato YYYY-MM-DD. Deve corrispondere a "
                            "check_availability."
                        ),
                    },
                    "time": {
                        "type": "string",
                        "description": (
                            "Orario in formato HH:MM:SS. Usa SOLO uno slot restituito da "
                            "check_availability."
                        ),
                    },
                    "party_size": {
                        "type": "integer",
                        "description": "Numero esatto di coperti DETTO dal cliente.",
                    },
                    "customer_name": {
                        "type": "string",
                        "description": "Solo il nome DETTO dal cliente. Non inventare.",
                    },
                    "special_requests": {
                        "type": "string",
                        "description": (
                            "Richieste speciali o note logistiche (es. cani, seggioloni, "
                            "preferenza tavolo fuori/dentro). Ometti allergie o richieste fuori policy."
                        ),
                    },
                },
                "required": ["date", "time", "party_size", "customer_name"],
                "additionalProperties": False,
            },
        },
        {
            "type": "function",
            "name": "modify_booking",
            "description": (
                "Modifica una prenotazione esistente. "
                "Usalo solo dopo un riepilogo finale e una conferma chiara del cliente, "
                "e solo per i campi che cambiano davvero. "
                "Se cambiano data, ora o coperti, verifica prima la nuova disponibilita."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "confirmation_code": {
                        "type": "string",
                        "description": "Codice della prenotazione da modificare.",
                    },
                    "date": {
                        "type": "string",
                        "description": "Nuova data in formato YYYY-MM-DD.",
                    },
                    "time": {
                        "type": "string",
                        "description": "Nuovo orario in formato HH:MM:SS.",
                    },
                    "party_size": {
                        "type": "integer",
                        "description": "Nuovo numero di coperti.",
                    },
                    "customer_name": {"type": "string", "description": "Nuovo nome se richiesto."},
                    "customer_phone": {
                        "type": "string",
                        "description": "Nuovo numero se richiesto.",
                    },
                    "special_requests": {
                        "type": "string",
                        "description": "Nuove richieste speciali.",
                    },
                    "confirmation_summary": {
                        "type": "string",
                        "description": (
                            "Riepilogo in una frase di ciò che il cliente ha appena "
                            "confermato in modo esplicito (es. 'riduce a 2 persone "
                            "domani alle 12'). Scrivilo solo dopo un sì chiaro."
                        ),
                    },
                },
                "required": ["confirmation_code"],
                "additionalProperties": False,
            },
        },
        {
            "type": "function",
            "name": "cancel_booking",
            "description": (
                "Cancella una prenotazione esistente. "
                "Usalo solo dopo un riepilogo finale e una conferma chiara del cliente."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "confirmation_code": {
                        "type": "string",
                        "description": "Codice della prenotazione da cancellare.",
                    },
                    "confirmation_summary": {
                        "type": "string",
                        "description": (
                            "Riepilogo in una frase di ciò che il cliente ha appena "
                            "confermato (es. 'cancella la prenotazione di domani alle 21'). "
                            "Scrivilo solo dopo un sì chiaro."
                        ),
                    },
                },
                "required": ["confirmation_code"],
                "additionalProperties": False,
            },
        },
        {
            "type": "function",
            "name": "escalate_to_human",
            "description": (
                "Trasferisce la chiamata a un umano quando il flusso richiede "
                "assistenza del ristorante."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "reason": {
                        "type": "string",
                        "description": "Motivo sintetico dell'escalation.",
                    },
                },
                "required": [],
                "additionalProperties": False,
            },
        },
    ]
    return [tool for tool in tools if tool["name"] in tool_names]


def build_session_update(
    restaurant: Restaurant,
    *,
    caller_phone: str,
    output_modalities: tuple[str, ...] = ("audio",),
    prompt_override: str | None = None,
    overrides: RealtimeSessionOverrides | None = None,
    include_diagnostics: bool = False,
    tool_names: tuple[str, ...] = INITIAL_TOOL_NAMES,
) -> dict[str, Any]:
    tuning = merged_session_overrides(restaurant, overrides)
    selected_model = tuning.model or settings.openai_realtime_model

    # -- Turn detection (flat session field per API spec) -------------------
    if tuning.turn_detection_type == "semantic_vad":
        turn_detection: dict[str, Any] = {
            "type": "semantic_vad",
            "eagerness": tuning.semantic_vad_eagerness,
            "create_response": tuning.create_response,
            "interrupt_response": tuning.interrupt_response,
        }
    else:
        turn_detection = {
            "type": "server_vad",
            "threshold": tuning.vad_threshold,
            "prefix_padding_ms": tuning.vad_prefix_padding_ms,
            "silence_duration_ms": tuning.vad_silence_duration_ms,
            "interrupt_response": tuning.interrupt_response,
            "create_response": tuning.create_response,
        }

    # -- Session payload (GA shape for current Realtime WebSocket interface) --
    session: dict[str, Any] = {
        "type": "realtime",
        "model": selected_model,
        "instructions": build_realtime_instructions(
            restaurant,
            caller_phone=caller_phone,
            prompt_override=prompt_override,
        ),
        "audio": {
            "input": {
                "format": {"type": "audio/pcmu"},
                "transcription": {
                    "model": tuning.transcription_model,
                    "prompt": _transcription_prompt(restaurant),
                },
                "turn_detection": turn_detection,
            }
        },
        "tools": build_realtime_tools(tool_names=tool_names),
        "tool_choice": tuning.tool_choice,
        "parallel_tool_calls": False,
        "tracing": "auto" if tuning.tracing_enabled else None,
    }
    # Realtime reasoning is gpt-realtime-2 only; older models reject the field.
    if _supports_realtime_reasoning(selected_model):
        session["reasoning"] = {"effort": tuning.reasoning_effort}
    if tuning.turn_detection_type == "server_vad":
        session["audio"]["input"]["turn_detection"]["idle_timeout_ms"] = tuning.vad_idle_timeout_ms

    if "audio" in output_modalities:
        # OpenAI Realtime accepts speed in [0.25, 1.5]; default 1.0. Clamp defensively.
        speed_clamped = max(0.25, min(1.5, float(tuning.speed)))
        session["audio"]["output"] = {
            "format": {"type": "audio/pcmu"},
            "voice": tuning.voice or settings.openai_realtime_voice,
            "speed": speed_clamped,
        }

    # Cap response length. The GA spec field is `max_output_tokens` (not `max_response_output_tokens`);
    # accepted range is [1, 4096]. Without this the API defaults to "inf", which lets the model
    # produce overly long responses — bad for a phone agent and especially with reasoning enabled.
    session["max_output_tokens"] = max(1, min(4096, int(tuning.max_response_output_tokens)))

    if tuning.noise_reduction_type != "off":
        session["audio"]["input"]["noise_reduction"] = {"type": tuning.noise_reduction_type}
    if tuning.input_language:
        session["audio"]["input"]["transcription"]["language"] = tuning.input_language

    if tuning.truncation_mode == "retention_ratio":
        session["truncation"] = {
            "type": "retention_ratio",
            "retention_ratio": tuning.truncation_retention_ratio,
        }

    if include_diagnostics:
        session["include"] = ["item.input_audio_transcription.logprobs"]

    return {"type": "session.update", "session": session}


def _build_tool_scope_update(
    *,
    tool_names: tuple[str, ...],
    overrides: RealtimeSessionOverrides,
) -> dict[str, Any]:
    return {
        "type": "session.update",
        "session": {
            "type": "realtime",
            "tools": build_realtime_tools(tool_names=tool_names),
            "tool_choice": overrides.tool_choice,
        },
    }


def _realtime_tool_requires_followup(tool_name: str, result: dict[str, Any]) -> bool:
    if tool_name == "wait_for_user" or result.get("wait") is True:
        return False
    return True


def _make_auto_escalate_dtmf_event(digit: str, transferred: bool) -> dict[str, Any]:
    """Synthetic tool_events entry for the DTMF escalation path. Pure function for testability."""
    now_iso = datetime.now(UTC).isoformat()
    return {
        "tool": "_auto_escalate_dtmf",
        "arguments": {"digit": digit},
        "result": {"transferred": bool(transferred)},
        "started_at": now_iso,
        "completed_at": now_iso,
        "latency_ms": 0,
    }


def _make_auto_escalate_exception_event(exc: BaseException, transferred: bool) -> dict[str, Any]:
    """Synthetic tool_events entry for the exception-fallback escalation path."""
    now_iso = datetime.now(UTC).isoformat()
    return {
        "tool": "_auto_escalate_exception",
        "arguments": {
            "error_type": type(exc).__name__,
            "error_message": str(exc)[:200],
        },
        "result": {"transferred": bool(transferred)},
        "started_at": now_iso,
        "completed_at": now_iso,
        "latency_ms": 0,
    }


def _buffer_twilio_media_payload(
    state: RealtimeCallState,
    *,
    payload: str,
    buffered_audio: bytearray,
    buffered_packets: int,
) -> int:
    try:
        decoded = base64.b64decode(payload)
    except Exception:
        return buffered_packets
    if not decoded:
        return buffered_packets
    buffered_audio.extend(decoded)
    state.recorded_caller_audio_ulaw.extend(decoded)
    return buffered_packets + 1


def studio_readiness(restaurant: Restaurant) -> list[dict[str, str]]:
    public_base_url = (settings.public_base_url or "").strip()
    readiness = [
        {
            "label": "OpenAI API key",
            "status": "good" if settings.openai_api_key else "warn",
            "detail": (
                "Presente: il bridge realtime puo aprire una sessione live."
                if settings.openai_api_key
                else "Manca OPENAI_API_KEY: simulazioni realtime e chiamate vere non partiranno."
            ),
        },
        {
            "label": "PUBLIC_BASE_URL",
            "status": "good" if public_base_url else "warn",
            "detail": (
                f"Configurato su {public_base_url}."
                if public_base_url
                else "Manca PUBLIC_BASE_URL: Twilio non sapra dove aprire stream e callback."
            ),
        },
        {
            "label": "Twilio number",
            "status": "good" if restaurant.twilio_phone else "warn",
            "detail": (
                f"Numero attivo: {restaurant.twilio_phone}."
                if restaurant.twilio_phone
                else "Il ristorante non ha ancora un numero Twilio assegnato."
            ),
        },
        {
            "label": "Voice provider",
            "status": "good" if restaurant.voice_provider == "openai_realtime" else "warn",
            "detail": (
                "Il ristorante usa OpenAI Realtime come runtime live."
                if restaurant.voice_provider == "openai_realtime"
                else f"Il ristorante risulta ancora su {restaurant.voice_provider}."
            ),
        },
        {
            "label": "Tool secret",
            "status": "good" if settings.tool_secret else "warn",
            "detail": (
                "Presente: il percorso tool HTTP resta verificabile e i test backend "
                "possono usare lo stesso secret."
                if settings.tool_secret
                else (
                    "Manca TOOL_SECRET: i controlli e le integrazioni tool "
                    "esterne non sono protetti correttamente."
                )
            ),
        },
        {
            "label": "Escalation path",
            "status": (
                "good"
                if restaurant.escalation_phone and settings.twilio_account_sid and settings.twilio_auth_token
                else "warn"
            ),
            "detail": (
                f"Trasferimento live pronto verso {restaurant.escalation_phone}."
                if restaurant.escalation_phone and settings.twilio_account_sid and settings.twilio_auth_token
                else (
                    "Per trasferire una chiamata servono escalation_phone del ristorante "
                    "e credenziali Twilio lato backend."
                )
            ),
        },
    ]
    return readiness


def studio_checklist(session_update: dict[str, Any]) -> list[dict[str, str]]:
    session = session_update["session"]
    audio_input = ((session.get("audio") or {}).get("input") or {})
    turn_detection = audio_input.get("turn_detection") or {}
    truncation = session.get("truncation")
    tool_count = len(session.get("tools") or [])
    checks = [
        {
            "label": "GA session shape",
            "status": "good" if session.get("type") == "realtime" else "warn",
            "detail": "La sessione deve dichiarare type=realtime per restare allineata alla GA interface.",
        },
        {
            "label": "Prompt strutturato",
            "status": "good" if "Role & Objective" in session["instructions"] else "warn",
            "detail": "Sezioni dedicate migliorano instruction following e debugging.",
        },
        {
            "label": "Lingua vincolata",
            "status": "good" if "Language" in session["instructions"] else "warn",
            "detail": "Riduce i cambi di lingua indesiderati nelle chiamate rumorose.",
        },
        {
            "label": "Preamboli tool",
            "status": "good" if "Controllo subito." in session["instructions"] else "warn",
            "detail": "Piccoli preamboli evitano silenzi strani durante i tool call.",
        },
        {
            "label": "Noise reduction",
            "status": "good" if audio_input.get("noise_reduction") else "warn",
            "detail": "Aiuta con telefono, bar e sala rumorosa.",
        },
        {
            "label": "Turn detection",
            "status": "good" if turn_detection else "warn",
            "detail": "VAD o semantic turn detection necessari per conversazione naturale.",
        },
        {
            "label": "Idle timeout",
            "status": (
                "good"
                if isinstance(turn_detection, dict)
                and (turn_detection.get("type") != "server_vad" or turn_detection.get("idle_timeout_ms"))
                else "warn"
            ),
            "detail": "L'idle timeout aiuta il modello a recuperare da VAD mancati o linee poco chiare.",
        },
        {
            "label": "Truncation controllata",
            "status": "good"
            if isinstance(truncation, dict) and truncation.get("retention_ratio", 1.0) < 1.0
            else "warn",
            "detail": "Riduce costi e cache busting nelle chiamate più lunghe.",
        },
        {
            "label": "Tool scope limitato",
            "status": "good" if tool_count <= 6 else "warn",
            "detail": "OpenAI consiglia agenti vocali focalizzati, con pochi tool e un escape hatch chiaro.",
        },
        {
            "label": "Tracing attivo",
            "status": "good" if session.get("tracing") else "warn",
            "detail": "Permette ispezione delle sessioni in produzione.",
        },
        {
            "label": "Guard rail di scrittura",
            "status": "good",
            "detail": "Il prompt deve imporre una conferma separata prima dei tool di scrittura.",
        },
    ]
    return checks


def studio_prompt_diagnostics(
    prompt: str,
    *,
    restaurant: Restaurant | None = None,
    effective_overrides: RealtimeSessionOverrides | None = None,
) -> list[dict[str, str]]:
    normalized = prompt.strip()
    lower_prompt = normalized.lower()
    repeated_greeting_rule = any(pattern.search(normalized) for pattern in REPEATED_GREETING_PATTERNS)
    create_booking_confirmation_rule = any(
        pattern.search(normalized) for pattern in CREATE_BOOKING_CONFIRMATION_PATTERNS
    )
    has_greeting_discipline = (
        "saluto iniziale" in lower_prompt
        and (
            "non aprire ogni risposta" in lower_prompt
            or "non iniziare più le risposte" in lower_prompt
            or "senza ripetere un saluto iniziale" in lower_prompt
        )
    )
    diagnostics = [
        {
            "label": "Structured sections",
            "status": "good" if all(section in normalized for section in PROMPT_SECTIONS) else "warn",
            "detail": (
                "Il prompt dovrebbe mantenere sezioni etichettate per ruolo, strumenti, "
                "flusso, sicurezza e audio poco chiaro."
            ),
        },
        {
            "label": "Language pinning",
            "status": (
                "good"
                if "language" in lower_prompt
                and ("segui la lingua del cliente" in lower_prompt or "italiano" in lower_prompt)
                else "warn"
            ),
            "detail": (
                "Serve una regola esplicita sulla lingua: "
                "o segui il cliente, o imponi un solo idioma senza conflitti."
            ),
        },
        {
            "label": "Bullets over paragraphs",
            "status": "good" if normalized.count("\n- ") >= 12 else "warn",
            "detail": "La guida Realtime raccomanda bullet chiari e brevi invece di paragrafi lunghi.",
        },
        {
            "label": "Tool preambles",
            "status": "good" if "controllo subito" in lower_prompt or "verifico adesso" in lower_prompt else "warn",
            "detail": (
                "Un preambolo corto prima del tool aiuta a evitare silenzi strani "
                "e riduce la percezione di instabilita."
            ),
        },
        {
            "label": "Write confirmation guard",
            "status": (
                "good"
                if "conferma esplicita" in lower_prompt
                and ("modify_booking" in lower_prompt or "cancel_booking" in lower_prompt)
                else "warn"
            ),
            "detail": (
                "Il prompt deve essere cauto su modify/cancel, ma create_booking "
                "deve partire subito dopo disponibilità positiva e nome cliente."
            ),
        },
        {
            "label": "Create booking flow",
            "status": (
                "warn"
                if create_booking_confirmation_rule
                or ("create_booking" in lower_prompt and "non chiedere" not in lower_prompt)
                else "good"
            ),
            "detail": (
                "Per nuove prenotazioni, chiedere una conferma finale dopo il nome rallenta la chiamata. "
                "Il nome dopo disponibilità positiva deve bastare per creare la prenotazione."
            ),
        },
        {
            "label": "Escalation wording",
            "status": "good" if "la metto in contatto con il ristorante" in lower_prompt else "warn",
            "detail": "Una frase di handoff stabile rende il trasferimento piu coerente e piu testabile.",
        },
        {
            "label": "Variety rule",
            "status": "good" if "varia" in lower_prompt or "non ripetere" in lower_prompt else "warn",
            "detail": "Serve una regola esplicita contro la ripetizione per evitare che l'agente sembri meccanico.",
        },
        {
            "label": "Greeting discipline",
            "status": "warn" if repeated_greeting_rule or not has_greeting_discipline else "good",
            "detail": (
                "Il saluto deve comparire solo all'apertura della chiamata; i turni successivi "
                "devono andare direttamente al dato mancante, al tool o alla conferma."
            ),
        },
        {
            "label": "Escape hatch",
            "status": "good" if "la metto in contatto con il ristorante" in lower_prompt else "warn",
            "detail": "Un agente vocale robusto deve avere un handoff chiaro quando il caso esce dal perimetro.",
        },
        {
            "label": "Examples and sample phrases",
            "status": (
                "good"
                if "\"mi chiamo edoardo\"" in lower_prompt or "\"controllo subito" in lower_prompt
                else "warn"
            ),
            "detail": "Il modello segue bene esempi brevi di frasi target per identita, preamboli ed escalation.",
        },
    ]
    if restaurant is not None:
        expected_closures = json.dumps(restaurant.weekly_closures or [], ensure_ascii=False).lower()
        expected_hours = json.dumps(restaurant.opening_hours or {}, ensure_ascii=False).lower()
        expected_turni = json.dumps(restaurant.turni or [], ensure_ascii=False).lower()
        context_drift = (
            expected_closures not in lower_prompt
            or expected_hours not in lower_prompt
            or expected_turni not in lower_prompt
        )
        diagnostics.append(
            {
                "label": "Restaurant context drift",
                "status": "warn" if context_drift else "good",
                "detail": (
                    "Il prompt live dovrebbe riflettere chiusure, orari e turni del ristorante salvati nel database."
                ),
            }
        )
    multilingual_prompt = (
        "segui la lingua del cliente" in lower_prompt
        or "cambia solo la lingua della risposta" in lower_prompt
    )
    fixed_input_language = (
        effective_overrides.input_language.strip().lower()
        if effective_overrides and effective_overrides.input_language
        else ""
    )
    diagnostics.append(
        {
            "label": "Multilingual runtime conflict",
            "status": "warn" if multilingual_prompt and fixed_input_language else "good",
            "detail": (
                "Se il prompt segue la lingua del cliente, evita di fissare input_language salvo scelta intenzionale."
            ),
        }
    )
    return diagnostics


def studio_recommendations(
    *,
    restaurant: Restaurant,
    prompt: str,
    effective_overrides: RealtimeSessionOverrides,
    session_update: dict[str, Any],
) -> list[dict[str, str]]:
    session = session_update["session"]
    audio_input = ((session.get("audio") or {}).get("input") or {})
    turn_detection = audio_input.get("turn_detection") or {}
    recommendations = [
        {
            "label": "Focused task scope",
            "status": "good" if len(build_realtime_tools()) <= 6 else "warn",
            "detail": "Per voice agents OpenAI consiglia pochi tool e un solo compito ben definito.",
        },
        {
            "label": "Critical facts in prompt",
            "status": "good" if "Context" in prompt and "Orari" in prompt and "Indirizzo" in prompt else "warn",
            "detail": (
                "Le informazioni operative importanti dovrebbero stare nel "
                "prompt, non dipendere da tool iniziali."
            ),
        },
        {
            "label": "Recommended voice",
            "status": (
                "good"
                if (effective_overrides.voice or settings.openai_realtime_voice)
                in RECOMMENDED_REALTIME_VOICES
                else "warn"
            ),
            "detail": "Marin e Cedar sono le voci più solide per questo flusso telefonico finora.",
        },
        {
            "label": "Tool choice mode",
            "status": "good" if session.get("tool_choice") == "auto" else "warn",
            "detail": "AUTO è la scelta più stabile qui: lascia usare i tool quando servono ma evita rigidità inutile.",
        },
        {
            "label": "Realtime 2 reasoning",
            "status": (
                "good"
                if not _supports_realtime_reasoning(str(session.get("model") or ""))
                or (session.get("reasoning") or {}).get("effort") in {"minimal", "low"}
                else "warn"
            ),
            "detail": (
                "Per un receptionist telefonico con flusso guidato da tool, "
                "OpenAI consiglia di partire da reasoning.effort=low."
            ),
        },
        {
            "label": "Flexible input language",
            "status": (
                "good"
                if audio_input.get("transcription", {}).get("language") in {None, ""}
                else "warn"
            ),
            "detail": (
                "Lasciare la trascrizione senza pinning rigido aiuta quando il chiamante cambia lingua; "
                "usa un codice ISO solo se vuoi forzare una lingua."
            ),
        },
        {
            "label": "Turn patience for phone calls",
            "status": (
                "good"
                if turn_detection.get("type") == "server_vad"
                and int(turn_detection.get("silence_duration_ms") or 0) >= 600
                else "warn"
            ),
            "detail": "Per telefonate reali conviene evitare un end-of-turn troppo aggressivo.",
        },
        {
            "label": "Context cost control",
            "status": "good" if isinstance(session.get("truncation"), dict) else "warn",
            "detail": (
                "Le chiamate lunghe costano di più a ogni turno: "
                "retention_ratio aiuta a contenere costo e drift."
            ),
        },
        {
            "label": "Human escape hatch",
            "status": (
                "good"
                if restaurant.escalation_phone
                or "la metto in contatto con il ristorante" in prompt.lower()
                else "warn"
            ),
            "detail": "Il sistema deve poter mollare rapidamente i casi fuori policy o troppo ambigui.",
        },
    ]
    return recommendations


def studio_presets() -> list[dict[str, Any]]:
    return STUDIO_PRESETS


def studio_scenarios() -> list[dict[str, str]]:
    return STUDIO_SCENARIOS


def studio_config_diff(
    restaurant: Restaurant,
    effective_overrides: RealtimeSessionOverrides,
) -> list[dict[str, str]]:
    baseline = default_session_overrides()
    saved = restaurant.openai_realtime_settings or {}
    diff: list[dict[str, str]] = []
    for field_name in RealtimeSessionOverrides.__dataclass_fields__.keys():
        baseline_value = getattr(baseline, field_name)
        effective_value = getattr(effective_overrides, field_name)
        if baseline_value == effective_value:
            continue
        diff.append(
            {
                "field": field_name,
                "label": OVERRIDE_FIELD_LABELS.get(field_name, field_name.replace("_", " ").title()),
                "baseline": _format_override_value(baseline_value),
                "effective": _format_override_value(effective_value),
                "source": "saved" if field_name in saved else "runtime",
            }
        )
    if not diff:
        diff.append(
            {
                "field": "defaults",
                "label": "Runtime defaults",
                "baseline": "system defaults",
                "effective": "system defaults",
                "source": "default",
            }
        )
    return diff


def studio_practical_config_diff(
    restaurant: Restaurant,
    effective_overrides: RealtimeSessionOverrides,
) -> list[dict[str, str]]:
    return [
        item
        for item in studio_config_diff(restaurant, effective_overrides)
        if item["field"] == "defaults" or item["field"] in PRACTICAL_STUDIO_FIELDS
    ]


def _looks_like_name_confirmation_request(text: str) -> bool:
    lowered = _normalized_text(text)
    if not any(
        cue in lowered
        for cue in {
            "corretto",
            "corretta",
            "corretto cosi",
            "correct",
            "correcto",
            "esatto",
            "giusto",
            "is that right",
            "is that correct",
            "right",
        }
    ):
        return False
    if any(
        cue in lowered
        for cue in {
            "prenot",
            "reservation",
            "booking",
            "tavolo",
            "table",
            "persone",
            "people",
            "party",
            "alle ",
            "ore ",
            "giorno",
            "day",
            "domani",
            "sabato",
            "venerdi",
            "saturday",
            "tonight",
        }
    ):
        return False
    name_tokens = re.findall(r"[A-Za-zÀ-ÿ]{2,}", text)
    return len(name_tokens) >= 2


def _ingest_assistant_transcript(state: RealtimeCallState, transcript: str) -> None:
    state.last_assistant_transcript = transcript
    state.last_requested_field = _detect_requested_field(transcript)


def _ingest_user_transcript(state: RealtimeCallState, transcript: str) -> None:
    state.last_user_transcript = transcript
    state.intent = _detect_intent(transcript, state.intent)
    _update_booking_context(state, transcript)

    if state.last_requested_field and not _reply_matches_field(transcript, state.last_requested_field):
        _mark_invalid_field_attempt(state, state.last_requested_field)
    elif state.last_requested_field:
        _clear_invalid_field_attempt(state, state.last_requested_field)


def _ingest_input_audio_transcription_delta(
    state: RealtimeCallState,
    *,
    item_id: str | None,
    delta: str,
) -> str:
    cleaned_delta = delta.strip()
    if not cleaned_delta:
        return ""
    key = item_id or "unknown"
    combined = f"{state.pending_user_audio_transcripts.get(key, '')}{delta}".strip()
    state.pending_user_audio_transcripts[key] = combined
    return combined


def _should_ignore_post_write_user_turn(state: RealtimeCallState) -> bool:
    return state.terminal_write_success


def _write_tools_unlocked(state: RealtimeCallState) -> bool:
    for event in reversed(state.tool_events):
        result = event.get("result") if isinstance(event.get("result"), dict) else {}
        if event.get("tool") == "check_availability" and result.get("available") is True:
            return True
        if event.get("tool") == "find_booking" and result.get("found") is True:
            return True
    return False


def _normalize_tool_time(value: Any) -> str:
    raw = str(value or "").strip()
    if not raw:
        return ""
    with suppress(ValueError):
        return time.fromisoformat(raw).strftime("%H:%M")
    return raw[:5]


def _has_matching_successful_availability(state: RealtimeCallState, arguments: dict[str, Any]) -> bool:
    requested_date = str(arguments.get("date") or "").strip()
    requested_time = _normalize_tool_time(arguments.get("time"))
    requested_party_size = _resolve_party_size(arguments, state.booking_context)
    if not requested_date or not requested_time or requested_party_size <= 0:
        return False

    for event in reversed(state.tool_events):
        if event.get("tool") != "check_availability":
            continue
        result = event.get("result") if isinstance(event.get("result"), dict) else {}
        if result.get("available") is not True:
            continue
        checked_args = event.get("arguments") if isinstance(event.get("arguments"), dict) else {}
        if str(checked_args.get("date") or "").strip() != requested_date:
            continue
        if _normalize_tool_time(checked_args.get("time")) != requested_time:
            continue
        try:
            checked_party_size = int(checked_args.get("party_size") or 0)
        except (TypeError, ValueError):
            checked_party_size = 0
        if checked_party_size == requested_party_size:
            return True
    return False


def _sync_call_update(
    db_factory: sessionmaker,
    *,
    call_sid: str,
    fields: dict[str, Any],
) -> None:
    if settings.reservation_lab_booking_enabled:
        from app.services.reservation_lab_voice import sync_voice_call_log_update

        sync_voice_call_log_update(call_sid=call_sid, fields=fields)
        return

    db: Session = db_factory()
    try:
        call = db.scalar(select(CallLog).where(CallLog.twilio_call_sid == call_sid))
        if not call:
            return
        for field, value in fields.items():
            if field == "extra_data" and isinstance(value, dict):
                current = call.extra_data if isinstance(call.extra_data, dict) else {}
                setattr(call, field, {**current, **value})
                continue
            # Never overwrite a non-null booking_id with null
            if field == "booking_id" and value is None and call.booking_id is not None:
                continue
            setattr(call, field, value)
        db.add(call)
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


async def _update_call(
    db_factory: sessionmaker,
    *,
    call_sid: str,
    **fields: Any,
) -> None:
    await asyncio.to_thread(_sync_call_update, db_factory, call_sid=call_sid, fields=fields)


def _modify_changes_from_arguments(arguments: dict[str, Any]) -> dict[str, Any]:
    changes = {
        key: arguments[key]
        for key in ["date", "time", "party_size", "customer_name", "customer_phone", "special_requests"]
        if arguments.get(key) is not None
    }
    return ModifyBookingChanges.model_validate(changes).model_dump(exclude_unset=True)


def _resolve_party_size(arguments: dict[str, Any], context: dict[str, Any]) -> int:
    """Normalize party-size arg regardless of which alias the model used."""
    raw = (
        arguments.get("party_size")
        or arguments.get("covers")
        or arguments.get("people")
        or arguments.get("num_guests")
        or arguments.get("guests")
        or context.get("party_size")
    )
    try:
        return int(raw) if raw else 0
    except (TypeError, ValueError):
        return 0


def _scrub_pii(arguments: dict[str, Any]) -> dict[str, Any]:
    """Remove phone number PII from logged tool arguments."""
    cleaned = dict(arguments)
    for k in ["phone_number", "contact_number", "customer_phone", "phone"]:
        if k in cleaned:
            cleaned[k] = "[redacted]"
    return cleaned


def _sync_dispatch_tool(
    db_factory: sessionmaker,
    *,
    restaurant: Restaurant,
    state: RealtimeCallState,
    tool_name: str,
    arguments: dict[str, Any],
) -> dict[str, Any]:
    db: Session = db_factory()
    try:
        try:
            if settings.reservation_lab_booking_enabled:
                from app.services.reservation_lab_voice import dispatch_reservation_lab_tool

                return dispatch_reservation_lab_tool(
                    restaurant=restaurant,
                    state=state,
                    tool_name=tool_name,
                    arguments=arguments,
                    sync_transfer=_sync_transfer_call_to_restaurant,
                )

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
                if state.last_requested_field and not state.last_user_reply_valid:
                    return _guard_failed_result(
                        state,
                        reason="L'ultima risposta del cliente non chiarisce il dettaglio richiesto.",
                        assistant_instruction=(
                            "Chiedi di ripetere solo il dettaglio mancante. "
                            "Se anche il secondo tentativo non e chiaro, trasferisci al ristorante."
                        ),
                    )
                requested_time = (
                    time.fromisoformat(arguments["time"])
                    if arguments.get("time")
                    else None
                )
                party_size = _resolve_party_size(arguments, state.booking_context)
                if party_size == 0:
                    return {
                        "success": False,
                        "available": False,
                        "reason": "Non ho il numero di persone. Chiedi al cliente quante persone saranno.",
                        "missing_field": "party_size",
                    }
                result = check_availability(
                    db,
                    restaurant=restaurant,
                    booking_date=date.fromisoformat(arguments["date"]),
                    requested_time=requested_time,
                    party_size=party_size,
                )
                return result

            if tool_name == "find_booking":
                bookings = find_bookings_for_caller(
                    db,
                    restaurant_id=restaurant.id,
                    caller_phone=state.caller_phone or None,
                    confirmation_code=arguments.get("confirmation_code"),
                )
                return {
                    "found": bool(bookings),
                    "bookings": [booking.model_dump(mode="json") for booking in bookings],
                }

            if tool_name == "create_booking":
                customer_name = str(
                    arguments.get("customer_name")
                    or arguments.get("name")
                    or ""
                ).strip()
                if not _has_matching_successful_availability(state, arguments):
                    return {
                        "success": False,
                        "reason": (
                            "Prima di creare la prenotazione serve una verifica disponibilità "
                            "positiva per la stessa data, ora e numero di persone."
                        ),
                        "retry_write": False,
                        "should_escalate": False,
                        "assistant_instruction": (
                            "Non creare ancora la prenotazione. Verifica prima la disponibilità "
                            "con data, ora e numero persone; se è disponibile, chiedi il nome e "
                            "poi riprova."
                        ),
                    }
                if not customer_name:
                    return {
                        "success": False,
                        "reason": "Manca il nome cliente per completare la prenotazione.",
                        "retry_write": False,
                        "should_escalate": False,
                        "assistant_instruction": "Chiedi solo il nome per la prenotazione.",
                    }
                existing_bookings = find_bookings_for_caller(
                    db,
                    restaurant_id=restaurant.id,
                    caller_phone=state.caller_phone or None,
                    confirmation_code=None,
                )
                duplicate_booking = next(
                    (
                        booking
                        for booking in existing_bookings
                        if str(booking.date) == str(arguments["date"])
                        and str(booking.time) == str(arguments["time"])
                        and booking.status in ACTIVE_STATUSES
                    ),
                    None,
                )
                if duplicate_booking:
                    return {
                        "success": False,
                        "reason": (
                            "Risulta già una prenotazione attiva da questo numero nello stesso giorno e orario."
                        ),
                        "duplicate_booking": duplicate_booking.model_dump(mode="json"),
                        "retry_write": False,
                        "should_escalate": False,
                        "can_self_service": True,
                        "assistant_instruction": (
                            "Non creare una seconda prenotazione. Di' che esiste già una prenotazione "
                            "da questo numero per lo stesso giorno e orario e offri di lasciarla così, "
                            "modificarla o cancellarla. Passa al ristorante solo se il cliente lo chiede."
                        ),
                    }
                booking, error = create_booking(
                    db,
                    payload=BookingCreate(
                        restaurant_id=restaurant.id,
                        date=date.fromisoformat(arguments["date"]),
                        time=time.fromisoformat(arguments["time"]),
                        party_size=_resolve_party_size(arguments, state.booking_context),
                        customer_name=customer_name,
                        customer_phone=state.caller_phone,
                        special_requests=arguments.get("special_requests"),
                        source=BookingSource.ai_phone,
                        status=BookingStatus.confirmed,
                    ),
                    changed_by="openai_realtime",
                )
                if error:
                    db.rollback()
                    return {"success": False, **error}
                call = db.scalar(select(CallLog).where(CallLog.twilio_call_sid == state.twilio_call_sid))
                if call:
                    call.booking_id = booking.id
                    db.add(call)
                db.commit()
                state.outcome = "booking_created"
                state.terminal_write_success = True
                state.end_call_after_response = True
                state.current_booking_id = booking.id
                state.booking_context.setdefault("date", arguments["date"])
                state.booking_context.setdefault("requested_time", arguments["time"])
                state.booking_context.setdefault("party_size", _resolve_party_size(arguments, state.booking_context))
                state.booking_context.setdefault("customer_name", customer_name)
                return {
                    "success": True,
                    "confirmation_code": booking.confirmation_code,
                    "booking_id": booking.id,
                    "assistant_instruction": (
                        "La prenotazione è confermata. Comunica l'esito in una sola frase breve, "
                        "saluta usando il nome del cliente (es. 'A presto Marco, buona serata!') "
                        "e chiudi la chiamata. Non aggiungere altre domande o frasi."
                    ),
                }

            if tool_name == "modify_booking":
                summary = str(arguments.get("confirmation_summary") or "").strip()
                if len(summary) < 12:
                    return {
                        "success": False,
                        "reason": (
                            "Serve un riepilogo esplicito di ciò che il cliente ha "
                            "confermato. Chiedi una conferma chiara e riprova."
                        ),
                        "retry_write": True,
                    }
                changes = _modify_changes_from_arguments(arguments)
                if not changes:
                    return {"success": False, "reason": "Nessuna modifica valida fornita."}
                booking = db.scalar(
                    select(Booking).where(
                        Booking.restaurant_id == restaurant.id,
                        Booking.confirmation_code == arguments["confirmation_code"],
                        Booking.status.in_(ACTIVE_STATUSES),
                    )
                )
                if not booking:
                    return {"success": False, "reason": "Prenotazione non trovata."}
                updated, error = update_booking(
                    db,
                    booking=booking,
                    changes=BookingUpdate(**changes),
                    changed_by="openai_realtime",
                )
                if error:
                    db.rollback()
                    return {"success": False, **error}
                db.commit()
                state.outcome = "booking_modified"
                state.terminal_write_success = True
                state.end_call_after_response = True
                state.current_booking_id = updated.id
                return {"success": True, "updated_booking": updated.id}

            if tool_name == "cancel_booking":
                summary = str(arguments.get("confirmation_summary") or "").strip()
                if len(summary) < 12:
                    return {
                        "success": False,
                        "reason": (
                            "Serve un riepilogo esplicito della cancellazione "
                            "appena confermata dal cliente. Chiedi conferma e riprova."
                        ),
                        "retry_write": True,
                    }
                booking = db.scalar(
                    select(Booking).where(
                        Booking.restaurant_id == restaurant.id,
                        Booking.confirmation_code == arguments["confirmation_code"],
                        Booking.status.in_(ACTIVE_STATUSES),
                    )
                )
                if not booking:
                    return {"success": False, "reason": "Prenotazione non trovata."}
                # Use the bookings service so that a BookingEvent is created
                # and the customer cancellation counter is incremented.
                cancel_booking_service(
                    db,
                    booking=booking,
                    changed_by="openai_realtime",
                )
                db.commit()
                state.outcome = "booking_cancelled"
                state.terminal_write_success = True
                state.end_call_after_response = True
                state.current_booking_id = booking.id
                return {"success": True}

            if tool_name == "escalate_to_human":
                state.outcome = "escalated"
                if state.twilio_call_sid.startswith("studio-"):
                    return {
                        "success": True,
                        "transferred": True,
                        "simulated": True,
                        "reason": arguments.get("reason"),
                    }
                transferred = _sync_transfer_call_to_restaurant(
                    restaurant=restaurant,
                    call_sid=state.twilio_call_sid,
                )
                return {
                    "success": transferred,
                    "transferred": transferred,
                    "reason": arguments.get("reason"),
                }

            return {"success": False, "reason": f"Tool sconosciuto: {tool_name}"}
        except Exception as exc:
            db.rollback()
            state.technical_tool_failures += 1
            json_log(
                "app.realtime",
                {
                    "event": "openai_tool_dispatch_failed",
                    "restaurant_id": restaurant.id,
                    "call_sid": state.twilio_call_sid,
                    "tool_name": tool_name,
                    "error": str(exc),
                    "technical_tool_failures": state.technical_tool_failures,
                },
            )
            return {
                "success": False,
                "reason": "Errore tecnico temporaneo del sistema.",
                "technical_error": True,
                "technical_tool_failures": state.technical_tool_failures,
                "should_escalate": state.technical_tool_failures >= 2,
            }
    finally:
        db.close()


async def dispatch_tool(
    db_factory: sessionmaker,
    *,
    restaurant: Restaurant,
    state: RealtimeCallState,
    tool_name: str,
    arguments: dict[str, Any],
) -> dict[str, Any]:
    return await asyncio.to_thread(
        _sync_dispatch_tool,
        db_factory,
        restaurant=restaurant,
        state=state,
        tool_name=tool_name,
        arguments=arguments,
    )


def _sync_end_twilio_call(*, restaurant: Restaurant, call_sid: str) -> bool:
    if not settings.twilio_account_sid or not settings.twilio_auth_token:
        return False
    response = httpx.post(
        f"https://api.twilio.com/2010-04-01/Accounts/{settings.twilio_account_sid}/Calls/{call_sid}.json",
        auth=(settings.twilio_account_sid, settings.twilio_auth_token),
        data={"Twiml": "<Response><Hangup/></Response>"},
        timeout=10.0,
    )
    return response.is_success


async def _end_twilio_call_after_delay(*, restaurant: Restaurant, call_sid: str, delay_ms: int) -> None:
    await asyncio.sleep(max(delay_ms, 0) / 1000)
    with suppress(Exception):
        await asyncio.to_thread(_sync_end_twilio_call, restaurant=restaurant, call_sid=call_sid)


def _sync_transfer_call_to_restaurant(*, restaurant: Restaurant, call_sid: str) -> bool:
    if not settings.twilio_account_sid or not settings.twilio_auth_token or not restaurant.escalation_phone:
        return False
    twiml = f"<Response><Dial>{restaurant.escalation_phone}</Dial></Response>"
    response = httpx.post(
        f"https://api.twilio.com/2010-04-01/Accounts/{settings.twilio_account_sid}/Calls/{call_sid}.json",
        auth=(settings.twilio_account_sid, settings.twilio_auth_token),
        data={"Twiml": twiml},
        timeout=10.0,
    )
    return response.is_success


async def _send_response_create(
    realtime_ws,
    *,
    output_modalities: tuple[str, ...] = ("audio",),
) -> None:
    response: dict[str, Any] = {"output_modalities": ["audio"] if "audio" in output_modalities else ["text"]}
    if "audio" in output_modalities:
        response["audio"] = {"output": {"format": {"type": "audio/pcmu"}}}
    await realtime_ws.send(json.dumps({"type": "response.create", "response": response}))


async def _send_runtime_context_message(realtime_ws: Any, restaurant: Restaurant, *, caller_phone: str) -> None:
    await realtime_ws.send(
        json.dumps(
            {
                "type": "conversation.item.create",
                "item": {
                    "type": "message",
                    "role": "system",
                    "content": [
                        {
                            "type": "input_text",
                            "text": _runtime_context_message(restaurant, caller_phone=caller_phone),
                        }
                    ],
                },
            }
        )
    )


def _current_tool_scope(state: RealtimeCallState) -> tuple[str, ...]:
    if state.should_escalate:
        return ("escalate_to_human",)
    if state.terminal_write_success:
        return ("escalate_to_human",)
    if _write_tools_unlocked(state):
        return FULL_TOOL_NAMES
    if state.end_call_after_response:
        return ("escalate_to_human",)
    return INITIAL_TOOL_NAMES


async def _sync_tool_scope(
    realtime_ws: Any,
    restaurant: Restaurant,
    state: RealtimeCallState,
    *,
    caller_phone: str,
    output_modalities: tuple[str, ...] = ("audio",),
    prompt_override: str | None = None,
    overrides: RealtimeSessionOverrides | None = None,
) -> None:
    tuning = merged_session_overrides(restaurant, overrides)
    await realtime_ws.send(
        json.dumps(
            _build_tool_scope_update(
                tool_names=_current_tool_scope(state),
                overrides=tuning,
            )
        )
    )


def _audio_duration_ms_from_payload(delta: str) -> int:
    try:
        return int((len(base64.b64decode(delta)) / 8000) * 1000)
    except Exception:
        return 0


def _finish_initial_greeting(state: RealtimeCallState) -> None:
    if not state.initial_greeting_in_progress:
        return
    state.initial_greeting_in_progress = False
    state.initial_greeting_grace_until = None


def _silent_response_retry_allowed(
    state: RealtimeCallState,
    *,
    initial_response_phase: bool,
) -> bool:
    if state.pending_tool_followup:
        return True
    return initial_response_phase and not state.caller_speech_detected


async def _truncate_unplayed_audio(state: RealtimeCallState, realtime_ws: Any, websocket: WebSocket) -> None:
    if not state.last_audio_item_id or state.last_audio_bytes_sent <= 0 or not state.stream_sid:
        return
    await websocket.send_json({"event": "clear", "streamSid": state.stream_sid})
    state.interruption_events.append(
        {
            "event": "twilio_audio_cleared",
            "timestamp": datetime.now(UTC).isoformat(),
            "stream_sid": state.stream_sid,
        }
    )
    audio_end_ms = int((state.last_audio_bytes_sent / 8000) * 1000)
    if audio_end_ms <= 0:
        return
    await realtime_ws.send(
        json.dumps(
            {
                "type": "conversation.item.truncate",
                "item_id": state.last_audio_item_id,
                "content_index": state.last_audio_content_index,
                "audio_end_ms": audio_end_ms,
            }
        )
    )
    state.interruption_events.append(
        {
            "event": "assistant_audio_truncated",
            "timestamp": datetime.now(UTC).isoformat(),
            "item_id": state.last_audio_item_id,
            "content_index": state.last_audio_content_index,
            "audio_end_ms": audio_end_ms,
        }
    )
    state.last_audio_item_id = None
    state.last_audio_content_index = 0
    state.last_audio_bytes_sent = 0
    state.assistant_is_speaking = False


async def _summarize_conversation_turns(turns: list[dict[str, str]]) -> str:
    if not turns or not settings.openai_api_key:
        return ""
    transcript = "\n".join(f"{turn['role']}: {turn['text']}" for turn in turns if turn.get("text"))
    if not transcript.strip():
        return ""
    prompt = _build_conversation_summary_prompt(turns)
    try:
        async with httpx.AsyncClient(timeout=12.0) as client:
            response = await client.post(
                "https://api.openai.com/v1/responses",
                headers={
                    "Authorization": f"Bearer {settings.openai_api_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": SUMMARY_MODEL,
                    "input": prompt,
                    "max_output_tokens": 220,
                },
            )
            response.raise_for_status()
    except Exception:
        return ""
    payload = response.json()
    parts: list[str] = []
    for item in payload.get("output", []):
        for content in item.get("content", []):
            text = content.get("text")
            if text:
                parts.append(str(text).strip())
    return "\n".join(part for part in parts if part).strip()


async def _maybe_refresh_summary(
    *,
    realtime_ws: Any,
    db_factory: sessionmaker,
    restaurant: Restaurant,
    state: RealtimeCallState,
    call_sid: str,
) -> None:
    # Do NOT inject a mid-call summary once the call is effectively over.
    # After a successful write or an escalation, hangup is already scheduled;
    # any `conversation.item.create` added here can be picked up by a late
    # VAD-triggered response (phone-line noise) and spoken aloud as a second
    # farewell — the model, with tool scope collapsed, reads the summary text.
    if state.terminal_write_success or state.should_escalate or state.end_call_after_response:
        return
    if len(state.conversation_turns) < SUMMARY_TRIGGER_TURNS:
        return
    candidate_turns = state.conversation_turns[:-SUMMARY_KEEP_LAST_TURNS]
    if len(candidate_turns) < SUMMARY_KEEP_LAST_TURNS:
        return
    summary = await _summarize_conversation_turns(candidate_turns)
    if not summary or summary == state.latest_summary:
        return
    state.latest_summary = summary
    await realtime_ws.send(
        json.dumps(
            {
                "type": "conversation.item.create",
                "item": {
                    "type": "message",
                    "role": "system",
                    "content": [{"type": "input_text", "text": f"Riassunto conversazione fin qui:\n{summary}"}],
                },
            }
        )
    )
    await _update_call(
        db_factory,
        call_sid=call_sid,
        extra_data={"conversation_summary": summary},
    )


async def _run_silent_response_watchdog(
    *,
    realtime_ws: Any,
    restaurant: Restaurant,
    state: RealtimeCallState,
    caller_phone: str,
    generation: int,
) -> None:
    initial_response_phase = state.initial_greeting_in_progress
    await asyncio.sleep(SILENT_RESPONSE_WATCHDOG_SECONDS)
    if generation != state.response_watchdog_generation:
        return
    if not state.response_in_progress or state.response_audio_started:
        return
    if state.silent_response_retries >= MAX_SILENT_RESPONSE_RETRIES:
        return
    if not _silent_response_retry_allowed(state, initial_response_phase=initial_response_phase):
        return
    state.silent_response_retries += 1
    state.response_in_progress = False
    with suppress(Exception):
        await realtime_ws.send(json.dumps({"type": "response.cancel"}))
        await _sync_tool_scope(realtime_ws, restaurant, state, caller_phone=caller_phone)
        await _send_response_create(realtime_ws, output_modalities=("audio",))


async def bridge_twilio_media_stream(
    *,
    websocket: WebSocket,
    db_factory: sessionmaker,
) -> None:
    initial_event = await websocket.receive_json()
    if initial_event.get("event") == "connected":
        initial_event = await websocket.receive_json()

    if initial_event.get("event") != "start":
        raise RuntimeError("Twilio stream did not send a start event.")

    start = initial_event.get("start", {})
    custom_parameters = start.get("customParameters") or {}
    token = str(custom_parameters.get("token") or "").strip()
    if not token:
        raise RuntimeError("Twilio stream token missing.")

    try:
        claims = jwt.decode(token, settings.jwt_secret, algorithms=["HS256"])
    except jwt.PyJWTError as exc:
        raise RuntimeError("Twilio stream token invalid.") from exc

    db: Session = db_factory()
    try:
        if settings.reservation_lab_booking_enabled:
            from app.services.reservation_lab_voice import default_restaurant_id, get_restaurant_for_voice

            restaurant = get_restaurant_for_voice(
                str(claims.get("restaurant_id") or default_restaurant_id())
            )
        else:
            restaurant = db.get(Restaurant, claims.get("restaurant_id"))
    finally:
        db.close()
    if not restaurant:
        raise RuntimeError("Restaurant not found for Twilio media stream.")

    call_sid = str(claims.get("call_sid") or "")
    from_number = str(claims.get("from_number") or "")
    to_number = str(claims.get("to_number") or "")
    state = RealtimeCallState(
        caller_phone=from_number,
        twilio_call_sid=call_sid,
        stream_sid=start.get("streamSid") or initial_event.get("streamSid"),
    )
    tuning = restaurant_session_overrides(restaurant)
    headers = realtime_headers()
    try:
        async with connect(realtime_ws_url(tuning.model), additional_headers=headers) as realtime_ws:
            await realtime_ws.send(
                json.dumps(
                    build_session_update(
                        restaurant,
                        caller_phone=from_number,
                        tool_names=_current_tool_scope(state),
                    )
                )
            )
            await _send_runtime_context_message(realtime_ws, restaurant, caller_phone=from_number)
            await _send_response_create(realtime_ws, output_modalities=("audio",))
            await _update_call(
                db_factory,
                call_sid=call_sid,
                twilio_call_sid=call_sid,
                voice_provider=restaurant.voice_provider,
            )

            async def twilio_to_openai() -> None:
                buffered_audio = bytearray()
                buffered_packets = 0

                async def flush_buffer() -> None:
                    nonlocal buffered_packets
                    if not buffered_audio:
                        buffered_audio.clear()
                        buffered_packets = 0
                        return
                    await realtime_ws.send(
                        json.dumps(
                            {
                                "type": "input_audio_buffer.append",
                                "audio": base64.b64encode(bytes(buffered_audio)).decode("ascii"),
                            }
                        )
                    )
                    buffered_audio.clear()
                    buffered_packets = 0

                while True:
                    event = await websocket.receive_json()
                    event_type = event.get("event")
                    if event_type == "media":
                        media = event.get("media", {})
                        payload = media.get("payload")
                        if payload:
                            buffered_packets = _buffer_twilio_media_payload(
                                state,
                                payload=payload,
                                buffered_audio=buffered_audio,
                                buffered_packets=buffered_packets,
                            )
                            if buffered_packets >= INPUT_AUDIO_BUFFER_PACKET_THRESHOLD:
                                await flush_buffer()
                    elif event_type == "dtmf":
                        digits = str((event.get("dtmf") or {}).get("digit") or "").strip()
                        if digits == "1":
                            await flush_buffer()
                            transferred = await asyncio.to_thread(
                                _sync_transfer_call_to_restaurant,
                                restaurant=restaurant,
                                call_sid=state.twilio_call_sid,
                            )
                            # Observability: record this non-tool escalation path so call audits don't lose it.
                            state.tool_events.append(_make_auto_escalate_dtmf_event(digits, bool(transferred)))
                            if not transferred:
                                raise RuntimeError("DTMF transfer to restaurant failed.")
                            state.outcome = "escalated"
                            state.end_call_after_response = transferred
                            break
                    elif event_type == "stop":
                        await flush_buffer()
                        break

            async def openai_to_twilio() -> None:
                async for raw_event in realtime_ws:
                    event = json.loads(raw_event)
                    event_type = event.get("type")

                    if event_type == "session.created":
                        session = event.get("session", {})
                        state.openai_session_id = session.get("id")
                        if state.openai_session_id:
                            await _update_call(
                                db_factory,
                                call_sid=call_sid,
                                provider_call_id=state.openai_session_id,
                            )
                        continue

                    if event_type == "input_audio_buffer.speech_started":
                        state.caller_speech_detected = True
                        state.last_caller_activity_at = datetime.now(UTC)
                        state.response_watchdog_generation += 1
                        state.interruption_events.append(
                            {
                                "event": "caller_speech_started",
                                "timestamp": datetime.now(UTC).isoformat(),
                                "response_in_progress": state.response_in_progress,
                                "assistant_is_speaking": state.assistant_is_speaking,
                                "initial_greeting_in_progress": state.initial_greeting_in_progress,
                            }
                        )
                        if (
                            state.initial_greeting_in_progress
                            and state.last_audio_item_id is None
                        ):
                            # Caller spoke into the cold-start silence before the
                            # greeting produced any audio. Do NOT cancel or
                            # truncate — let the greeting come through. The
                            # caller's utterance stays buffered and becomes the
                            # first user turn after the greeting completes.
                            state.interruption_events.append(
                                {
                                    "event": "greeting_protected_from_cancel",
                                    "timestamp": datetime.now(UTC).isoformat(),
                                }
                            )
                            continue
                        if _should_ignore_post_write_user_turn(state):
                            continue
                        if state.response_in_progress:
                            state.response_in_progress = False
                            await realtime_ws.send(json.dumps({"type": "response.cancel"}))
                            state.interruption_events.append(
                                {
                                    "event": "response_cancel_sent",
                                    "timestamp": datetime.now(UTC).isoformat(),
                                    "last_audio_item_id": state.last_audio_item_id,
                                }
                            )
                        await _truncate_unplayed_audio(state, realtime_ws, websocket)
                        continue

                    if event_type == "conversation.item.input_audio_transcription.delta":
                        _ingest_input_audio_transcription_delta(
                            state,
                            item_id=str(event.get("item_id") or "unknown"),
                            delta=str(event.get("delta") or ""),
                        )
                        continue

                    if event_type == "conversation.item.input_audio_transcription.completed":
                        item_id = str(event.get("item_id") or "unknown")
                        partial_transcript = state.pending_user_audio_transcripts.pop(item_id, "")
                        transcript = str(event.get("transcript") or partial_transcript).strip()
                        if transcript:
                            # Drop Whisper prompt echoes before they enter the pipeline.
                            if _is_transcription_prompt_echo(transcript):
                                continue
                            if _should_ignore_post_write_user_turn(state):
                                continue
                            _ingest_user_transcript(state, transcript)
                            _record_conversation_turn(state, role="user", text=transcript, item_id=item_id)
                            _append_transcript_line(state, "Cliente", transcript)
                            await _sync_tool_scope(
                                realtime_ws,
                                restaurant,
                                state,
                                caller_phone=from_number,
                                output_modalities=("audio",),
                            )
                            if event.get("usage"):
                                state.transcription_events.append(
                                    {
                                        "transcript": transcript,
                                        "usage": event.get("usage"),
                                    }
                                )
                            await _update_call(
                                db_factory,
                                call_sid=call_sid,
                                transcript_preview="\n".join(state.transcript_lines),
                            )
                        continue

                    if event_type == "response.created":
                        state.response_in_progress = True
                        state.response_audio_started = False
                        state.assistant_is_speaking = False
                        state.response_watchdog_generation += 1
                        asyncio.create_task(
                            _run_silent_response_watchdog(
                                realtime_ws=realtime_ws,
                                restaurant=restaurant,
                                state=state,
                                caller_phone=from_number,
                                generation=state.response_watchdog_generation,
                            )
                        )
                        continue

                    if event_type in {"response.output_item.added", "response.output_item.created"}:
                        item = event.get("item", {})
                        if item.get("type") == "message" and item.get("role") == "assistant":
                            state.last_audio_item_id = item.get("id")
                            state.last_audio_content_index = 0
                            state.last_audio_bytes_sent = 0
                        continue

                    if event_type in {"response.output_audio.delta", "response.audio.delta"} and state.stream_sid:
                        delta = event.get("delta")
                        if delta:
                            state.response_audio_started = True
                            state.assistant_is_speaking = True
                            state.silent_response_retries = 0
                            state.last_audio_bytes_sent += len(base64.b64decode(delta))
                            if event.get("item_id"):
                                state.last_audio_item_id = event["item_id"]
                            if event.get("content_index") is not None:
                                state.last_audio_content_index = int(event["content_index"])
                            await websocket.send_json(
                                {
                                    "event": "media",
                                    "streamSid": state.stream_sid,
                                    "media": {"payload": delta},
                                }
                            )
                        continue

                    if event_type == "response.output_audio.done":
                        _finish_initial_greeting(state)
                        state.assistant_is_speaking = False
                        continue

                    if event_type == "response.output_audio_transcript.delta":
                        delta = str(event.get("delta") or "")
                        if delta:
                            state.pending_assistant_audio_transcript += delta
                        continue

                    if event_type == "response.output_audio_transcript.done":
                        item_id = str(event.get("item_id") or "")
                        transcript = state.pending_assistant_audio_transcript.strip()
                        state.pending_assistant_audio_transcript = ""
                        if transcript:
                            _remember_assistant_audio_transcript(
                                state,
                                item_id=item_id,
                                text=transcript,
                            )
                            _ingest_assistant_transcript(state, transcript)
                            _record_conversation_turn(state, role="assistant", text=transcript)
                            _append_transcript_line(state, "Agente", transcript)
                            await _update_call(
                                db_factory,
                                call_sid=call_sid,
                                transcript_preview="\n".join(state.transcript_lines),
                                summary=transcript[:500],
                            )
                        continue

                    # Dispatch tool calls eagerly as each item completes
                    # (instead of waiting for the full response.done)
                    if event_type == "response.output_item.done":
                        item = event.get("item", {})
                        if item.get("type") == "function_call":
                            try:
                                arguments = json.loads(item.get("arguments") or "{}")
                            except json.JSONDecodeError as exc:
                                json_log(
                                    "app.realtime",
                                    {
                                        "event": "openai_tool_arguments_malformed",
                                        "restaurant_id": restaurant.id,
                                        "call_sid": state.twilio_call_sid,
                                        "tool_name": item.get("name"),
                                        "error": str(exc),
                                        "partial_arguments": item.get("arguments"),
                                    },
                                )
                                continue

                            tool_started_at = datetime.now(UTC)
                            result = await dispatch_tool(
                                db_factory,
                                restaurant=restaurant,
                                state=state,
                                tool_name=str(item.get("name")),
                                arguments=arguments,
                            )
                            tool_completed_at = datetime.now(UTC)
                            state.tool_events.append(
                                {
                                    "tool": item.get("name"),
                                    "arguments": _scrub_pii(arguments),
                                    "result": result,
                                    "started_at": tool_started_at.isoformat(),
                                    "completed_at": tool_completed_at.isoformat(),
                                    "latency_ms": int(
                                        (tool_completed_at - tool_started_at).total_seconds() * 1000
                                    ),
                                }
                            )
                            _record_conversation_turn(
                                state,
                                role="system",
                                text=_format_tool_event_for_summary(str(item.get("name")), result),
                            )
                            await realtime_ws.send(
                                json.dumps(
                                    {
                                        "type": "conversation.item.create",
                                        "item": {
                                            "type": "function_call_output",
                                            "call_id": item.get("call_id"),
                                            "output": json.dumps(result, ensure_ascii=False),
                                        },
                                    }
                                )
                            )
                            state.pending_tool_followup = _realtime_tool_requires_followup(
                                str(item.get("name")),
                                result,
                            )
                            await _sync_tool_scope(
                                realtime_ws,
                                restaurant,
                                state,
                                caller_phone=from_number,
                                output_modalities=("audio",),
                            )
                        elif item.get("type") == "message":
                            message_text = _extract_message_text(item)
                            item_id = str(item.get("id") or "")
                            if message_text:
                                if _assistant_message_already_captured_from_audio(
                                    state,
                                    item_id=item_id,
                                    text=message_text,
                                ):
                                    continue
                                _ingest_assistant_transcript(state, message_text)
                                _record_conversation_turn(
                                    state,
                                    role="assistant",
                                    text=message_text,
                                    item_id=item_id,
                                )
                                if _append_transcript_line(state, "Agente", message_text):
                                    await _update_call(
                                        db_factory,
                                        call_sid=call_sid,
                                        transcript_preview="\n".join(state.transcript_lines),
                                        summary=message_text[:500],
                                    )
                        continue

                    if event_type == "response.done":
                        initial_response_phase = state.initial_greeting_in_progress
                        hangup_delay_ms = 0
                        if state.end_call_after_response and not state.pending_tool_followup:
                            # Most audio has already been played by the time response.done
                            # arrives, so a short buffer is enough to avoid clipping the
                            # goodbye while still ending the call promptly.
                            hangup_delay_ms = int((state.last_audio_bytes_sent / 8000) * 1000) + 3000
                        state.response_in_progress = False
                        state.assistant_is_speaking = False
                        _finish_initial_greeting(state)
                        state.last_audio_item_id = None
                        state.last_audio_content_index = 0
                        state.last_audio_bytes_sent = 0
                        response_payload = event.get("response", {})
                        if response_payload.get("usage"):
                            state.last_response_usage = response_payload["usage"]
                        if (
                            not state.response_audio_started
                            and state.silent_response_retries < MAX_SILENT_RESPONSE_RETRIES
                            and _silent_response_retry_allowed(
                                state,
                                initial_response_phase=initial_response_phase,
                            )
                        ):
                            state.silent_response_retries += 1
                            await _send_response_create(realtime_ws, output_modalities=("audio",))
                            continue
                        if settings.reservation_lab_booking_enabled and state.pending_reservation_id:
                            from app.reservation_lab.models import BookingRequest, Preferences
                            from app.services.reservation_lab_voice import confirm_booking_for_voice, default_restaurant_id

                            payload = state.pending_confirm_payload or {}
                            request = BookingRequest(
                                restaurant_id=str(getattr(restaurant, "id", None) or default_restaurant_id()),
                                date=date.fromisoformat(str(payload.get("date"))),
                                time=time.fromisoformat(str(payload.get("time"))),
                                customer_name=str(payload.get("customer_name") or ""),
                                phone_number=state.caller_phone,
                                party_size=int(payload.get("party_size") or 1),
                                preferences=Preferences(
                                    preferred_area=payload.get("preferred_area"),
                                    special_requests=payload.get("special_requests"),
                                ),
                                original_agent_text="voice confirm after assistant speech",
                            )
                            confirm_result = confirm_booking_for_voice(
                                reservation_id=state.pending_reservation_id,
                                request=request,
                            )
                            if confirm_result.get("success"):
                                state.terminal_write_success = True
                                state.outcome = "booking_created"
                                state.current_booking_id = state.pending_reservation_id
                            state.pending_reservation_id = None
                            state.pending_confirm_payload = None
                        await _maybe_refresh_summary(
                            realtime_ws=realtime_ws,
                            db_factory=db_factory,
                            restaurant=restaurant,
                            state=state,
                            call_sid=call_sid,
                        )
                        if (
                            hangup_delay_ms
                            and state.terminal_write_success
                            and not state.twilio_call_sid.startswith("studio-")
                        ):
                            asyncio.create_task(
                                _end_twilio_call_after_delay(
                                    restaurant=restaurant,
                                    call_sid=state.twilio_call_sid,
                                    delay_ms=hangup_delay_ms,
                                )
                            )
                            state.end_call_after_response = False
                            continue
                        if state.pending_tool_followup:
                            state.pending_tool_followup = False
                            await _send_response_create(realtime_ws, output_modalities=("audio",))
                        elif hangup_delay_ms and not state.twilio_call_sid.startswith("studio-"):
                            asyncio.create_task(
                                _end_twilio_call_after_delay(
                                    restaurant=restaurant,
                                    call_sid=state.twilio_call_sid,
                                    delay_ms=hangup_delay_ms,
                                )
                            )
                            state.end_call_after_response = False
                        continue

                    if event_type == "session.updated":
                        json_log(
                            "app.realtime",
                            {
                                "event": "openai_session_updated",
                                "restaurant_id": restaurant.id,
                                "call_sid": call_sid,
                            },
                        )
                        continue

                    if event_type == "rate_limits.updated":
                        json_log(
                            "app.realtime",
                            {
                                "event": "openai_rate_limits",
                                "restaurant_id": restaurant.id,
                                "call_sid": call_sid,
                                "rate_limits": event.get("rate_limits"),
                            },
                        )
                        continue

                    if event_type == "error":
                        error_body = event.get("error", {})
                        error_type = error_body.get("type", "")
                        error_msg = str(error_body.get("message") or error_body or event)
                        if (
                            error_type == "invalid_request_error"
                            and "no active response found" in error_msg.lower()
                        ):
                            state.response_in_progress = False
                            state.assistant_is_speaking = False
                            continue
                        json_log(
                            "app.realtime",
                            {
                                "event": "openai_realtime_error",
                                "restaurant_id": restaurant.id,
                                "call_sid": call_sid,
                                "error_type": error_type,
                                "error": error_msg,
                            },
                        )
                        # Server errors are fatal; input validation errors are recoverable
                        if error_type == "server_error":
                            raise RuntimeError(error_msg)
                        continue

            async def caller_inactivity_watchdog() -> None:
                while True:
                    await asyncio.sleep(5)
                    if state.end_call_after_response or state.response_in_progress or state.assistant_is_speaking:
                        continue
                    idle_seconds = (datetime.now(UTC) - state.last_caller_activity_at).total_seconds()
                    if idle_seconds >= CALLER_INACTIVITY_TIMEOUT_SECONDS and state.caller_speech_detected:
                        state.outcome = "abandoned"
                        if state.twilio_call_sid and not state.twilio_call_sid.startswith("studio-"):
                            with suppress(Exception):
                                await asyncio.to_thread(
                                    _sync_end_twilio_call,
                                    restaurant=restaurant,
                                    call_sid=state.twilio_call_sid,
                                )
                        return

            send_task = asyncio.create_task(twilio_to_openai())
            receive_task = asyncio.create_task(openai_to_twilio())
            inactivity_task = asyncio.create_task(caller_inactivity_watchdog())
            done, pending = await asyncio.wait(
                {send_task, receive_task, inactivity_task},
                return_when=asyncio.FIRST_COMPLETED,
            )
            for task in pending:
                task.cancel()
                with suppress(asyncio.CancelledError):
                    await task
            for task in done:
                task.result()
    except Exception as exc:
        state.call_status = "failed"
        state.outcome = "tool_error" if state.tool_events else "abandoned"
        transferred = False
        if not state.twilio_call_sid.startswith("studio-"):
            transferred = await asyncio.to_thread(
                _sync_transfer_call_to_restaurant,
                restaurant=restaurant,
                call_sid=state.twilio_call_sid,
            )
            # Observability: record the exception-fallback escalation so the call_log audit captures why.
            state.tool_events.append(_make_auto_escalate_exception_event(exc, bool(transferred)))
            if transferred:
                state.outcome = "escalated"
        final_fields = {
            "provider_call_id": state.openai_session_id or call_sid,
            "call_status": state.call_status,
            "outcome": state.outcome,
            "transcript_preview": "\n".join(state.transcript_lines),
            "summary": _build_final_call_summary(state),
            "booking_id": state.current_booking_id,
            "extra_data": {
                "openai_session_id": state.openai_session_id,
                "caller_phone": from_number,
                "called_number": to_number,
                "tool_events": state.tool_events,
                "response_usage": state.last_response_usage,
                "transcription_events": state.transcription_events,
                "interruption_events": state.interruption_events,
                "conversation_summary": _build_final_conversation_summary(state),
                "dropped_input_audio_packets": state.dropped_input_audio_packets,
                "transferred_to_restaurant": transferred,
                "error": str(exc),
            },
            "duration_seconds": max(int((datetime.now(UTC) - state.started_at).total_seconds()), 0),
        }
        if settings.reservation_lab_booking_enabled:
            from app.services.reservation_lab_voice import finalize_voice_call_log

            await asyncio.to_thread(
                finalize_voice_call_log,
                call_sid=call_sid,
                restaurant_id=str(getattr(restaurant, "id", None) or settings.default_restaurant_id),
                caller_audio_ulaw=bytes(state.recorded_caller_audio_ulaw),
                **final_fields,
            )
        else:
            await _update_call(db_factory, call_sid=call_sid, **final_fields)
        json_log(
            "app.twilio",
            {
                "event": "twilio_media_stream_failed",
                "restaurant_id": restaurant.id,
                "call_sid": call_sid,
                "provider_call_id": state.openai_session_id,
                "error": str(exc),
            },
        )
        raise

    state.call_status = "successful"
    state.outcome = _successful_call_outcome(state)
    # Fallback: if current_booking_id was not set but a write tool succeeded,
    # extract the booking_id from tool events to avoid losing the linkage.
    final_booking_id = state.current_booking_id
    if not final_booking_id:
        for tool_event in reversed(state.tool_events):
            if (
                tool_event.get("tool") in {"create_booking", "modify_booking", "cancel_booking"}
                and isinstance(tool_event.get("result"), dict)
                and tool_event["result"].get("success") is True
            ):
                final_booking_id = tool_event["result"].get("booking_id")
                break
    final_fields = {
        "provider_call_id": state.openai_session_id or call_sid,
        "call_status": state.call_status,
        "outcome": state.outcome,
        "transcript_preview": "\n".join(state.transcript_lines),
        "summary": _build_final_call_summary(state),
        "booking_id": final_booking_id,
        "extra_data": {
            "openai_session_id": state.openai_session_id,
            "caller_phone": from_number,
            "called_number": to_number,
            "tool_events": state.tool_events,
            "response_usage": state.last_response_usage,
            "transcription_events": state.transcription_events,
            "interruption_events": state.interruption_events,
            "conversation_summary": _build_final_conversation_summary(state),
            "dropped_input_audio_packets": state.dropped_input_audio_packets,
        },
        "duration_seconds": max(int((datetime.now(UTC) - state.started_at).total_seconds()), 0),
    }
    if settings.reservation_lab_booking_enabled:
        from app.services.reservation_lab_voice import finalize_voice_call_log

        await asyncio.to_thread(
            finalize_voice_call_log,
            call_sid=call_sid,
            restaurant_id=str(getattr(restaurant, "id", None) or settings.default_restaurant_id),
            caller_audio_ulaw=bytes(state.recorded_caller_audio_ulaw),
            **final_fields,
        )
    else:
        await _update_call(db_factory, call_sid=call_sid, **final_fields)
    json_log(
        "app.twilio",
        {
            "event": "twilio_media_stream_completed",
            "restaurant_id": restaurant.id,
            "call_sid": call_sid,
            "provider_call_id": state.openai_session_id,
            "outcome": state.outcome,
        },
    )


async def run_text_simulation(
    *,
    db_factory: sessionmaker,
    restaurant: Restaurant,
    caller_phone: str,
    user_messages: list[str],
    prompt_override: str | None = None,
    overrides: RealtimeSessionOverrides | None = None,
) -> dict[str, Any]:
    if not user_messages:
        return {"assistant_message": "", "transcript": [], "tool_events": [], "usage": None}

    state = RealtimeCallState(caller_phone=caller_phone, twilio_call_sid="studio-sim")
    tuning = merged_session_overrides(restaurant, overrides)
    headers = realtime_headers()
    transcript: list[dict[str, str]] = []
    final_usage: dict[str, Any] | None = None

    async with connect(realtime_ws_url(tuning.model), additional_headers=headers) as realtime_ws:
        await realtime_ws.send(
            json.dumps(
                build_session_update(
                    restaurant,
                    caller_phone=caller_phone,
                    output_modalities=("text",),
                    prompt_override=prompt_override,
                    overrides=overrides,
                    include_diagnostics=True,
                    tool_names=_current_tool_scope(state),
                )
            )
        )
        await _send_runtime_context_message(realtime_ws, restaurant, caller_phone=caller_phone)

        async def run_single_turn(message: str) -> str:
            await realtime_ws.send(
                json.dumps(
                    {
                        "type": "conversation.item.create",
                        "item": {
                            "type": "message",
                            "role": "user",
                            "content": [{"type": "input_text", "text": message}],
                        },
                    }
                )
            )
            _ingest_user_transcript(state, message)
            _record_conversation_turn(state, role="user", text=message)
            transcript.append({"role": "user", "content": message})
            await _sync_tool_scope(
                realtime_ws,
                restaurant,
                state,
                caller_phone=caller_phone,
                output_modalities=("text",),
                prompt_override=prompt_override,
                overrides=overrides,
            )
            await _send_response_create(realtime_ws, output_modalities=("text",))

            assistant_chunks: list[str] = []
            pending_tool_followup = False
            while True:
                raw_event = await realtime_ws.recv()
                event = json.loads(raw_event)
                event_type = event.get("type")

                if event_type in {"response.output_text.delta", "response.text.delta"}:
                    delta = str(event.get("delta") or "")
                    if delta:
                        assistant_chunks.append(delta)
                    continue

                if event_type == "response.output_item.done":
                    item = event.get("item", {})
                    if item.get("type") == "function_call":
                        try:
                            arguments = json.loads(item.get("arguments") or "{}")
                        except json.JSONDecodeError:
                            continue

                        tool_started_at = datetime.now(UTC)
                        result = await dispatch_tool(
                            db_factory,
                            restaurant=restaurant,
                            state=state,
                            tool_name=str(item.get("name")),
                            arguments=arguments,
                        )
                        tool_completed_at = datetime.now(UTC)
                        state.tool_events.append(
                            {
                                "tool": item.get("name"),
                                "arguments": _scrub_pii(arguments),
                                "result": result,
                                "started_at": tool_started_at.isoformat(),
                                "completed_at": tool_completed_at.isoformat(),
                                "latency_ms": int(
                                    (tool_completed_at - tool_started_at).total_seconds() * 1000
                                ),
                            }
                        )
                        await realtime_ws.send(
                            json.dumps(
                                {
                                    "type": "conversation.item.create",
                                    "item": {
                                        "type": "function_call_output",
                                        "call_id": item.get("call_id"),
                                        "output": json.dumps(result, ensure_ascii=False),
                                    },
                                }
                            )
                        )
                        pending_tool_followup = _realtime_tool_requires_followup(
                            str(item.get("name")),
                            result,
                        )
                    continue

                if event_type == "response.done":
                    response_payload = event.get("response", {})
                    if pending_tool_followup:
                        pending_tool_followup = False
                        assistant_chunks.clear()
                        await _send_response_create(realtime_ws, output_modalities=("text",))
                        continue
                    text = "".join(assistant_chunks).strip()
                    if not text:
                        text = "Nessuna risposta generata."
                    _ingest_assistant_transcript(state, text)
                    _record_conversation_turn(state, role="assistant", text=text)
                    transcript.append({"role": "assistant", "content": text})
                    nonlocal final_usage
                    final_usage = response_payload.get("usage")
                    return text

                if event_type == "error":
                    raise RuntimeError(str(event.get("error") or event))

        assistant_message = ""
        for message in user_messages:
            assistant_message = await run_single_turn(message)

    return {
        "assistant_message": assistant_message,
        "transcript": transcript,
        "tool_events": state.tool_events,
        "usage": final_usage,
    }
