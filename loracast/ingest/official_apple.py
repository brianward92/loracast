"""Fetch transcripts from Apple Podcasts using iTunes API and episode page scraping."""

import json
import re
from datetime import datetime
from urllib.request import Request, urlopen

from .fetch import USER_AGENT
from .official_video import title_similarity

DATE_TOLERANCE_DAYS = 2
TITLE_MATCH_FLOOR = 0.72
ITUNES_LOOKUP_URL = "https://itunes.apple.com/lookup"


def list_apple_episode_index(collection_id: str) -> list[dict]:
    """Fetch all episodes from iTunes lookup API with pagination.

    Returns list of dicts: [{trackId, trackName, releaseDate, trackTimeMillis}, ...]
    The first item (the podcast itself) is always skipped.
    """
    episodes = []
    offset = 0
    while True:
        url = f"{ITUNES_LOOKUP_URL}?id={collection_id}&entity=podcastEpisode&limit=200&offset={offset}"
        try:
            req = Request(url, headers={"User-Agent": USER_AGENT})
            with urlopen(req, timeout=10) as resp:
                data = json.loads(resp.read().decode("utf-8"))
        except Exception:
            break

        results = data.get("results", [])
        if not results:
            break

        # Skip first result (podcast itself, wantedType=podcast)
        for result in results[1:]:
            if result.get("wrapperType") == "podcastEpisode":
                episodes.append(
                    {
                        "trackId": result["trackId"],
                        "trackName": result["trackName"],
                        "releaseDate": result.get("releaseDate"),
                        "trackTimeMillis": result.get("trackTimeMillis"),
                    }
                )

        # Stop if we got fewer than 200 (last page)
        if len(results) < 200:
            break

        offset += 200

    return episodes


def find_apple_episode(
    index: list[dict], episode_title: str, published_at: str | None
) -> dict | None:
    """Match RSS episode to Apple episode by title_similarity + date proximity.

    Returns the best-matching episode dict from the index, or None.
    Requires: title_similarity >= TITLE_MATCH_FLOOR, date within ±2 days (if published_at set).
    """
    best_match = None
    best_score = 0.0

    target_date = None
    if published_at:
        try:
            target_date = datetime.fromisoformat(
                published_at.replace("Z", "+00:00")
            ).date()
        except (ValueError, AttributeError):
            pass

    for entry in index:
        # Title similarity
        sim = title_similarity(episode_title, entry["trackName"])
        if sim < TITLE_MATCH_FLOOR:
            continue

        # Date check (if both dates are available)
        if target_date and entry.get("releaseDate"):
            try:
                apple_date = datetime.fromisoformat(
                    entry["releaseDate"].replace("Z", "+00:00")
                ).date()
                days_diff = abs((apple_date - target_date).days)
                if days_diff > DATE_TOLERANCE_DAYS:
                    continue
            except (ValueError, AttributeError):
                # If date parsing fails, accept the title match
                pass

        if sim > best_score:
            best_score = sim
            best_match = entry

    return best_match


def fetch_apple_transcript(collection_id: str, track_id: str) -> str | None:
    """Fetch transcript from Apple episode page.

    Retrieves the episode page, extracts the transcript JSON URL from the
    serverData script, fetches the transcript JSON, and returns joined text.
    Returns None if any step fails.
    """
    episode_url = f"https://podcasts.apple.com/podcast/id{collection_id}?i={track_id}"

    # Fetch episode page
    try:
        req = Request(episode_url, headers={"User-Agent": USER_AGENT})
        with urlopen(req, timeout=10) as resp:
            page_html = resp.read().decode("utf-8")
    except Exception:
        return None

    # Extract serverData JSON blob
    match = re.search(
        r'<script[^>]+id=["\']?serialized-server-data["\']?[^>]*>([^<]+)</script>',
        page_html,
    )
    if not match:
        return None

    try:
        server_data = json.loads(match.group(1))
    except (json.JSONDecodeError, IndexError):
        return None

    # Navigate the nested structure to find transcript URLs
    # Apple's structure: d[0].data.attributes.transcriptUrls
    transcript_url = None
    try:
        for item in server_data.get("d", []):
            if "data" in item and "attributes" in item["data"]:
                urls = item["data"]["attributes"].get("transcriptUrls", [])
                if urls:
                    # Prefer VTT if available, else use first available
                    for url_item in urls:
                        url = url_item.get("url")
                        if url:
                            transcript_url = url
                            if url.endswith(".vtt"):
                                break
                    if transcript_url:
                        break
    except (KeyError, TypeError, AttributeError):
        pass

    if not transcript_url:
        return None

    # Fetch and parse transcript
    try:
        req = Request(transcript_url, headers={"User-Agent": USER_AGENT})
        with urlopen(req, timeout=10) as resp:
            transcript_data = json.loads(resp.read().decode("utf-8"))
    except Exception:
        return None

    # Join segments
    segments = []
    try:
        for segment in transcript_data.get("segments", []):
            text = segment.get("text", "").strip()
            if text:
                segments.append(text)
    except (KeyError, TypeError, AttributeError):
        pass

    if not segments:
        return None

    return " ".join(segments)
