from __future__ import annotations

import re
from html import unescape

_INVISIBLE_CONTENT = re.compile(
    r"<(script|style|noscript)\b.*?</\1\s*>", re.DOTALL | re.IGNORECASE
)


def split_fallback_text(value: str) -> list[str]:
    stripped = _INVISIBLE_CONTENT.sub("\n", value)
    stripped = re.sub(r"<[^>]+>", "\n", stripped)
    stripped = unescape(stripped)
    parts = [clean_text(part) for part in stripped.splitlines()]
    return [part for part in parts if part]


def clean_text(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip()


def html_to_transcript_text(value: str) -> str:
    parts = split_fallback_text(value)
    return "\n".join(parts)
