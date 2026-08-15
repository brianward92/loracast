"""Per-episode transcript acquisition strategies.

Each strategy takes the pipeline (for caches, fetching, and artifact paths)
and returns a result dict with ``content``/``source_type``/... or ``None``
when the strategy produced nothing.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from .asr import DEFAULT_FASTER_WHISPER_MODEL, load_whisper_model, transcribe_audio
from .adapters import get_adapter
from .fetch import log
from .normalize import html_to_transcript_text
from .official_apple import (
    fetch_apple_transcript,
    find_apple_episode,
    list_apple_episode_index,
)
from .official_video import (
    find_official_youtube_caption,
    list_official_youtube_entries,
)
from .store import record_attempt, write_raw_artifact


def strategy_order(source: dict) -> list[str]:
    if source.get("strategy_order"):
        return list(source["strategy_order"])
    order = []
    if not source.get("official_youtube_only"):
        order.append("official_site")
    order.extend(["official_youtube", "asr"])
    return order


def official_strategy_order(source: dict) -> list[str]:
    return [strategy for strategy in strategy_order(source) if strategy != "asr"]


def run_strategy(
    pipeline, conn: sqlite3.Connection, source: dict, episode: dict, strategy: str
) -> dict | None:
    try:
        if strategy == "official_site":
            return try_official_site(pipeline, conn, source, episode)
        if strategy == "official_youtube":
            return try_official_youtube(pipeline, conn, source, episode)
        if strategy == "apple_podcasts":
            return try_apple_podcasts(pipeline, conn, source, episode)
        if strategy == "asr":
            return try_asr(pipeline, conn, source, episode)
        return None
    except Exception as exc:  # noqa: BLE001
        record_attempt(
            conn=conn,
            episode_id=episode["episode_id"],
            attempt_type=strategy,
            source_url=episode.get("episode_url"),
            outcome="error",
            error=str(exc),
            note=f"{strategy} strategy failed",
            audio_path=episode.get("audio_path"),
        )
        return None


def try_official_site(
    pipeline, conn: sqlite3.Connection, source: dict, episode: dict
) -> dict | None:
    if source.get("official_youtube_only"):
        return None
    adapter = get_adapter(source["adapter"])
    episode_html = pipeline.fetch_text(episode["episode_url"])
    transcript_url, resolution_note = adapter.resolve_transcript_url(
        source=source,
        episode_url=episode["episode_url"],
        episode_html=episode_html,
    )
    if not transcript_url:
        record_attempt(
            conn=conn,
            episode_id=episode["episode_id"],
            attempt_type="official_site",
            source_url=episode["episode_url"],
            outcome="no_result",
            note=resolution_note,
            audio_path=episode.get("audio_path"),
        )
        return None
    pipeline.check_policy(source, transcript_url, episode_html)
    transcript_html = pipeline.fetch_text(transcript_url)
    pipeline.check_policy(source, transcript_url, transcript_html)
    artifact_path = write_raw_artifact(
        pipeline.artifact_dir, episode=episode, suffix=".html", content=transcript_html
    )
    transcript_text = html_to_transcript_text(transcript_html)
    return {
        "content": transcript_text,
        "source_url": transcript_url,
        "source_type": "official_site",
        "resolution_note": resolution_note,
        "artifact_path": str(artifact_path),
        "audio_path": episode.get("audio_path"),
        "is_machine_generated": False,
    }


def try_official_youtube(
    pipeline, conn: sqlite3.Connection, source: dict, episode: dict
) -> dict | None:
    slug = source["slug"]
    if slug not in pipeline.youtube_entries_cache:
        try:
            pipeline.youtube_entries_cache[slug] = list_official_youtube_entries(source)
        except Exception as exc:  # noqa: BLE001
            # Cache the failure so we don't reattempt the same broken
            # playlist/channel listing once per episode for the rest of the
            # run. The per-episode ytsearch fallback inside
            # find_official_youtube_caption can still find videos.
            log(
                f"youtube listing failed source={slug} error={exc}; "
                "continuing with empty entries cache"
            )
            pipeline.youtube_entries_cache[slug] = []
    result = find_official_youtube_caption(
        source=source,
        episode_title=episode["title"],
        cached_entries=pipeline.youtube_entries_cache[slug],
        episode_duration_seconds=episode.get("duration_seconds"),
    )
    if result is None:
        record_attempt(
            conn=conn,
            episode_id=episode["episode_id"],
            attempt_type="official_youtube",
            source_url=source.get("youtube_playlist_url")
            or source.get("youtube_channel_handle"),
            outcome="no_result",
            note="no official youtube captions found",
            audio_path=episode.get("audio_path"),
        )
        return None
    artifact_path = write_raw_artifact(
        pipeline.artifact_dir,
        episode=episode,
        suffix=".txt",
        content=result["content"],
        namespace="captions",
    )
    result["artifact_path"] = str(artifact_path)
    result["audio_path"] = episode.get("audio_path")
    return result


def try_apple_podcasts(
    pipeline, conn: sqlite3.Connection, source: dict, episode: dict
) -> dict | None:
    collection_id = source.get("apple_podcast_id")
    if not collection_id:
        return None

    slug = source["slug"]
    if slug not in pipeline.apple_episode_index_cache:
        try:
            pipeline.apple_episode_index_cache[slug] = list_apple_episode_index(
                collection_id
            )
        except Exception as exc:  # noqa: BLE001
            # Cache the failure so we don't reattempt the same broken index
            # lookup for every episode.
            log(
                f"apple podcast index failed source={slug} error={exc}; "
                "continuing with empty index cache"
            )
            pipeline.apple_episode_index_cache[slug] = []

    match = find_apple_episode(
        pipeline.apple_episode_index_cache[slug],
        episode_title=episode["title"],
        published_at=episode.get("published_at"),
    )
    if match is None:
        record_attempt(
            conn=conn,
            episode_id=episode["episode_id"],
            attempt_type="apple_podcasts",
            source_url=f"https://podcasts.apple.com/podcast/id{collection_id}",
            outcome="no_result",
            note="no matching episode in apple podcasts",
            audio_path=episode.get("audio_path"),
        )
        return None

    episode_apple_url = (
        f"https://podcasts.apple.com/podcast/id{collection_id}?i={match['trackId']}"
    )
    transcript_text = fetch_apple_transcript(collection_id, str(match["trackId"]))
    if not transcript_text:
        record_attempt(
            conn=conn,
            episode_id=episode["episode_id"],
            attempt_type="apple_podcasts",
            source_url=episode_apple_url,
            outcome="no_result",
            note="failed to fetch apple transcript",
            audio_path=episode.get("audio_path"),
        )
        return None

    artifact_path = write_raw_artifact(
        pipeline.artifact_dir,
        episode=episode,
        suffix=".txt",
        content=transcript_text,
        namespace="apple",
    )
    return {
        "content": transcript_text,
        "source_url": episode_apple_url,
        "source_type": "apple_podcasts",
        "resolution_note": f"apple trackId={match['trackId']}",
        "artifact_path": str(artifact_path),
        "audio_path": episode.get("audio_path"),
        "is_machine_generated": True,
    }


def try_asr(
    pipeline, conn: sqlite3.Connection, source: dict, episode: dict
) -> dict | None:
    audio_path = episode.get("audio_path")
    if not audio_path or not Path(audio_path).exists():
        audio_path = pipeline.ensure_audio_downloaded(conn, episode)
        if audio_path:
            episode["audio_path"] = audio_path
    if not audio_path or not Path(audio_path).exists():
        record_attempt(
            conn=conn,
            episode_id=episode["episode_id"],
            attempt_type="asr",
            source_url=episode.get("audio_url"),
            outcome="no_result",
            note="audio unavailable for asr",
        )
        return None
    model_name = source.get("asr_model") or DEFAULT_FASTER_WHISPER_MODEL
    if model_name not in pipeline.asr_model_cache:
        log(f"asr loading model={model_name}")
        instance = load_whisper_model(model_name)
        if instance is None:
            record_attempt(
                conn=conn,
                episode_id=episode["episode_id"],
                attempt_type="asr",
                source_url=episode.get("audio_url"),
                outcome="error",
                error="faster_whisper package not installed",
                audio_path=audio_path,
            )
            return None
        pipeline.asr_model_cache[model_name] = instance
    result = transcribe_audio(
        audio_path=Path(audio_path),
        model=model_name,
        language=source.get("language", "en"),
        model_instance=pipeline.asr_model_cache[model_name],
    )
    if not result or not result.get("content"):
        record_attempt(
            conn=conn,
            episode_id=episode["episode_id"],
            attempt_type="asr",
            source_url=episode.get("audio_url"),
            outcome="no_result",
            note=(result or {}).get("error", "no asr backend produced a transcript"),
            audio_path=audio_path,
            is_machine_generated=True,
        )
        return None
    artifact_path = write_raw_artifact(
        pipeline.artifact_dir,
        episode=episode,
        suffix=".txt",
        content=result["content"],
        namespace="asr",
    )
    result["artifact_path"] = str(artifact_path)
    result["audio_path"] = audio_path
    return result
