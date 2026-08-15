"""SQLite and filesystem writes: episode rows, attempt logs, canonical transcripts."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from pathlib import Path
from urllib.parse import urlparse

from .adapters import EpisodeSeed
from .fetch import utcnow


def upsert_episode(conn: sqlite3.Connection, seed: EpisodeSeed) -> int:
    existing = conn.execute(
        "SELECT * FROM episodes WHERE episode_id = ?", (seed.episode_id,)
    ).fetchone()
    if existing:
        conn.execute(
            """
            UPDATE episodes
            SET title = ?, published_at = ?, episode_url = ?, audio_url = ?, guid = ?,
                duration_seconds = COALESCE(?, duration_seconds)
            WHERE episode_id = ?
            """,
            (
                seed.title,
                seed.published_at,
                seed.episode_url,
                seed.audio_url,
                seed.guid,
                seed.duration_seconds,
                seed.episode_id,
            ),
        )
        return 0
    conn.execute(
        """
        INSERT INTO episodes (
            episode_id, podcast_slug, title, published_at, episode_url, audio_url, guid,
            duration_seconds, pull_status
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'discovered')
        """,
        (
            seed.episode_id,
            seed.podcast_slug,
            seed.title,
            seed.published_at,
            seed.episode_url,
            seed.audio_url,
            seed.guid,
            seed.duration_seconds,
        ),
    )
    return 1


def record_attempt(
    conn: sqlite3.Connection,
    episode_id: str,
    attempt_type: str,
    source_url: str | None,
    outcome: str,
    note: str | None = None,
    error: str | None = None,
    artifact_path: str | None = None,
    transcript_path: str | None = None,
    audio_path: str | None = None,
    content_hash: str | None = None,
    is_machine_generated: bool = False,
) -> None:
    now = utcnow()
    conn.execute(
        """
        INSERT INTO transcript_attempts (
            episode_id, attempt_type, source_url, outcome, is_machine_generated, error, note,
            artifact_path, transcript_path, audio_path, content_hash, created_at, finished_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            episode_id,
            attempt_type,
            source_url,
            outcome,
            int(is_machine_generated),
            error,
            note,
            artifact_path,
            transcript_path,
            audio_path,
            content_hash,
            now,
            now,
        ),
    )


def store_canonical_transcript(
    conn: sqlite3.Connection,
    transcript_dir: Path,
    transcript_meta_dir: Path,
    episode: dict,
    transcript_text: str,
    source_type: str,
    source_url: str | None,
    resolution_note: str,
    artifact_path: str | None,
    audio_path: str | None,
    is_machine_generated: bool = False,
) -> None:
    content_hash = hashlib.sha1(transcript_text.encode("utf-8")).hexdigest()
    transcript_path = (
        transcript_dir / episode["podcast_slug"] / f"{episode['episode_id']}.txt"
    )
    transcript_path.parent.mkdir(parents=True, exist_ok=True)
    transcript_path.write_text(
        transcript_text
        + ("\n" if transcript_text and not transcript_text.endswith("\n") else "")
    )

    metadata_path = (
        transcript_meta_dir / episode["podcast_slug"] / f"{episode['episode_id']}.json"
    )
    metadata_path.parent.mkdir(parents=True, exist_ok=True)
    metadata = {
        "episode_id": episode["episode_id"],
        "podcast_slug": episode["podcast_slug"],
        "title": episode["title"],
        "transcript_source_type": source_type,
        "transcript_source_url": source_url,
        "resolution_note": resolution_note,
        "artifact_path": artifact_path,
        "audio_path": audio_path,
        "is_machine_generated": is_machine_generated,
        "content_hash": content_hash,
        "selected_at": utcnow(),
    }
    metadata_path.write_text(json.dumps(metadata, indent=2, sort_keys=True) + "\n")

    conn.execute(
        """
        INSERT INTO episode_transcripts (
            episode_id, transcript_path, transcript_source_type, transcript_source_url, acquisition_method,
            artifact_path, audio_path, is_machine_generated, language, confidence, content_hash, selected_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'en', ?, ?, ?)
        ON CONFLICT(episode_id) DO UPDATE SET
            transcript_path = excluded.transcript_path,
            transcript_source_type = excluded.transcript_source_type,
            transcript_source_url = excluded.transcript_source_url,
            acquisition_method = excluded.acquisition_method,
            artifact_path = excluded.artifact_path,
            audio_path = excluded.audio_path,
            is_machine_generated = excluded.is_machine_generated,
            language = excluded.language,
            confidence = excluded.confidence,
            content_hash = excluded.content_hash,
            selected_at = excluded.selected_at
        """,
        (
            episode["episode_id"],
            str(transcript_path),
            source_type,
            source_url,
            source_type,
            artifact_path,
            audio_path,
            int(is_machine_generated),
            0.75 if is_machine_generated else 1.0,
            content_hash,
            utcnow(),
        ),
    )
    conn.execute(
        """
        UPDATE episodes
        SET transcript_source_url = ?, transcript_source_type = ?, transcript_resolution_note = ?,
            license_notes = ?, pull_status = 'transcript_ready', content_hash = ?, raw_path = ?,
            fetched_at = ?, skip_reason = NULL, transcript_path = ?, transcript_metadata_path = ?,
            audio_path = ?, last_attempted_at = ?
        WHERE episode_id = ?
        """,
        (
            source_url,
            source_type,
            resolution_note,
            f"transcript acquired from {urlparse(source_url).netloc if source_url else 'local'}",
            content_hash,
            artifact_path,
            utcnow(),
            str(transcript_path),
            str(metadata_path),
            audio_path,
            utcnow(),
            episode["episode_id"],
        ),
    )


def write_raw_artifact(
    artifact_dir: Path, episode: dict, suffix: str, content: str, namespace: str = "raw"
) -> Path:
    base = artifact_dir / namespace / episode["podcast_slug"]
    base.mkdir(parents=True, exist_ok=True)
    path = base / f"{episode['episode_id']}{suffix}"
    path.write_text(content)
    return path
