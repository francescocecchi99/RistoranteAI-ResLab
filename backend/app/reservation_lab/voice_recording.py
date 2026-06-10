from __future__ import annotations

import os
import struct
import wave
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[3]

_ULAW_BIAS = 0x84


def _ulaw_byte_to_linear(byte_val: int) -> int:
    byte_val = (~byte_val) & 0xFF
    sign = byte_val & 0x80
    exponent = (byte_val >> 4) & 0x07
    mantissa = byte_val & 0x0F
    sample = ((mantissa << 4) + _ULAW_BIAS) << exponent
    sample -= _ULAW_BIAS
    if sign:
        sample = -sample
    return max(-32768, min(32767, sample))


def _ulaw_to_pcm16(ulaw_payload: bytes) -> bytes:
    return b"".join(
        struct.pack("<h", _ulaw_byte_to_linear(b)) for b in ulaw_payload
    )


def recordings_root() -> Path:
    raw = (os.getenv("VOICE_RECORDINGS_DIR") or "").strip()
    if raw:
        return Path(raw)
    return _REPO_ROOT / "data" / "voice_recordings"


def save_caller_ulaw_wav(*, restaurant_id: str, call_log_id: str, ulaw_payload: bytes) -> str | None:
    """Persist caller-side μ-law audio as WAV. Returns storage path for DB."""
    if not ulaw_payload:
        return None
    root = recordings_root()
    dest_dir = root / restaurant_id
    dest_dir.mkdir(parents=True, exist_ok=True)
    wav_path = dest_dir / f"{call_log_id}.wav"
    pcm = _ulaw_to_pcm16(ulaw_payload)
    with wave.open(str(wav_path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(8000)
        wf.writeframes(pcm)
    try:
        return str(wav_path.relative_to(root)).replace("\\", "/")
    except ValueError:
        return str(wav_path)


def resolve_recording_file(storage_path: str) -> Path | None:
    if not storage_path or storage_path.startswith("http"):
        return None
    candidate = recordings_root() / storage_path.replace("\\", "/")
    if candidate.is_file():
        return candidate
    return None
