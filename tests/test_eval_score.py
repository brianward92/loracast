from __future__ import annotations

import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from loracast.train import eval_score


class FakeTokenizer:
    """Whitespace-token fake that supports apply_chat_template.

    Each message becomes ``<role>:`` then its whitespace tokens. The
    generation-prompt case appends ``assistant:`` so the assistant target
    tokens follow. Token IDs are stable integer hashes into a small dict.
    """

    def __init__(self) -> None:
        self._vocab: dict[str, int] = {}

    def _id(self, token: str) -> int:
        if token not in self._vocab:
            self._vocab[token] = len(self._vocab) + 1
        return self._vocab[token]

    def apply_chat_template(
        self,
        messages: list[dict],
        *,
        tokenize: bool,
        add_generation_prompt: bool,
    ):
        tokens: list[str] = []
        for msg in messages:
            tokens.append(f"{msg['role']}:")
            tokens.extend(msg["content"].split())
        if add_generation_prompt:
            tokens.append("assistant:")
        if not tokenize:
            return " ".join(tokens)
        return [self._id(t) for t in tokens]


class TokenizeMessagesTests(unittest.TestCase):
    def test_prompt_prefix_split(self) -> None:
        tok = FakeTokenizer()
        messages = [
            {"role": "user", "content": "hi there"},
            {"role": "assistant", "content": "hello friend"},
        ]
        full_ids, prompt_len = eval_score._tokenize_messages(tok, messages)
        self.assertEqual(len(full_ids), 6)  # user: hi there assistant: hello friend
        self.assertEqual(prompt_len, 4)  # user: hi there assistant:
        self.assertEqual(full_ids[prompt_len:], [tok._id("hello"), tok._id("friend")])

    def test_missing_assistant_turn_returns_none(self) -> None:
        tok = FakeTokenizer()
        messages = [{"role": "user", "content": "only user"}]
        self.assertIsNone(eval_score._tokenize_messages(tok, messages))

    def test_non_prefix_tokenization_returns_none(self) -> None:
        tok = FakeTokenizer()

        def tricky(messages, *, tokenize, add_generation_prompt):
            if add_generation_prompt:
                return [99, 99, 99]
            return [1, 2, 3, 4, 5]

        tok.apply_chat_template = tricky  # type: ignore[assignment]
        messages = [
            {"role": "user", "content": "x"},
            {"role": "assistant", "content": "y"},
        ]
        self.assertIsNone(eval_score._tokenize_messages(tok, messages))


class AggregateTests(unittest.TestCase):
    def test_aggregate_accumulates_and_skips(self) -> None:
        calls = iter(
            [
                (2.0, 4),  # 0.5 nll/tok
                None,  # skipped
                (1.0, 2),  # 0.5 nll/tok
            ]
        )

        def scorer(_messages):
            return next(calls)

        messages_iter = [["m0"], ["m1"], ["m2"]]
        acc = eval_score.aggregate(iter(messages_iter), scorer)
        self.assertEqual(acc.num_examples, 2)
        self.assertEqual(acc.num_tokens, 6)
        self.assertAlmostEqual(acc.sum_nll, 3.0)
        self.assertEqual(acc.skipped, 1)
        d = acc.as_dict()
        self.assertAlmostEqual(d["nll_per_token"], 0.5)

    def test_aggregate_respects_limit(self) -> None:
        def scorer(_):
            return (1.0, 1)

        messages_iter = [["a"], ["b"], ["c"], ["d"]]
        acc = eval_score.aggregate(iter(messages_iter), scorer, limit=2)
        self.assertEqual(acc.num_examples, 2)

    def test_aggregate_catches_scorer_exceptions(self) -> None:
        def scorer(_):
            raise RuntimeError("boom")

        acc = eval_score.aggregate(iter([["m"]]), scorer)
        self.assertEqual(acc.num_examples, 0)
        self.assertEqual(acc.skipped, 1)
        self.assertIn("error:RuntimeError", acc.skip_reasons)

    def test_empty_accumulator_nll_is_none(self) -> None:
        acc = eval_score.ScoreAccumulator()
        self.assertIsNone(acc.as_dict()["nll_per_token"])


class LoadMessagesTests(unittest.TestCase):
    def test_reads_jsonl_and_skips_blanks(self) -> None:
        with TemporaryDirectory() as tmp:
            p = Path(tmp) / "test.jsonl"
            p.write_text(
                json.dumps({"messages": [{"role": "user", "content": "hi"}]})
                + "\n\n"
                + json.dumps({"messages": [{"role": "assistant", "content": "yo"}]})
                + "\n"
                + json.dumps({"no_messages": True})
                + "\n",
                encoding="utf-8",
            )
            out = list(eval_score.load_messages(p))
            self.assertEqual(len(out), 2)


if __name__ == "__main__":
    unittest.main()
