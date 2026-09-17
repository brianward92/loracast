"""LoRACast command line: ingest, extract, train, eval."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .paths import data_root


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="loracast")
    subparsers = parser.add_subparsers(dest="command", required=True)

    ingest = subparsers.add_parser(
        "ingest", help="Discover episodes and acquire transcripts."
    )
    ingest.add_argument(
        "action",
        choices=["run", "status", "manifest", "init-registry"],
        help="Ingest action.",
    )
    ingest.add_argument(
        "--registry",
        default=None,
        help=(
            "Path to the source registry TOML. Defaults to "
            "$LORACAST_DATA/registry.toml when that file exists, otherwise the "
            "built-in example registry."
        ),
    )
    ingest.add_argument("--source", action="append", default=None)
    ingest.add_argument("--limit-per-source", type=int, default=None)
    ingest.add_argument("--episode-limit", type=int, default=None)
    ingest.add_argument("--skip-asr", action="store_true")
    ingest.add_argument(
        "--force",
        action="store_true",
        help="init-registry: overwrite an existing registry file.",
    )

    extract = subparsers.add_parser(
        "extract", help="Extract training Q&A pairs from ready transcripts."
    )
    extract.add_argument("--backend", choices=["cli", "api"], default="cli")
    extract.add_argument("--model", default=None, help="Backend model override.")
    extract.add_argument(
        "--effort", default=None, help="Reasoning effort (cli backend only)."
    )
    extract.add_argument("--source", default=None, help="Restrict to one slug.")
    extract.add_argument("--limit", type=int, default=None)
    extract.add_argument("--parallel", type=int, default=4)
    extract.add_argument("--timeout", type=int, default=600)

    train = subparsers.add_parser(
        "train", help="Build the dataset and fine-tune a LoRA adapter (mlx-lm)."
    )
    train.add_argument("--model", default=None, help="Base model identifier.")
    train.add_argument("--config", default=None, help="LoRA config YAML override.")
    train.add_argument("--adapter-dir", default=None)
    train.add_argument("--max-seq-length", type=int, default=2048)
    train.add_argument("--seed", type=int, default=42)

    eval_ = subparsers.add_parser(
        "eval", help="Score base vs adapter NLL on the test split."
    )
    eval_.add_argument("--model", default=None, help="Base model identifier.")
    eval_.add_argument("--adapter-dir", default=None)
    eval_.add_argument("--test-file", default=None)
    eval_.add_argument("--out-dir", default=None)
    eval_.add_argument("--limit", type=int, default=None)

    return parser


def main() -> None:
    args, passthrough = build_parser().parse_known_args()

    if args.command == "train":
        from .train.run import DEFAULT_MODEL, run_train

        podcasts = data_root() / "podcasts"
        raise SystemExit(
            run_train(
                examples_dir=podcasts / "training_examples",
                data_dir=data_root() / "combined",
                adapter_dir=Path(args.adapter_dir)
                if args.adapter_dir
                else data_root() / "adapters" / "run_001",
                model=args.model or DEFAULT_MODEL,
                config=Path(args.config) if args.config else None,
                max_seq_length=args.max_seq_length,
                seed=args.seed,
                extra_args=passthrough,
            )
        )

    if args.command == "eval":
        from .train import eval_score
        from .train.run import DEFAULT_MODEL

        adapter_dir = (
            Path(args.adapter_dir)
            if args.adapter_dir
            else data_root() / "adapters" / "run_001"
        )
        argv = [
            "--model", args.model or DEFAULT_MODEL,
            "--adapter-dir", str(adapter_dir),
            "--test-file",
            args.test_file or str(data_root() / "combined" / "test.jsonl"),
            "--out-dir", args.out_dir or str(adapter_dir),
        ]
        if args.limit is not None:
            argv += ["--limit", str(args.limit)]
        sys.argv = ["loracast-eval", *argv]
        eval_score.main()
        return

    if args.command == "extract":
        from .extract.backends import get_backend
        from .extract.run import run_extract

        podcasts = data_root() / "podcasts"
        stats = run_extract(
            backend=get_backend(args.backend, args.model, args.effort),
            db_path=podcasts / "state.sqlite3",
            out_root=podcasts / "training_examples",
            source=args.source,
            limit=args.limit,
            parallel=args.parallel,
            timeout_s=args.timeout,
        )
        print(json.dumps(stats, indent=2, sort_keys=True))
        # Every dispatched episode failed, so the cause is systemic rather than
        # a bad transcript: an expired subscription, an exhausted rate limit, a
        # missing binary. Exit non-zero so a scheduler shows the run as failed.
        # A partial failure stays successful: extraction is idempotent and the
        # episodes that failed are retried on the next run.
        if stats["episodes"] and not stats["ok"]:
            raise SystemExit(
                f"extraction failed for all {stats['episodes']} dispatched "
                "episode(s); see the errors above"
            )
        return

    from .ingest.registry import (
        default_registry_text,
        load_registry,
        user_registry_path,
    )

    if args.action == "init-registry":
        target = user_registry_path()
        if target.exists() and not args.force:
            raise SystemExit(
                f"registry already exists: {target}\n"
                "Edit it, or pass --force to overwrite it with the built-in "
                "example registry."
            )
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(default_registry_text(), encoding="utf-8")
        print(f"wrote registry: {target}")
        print("Edit it to add or remove sources, then run: loracast ingest run")
        return

    from .ingest import reports
    from .ingest.pipeline import PodcastPipeline

    try:
        config = load_registry(args.registry)
    except (FileNotFoundError, ValueError) as exc:
        raise SystemExit(str(exc)) from exc

    pipeline = PodcastPipeline(config=config, root_dir=data_root() / "podcasts")

    if args.action == "run":
        stats = pipeline.run(
            limit_per_source=args.limit_per_source,
            episode_limit=args.episode_limit,
            source_slugs=args.source,
            skip_asr=args.skip_asr,
        )
    elif args.action == "status":
        stats = reports.status(pipeline, source_slugs=args.source)
    else:
        stats = reports.export_manifest(pipeline, source_slugs=args.source)

    print(json.dumps(stats, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
