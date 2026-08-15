"""Build the canonical train/valid/test dataset from extracted Q&A files.

Split assignment is a deterministic hash of (seed, example_id), so re-running
over a grown corpus never moves an existing example between splits.
"""

from __future__ import annotations

import hashlib
import json
from collections import Counter, defaultdict
from pathlib import Path

SPLITS = ("train", "valid", "test")


def load_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    rows: list[dict] = []
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def assign_split(key: str, seed: int, train_ratio: float, valid_ratio: float) -> str:
    digest = hashlib.sha256(f"{seed}:{key}".encode("utf-8")).hexdigest()
    bucket = int(digest[:12], 16) / float(16**12)
    if bucket < train_ratio:
        return "train"
    if bucket < train_ratio + valid_ratio:
        return "valid"
    return "test"


def build_podcast_examples(
    examples_dir: Path,
    target: dict[str, list[dict]],
    *,
    seed: int,
    train_ratio: float,
    valid_ratio: float,
) -> Counter:
    counts: Counter = Counter()
    if not examples_dir.exists():
        return counts
    for jsonl_path in sorted(examples_dir.glob("*/*.jsonl")):
        podcast_slug = jsonl_path.parent.name
        episode_id = jsonl_path.stem
        for idx, row in enumerate(load_jsonl(jsonl_path)):
            example_id = f"podcast:{podcast_slug}:{episode_id}:{idx}"
            split = assign_split(example_id, seed, train_ratio, valid_ratio)
            target[split].append(
                {
                    "messages": row["messages"],
                    "source": {
                        "kind": "podcast",
                        "example_id": example_id,
                        "podcast_slug": podcast_slug,
                        "episode_id": episode_id,
                    },
                }
            )
            counts[split] += 1
    return counts


def split_targets(
    sample_size: int, train_ratio: float, valid_ratio: float, test_ratio: float
) -> dict[str, int]:
    """Allocate ``sample_size`` examples across the three splits.

    Uses floor(ratio * N) and pushes the rounding remainder into train so the
    totals always sum to exactly ``sample_size``. Returns zero-counts when
    sampling is disabled.
    """
    if sample_size <= 0:
        return {split: 0 for split in SPLITS}
    targets = {
        "train": int(train_ratio * sample_size),
        "valid": int(valid_ratio * sample_size),
        "test": int(test_ratio * sample_size),
    }
    targets["train"] += sample_size - sum(targets.values())
    return targets


def downsample(
    data: dict[str, list[dict]],
    *,
    sample_size: int,
    train_ratio: float,
    valid_ratio: float,
    test_ratio: float,
    seed: int,
    sample_key: str | None,
) -> dict[str, list[dict]]:
    """Return a deterministic subsample of ``data`` per split targets.

    Rows within each split are ranked by ``sha256(seed, sample_key, example_id)``
    and the top-k are kept. Changing ``sample_key`` rotates the sample without
    touching the base split seed.
    """
    if sample_size <= 0:
        return data
    targets = split_targets(sample_size, train_ratio, valid_ratio, test_ratio)
    prefix = f"{seed}:{sample_key or ''}:"
    sampled: dict[str, list[dict]] = {}
    for split in SPLITS:
        ranked = sorted(
            data[split],
            key=lambda row: hashlib.sha256(
                (prefix + row["source"]["example_id"]).encode("utf-8")
            ).hexdigest(),
        )
        sampled[split] = ranked[: targets[split]]
    return sampled


def write_output(output_dir: Path, data: dict[str, list[dict]]) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    for split in SPLITS:
        rows = sorted(
            data[split],
            key=lambda row: (row["source"]["kind"], row["source"]["example_id"]),
        )
        with (output_dir / f"{split}.jsonl").open("w", encoding="utf-8") as handle:
            for row in rows:
                handle.write(json.dumps(row, ensure_ascii=True) + "\n")


def write_manifest(
    output_dir: Path,
    *,
    seed: int,
    train_ratio: float,
    valid_ratio: float,
    test_ratio: float,
    data: dict[str, list[dict]],
    sample_size: int = 0,
    sample_key: str | None = None,
) -> None:
    by_source: dict[str, dict[str, int]] = defaultdict(dict)
    totals: dict[str, int] = {}
    for split, rows in data.items():
        totals[split] = len(rows)
        for source, count in Counter(row["source"]["kind"] for row in rows).items():
            by_source[source][split] = count
    manifest = {
        "seed": seed,
        "split_policy": {
            "train_ratio": train_ratio,
            "valid_ratio": valid_ratio,
            "test_ratio": test_ratio,
            "strategy": "sha256(seed, example_id)",
        },
        "sampling": {
            "sample_size": sample_size,
            "sample_key": sample_key,
            "strategy": (
                "sha256(seed, sample_key, example_id)" if sample_size > 0 else None
            ),
        },
        "counts": {"by_split": totals, "by_source": by_source},
    }
    with (output_dir / "build_manifest.json").open("w", encoding="utf-8") as handle:
        json.dump(manifest, handle, indent=2, sort_keys=True)
        handle.write("\n")


def build(
    examples_dir: Path,
    output_dir: Path,
    *,
    seed: int = 42,
    train_ratio: float = 0.7,
    valid_ratio: float = 0.2,
    test_ratio: float = 0.1,
    sample_size: int = 0,
    sample_key: str | None = None,
) -> dict[str, int]:
    if round(train_ratio + valid_ratio + test_ratio, 6) != 1.0:
        raise ValueError("split ratios must sum to 1.0")
    data: dict[str, list[dict]] = {split: [] for split in SPLITS}
    build_podcast_examples(
        examples_dir,
        data,
        seed=seed,
        train_ratio=train_ratio,
        valid_ratio=valid_ratio,
    )
    if sample_size > 0:
        data = downsample(
            data,
            sample_size=sample_size,
            train_ratio=train_ratio,
            valid_ratio=valid_ratio,
            test_ratio=test_ratio,
            seed=seed,
            sample_key=sample_key,
        )
    write_output(output_dir, data)
    write_manifest(
        output_dir,
        seed=seed,
        train_ratio=train_ratio,
        valid_ratio=valid_ratio,
        test_ratio=test_ratio,
        data=data,
        sample_size=sample_size,
        sample_key=sample_key,
    )
    return {split: len(rows) for split, rows in data.items()}
