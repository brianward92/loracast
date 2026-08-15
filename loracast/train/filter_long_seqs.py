"""Drop dataset examples exceeding the max sequence length in tokens,
measured with the model's own tokenizer."""

from __future__ import annotations

import json
from pathlib import Path


def filter_long_seqs(
    model: str, input_dir: Path, output_dir: Path, max_seq_length: int = 2048
) -> dict[str, dict[str, int]]:
    from transformers import AutoTokenizer

    tokenizer = AutoTokenizer.from_pretrained(model)
    output_dir.mkdir(parents=True, exist_ok=True)
    stats: dict[str, dict[str, int]] = {}
    for split in ("train", "valid", "test"):
        src = input_dir / f"{split}.jsonl"
        if not src.exists():
            continue
        kept, dropped = [], 0
        with src.open() as handle:
            for line in handle:
                obj = json.loads(line)
                text = " ".join(m["content"] for m in obj.get("messages", []))
                if len(tokenizer.encode(text)) <= max_seq_length:
                    kept.append(line)
                else:
                    dropped += 1
        (output_dir / f"{split}.jsonl").write_text("".join(kept))
        stats[split] = {"kept": len(kept), "dropped": dropped}
    return stats
