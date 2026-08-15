"""Extract Q&A pairs from ready transcripts.

Idempotent: episodes whose output JSONL already exists are skipped, so the
LLM's non-determinism across runs never compounds — each episode is
extracted exactly once. Episodes run in parallel via a thread pool.
"""

from __future__ import annotations

import concurrent.futures as cf
import json
import sqlite3
import sys
from importlib import resources
from pathlib import Path

from .backends import ExtractorBackend

DEFAULT_TIMEOUT_SECONDS = 600
MAX_PAIRS_PER_EPISODE = 10


def load_spec() -> str:
    return (resources.files("loracast.extract") / "prompt.md").read_text()


def output_path_for(out_root: Path, slug: str, episode_id: str) -> Path:
    return out_root / slug / f"{episode_id}.jsonl"


def iter_pending(
    db_path: Path, out_root: Path, source: str | None, limit: int | None
) -> list[dict]:
    """Ready episodes without an existing output file, newest first."""
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    sql = (
        "SELECT episode_id, podcast_slug, title, published_at, transcript_path "
        "FROM episodes WHERE pull_status='transcript_ready' "
        "AND transcript_path IS NOT NULL "
    )
    params: list = []
    if source:
        sql += "AND podcast_slug = ? "
        params.append(source)
    sql += "ORDER BY published_at DESC, podcast_slug, episode_id"
    rows = list(conn.execute(sql, params))

    pending: list[dict] = []
    for row in rows:
        out_path = output_path_for(out_root, row["podcast_slug"], row["episode_id"])
        if out_path.exists():
            continue
        pending.append(
            {
                "episode_id": row["episode_id"],
                "podcast_slug": row["podcast_slug"],
                "title": row["title"],
                "published_at": row["published_at"],
                "transcript_path": row["transcript_path"],
                "output_path": str(out_path),
            }
        )
        if limit is not None and len(pending) >= limit:
            break
    return pending


def build_prompt(spec: str, episode: dict, transcript: str) -> str:
    return (
        f"{spec}\n\n---\n\n"
        f"Episode metadata:\n"
        f"  podcast_slug: {episode['podcast_slug']}\n"
        f"  episode_id:   {episode['episode_id']}\n"
        f"  title:        {episode['title']}\n"
        f"  published_at: {episode['published_at']}\n\n"
        f"Transcript:\n\n{transcript}\n"
    )


def parse_pairs(text: str) -> list[dict]:
    """Parse and validate JSONL pairs from a model response.

    Tolerates surrounding prose and code fences; keeps only lines that parse
    as a valid two-turn messages object.
    """
    pairs: list[dict] = []
    for line in text.splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        messages = obj.get("messages")
        if (
            isinstance(messages, list)
            and len(messages) == 2
            and messages[0].get("role") == "user"
            and messages[1].get("role") == "assistant"
            and isinstance(messages[0].get("content"), str)
            and isinstance(messages[1].get("content"), str)
            and messages[0]["content"].strip()
            and messages[1]["content"].strip()
        ):
            pairs.append({"messages": messages})
    return pairs[:MAX_PAIRS_PER_EPISODE]


def extract_one(
    backend: ExtractorBackend, spec: str, episode: dict, timeout_s: int
) -> tuple[str, int]:
    """Extract one episode; write its JSONL. Returns (episode_id, pair_count)."""
    transcript = Path(episode["transcript_path"]).read_text()
    response = backend.complete(build_prompt(spec, episode, transcript), timeout_s)
    pairs = parse_pairs(response)

    out_path = Path(episode["output_path"])
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", encoding="utf-8") as handle:
        for pair in pairs:
            record = dict(pair)
            record["source"] = {
                "podcast_slug": episode["podcast_slug"],
                "episode_id": episode["episode_id"],
                "model": getattr(backend, "model", None),
            }
            handle.write(json.dumps(record, ensure_ascii=True, sort_keys=True) + "\n")
    return episode["episode_id"], len(pairs)


def run_extract(
    backend: ExtractorBackend,
    db_path: Path,
    out_root: Path,
    source: str | None = None,
    limit: int | None = None,
    parallel: int = 4,
    timeout_s: int = DEFAULT_TIMEOUT_SECONDS,
) -> dict:
    spec = load_spec()
    pending = iter_pending(db_path, out_root, source, limit)
    stats = {"episodes": len(pending), "pairs": 0, "ok": 0, "failed": 0, "errors": []}
    if not pending:
        print("nothing to do", file=sys.stderr)
        return stats

    print(
        f"dispatching {len(pending)} episodes "
        f"(backend={backend.name}, parallel={parallel})",
        file=sys.stderr,
    )
    with cf.ThreadPoolExecutor(max_workers=parallel) as pool:
        futures = {
            pool.submit(extract_one, backend, spec, episode, timeout_s): episode
            for episode in pending
        }
        for future in cf.as_completed(futures):
            episode = futures[future]
            label = f"{episode['podcast_slug']}/{episode['episode_id']}"
            try:
                _, pair_count = future.result()
            except Exception as exc:  # noqa: BLE001
                stats["failed"] += 1
                stats["errors"].append({"episode": label, "error": str(exc)})
                print(f"  [FAIL] {label}: {exc}", file=sys.stderr)
                continue
            stats["ok"] += 1
            stats["pairs"] += pair_count
            print(f"  [OK  ] {label}: {pair_count} pairs", file=sys.stderr)
    print(
        f"done: ok={stats['ok']} fail={stats['failed']} pairs={stats['pairs']}",
        file=sys.stderr,
    )
    return stats
