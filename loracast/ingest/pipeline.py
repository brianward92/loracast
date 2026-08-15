from __future__ import annotations

import hashlib
import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterator
from urllib.parse import urlparse
import fcntl

from . import fetch, strategies
from .adapters import get_adapter
from .db import connect, connect_readonly
from .fetch import (
    PER_EPISODE_ACQUIRE_SECONDS,
    PER_EPISODE_ASR_SECONDS,
    StageTimeout,
    log,
    time_limit,
    utcnow,
)
from .store import record_attempt, store_canonical_transcript, upsert_episode

# How far back to keep re-checking for an official transcript to replace a
# machine (ASR) one. Beyond this an official transcript is unlikely to ever
# appear, so stop spending network on it.
UPGRADE_MAX_AGE_DAYS = 21


class PodcastPipeline:
    STALE_ACQUIRING_MINUTES = 30
    WRITE_LOCK_NAME = ".writer.lock"

    def __init__(self, config: dict, root_dir: Path) -> None:
        self.config = config
        self.root_dir = root_dir
        self.audio_dir = root_dir / "audio"
        self.transcript_dir = root_dir / "transcripts"
        self.transcript_meta_dir = root_dir / "transcript_metadata"
        self.artifact_dir = root_dir / "artifacts"
        self.db_path = root_dir / "state.sqlite3"
        self.youtube_entries_cache: dict[str, list[dict]] = {}
        self.apple_episode_index_cache: dict[str, list[dict]] = {}
        self.asr_model_cache: dict[str, object] = {}

    # -- stages ----------------------------------------------------------

    def discover(
        self, limit_per_source: int | None = None, source_slugs: list[str] | None = None
    ) -> dict:
        stats = {"sources": {}, "discovered": 0, "errors": []}
        allowed_sources = set(source_slugs or [])
        with self._writer_lock("discover"), self._connection() as conn:
            for source in self.config["sources"]:
                if allowed_sources and source["slug"] not in allowed_sources:
                    continue
                try:
                    log(f"discover start source={source['slug']}")
                    adapter = get_adapter(source["adapter"])
                    feed_xml = self.fetch_text(source["feed_url"])
                    seeds = adapter.discover(source, feed_xml)
                    if limit_per_source is not None:
                        seeds = seeds[:limit_per_source]
                    count = 0
                    for seed in seeds:
                        count += upsert_episode(conn, seed)
                    stats["sources"][source["slug"]] = {
                        "episodes_seen": len(seeds),
                        "episodes_added": count,
                    }
                    stats["discovered"] += count
                    conn.commit()
                    log(
                        f"discover done source={source['slug']} "
                        f"episodes_seen={len(seeds)} episodes_added={count}"
                    )
                except Exception as exc:  # noqa: BLE001
                    stats["sources"][source["slug"]] = {
                        "episodes_seen": 0,
                        "episodes_added": 0,
                        "error": str(exc),
                    }
                    stats["errors"].append(
                        {
                            "source": source["slug"],
                            "feed_url": source["feed_url"],
                            "error": str(exc),
                        }
                    )
                    log(f"discover error source={source['slug']} error={exc}")
        return stats

    def acquire(
        self,
        episode_limit: int | None = None,
        source_slugs: list[str] | None = None,
        retry_no_transcript: bool = False,
        retry_errors: bool = True,
        force_refresh: bool = False,
    ) -> dict:
        stats = {
            "transcript_ready": 0,
            "asr_pending": 0,
            "no_transcript_found": 0,
            "errors": [],
        }
        with self._writer_lock("acquire"), self._connection() as conn:
            self.requeue_stale_acquiring(conn)
            episodes = self._select_episodes(
                conn=conn,
                statuses=self._acquire_statuses(
                    retry_no_transcript, retry_errors, force_refresh
                ),
                source_slugs=source_slugs,
            )
            if episode_limit is not None:
                episodes = episodes[:episode_limit]
            total = len(episodes)
            log(
                f"acquire start episodes={total} retry_no_transcript={retry_no_transcript} "
                f"retry_errors={retry_errors} force_refresh={force_refresh}"
            )
            for index, row in enumerate(episodes, start=1):
                episode = dict(row)
                try:
                    with time_limit(PER_EPISODE_ACQUIRE_SECONDS):
                        outcome = self._acquire_episode(
                            conn, episode, force_refresh=force_refresh
                        )
                    conn.commit()
                    stats[outcome] += 1
                    log(
                        f"acquire progress index={index}/{total} "
                        f"source={episode['podcast_slug']} outcome={outcome}"
                    )
                except StageTimeout:
                    # Hung strategy past the per-episode budget. Queue for ASR
                    # when audio exists (the normal official->asr fallback) so a
                    # transcript-less episode cannot wedge the run; else error.
                    if episode.get("audio_url"):
                        self._set_status(
                            conn,
                            episode["episode_id"],
                            "asr_pending",
                            f"acquire exceeded {PER_EPISODE_ACQUIRE_SECONDS}s; queued for asr",
                        )
                        stats["asr_pending"] += 1
                    else:
                        self._set_status(
                            conn,
                            episode["episode_id"],
                            "acquire_error",
                            f"acquire exceeded {PER_EPISODE_ACQUIRE_SECONDS}s; no audio for asr",
                        )
                        stats["errors"].append(
                            {
                                "episode_id": episode["episode_id"],
                                "error": "acquire timeout",
                            }
                        )
                    log(
                        f"acquire timeout index={index}/{total} "
                        f"source={episode['podcast_slug']} episode_id={episode['episode_id']}"
                    )
                except Exception as exc:  # noqa: BLE001
                    self._set_status(
                        conn, episode["episode_id"], "acquire_error", str(exc)
                    )
                    stats["errors"].append(
                        {"episode_id": episode["episode_id"], "error": str(exc)}
                    )
                    record_attempt(
                        conn=conn,
                        episode_id=episode["episode_id"],
                        attempt_type="pipeline",
                        source_url=episode["episode_url"],
                        outcome="error",
                        error=str(exc),
                        note="uncaught acquire exception",
                    )
                    conn.commit()
                    log(
                        f"acquire error index={index}/{total} "
                        f"source={episode['podcast_slug']} "
                        f"episode_id={episode['episode_id']} error={exc}"
                    )
        return stats

    def run_asr(
        self,
        episode_limit: int | None = None,
        source_slugs: list[str] | None = None,
        retry_errors: bool = True,
        force_refresh: bool = False,
    ) -> dict:
        stats = {"transcript_ready": 0, "asr_error": 0, "errors": []}
        with self._writer_lock("asr"), self._connection() as conn:
            self.requeue_stale_acquiring(conn)
            statuses = ["asr_pending"]
            if retry_errors:
                statuses.append("asr_error")
            if force_refresh:
                statuses.append("transcript_ready")
            episodes = self._select_episodes(
                conn=conn,
                statuses=statuses,
                source_slugs=source_slugs,
                require_audio=True,
            )
            if episode_limit is not None:
                episodes = episodes[:episode_limit]
            total = len(episodes)
            log(
                f"asr start episodes={total} retry_errors={retry_errors} "
                f"force_refresh={force_refresh}"
            )
            for index, row in enumerate(episodes, start=1):
                episode = dict(row)
                try:
                    with time_limit(PER_EPISODE_ASR_SECONDS):
                        outcome = self._run_asr_episode(
                            conn, episode, force_refresh=force_refresh
                        )
                    conn.commit()
                    stats[outcome] += 1
                    log(
                        f"asr progress index={index}/{total} "
                        f"source={episode['podcast_slug']} outcome={outcome}"
                    )
                except StageTimeout:
                    self._set_status(
                        conn,
                        episode["episode_id"],
                        "asr_error",
                        f"asr exceeded {PER_EPISODE_ASR_SECONDS}s",
                    )
                    stats["asr_error"] += 1
                    log(
                        f"asr timeout index={index}/{total} "
                        f"source={episode['podcast_slug']} episode_id={episode['episode_id']}"
                    )
                except Exception as exc:  # noqa: BLE001
                    self._set_status(conn, episode["episode_id"], "asr_error", str(exc))
                    stats["errors"].append(
                        {"episode_id": episode["episode_id"], "error": str(exc)}
                    )
                    record_attempt(
                        conn=conn,
                        episode_id=episode["episode_id"],
                        attempt_type="asr_pipeline",
                        source_url=episode.get("audio_url"),
                        outcome="error",
                        error=str(exc),
                        note="uncaught asr exception",
                    )
                    conn.commit()
            return stats

    def run(
        self,
        limit_per_source: int | None = None,
        episode_limit: int | None = None,
        source_slugs: list[str] | None = None,
        skip_asr: bool = False,
        upgrade_machine: bool = True,
    ) -> dict:
        """Full pipeline: discover → acquire → asr. Suitable for scheduled jobs."""
        stats: dict = {
            "discover": self.discover(
                limit_per_source=limit_per_source, source_slugs=source_slugs
            ),
            "acquire": self.acquire(
                episode_limit=episode_limit, source_slugs=source_slugs
            ),
        }
        if skip_asr:
            stats["asr"] = {"skipped": True}
        else:
            stats["asr"] = self.run_asr(
                episode_limit=episode_limit, source_slugs=source_slugs
            )
        # Prefer an official transcript once it lands: re-attempt the official
        # strategies for recent episodes still served by a machine (ASR)
        # transcript and repoint the canonical to the official one. The ASR
        # transcript/artifact and its attempt rows are retained -- only the
        # *preferred* transcript changes, and ASR is never re-run here.
        if upgrade_machine:
            stats["upgrade"] = self.upgrade_machine_transcripts(
                source_slugs=source_slugs
            )
        else:
            stats["upgrade"] = {"skipped": True}
        return stats

    def upgrade_machine_transcripts(
        self,
        max_age_days: int | None = None,
        episode_limit: int | None = None,
        source_slugs: list[str] | None = None,
    ) -> dict:
        """Repoint recent machine (ASR) transcripts to an official one once it
        has been published, while preserving the ASR work.

        Selects ``transcript_ready`` episodes whose canonical transcript is
        machine-generated and were published within ``max_age_days``, re-runs
        the official strategies only (never ASR), and upgrades the canonical
        when a genuine (non-machine) official transcript is found.
        """
        max_age_days = max_age_days or UPGRADE_MAX_AGE_DAYS
        stats: dict = {"upgraded": 0, "still_machine": 0, "errors": []}
        cutoff = (datetime.now(timezone.utc) - timedelta(days=max_age_days)).isoformat()
        with self._writer_lock("upgrade"), self._connection() as conn:
            where = [
                "e.pull_status = 'transcript_ready'",
                "t.is_machine_generated = 1",
                "e.published_at >= ?",
            ]
            params: list = [cutoff]
            if source_slugs:
                where.append(
                    "e.podcast_slug IN (%s)" % ", ".join("?" for _ in source_slugs)
                )
                params.extend(source_slugs)
            rows = conn.execute(
                f"""
                SELECT e.*
                FROM episodes e
                JOIN episode_transcripts t ON t.episode_id = e.episode_id
                WHERE {' AND '.join(where)}
                ORDER BY e.published_at DESC
                """,
                params,
            ).fetchall()
            if episode_limit is not None:
                rows = rows[:episode_limit]
            log(f"upgrade start episodes={len(rows)} max_age_days={max_age_days}")
            for row in rows:
                episode = dict(row)
                source = self.get_source(episode["podcast_slug"])
                try:
                    with time_limit(PER_EPISODE_ACQUIRE_SECONDS):
                        upgraded = self._try_official_upgrade(conn, source, episode)
                    conn.commit()
                    if upgraded:
                        stats["upgraded"] += 1
                        log(
                            f"upgrade success source={episode['podcast_slug']} "
                            f"episode_id={episode['episode_id']} machine->official"
                        )
                    else:
                        stats["still_machine"] += 1
                except StageTimeout:
                    conn.rollback()
                    stats["still_machine"] += 1
                    log(
                        f"upgrade timeout source={episode['podcast_slug']} "
                        f"episode_id={episode['episode_id']} kept_asr"
                    )
                except Exception as exc:  # noqa: BLE001
                    conn.rollback()
                    stats["errors"].append(
                        {"episode_id": episode["episode_id"], "error": str(exc)}
                    )
                    log(
                        f"upgrade error source={episode['podcast_slug']} "
                        f"episode_id={episode['episode_id']} error={exc}; kept_asr"
                    )
        return stats

    # -- per-episode logic ----------------------------------------------

    def _acquire_episode(
        self, conn: sqlite3.Connection, episode: dict, force_refresh: bool = False
    ) -> str:
        source = self.get_source(episode["podcast_slug"])
        conn.execute(
            "UPDATE episodes SET pull_status = 'acquiring', last_attempted_at = ? WHERE episode_id = ?",
            (utcnow(), episode["episode_id"]),
        )
        conn.commit()

        if self._existing_transcript_ok(conn, episode, force_refresh):
            return "transcript_ready"

        for strategy in strategies.official_strategy_order(source):
            result = strategies.run_strategy(
                self, conn=conn, source=source, episode=episode, strategy=strategy
            )
            if result is None:
                continue
            transcript_text = result["content"].strip()
            if len(transcript_text.split()) < 20:
                record_attempt(
                    conn=conn,
                    episode_id=episode["episode_id"],
                    attempt_type=strategy,
                    source_url=result.get("source_url"),
                    outcome="rejected",
                    note="transcript too short",
                    artifact_path=result.get("artifact_path"),
                    audio_path=result.get("audio_path"),
                )
                continue
            self._store_result(conn, episode, strategy, result, transcript_text)
            return "transcript_ready"

        if episode.get("audio_url"):
            self._set_status(
                conn, episode["episode_id"], "asr_pending", "queued for asr"
            )
            return "asr_pending"

        self._set_status(
            conn,
            episode["episode_id"],
            "no_transcript_found",
            "no acquisition strategy succeeded and no audio was available for asr",
        )
        return "no_transcript_found"

    def _run_asr_episode(
        self, conn: sqlite3.Connection, episode: dict, force_refresh: bool = False
    ) -> str:
        conn.execute(
            "UPDATE episodes SET pull_status = 'asr_in_progress', last_attempted_at = ? WHERE episode_id = ?",
            (utcnow(), episode["episode_id"]),
        )
        conn.commit()

        if self._existing_transcript_ok(conn, episode, force_refresh):
            return "transcript_ready"

        result = strategies.try_asr(
            self, conn, self.get_source(episode["podcast_slug"]), episode
        )
        if result is None:
            refreshed = conn.execute(
                """
                SELECT note
                FROM transcript_attempts
                WHERE episode_id = ? AND attempt_type = 'asr'
                ORDER BY id DESC
                LIMIT 1
                """,
                (episode["episode_id"],),
            ).fetchone()
            self._set_status(
                conn,
                episode["episode_id"],
                "asr_error",
                refreshed["note"] if refreshed else "asr failed",
            )
            return "asr_error"

        transcript_text = result["content"].strip()
        if len(transcript_text.split()) < 20:
            record_attempt(
                conn=conn,
                episode_id=episode["episode_id"],
                attempt_type="asr",
                source_url=result.get("source_url"),
                outcome="rejected",
                note="transcript too short",
                artifact_path=result.get("artifact_path"),
                audio_path=result.get("audio_path"),
                is_machine_generated=True,
            )
            self._set_status(
                conn, episode["episode_id"], "asr_error", "asr transcript too short"
            )
            return "asr_error"

        self._store_result(conn, episode, "asr", result, transcript_text)
        log(
            f"asr success source={episode['podcast_slug']} "
            f"episode_id={episode['episode_id']} source_type={result['source_type']}"
        )
        return "transcript_ready"

    def _try_official_upgrade(
        self, conn: sqlite3.Connection, source: dict, episode: dict
    ) -> bool:
        """Run official strategies only; repoint the canonical to the first
        genuine (non-machine) official transcript found. Returns True on
        upgrade, False otherwise (the ASR canonical is left intact)."""
        for strategy in strategies.official_strategy_order(source):
            result = strategies.run_strategy(
                self, conn=conn, source=source, episode=episode, strategy=strategy
            )
            if result is None:
                continue
            transcript_text = result["content"].strip()
            if len(transcript_text.split()) < 20:
                record_attempt(
                    conn=conn,
                    episode_id=episode["episode_id"],
                    attempt_type=strategy,
                    source_url=result.get("source_url"),
                    outcome="rejected",
                    note="upgrade: official transcript too short",
                )
                continue
            if result.get("is_machine_generated", False):
                # Only a machine transcript is available (e.g. Apple / YouTube
                # auto-captions); not a genuine upgrade over our ASR. Keep ASR
                # and avoid lateral machine->machine churn on every run.
                record_attempt(
                    conn=conn,
                    episode_id=episode["episode_id"],
                    attempt_type=strategy,
                    source_url=result.get("source_url"),
                    outcome="no_result",
                    note="upgrade: only a machine transcript available; kept ASR",
                )
                continue
            store_canonical_transcript(
                conn=conn,
                transcript_dir=self.transcript_dir,
                transcript_meta_dir=self.transcript_meta_dir,
                episode=episode,
                transcript_text=transcript_text,
                source_type=result["source_type"],
                source_url=result.get("source_url"),
                resolution_note=result.get("resolution_note", strategy),
                artifact_path=result.get("artifact_path"),
                audio_path=episode.get("audio_path"),
                is_machine_generated=False,
            )
            record_attempt(
                conn=conn,
                episode_id=episode["episode_id"],
                attempt_type=strategy,
                source_url=result.get("source_url"),
                outcome="upgraded",
                note="upgraded machine->official; asr artifact retained",
                artifact_path=result.get("artifact_path"),
                transcript_path=result.get("transcript_path"),
                content_hash=hashlib.sha1(transcript_text.encode("utf-8")).hexdigest(),
                is_machine_generated=False,
            )
            return True
        return False

    def _store_result(
        self,
        conn: sqlite3.Connection,
        episode: dict,
        strategy: str,
        result: dict,
        transcript_text: str,
    ) -> None:
        store_canonical_transcript(
            conn=conn,
            transcript_dir=self.transcript_dir,
            transcript_meta_dir=self.transcript_meta_dir,
            episode=episode,
            transcript_text=transcript_text,
            source_type=result["source_type"],
            source_url=result.get("source_url"),
            resolution_note=result.get("resolution_note", strategy),
            artifact_path=result.get("artifact_path"),
            audio_path=result.get("audio_path"),
            is_machine_generated=result.get("is_machine_generated", False),
        )
        record_attempt(
            conn=conn,
            episode_id=episode["episode_id"],
            attempt_type=strategy,
            source_url=result.get("source_url"),
            outcome="success",
            note=result.get("resolution_note"),
            artifact_path=result.get("artifact_path"),
            transcript_path=result.get("transcript_path"),
            audio_path=result.get("audio_path"),
            content_hash=hashlib.sha1(transcript_text.encode("utf-8")).hexdigest(),
            is_machine_generated=result.get("is_machine_generated", False),
        )

    def _existing_transcript_ok(
        self, conn: sqlite3.Connection, episode: dict, force_refresh: bool
    ) -> bool:
        if (
            episode.get("transcript_path")
            and not force_refresh
            and Path(episode["transcript_path"]).exists()
        ):
            conn.execute(
                "UPDATE episodes SET pull_status = 'transcript_ready', last_attempted_at = ? WHERE episode_id = ?",
                (utcnow(), episode["episode_id"]),
            )
            return True
        return False

    def ensure_audio_downloaded(
        self, conn: sqlite3.Connection, episode: dict
    ) -> str | None:
        audio_url = episode.get("audio_url")
        if not audio_url:
            return None
        existing_path = episode.get("audio_path")
        if existing_path and Path(existing_path).exists():
            return existing_path

        suffix = Path(urlparse(audio_url).path).suffix or ".mp3"
        audio_path = (
            self.audio_dir / episode["podcast_slug"] / f"{episode['episode_id']}{suffix}"
        )
        audio_path.parent.mkdir(parents=True, exist_ok=True)
        if not audio_path.exists():
            log(
                f"audio download start source={episode['podcast_slug']} "
                f"episode_id={episode['episode_id']} url={audio_url}"
            )
            audio_path.write_bytes(self.fetch_bytes(audio_url))
        conn.execute(
            "UPDATE episodes SET audio_path = ? WHERE episode_id = ?",
            (str(audio_path), episode["episode_id"]),
        )
        record_attempt(
            conn=conn,
            episode_id=episode["episode_id"],
            attempt_type="audio_archive",
            source_url=audio_url,
            outcome="success",
            note="audio archived",
            audio_path=str(audio_path),
        )
        return str(audio_path)

    # -- selection & bookkeeping ----------------------------------------

    @staticmethod
    def _acquire_statuses(
        retry_no_transcript: bool, retry_errors: bool, force_refresh: bool
    ) -> list[str]:
        statuses = ["discovered"]
        if retry_errors:
            statuses.append("acquire_error")
        if retry_no_transcript:
            statuses.append("no_transcript_found")
        if force_refresh:
            statuses.append("transcript_ready")
        return statuses

    @staticmethod
    def _select_episodes(
        conn: sqlite3.Connection,
        statuses: list[str],
        source_slugs: list[str] | None,
        require_audio: bool = False,
    ) -> list[sqlite3.Row]:
        placeholders = ", ".join("?" for _ in statuses)
        where_parts = [f"pull_status IN ({placeholders})"]
        params: list[str] = list(statuses)
        if require_audio:
            where_parts.append("audio_url IS NOT NULL")
        if source_slugs:
            source_placeholders = ", ".join("?" for _ in source_slugs)
            where_parts.append(f"podcast_slug IN ({source_placeholders})")
            params.extend(source_slugs)
        query = f"""
            SELECT *
            FROM episodes
            WHERE {' AND '.join(where_parts)}
            ORDER BY published_at IS NULL, published_at DESC, episode_id
        """
        return conn.execute(query, params).fetchall()

    @staticmethod
    def _set_status(
        conn: sqlite3.Connection, episode_id: str, status: str, reason: str
    ) -> None:
        conn.execute(
            """
            UPDATE episodes
            SET pull_status = ?, skip_reason = ?, last_attempted_at = ?
            WHERE episode_id = ?
            """,
            (status, reason, utcnow(), episode_id),
        )
        conn.commit()

    def requeue_stale_acquiring(self, conn: sqlite3.Connection) -> int:
        cutoff = (
            datetime.now(timezone.utc) - timedelta(minutes=self.STALE_ACQUIRING_MINUTES)
        ).isoformat()
        result = conn.execute(
            """
            UPDATE episodes
            SET pull_status = CASE
                    WHEN pull_status = 'asr_in_progress' THEN 'asr_error'
                    ELSE 'acquire_error'
                END,
                skip_reason = CASE
                    WHEN pull_status = 'asr_in_progress' THEN 'stale asr state requeued'
                    ELSE 'stale acquiring state requeued'
                END,
                last_attempted_at = ?
            WHERE pull_status IN ('acquiring', 'asr_in_progress')
              AND COALESCE(last_attempted_at, '') < ?
            """,
            (utcnow(), cutoff),
        )
        if result.rowcount:
            conn.commit()
        return result.rowcount

    def get_source(self, slug: str) -> dict:
        for source in self.config["sources"]:
            if source["slug"] == slug:
                return source
        raise ValueError(f"unknown source slug: {slug}")

    def check_policy(self, source: dict, url: str, text: str) -> None:
        domain = urlparse(url).netloc
        allowed = source.get("allowed_domains", [])
        if allowed and domain not in allowed:
            raise ValueError(f"domain not allowed by source policy: {domain}")
        lowered = text.lower()
        for term in source.get("forbidden_terms", []):
            if term.lower() in lowered:
                raise ValueError(
                    f"source policy violation: found forbidden term '{term}'"
                )

    # -- plumbing --------------------------------------------------------

    @staticmethod
    def fetch_text(url: str) -> str:
        return fetch.fetch_text(url)

    @staticmethod
    def fetch_bytes(url: str) -> bytes:
        return fetch.fetch_bytes(url)

    def _connection(self, readonly: bool = False) -> sqlite3.Connection:
        if readonly:
            if not self.db_path.exists():
                return connect(self.db_path)
            return connect_readonly(self.db_path)
        return connect(self.db_path)

    @contextmanager
    def _writer_lock(self, operation: str) -> Iterator[None]:
        self.root_dir.mkdir(parents=True, exist_ok=True)
        lock_path = self.root_dir / self.WRITE_LOCK_NAME
        with lock_path.open("a+") as handle:
            try:
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as exc:
                raise RuntimeError(
                    f"another loracast ingest writer is active; wait for it to "
                    f"finish before running {operation}"
                ) from exc
            handle.seek(0)
            handle.truncate()
            handle.write(f"{operation}\n{os.getpid()}\n{utcnow()}\n")
            handle.flush()
            try:
                yield
            finally:
                handle.seek(0)
                handle.truncate()
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
