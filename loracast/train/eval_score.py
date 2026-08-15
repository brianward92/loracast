#!/usr/bin/env python3
"""Compute per-token NLL on a test split for base and adapter.

Writes a scalar metric (mean negative log-likelihood over the assistant
target tokens) to ``<out-dir>/score.json`` giving an objective signal for
comparing runs.

Only the final assistant turn of each example contributes tokens to the
score: everything up to (and including) the generation prompt is masked
out, matching the ``mask_prompt: true`` convention used during training.

The script imports ``mlx_lm`` lazily so unit tests can exercise the pure
helpers (``example_nll``, ``aggregate``, ``load_messages``) without MLX
installed.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Iterable


@dataclass
class ScoreAccumulator:
    sum_nll: float = 0.0
    num_tokens: int = 0
    num_examples: int = 0
    skipped: int = 0
    skip_reasons: dict[str, int] = field(default_factory=dict)

    def add(self, nll_sum: float, num_tokens: int) -> None:
        self.sum_nll += nll_sum
        self.num_tokens += num_tokens
        self.num_examples += 1

    def skip(self, reason: str) -> None:
        self.skipped += 1
        self.skip_reasons[reason] = self.skip_reasons.get(reason, 0) + 1

    def as_dict(self) -> dict:
        nll_per_token = self.sum_nll / self.num_tokens if self.num_tokens else None
        return {
            "nll_per_token": nll_per_token,
            "num_tokens": self.num_tokens,
            "num_examples": self.num_examples,
            "skipped": self.skipped,
            "skip_reasons": dict(self.skip_reasons),
        }


def load_messages(test_path: Path) -> Iterable[list[dict]]:
    with test_path.open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            row = json.loads(line)
            messages = row.get("messages")
            if messages:
                yield messages


def _tokenize_messages(tokenizer, messages: list[dict]) -> tuple[list[int], int] | None:
    """Return (full_ids, prompt_len) or None if the example is unusable.

    ``prompt_len`` is the number of tokens that should be masked out of the
    loss (the full conversation up to and including the assistant generation
    prompt). The target tokens are ``full_ids[prompt_len:]``.
    """
    if not messages or messages[-1].get("role") != "assistant":
        return None
    prompt_messages = messages[:-1]
    full_ids = tokenizer.apply_chat_template(
        messages, tokenize=True, add_generation_prompt=False
    )
    prompt_ids = tokenizer.apply_chat_template(
        prompt_messages, tokenize=True, add_generation_prompt=True
    )
    if not full_ids or not prompt_ids:
        return None
    if list(full_ids[: len(prompt_ids)]) != list(prompt_ids):
        return None
    if len(full_ids) <= len(prompt_ids):
        return None
    return list(full_ids), len(prompt_ids)


def example_nll(model, tokenizer, messages: list[dict]):
    """Return (sum_nll, num_target_tokens) for one example, or None.

    Lives in its own function so the heavy MLX forward-pass path is isolated
    and the surrounding plumbing is easy to unit-test.
    """
    import mlx.core as mx
    import mlx.nn as nn

    tokenized = _tokenize_messages(tokenizer, messages)
    if tokenized is None:
        return None
    full_ids, prompt_len = tokenized
    target_len = len(full_ids) - prompt_len

    input_ids = mx.array(full_ids)[None, :]
    logits = model(input_ids)
    # logits[:, i, :] predicts token at position i+1. Targets cover positions
    # [prompt_len, full_len); the predicting logits are at [prompt_len-1, full_len-1).
    shift_logits = logits[0, prompt_len - 1 : len(full_ids) - 1, :]
    shift_targets = mx.array(full_ids[prompt_len:])
    log_probs = nn.log_softmax(shift_logits, axis=-1)
    gathered = log_probs[mx.arange(target_len), shift_targets]
    nll_sum = -mx.sum(gathered)
    mx.eval(nll_sum)
    return float(nll_sum.item()), target_len


def aggregate(
    messages_iter: Iterable[list[dict]],
    scorer: Callable[[list[dict]], tuple[float, int] | None],
    *,
    limit: int | None = None,
) -> ScoreAccumulator:
    acc = ScoreAccumulator()
    for idx, messages in enumerate(messages_iter):
        if limit is not None and idx >= limit:
            break
        try:
            result = scorer(messages)
        except Exception as exc:  # noqa: BLE001
            acc.skip(f"error:{type(exc).__name__}")
            continue
        if result is None:
            acc.skip("unusable")
            continue
        nll_sum, num_tokens = result
        acc.add(nll_sum, num_tokens)
    return acc


def score_with_model(
    model_id: str,
    adapter_path: Path | None,
    test_path: Path,
    *,
    limit: int | None,
) -> ScoreAccumulator:
    from mlx_lm import load

    model, tokenizer = load(
        model_id,
        adapter_path=str(adapter_path) if adapter_path else None,
    )

    def scorer(messages: list[dict]):
        return example_nll(model, tokenizer, messages)

    return aggregate(load_messages(test_path), scorer, limit=limit)


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--model", required=True, help="Base model identifier.")
    p.add_argument(
        "--adapter-dir",
        required=True,
        help="Adapter directory for the candidate run.",
    )
    p.add_argument(
        "--test-file",
        required=True,
        help="Path to test.jsonl used for scoring.",
    )
    p.add_argument(
        "--out-dir",
        required=True,
        help="Directory to write score.json into (created if missing).",
    )
    p.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Optional cap on the number of test examples scored.",
    )
    p.add_argument(
        "--skip-base",
        action="store_true",
        help="Only score the adapter (debugging).",
    )
    return p.parse_args()


def main() -> None:
    args = parse_args()
    test_path = Path(args.test_file)
    if not test_path.exists():
        raise SystemExit(f"missing test file: {test_path}")
    adapter_dir = Path(args.adapter_dir)
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    print(f"[eval_score] model={args.model} test={test_path} adapter={adapter_dir}")

    base = None
    if not args.skip_base:
        print("[eval_score] scoring base model...")
        base = score_with_model(
            args.model, adapter_path=None, test_path=test_path, limit=args.limit
        ).as_dict()
        print(f"[eval_score] base nll_per_token={base['nll_per_token']}")

    print("[eval_score] scoring adapter...")
    adapter = score_with_model(
        args.model,
        adapter_path=adapter_dir,
        test_path=test_path,
        limit=args.limit,
    ).as_dict()
    print(f"[eval_score] adapter nll_per_token={adapter['nll_per_token']}")

    improvement = None
    if (
        base
        and base["nll_per_token"] is not None
        and adapter["nll_per_token"] is not None
    ):
        improvement = base["nll_per_token"] - adapter["nll_per_token"]

    score = {
        "computed_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "model": args.model,
        "adapter_dir": str(adapter_dir),
        "test_file": str(test_path),
        "limit": args.limit,
        "base": base,
        "adapter": adapter,
        "improvement_nll": improvement,
    }
    (out_dir / "score.json").write_text(
        json.dumps(score, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    print(f"[eval_score] wrote {out_dir / 'score.json'}")


if __name__ == "__main__":
    main()
