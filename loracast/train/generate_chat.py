#!/usr/bin/env python3
"""Generate one assistant reply using the model's chat template."""

from __future__ import annotations

import argparse
from pathlib import Path

from mlx_lm import generate, load
from mlx_lm.sample_utils import make_sampler


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", required=True, help="Model identifier.")
    parser.add_argument("--prompt", required=True, help="User prompt.")
    parser.add_argument(
        "--adapter-path",
        default=None,
        help="Optional adapter directory.",
    )
    parser.add_argument(
        "--system",
        default=None,
        help="Optional system message.",
    )
    parser.add_argument(
        "--max-tokens",
        type=int,
        default=160,
        help="Maximum new tokens to generate.",
    )
    parser.add_argument(
        "--temp",
        type=float,
        default=0.0,
        help="Sampling temperature.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    adapter_path = None
    if args.adapter_path:
        adapter_path = str(Path(args.adapter_path))

    model, tokenizer = load(args.model, adapter_path=adapter_path)

    messages: list[dict[str, str]] = []
    if args.system:
        messages.append({"role": "system", "content": args.system})
    messages.append({"role": "user", "content": args.prompt})

    prompt = tokenizer.apply_chat_template(
        messages,
        tokenize=False,
        add_generation_prompt=True,
    )
    response = generate(
        model,
        tokenizer,
        prompt=prompt,
        max_tokens=args.max_tokens,
        sampler=make_sampler(temp=args.temp),
        verbose=False,
    )
    print(response)


if __name__ == "__main__":
    main()
