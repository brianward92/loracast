from __future__ import annotations

import sqlite3
from pathlib import Path

EPISODES_SCHEMA = """
CREATE TABLE IF NOT EXISTS episodes (
    episode_id TEXT PRIMARY KEY,
    podcast_slug TEXT NOT NULL,
    title TEXT NOT NULL,
    published_at TEXT,
    episode_url TEXT NOT NULL,
    audio_url TEXT,
    guid TEXT,
    duration_seconds INTEGER,
    transcript_source_url TEXT,
    transcript_source_type TEXT,
    transcript_resolution_note TEXT,
    license_notes TEXT,
    pull_status TEXT NOT NULL DEFAULT 'discovered',
    content_hash TEXT,
    raw_path TEXT,
    fetched_at TEXT,
    skip_reason TEXT,
    transcript_path TEXT,
    transcript_metadata_path TEXT,
    audio_path TEXT,
    last_attempted_at TEXT
);

CREATE INDEX IF NOT EXISTS idx_episodes_status ON episodes (pull_status);
CREATE INDEX IF NOT EXISTS idx_episodes_podcast_slug ON episodes (podcast_slug);
"""


TRANSCRIPT_ATTEMPTS_SCHEMA = """
CREATE TABLE IF NOT EXISTS transcript_attempts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    episode_id TEXT NOT NULL,
    attempt_type TEXT NOT NULL,
    source_url TEXT,
    outcome TEXT NOT NULL,
    is_machine_generated INTEGER NOT NULL DEFAULT 0,
    error TEXT,
    note TEXT,
    artifact_path TEXT,
    transcript_path TEXT,
    audio_path TEXT,
    content_hash TEXT,
    created_at TEXT NOT NULL,
    finished_at TEXT
);

CREATE INDEX IF NOT EXISTS idx_transcript_attempts_episode_id ON transcript_attempts (episode_id);
CREATE INDEX IF NOT EXISTS idx_transcript_attempts_outcome ON transcript_attempts (outcome);
"""


EPISODE_TRANSCRIPTS_SCHEMA = """
CREATE TABLE IF NOT EXISTS episode_transcripts (
    episode_id TEXT PRIMARY KEY,
    transcript_path TEXT NOT NULL,
    transcript_source_type TEXT NOT NULL,
    transcript_source_url TEXT,
    acquisition_method TEXT NOT NULL,
    artifact_path TEXT,
    audio_path TEXT,
    is_machine_generated INTEGER NOT NULL DEFAULT 0,
    language TEXT NOT NULL DEFAULT 'en',
    confidence REAL,
    content_hash TEXT NOT NULL,
    selected_at TEXT NOT NULL
);
"""


def connect(db_path: Path) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path, timeout=60.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout=60000")
    try:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
    except sqlite3.OperationalError:
        pass
    conn.executescript(EPISODES_SCHEMA)
    conn.executescript(TRANSCRIPT_ATTEMPTS_SCHEMA)
    conn.executescript(EPISODE_TRANSCRIPTS_SCHEMA)
    return conn


def connect_readonly(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=5.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout=5000")
    return conn
