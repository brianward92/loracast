"""Read-only reporting over the ingest state database."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from .pipeline import PodcastPipeline


def status(pipeline: PodcastPipeline, source_slugs: list[str] | None = None) -> dict:
    allowed_sources = set(source_slugs or [])
    rows = []
    with pipeline._connection(readonly=True) as conn:
        for source in pipeline.config["sources"]:
            if allowed_sources and source["slug"] not in allowed_sources:
                continue
            status_rows = conn.execute(
                """
                SELECT pull_status, COUNT(*) AS n
                FROM episodes
                WHERE podcast_slug = ?
                GROUP BY pull_status
                ORDER BY pull_status
                """,
                (source["slug"],),
            ).fetchall()
            streak, oldest_ready = _coverage_metrics(conn, source["slug"])
            rows.append(
                {
                    "slug": source["slug"],
                    "statuses": {row["pull_status"]: row["n"] for row in status_rows},
                    "contiguous_streak_from_latest": streak,
                    "oldest_transcript_ready": oldest_ready,
                }
            )
    return {"root": str(pipeline.root_dir), "sources": rows}


def _coverage_metrics(
    conn: sqlite3.Connection, podcast_slug: str
) -> tuple[int, str | None]:
    """Return (contiguous_streak_from_latest, oldest_transcript_ready_iso).

    The streak is the count of consecutive `transcript_ready` episodes
    starting from the most recently published episode and walking backwards.
    It stops at the first non-ready episode. Episodes with a null
    `published_at` are sorted last and never start the streak.
    """
    rows = conn.execute(
        """
        SELECT pull_status
        FROM episodes
        WHERE podcast_slug = ?
        ORDER BY published_at IS NULL, published_at DESC, episode_id
        """,
        (podcast_slug,),
    ).fetchall()
    streak = 0
    for row in rows:
        if row["pull_status"] == "transcript_ready":
            streak += 1
        else:
            break
    oldest_row = conn.execute(
        """
        SELECT MIN(published_at) AS oldest
        FROM episodes
        WHERE podcast_slug = ? AND pull_status = 'transcript_ready'
        """,
        (podcast_slug,),
    ).fetchone()
    return streak, (oldest_row["oldest"] if oldest_row else None)


def export_manifest(
    pipeline: PodcastPipeline,
    output_path: Path | None = None,
    source_slugs: list[str] | None = None,
) -> dict:
    output = output_path or (pipeline.root_dir / "manifest.jsonl")
    output.parent.mkdir(parents=True, exist_ok=True)
    allowed_sources = set(source_slugs or [])
    rows_written = 0
    with pipeline._connection(readonly=True) as conn, output.open(
        "w", encoding="utf-8"
    ) as handle:
        rows = conn.execute("""
            SELECT
                e.episode_id,
                e.podcast_slug,
                e.title,
                e.published_at,
                e.episode_url,
                e.audio_url,
                e.guid,
                e.transcript_source_type,
                e.transcript_source_url,
                e.transcript_resolution_note,
                e.transcript_path,
                e.transcript_metadata_path,
                e.audio_path,
                e.content_hash,
                t.acquisition_method,
                t.is_machine_generated,
                t.language,
                t.confidence,
                t.selected_at
            FROM episodes e
            JOIN episode_transcripts t ON t.episode_id = e.episode_id
            WHERE e.pull_status = 'transcript_ready'
            ORDER BY e.podcast_slug, e.published_at, e.episode_id
            """).fetchall()
        for row in rows:
            record = dict(row)
            if allowed_sources and record["podcast_slug"] not in allowed_sources:
                continue
            handle.write(json.dumps(record, sort_keys=True) + "\n")
            rows_written += 1
    return {
        "root": str(pipeline.root_dir),
        "output_path": str(output),
        "rows_written": rows_written,
    }
