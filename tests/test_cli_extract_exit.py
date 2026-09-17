from __future__ import annotations

import unittest
from unittest.mock import patch

from loracast import cli


def _run_extract(stats: dict):
    """Invoke `loracast extract` with run_extract stubbed to return stats."""
    with patch("loracast.extract.run.run_extract", return_value=stats), patch(
        "loracast.extract.backends.get_backend", return_value=object()
    ), patch("sys.argv", ["loracast", "extract"]):
        cli.main()


class ExtractExitCodeTest(unittest.TestCase):
    """A run in which every dispatched episode failed must exit non-zero.

    The failure mode this guards against is systemic: an expired subscription,
    an exhausted rate limit, or a missing binary makes every worker fail while
    the command still reports success, so a scheduler shows the run as green.
    """

    def test_total_failure_exits_non_zero(self) -> None:
        stats = {"episodes": 2, "ok": 0, "failed": 2, "pairs": 0, "errors": ["x"]}
        with self.assertRaises(SystemExit) as caught:
            _run_extract(stats)
        self.assertNotEqual(
            caught.exception.code, 0, "Total extraction failure must exit non-zero."
        )

    def test_partial_failure_succeeds(self) -> None:
        stats = {"episodes": 3, "ok": 2, "failed": 1, "pairs": 5, "errors": ["x"]}
        _run_extract(stats)

    def test_all_succeeded(self) -> None:
        stats = {"episodes": 3, "ok": 3, "failed": 0, "pairs": 9, "errors": []}
        _run_extract(stats)

    def test_nothing_pending_succeeds(self) -> None:
        stats = {"episodes": 0, "ok": 0, "failed": 0, "pairs": 0, "errors": []}
        _run_extract(stats)


if __name__ == "__main__":
    unittest.main()
