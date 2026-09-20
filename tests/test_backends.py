from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from loracast.extract import run
from loracast.extract.backends import ClaudeCLIBackend, get_backend
from loracast.ingest.db import connect


class FakeBackend:
    name = "fake"
    model = "fake-model"

    def __init__(self, response: str) -> None:
        self.response = response
        self.prompts: list[str] = []

    def complete(self, prompt: str, timeout_s: int) -> str:
        self.prompts.append(prompt)
        return self.response


def _pair_line(question: str = "Why X?", answer: str = "Because Y, so Z.") -> str:
    return json.dumps(
        {
            "messages": [
                {"role": "user", "content": question},
                {"role": "assistant", "content": answer},
            ]
        }
    )


class ParsePairsTests(unittest.TestCase):
    def test_keeps_valid_pairs_and_drops_noise(self) -> None:
        text = "\n".join(
            [
                "Here are the pairs:",
                "```jsonl",
                _pair_line("Why does A cause B?"),
                '{"messages": "not-a-list"}',
                "not json {",
                _pair_line("How does C work?"),
                "```",
            ]
        )
        pairs = run.parse_pairs(text)
        self.assertEqual(len(pairs), 2)
        self.assertEqual(pairs[0]["messages"][0]["content"], "Why does A cause B?")

    def test_rejects_empty_content_and_wrong_roles(self) -> None:
        bad = [
            json.dumps(
                {
                    "messages": [
                        {"role": "assistant", "content": "A"},
                        {"role": "user", "content": "Q"},
                    ]
                }
            ),
            json.dumps(
                {
                    "messages": [
                        {"role": "user", "content": "  "},
                        {"role": "assistant", "content": "A"},
                    ]
                }
            ),
        ]
        self.assertEqual(run.parse_pairs("\n".join(bad)), [])

    def test_caps_pairs_per_episode(self) -> None:
        text = "\n".join(_pair_line(f"Why {i}?") for i in range(20))
        self.assertEqual(len(run.parse_pairs(text)), run.MAX_PAIRS_PER_EPISODE)


class ExtractRunTests(unittest.TestCase):
    def _seed_episode(self, root: Path, episode_id: str = "ep1") -> Path:
        transcript = root / f"{episode_id}.txt"
        transcript.write_text("A substantive discussion of markets.\n")
        db_path = root / "state.sqlite3"
        with connect(db_path) as conn:
            conn.execute(
                """
                INSERT INTO episodes (
                    episode_id, podcast_slug, title, episode_url,
                    published_at, pull_status, transcript_path
                ) VALUES (?, 'test-pod', 'Ep', 'https://example.com/ep',
                          '2026-08-01T00:00:00+00:00', 'transcript_ready', ?)
                """,
                (episode_id, str(transcript)),
            )
        return db_path

    def test_extracts_writes_jsonl_with_source_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            db_path = self._seed_episode(root)
            out_root = root / "out"
            backend = FakeBackend(_pair_line())

            stats = run.run_extract(backend, db_path, out_root, parallel=1)
            self.assertEqual(stats["ok"], 1)
            self.assertEqual(stats["pairs"], 1)
            # Spec and transcript both reach the model.
            self.assertIn("Q&A Extraction Spec", backend.prompts[0])
            self.assertIn("substantive discussion", backend.prompts[0])

            record = json.loads(
                (out_root / "test-pod" / "ep1.jsonl").read_text().strip()
            )
            self.assertEqual(record["source"]["episode_id"], "ep1")
            self.assertEqual(record["source"]["model"], "fake-model")

    def test_idempotent_skips_existing_output(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            db_path = self._seed_episode(root)
            out_root = root / "out"
            out_path = run.output_path_for(out_root, "test-pod", "ep1")
            out_path.parent.mkdir(parents=True)
            out_path.write_text("")

            backend = FakeBackend(_pair_line())
            stats = run.run_extract(backend, db_path, out_root, parallel=1)
            self.assertEqual(stats["episodes"], 0)
            self.assertEqual(backend.prompts, [])

    def test_empty_response_writes_marker_not_jsonl(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            db_path = self._seed_episode(root)
            out_root = root / "out"
            reply = "Sorry, I cannot help with that."
            stats = run.run_extract(FakeBackend(reply), db_path, out_root, parallel=1)
            self.assertEqual((stats["ok"], stats["empty"], stats["pairs"]), (0, 1, 0))
            self.assertFalse(run.output_path_for(out_root, "test-pod", "ep1").exists())
            marker = run.empty_marker_for(out_root, "test-pod", "ep1")
            record = json.loads(marker.read_text())
            self.assertEqual(record["episode_id"], "ep1")
            self.assertEqual(record["response_head"], reply)
            self.assertEqual(record["model"], "fake-model")

    def test_marked_empty_episode_is_skipped_until_retry(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            db_path = self._seed_episode(root)
            out_root = root / "out"
            run.run_extract(FakeBackend(""), db_path, out_root, parallel=1)

            backend = FakeBackend(_pair_line())
            stats = run.run_extract(backend, db_path, out_root, parallel=1)
            self.assertEqual(stats["episodes"], 0, "marked episode must be skipped")
            self.assertEqual(backend.prompts, [])

            stats = run.run_extract(
                backend, db_path, out_root, parallel=1, retry_empty=True
            )
            self.assertEqual((stats["episodes"], stats["ok"]), (1, 1))
            self.assertTrue(run.output_path_for(out_root, "test-pod", "ep1").exists())
            self.assertFalse(
                run.empty_marker_for(out_root, "test-pod", "ep1").exists(),
                "a successful retry must remove the marker",
            )

    def test_jsonl_is_written_atomically(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            db_path = self._seed_episode(root)
            out_root = root / "out"
            run.run_extract(FakeBackend(_pair_line()), db_path, out_root, parallel=1)
            leftovers = list((out_root / "test-pod").glob("*.tmp"))
            self.assertEqual(leftovers, [])


class BackendTests(unittest.TestCase):
    def test_cli_backend_requires_claude_on_path(self) -> None:
        with patch("loracast.extract.backends.shutil.which", return_value=None):
            with self.assertRaises(RuntimeError):
                ClaudeCLIBackend()

    def test_cli_backend_invokes_claude_print_mode(self) -> None:
        with patch(
            "loracast.extract.backends.shutil.which", return_value="/usr/bin/claude"
        ):
            backend = ClaudeCLIBackend(model="sonnet")

        completed = unittest.mock.Mock(returncode=0, stdout=_pair_line(), stderr="")
        with patch(
            "loracast.extract.backends.subprocess.run", return_value=completed
        ) as run_mock:
            out = backend.complete("prompt text", timeout_s=5)
        self.assertEqual(out, _pair_line())
        cmd = run_mock.call_args.args[0]
        self.assertEqual(cmd[:3], ["/usr/bin/claude", "-p", "prompt text"])
        self.assertIn("sonnet", cmd)

    def test_get_backend_rejects_unknown(self) -> None:
        with self.assertRaises(ValueError):
            get_backend("nope")


if __name__ == "__main__":
    unittest.main()
