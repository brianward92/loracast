from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from loracast.train import build_dataset


class BuildDatasetTests(unittest.TestCase):
    def test_assign_split_is_deterministic(self) -> None:
        first = build_dataset.assign_split("podcast:abc:0", 42, 0.7, 0.2)
        second = build_dataset.assign_split("podcast:abc:0", 42, 0.7, 0.2)
        self.assertEqual(first, second)

    def test_build_podcast_examples_reads_jsonl_tree(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            examples_dir = root / "examples"
            (examples_dir / "showA").mkdir(parents=True)
            (examples_dir / "showB").mkdir(parents=True)
            (examples_dir / "showA" / "ep1.jsonl").write_text(
                json.dumps(
                    {
                        "messages": [
                            {"role": "user", "content": "Q1"},
                            {"role": "assistant", "content": "A1"},
                        ],
                    }
                )
                + "\n"
                + json.dumps(
                    {
                        "messages": [
                            {"role": "user", "content": "Q2"},
                            {"role": "assistant", "content": "A2"},
                        ],
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            (examples_dir / "showB" / "ep2.jsonl").write_text("", encoding="utf-8")

            data = {"train": [], "valid": [], "test": []}
            counts = build_dataset.build_podcast_examples(
                examples_dir,
                data,
                seed=42,
                train_ratio=0.7,
                valid_ratio=0.2,
            )
            total = sum(counts.values())
            self.assertEqual(total, 2)
            all_rows = data["train"] + data["valid"] + data["test"]
            ids = [row["source"]["example_id"] for row in all_rows]
            self.assertIn("podcast:showA:ep1:0", ids)
            self.assertIn("podcast:showA:ep1:1", ids)
            podcast_row = next(
                row
                for row in all_rows
                if row["source"]["example_id"] == "podcast:showA:ep1:0"
            )
            self.assertEqual(podcast_row["source"]["podcast_slug"], "showA")
            self.assertEqual(podcast_row["source"]["episode_id"], "ep1")


class SamplingTests(unittest.TestCase):
    def _make_data(self, n: int) -> dict:
        data = {"train": [], "valid": [], "test": []}
        for i in range(n):
            data["train"].append(
                {
                    "messages": [{"role": "user", "content": str(i)}],
                    "source": {"kind": "synthetic", "example_id": f"syn:train:{i}"},
                }
            )
        for i in range(n):
            data["valid"].append(
                {
                    "messages": [{"role": "user", "content": str(i)}],
                    "source": {"kind": "synthetic", "example_id": f"syn:valid:{i}"},
                }
            )
        for i in range(n):
            data["test"].append(
                {
                    "messages": [{"role": "user", "content": str(i)}],
                    "source": {"kind": "synthetic", "example_id": f"syn:test:{i}"},
                }
            )
        return data

    def test_split_targets_standard_ratios(self) -> None:
        t = build_dataset.split_targets(100, 0.7, 0.2, 0.1)
        self.assertEqual(t, {"train": 70, "valid": 20, "test": 10})

    def test_split_targets_sum_preserved_on_rounding(self) -> None:
        t = build_dataset.split_targets(7, 0.7, 0.2, 0.1)
        self.assertEqual(sum(t.values()), 7)

    def test_downsample_keeps_exact_counts(self) -> None:
        data = self._make_data(50)
        sampled = build_dataset.downsample(
            data,
            sample_size=10,
            train_ratio=0.7,
            valid_ratio=0.2,
            test_ratio=0.1,
            seed=42,
            sample_key="run_alpha",
        )
        self.assertEqual(len(sampled["train"]), 7)
        self.assertEqual(len(sampled["valid"]), 2)
        self.assertEqual(len(sampled["test"]), 1)

    def test_downsample_deterministic_for_same_key(self) -> None:
        data = self._make_data(50)
        a = build_dataset.downsample(
            data,
            sample_size=10,
            train_ratio=0.7,
            valid_ratio=0.2,
            test_ratio=0.1,
            seed=42,
            sample_key="run_alpha",
        )
        b = build_dataset.downsample(
            data,
            sample_size=10,
            train_ratio=0.7,
            valid_ratio=0.2,
            test_ratio=0.1,
            seed=42,
            sample_key="run_alpha",
        )
        self.assertEqual(
            [r["source"]["example_id"] for r in a["train"]],
            [r["source"]["example_id"] for r in b["train"]],
        )

    def test_downsample_differs_across_sample_keys(self) -> None:
        data = self._make_data(200)
        a = build_dataset.downsample(
            data,
            sample_size=20,
            train_ratio=0.7,
            valid_ratio=0.2,
            test_ratio=0.1,
            seed=42,
            sample_key="run_alpha",
        )
        b = build_dataset.downsample(
            data,
            sample_size=20,
            train_ratio=0.7,
            valid_ratio=0.2,
            test_ratio=0.1,
            seed=42,
            sample_key="run_beta",
        )
        self.assertNotEqual(
            [r["source"]["example_id"] for r in a["train"]],
            [r["source"]["example_id"] for r in b["train"]],
        )

    def test_downsample_no_op_when_disabled(self) -> None:
        data = self._make_data(5)
        out = build_dataset.downsample(
            data,
            sample_size=0,
            train_ratio=0.7,
            valid_ratio=0.2,
            test_ratio=0.1,
            seed=42,
            sample_key=None,
        )
        self.assertIs(out, data)


if __name__ == "__main__":
    unittest.main()
