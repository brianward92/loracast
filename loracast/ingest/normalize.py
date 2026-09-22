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


# The container that holds the spoken transcript on an NPR transcript page.
# Every saved page from 2019 to 2026 uses it. Other sites fall through to the
# whole-page splitter below.
_TRANSCRIPT_CONTAINER = re.compile(
    r'<div\b[^>]*class="[^"]*\btranscript\b[^"]*\bstorytext\b[^"]*"[^>]*>',
    re.IGNORECASE,
)
_DIV_TAG = re.compile(r"<(/?)div\b[^>]*>", re.IGNORECASE)
# Opening tags count as breaks too: HTML lets a paragraph omit its closing
# tag, and a fifth of the saved NPR pages do, which would otherwise glue every
# turn of the conversation into one line.
_BLOCK_BREAK = re.compile(
    r"</?(?:p|div|li|h[1-6]|blockquote|tr)\b[^>]*>|<br\s*/?>", re.IGNORECASE
)


def extract_container_html(value: str) -> str | None:
    """Inner HTML of the transcript container, or None when the page has none.

    Walks nested ``<div>`` tags so an advert or player block inside the
    container does not end the match early.
    """
    opener = _TRANSCRIPT_CONTAINER.search(value)
    if opener is None:
        return None
    start = opener.end()
    depth = 1
    for tag in _DIV_TAG.finditer(value, start):
        depth += -1 if tag.group(1) else 1
        if depth == 0:
            return value[start : tag.start()]
    return value[start:]


_SPEAKER_LINE = re.compile(r"^[A-Z][A-Z0-9 .,'&-]{1,60}(?:, [A-Z ]+)?: ", re.MULTILINE)
_NAV_SIGNATURE = (
    "Accessibility links",
    "Skip to main content",
    "Keyboard shortcuts for audio player",
)


def has_transcript_body(text: str) -> bool:
    """Whether parsed page text holds a transcript rather than page chrome.

    A transcript page that never rendered its body still parses to a few
    kilobytes of navigation and footer, and once stored that text looks like
    a transcript to everything downstream. Speaker lines settle it; failing
    that, page chrome at the top of the text means there is no body.
    """
    if len(text) < 500:
        return False
    if len(_SPEAKER_LINE.findall(text)) >= 3:
        return True
    return not any(marker in text[:600] for marker in _NAV_SIGNATURE)


def html_to_transcript_text(value: str) -> str:
    """Transcript text from a transcript page, one paragraph per line.

    When the page carries a transcript container, only its paragraphs are
    kept: navigation, cookie banners, footers and scripts never reach the
    transcript. A container with no paragraphs means the page has no
    transcript body, and the result is empty so the caller can say so. Pages
    without a recognised container get the whole-page fallback.
    """
    stripped = _INVISIBLE_CONTENT.sub("\n", value)
    inner = extract_container_html(stripped)
    if inner is not None:
        inner = _BLOCK_BREAK.sub("\n", inner)
        inner = unescape(re.sub(r"<[^>]+>", "", inner))
        parts = [clean_text(part) for part in inner.splitlines()]
        return "\n".join(part for part in parts if part)
    return "\n".join(split_fallback_text(value))
