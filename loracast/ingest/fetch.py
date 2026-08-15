"""HTTP fetching with retries, plus per-episode wall-clock watchdogs."""

from __future__ import annotations

import signal
import socket
import ssl
import sys
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

USER_AGENT = "loracast/0.1 (+https://github.com/brianward92/loracast)"

FETCH_RETRY_ATTEMPTS = 4
FETCH_RETRY_BASE_SLEEP_SECONDS = 0.75

# Per-episode wall-clock budgets. urlopen's timeout bounds each socket read,
# not total read time, so a slow-trickling CDN never returns and a single
# transcript-less episode can wedge the whole acquire stage.
PER_EPISODE_ACQUIRE_SECONDS = 180
PER_EPISODE_ASR_SECONDS = 1500


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def log(message: str) -> None:
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S%z")
    print(f"[{stamp}] {message}", file=sys.stderr, flush=True)


class StageTimeout(BaseException):
    """Raised by the per-episode watchdog. Subclasses BaseException so the
    broad ``except Exception`` guards inside individual strategies cannot
    swallow it -- a hung fetch must unwind to the per-episode handler, not be
    mistaken for a merely-failed strategy."""


@contextmanager
def time_limit(seconds: int):
    """Best-effort wall-clock cap via SIGALRM (POSIX, main thread only).

    Raises ``StageTimeout`` on expiry. Degrades to a no-op where SIGALRM
    cannot be armed (non-main thread / unsupported platform) so callers keep
    their prior behavior instead of crashing.
    """
    if seconds <= 0 or not hasattr(signal, "SIGALRM"):
        yield
        return

    def _handler(signum, frame):  # noqa: ANN001
        raise StageTimeout(f"timed out after {seconds}s")

    try:
        previous = signal.signal(signal.SIGALRM, _handler)
    except ValueError:
        yield
        return
    signal.alarm(seconds)
    try:
        yield
    finally:
        signal.alarm(0)
        signal.signal(signal.SIGALRM, previous)


def fetch_text(url: str) -> str:
    request = Request(
        url,
        headers={
            "User-Agent": USER_AGENT,
            "Accept": "text/html,application/xml,text/xml;q=0.9,*/*;q=0.8",
        },
    )
    with _fetch_response(request) as response:
        charset = response.headers.get_content_charset() or "utf-8"
        return response.read().decode(charset, errors="replace")


def fetch_bytes(url: str) -> bytes:
    request = Request(url, headers={"User-Agent": USER_AGENT, "Accept": "*/*"})
    with _fetch_response(request) as response:
        return response.read()


def _fetch_response(request: Request):
    for attempt in range(1, FETCH_RETRY_ATTEMPTS + 1):
        try:
            return urlopen(request, timeout=45)
        except Exception as exc:  # noqa: BLE001
            if not _should_retry_fetch(exc) or attempt == FETCH_RETRY_ATTEMPTS:
                raise
            sleep_seconds = FETCH_RETRY_BASE_SLEEP_SECONDS * (2 ** (attempt - 1))
            log(
                f"fetch retry attempt={attempt}/{FETCH_RETRY_ATTEMPTS} "
                f"url={request.full_url} sleep={sleep_seconds:.2f}s error={exc}"
            )
            time.sleep(sleep_seconds)
    raise RuntimeError("unreachable")


def _should_retry_fetch(exc: Exception) -> bool:
    if isinstance(exc, HTTPError):
        return exc.code in {408, 425, 429, 500, 502, 503, 504}
    if isinstance(exc, URLError):
        reason = exc.reason
        if isinstance(
            reason,
            (
                socket.timeout,
                TimeoutError,
                ConnectionResetError,
                ssl.SSLError,
                socket.gaierror,
            ),
        ):
            return True
        if isinstance(reason, str):
            lowered = reason.lower()
            return (
                "timed out" in lowered
                or "reset" in lowered
                or "temporarily unavailable" in lowered
            )
        return False
    if isinstance(exc, OSError):
        lowered = str(exc).lower()
        return (
            "reset" in lowered
            or "timed out" in lowered
            or "temporarily unavailable" in lowered
        )
    return isinstance(
        exc, (TimeoutError, ConnectionResetError, ssl.SSLError, socket.timeout)
    )
