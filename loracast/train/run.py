"""Fine-tune a LoRA adapter with mlx-lm on Apple Silicon.

Builds the dataset from extracted Q&A files, filters over-length examples
with the model's tokenizer, then shells out to ``python -m mlx_lm lora``.
"""

from __future__ import annotations

import subprocess
import sys
from importlib import resources
from pathlib import Path

from .build_dataset import build
from .filter_long_seqs import filter_long_seqs

DEFAULT_MODEL = "mlx-community/gemma-4-26b-a4b-it-4bit"


def default_config() -> Path:
    return Path(str(resources.files("loracast.train") / "lora_default.yaml"))


def run_train(
    examples_dir: Path,
    data_dir: Path,
    adapter_dir: Path,
    *,
    model: str = DEFAULT_MODEL,
    config: Path | None = None,
    max_seq_length: int = 2048,
    seed: int = 42,
    extra_args: list[str] | None = None,
) -> int:
    counts = build(examples_dir, data_dir, seed=seed)
    print(f"dataset: {counts}", file=sys.stderr)
    if not counts["train"]:
        print("no training examples; run `loracast extract` first", file=sys.stderr)
        return 1

    filtered_dir = data_dir.parent / f"{data_dir.name}_filtered"
    stats = filter_long_seqs(model, data_dir, filtered_dir, max_seq_length)
    print(f"filtered: {stats}", file=sys.stderr)

    cmd = [
        sys.executable,
        "-m",
        "mlx_lm",
        "lora",
        "--model",
        model,
        "--train",
        "--data",
        str(filtered_dir),
        "--config",
        str(config or default_config()),
        "--adapter-path",
        str(adapter_dir),
        *(extra_args or []),
    ]
    print(f"exec: {' '.join(cmd)}", file=sys.stderr)
    return subprocess.run(cmd).returncode
