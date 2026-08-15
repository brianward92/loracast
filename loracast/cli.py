"""LoRACast command line: ingest, extract, train, eval."""

from __future__ import annotations

import argparse
import json

from .paths import data_root


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="loracast")
    subparsers = parser.add_subparsers(dest="command", required=True)

    ingest = subparsers.add_parser(
        "ingest", help="Discover episodes and acquire transcripts."
    )
    ingest.add_argument(
        "action", choices=["run", "status", "manifest"], help="Ingest action."
    )
    ingest.add_argument(
        "--registry",
        default="configs/registry.toml",
        help="Path to the source registry TOML.",
    )
    ingest.add_argument("--source", action="append", default=None)
    ingest.add_argument("--limit-per-source", type=int, default=None)
    ingest.add_argument("--episode-limit", type=int, default=None)
    ingest.add_argument("--skip-asr", action="store_true")

    extract = subparsers.add_parser(
        "extract", help="Extract training Q&A pairs from ready transcripts."
    )
    extract.add_argument("--backend", choices=["cli", "api"], default="cli")
    extract.add_argument("--model", default=None, help="Backend model override.")
    extract.add_argument("--source", default=None, help="Restrict to one slug.")
    extract.add_argument("--limit", type=int, default=None)
    extract.add_argument("--parallel", type=int, default=4)
    extract.add_argument("--timeout", type=int, default=600)

    for name, help_text in (
        ("train", "Fine-tune a LoRA adapter on extracted data."),
        ("eval", "Score an adapter on held-out prompts."),
    ):
        subparsers.add_parser(name, help=help_text, add_help=False)

    return parser


def main() -> None:
    args, _ = build_parser().parse_known_args()

    if args.command in {"train", "eval"}:
        raise SystemExit(f"loracast {args.command}: not yet available in this release")

    if args.command == "extract":
        from .extract.backends import get_backend
        from .extract.run import run_extract

        podcasts = data_root() / "podcasts"
        stats = run_extract(
            backend=get_backend(args.backend, args.model),
            db_path=podcasts / "state.sqlite3",
            out_root=podcasts / "training_examples",
            source=args.source,
            limit=args.limit,
            parallel=args.parallel,
            timeout_s=args.timeout,
        )
        print(json.dumps(stats, indent=2, sort_keys=True))
        return

    from .ingest import reports
    from .ingest.pipeline import PodcastPipeline
    from .ingest.registry import load_registry

    config = load_registry(args.registry)
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
