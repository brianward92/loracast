from __future__ import annotations

import importlib
import re
import tempfile
from difflib import SequenceMatcher
from pathlib import Path

YTDLP_SOCKET_TIMEOUT_SECONDS = 20
YTDLP_EXTRACTION_LIMIT = 5000


def list_official_youtube_entries(source: dict) -> list[dict]:
    """Fetch the title-independent set of candidate videos for a source.

    This is the expensive part of the official-youtube path (playlist + channel
    listings), so callers should cache the result across episodes within a run.
    """
    if not source.get("youtube_channel_handle") and not source.get(
        "youtube_playlist_url"
    ):
        return []

    yt_dlp = _load_yt_dlp()
    if yt_dlp is None:
        return []

    entries: list[dict] = []
    playlist_url = source.get("youtube_playlist_url")
    if playlist_url:
        entries.extend(_extract_playlist_entries(yt_dlp, playlist_url))

    channel_handle = source.get("youtube_channel_handle")
    if channel_handle:
        entries.extend(_extract_channel_entries(yt_dlp, channel_handle))

    return entries


def find_official_youtube_caption(
    source: dict,
    episode_title: str,
    cached_entries: list[dict] | None = None,
    episode_duration_seconds: int | None = None,
) -> dict | None:
    if not source.get("youtube_channel_handle") and not source.get(
        "youtube_playlist_url"
    ):
        return None

    yt_dlp = _load_yt_dlp()
    if yt_dlp is None:
        return None

    if cached_entries is None:
        cached_entries = list_official_youtube_entries(source)

    candidate = _find_best_video_match(
        yt_dlp,
        source,
        episode_title,
        cached_entries=cached_entries,
        episode_duration_seconds=episode_duration_seconds,
    )
    if candidate is None:
        return None

    transcript_text = _download_caption_text(yt_dlp, candidate["webpage_url"])
    if not transcript_text:
        return None

    return {
        "source_url": candidate["webpage_url"],
        "source_type": "youtube_caption",
        "resolution_note": f"official youtube captions: {candidate['title']}",
        "content": transcript_text,
    }


def _load_yt_dlp():
    try:
        return importlib.import_module("yt_dlp")
    except ModuleNotFoundError:
        return None


DURATION_TOLERANCE_SECONDS = 60
# Title floor required before a duration match counts. Without this guard,
# two unrelated episodes of similar length (e.g. weekly ~45-min shows) would
# get matched purely on runtime.
DURATION_MATCH_TITLE_FLOOR = 0.55


def _find_best_video_match(
    yt_dlp,
    source: dict,
    episode_title: str,
    cached_entries: list[dict] | None = None,
    episode_duration_seconds: int | None = None,
) -> dict | None:
    entries: list[dict] = list(cached_entries or [])
    playlist_url = source.get("youtube_playlist_url")
    channel_handle = source.get("youtube_channel_handle")

    # Per-title channel search is cheap and title-specific, so we run it even
    # when bulk entries were pre-fetched. This catches videos that fall outside
    # the playlist or channel listing pages.
    if channel_handle:
        entries.extend(_search_channel_entries(yt_dlp, channel_handle, episode_title))

    expected_handle = normalize_text(channel_handle or "")
    best: dict | None = None
    best_score = 0.0

    for entry in entries:
        webpage_url = entry.get("webpage_url") or entry.get("url")
        title = entry.get("title") or ""
        if not webpage_url or not title:
            continue
        if not _matches_official_source(entry, expected_handle, playlist_url):
            continue
        score = title_similarity(episode_title, title)
        # Duration is a strong fingerprint that survives title rewrites.
        # When the RSS duration matches the video duration within tolerance,
        # treat anything above a loose title floor as a confident match.
        # The floor avoids matching unrelated videos that happen to share
        # a similar runtime (e.g. two ~45-minute weekly episodes).
        if (
            episode_duration_seconds
            and entry.get("duration")
            and score >= DURATION_MATCH_TITLE_FLOOR
        ):
            duration_delta = abs(int(entry["duration"]) - episode_duration_seconds)
            if duration_delta <= DURATION_TOLERANCE_SECONDS:
                score = max(score, 0.95)
        if score > best_score:
            best = {"webpage_url": webpage_url, "title": title}
            best_score = score

    if best_score < 0.72:
        return None
    return best


def _extract_playlist_entries(yt_dlp, playlist_url: str) -> list[dict]:
    options = {
        "quiet": True,
        "no_warnings": True,
        "extract_flat": True,
        "skip_download": True,
        "playlistend": YTDLP_EXTRACTION_LIMIT,
        "socket_timeout": YTDLP_SOCKET_TIMEOUT_SECONDS,
        "retries": 1,
    }
    try:
        with yt_dlp.YoutubeDL(options) as ydl:
            info = ydl.extract_info(playlist_url, download=False)
    except Exception:  # noqa: BLE001
        return []
    entries = list(info.get("entries") or [])
    # Tag with provenance: by construction these are all from the requested
    # playlist, so per-entry channel matching is unnecessary (and yt-dlp's
    # flat extraction frequently returns null channel/uploader fields).
    for entry in entries:
        entry["_official_source"] = True
    return entries


def _search_channel_entries(
    yt_dlp, channel_handle: str, episode_title: str
) -> list[dict]:
    options = {
        "quiet": True,
        "no_warnings": True,
        "extract_flat": True,
        "skip_download": True,
        "socket_timeout": YTDLP_SOCKET_TIMEOUT_SECONDS,
        "retries": 1,
    }
    try:
        with yt_dlp.YoutubeDL(options) as ydl:
            info = ydl.extract_info(
                f"ytsearch5:{channel_handle} {episode_title}", download=False
            )
        return list(info.get("entries") or [])
    except Exception:  # noqa: BLE001
        # Network failures or YT API issues; return empty list so strategy
        # can fall back to other approaches. This should not surface as a
        # strategy error.
        return []


def _extract_channel_entries(yt_dlp, channel_handle: str) -> list[dict]:
    entries = []
    normalized_handle = (
        channel_handle if channel_handle.startswith("@") else f"@{channel_handle}"
    )
    candidate_urls = [
        f"https://www.youtube.com/{normalized_handle}/videos",
    ]
    options = {
        "quiet": True,
        "no_warnings": True,
        "extract_flat": True,
        "skip_download": True,
        "playlistend": YTDLP_EXTRACTION_LIMIT,
        "socket_timeout": YTDLP_SOCKET_TIMEOUT_SECONDS,
        "retries": 1,
    }
    for candidate_url in candidate_urls:
        try:
            with yt_dlp.YoutubeDL(options) as ydl:
                info = ydl.extract_info(candidate_url, download=False)
        except Exception:  # noqa: BLE001
            continue
        for entry in info.get("entries") or []:
            # Tag with provenance: these came from the channel's own /videos
            # page, so per-entry channel matching is unnecessary.
            entry["_official_source"] = True
            entries.append(entry)
    deduped = []
    seen = set()
    for entry in entries:
        key = entry.get("id") or entry.get("webpage_url") or entry.get("url")
        if not key or key in seen:
            continue
        seen.add(key)
        deduped.append(entry)
    return deduped


def _matches_official_source(
    entry: dict, expected_handle: str, playlist_url: str | None
) -> bool:
    # Trust provenance tags from extractors that pull from a known-channel
    # or known-playlist URL. yt-dlp's flat extraction often returns null
    # channel/uploader fields, so per-entry filtering would falsely reject
    # legitimate videos.
    if entry.get("_official_source"):
        return True
    if playlist_url:
        playlist_id = playlist_url.split("list=")[-1]
        if entry.get("playlist_id") == playlist_id:
            return True
    if expected_handle:
        haystacks = [
            entry.get("channel", ""),
            entry.get("channel_url", ""),
            entry.get("uploader", ""),
            entry.get("uploader_id", ""),
            entry.get("webpage_url", ""),
            entry.get("url", ""),
        ]
        # Compare both with and without spaces so that a config handle like
        # "@ProfGMarkets" matches a YouTube display name like "Prof G Markets".
        expected_compact = expected_handle.replace(" ", "")
        for value in haystacks:
            if not value:
                continue
            normalized = normalize_text(value)
            if expected_handle in normalized:
                return True
            if expected_compact and expected_compact in normalized.replace(" ", ""):
                return True
        return False
    return playlist_url is not None


def _download_caption_text(yt_dlp, video_url: str) -> str | None:
    with tempfile.TemporaryDirectory() as tmp:
        outtmpl = str(Path(tmp) / "%(id)s.%(ext)s")
        options = {
            "quiet": True,
            "no_warnings": True,
            "noprogress": True,
            "skip_download": True,
            "writesubtitles": True,
            "writeautomaticsub": True,
            "subtitleslangs": ["en", "en-US", "en-GB"],
            "subtitlesformat": "vtt/best",
            "outtmpl": outtmpl,
            "socket_timeout": YTDLP_SOCKET_TIMEOUT_SECONDS,
            "retries": 1,
        }
        with yt_dlp.YoutubeDL(options) as ydl:
            ydl.extract_info(video_url, download=True)

        for path in sorted(Path(tmp).glob("*.vtt")):
            text = parse_vtt(path.read_text())
            if text:
                return text
    return None


def parse_vtt(text: str) -> str:
    lines = []
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line == "WEBVTT" or line.startswith("NOTE"):
            continue
        if "-->" in line:
            continue
        if re.fullmatch(r"\d+", line):
            continue
        cleaned = re.sub(r"<[^>]+>", "", line)
        cleaned = re.sub(r"&[a-z]+;", " ", cleaned)
        cleaned = re.sub(r"\s+", " ", cleaned).strip()
        if cleaned:
            lines.append(cleaned)

    deduped = []
    for line in lines:
        if deduped and deduped[-1] == line:
            continue
        deduped.append(line)
    return "\n".join(deduped)


def title_similarity(left: str, right: str) -> float:
    left_norm = normalize_text(left)
    right_norm = normalize_text(right)
    if not left_norm or not right_norm:
        return 0.0
    if left_norm == right_norm:
        return 1.0
    if left_norm in right_norm or right_norm in left_norm:
        return 0.92
    # Token-coverage rule: catches reordered titles and titles with extra
    # suffix/prefix segments (e.g. RSS "Foo: How Bar Works" vs YouTube
    # "How Bar Works | Foo w/ Host"). Only accept when the source title has
    # enough meaningful tokens to make a coincidence unlikely.
    left_tokens = {token for token in left_norm.split() if len(token) >= 3}
    right_tokens = {token for token in right_norm.split() if len(token) >= 3}
    if len(left_tokens) >= 4:
        coverage = len(left_tokens & right_tokens) / len(left_tokens)
        if coverage >= 0.85:
            return 0.9
    return SequenceMatcher(a=left_norm, b=right_norm).ratio()


def normalize_text(value: str) -> str:
    value = value.lower()
    value = re.sub(r"[^a-z0-9]+", " ", value)
    return " ".join(value.split())
