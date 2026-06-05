from __future__ import annotations

import re


def normalize_phone_e164(raw: str | None, *, default_region: str = "IT") -> str | None:
    if not raw:
        return None
    cleaned = re.sub(r"[^\d+]", "", str(raw).strip())
    if not cleaned:
        return None
    if cleaned.startswith("00"):
        cleaned = "+" + cleaned[2:]
    if cleaned.startswith("+"):
        return cleaned
    if default_region.upper() == "IT":
        if cleaned.startswith("39") and len(cleaned) >= 10:
            return "+" + cleaned
        if cleaned.startswith("0"):
            return "+39" + cleaned[1:]
        return "+39" + cleaned
    return "+" + cleaned


def phones_equivalent(a: str | None, b: str | None) -> bool:
    na, nb = normalize_phone_e164(a), normalize_phone_e164(b)
    if not na or not nb:
        return False
    return na == nb
