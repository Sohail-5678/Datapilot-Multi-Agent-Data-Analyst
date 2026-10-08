"""PII redaction for traces and prompts (SPEC §10.4, §S.2)."""

from __future__ import annotations

import re

_EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")
_PHONE = re.compile(r"(?<!\w)(?:\+?\d[\d\s().-]{7,}\d)(?!\w)")
_CARD = re.compile(r"\b(?:\d[ -]?){13,19}\b")


def redact_text(text: str) -> str:
    if not text:
        return text
    text = _EMAIL.sub("[email]", text)
    text = _CARD.sub("[card]", text)
    return _PHONE.sub(lambda m: "[phone]" if sum(ch.isdigit() for ch in m.group()) >= 9 else m.group(), text)


def mask_value(v: object) -> str:
    s = str(v)
    if "@" in s:
        name, _, domain = s.partition("@")
        return f"{name[:1]}•••@{domain}"
    if len(s) <= 2:
        return "••"
    return s[:1] + "•" * min(6, len(s) - 1)
