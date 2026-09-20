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

    def _stats(self, episodes: int, ok: int, failed: int, empty: int = 0) -> dict:
        return {
            "episodes": episodes,
            "ok": ok,
            "failed": failed,
            "empty": empty,
            "pairs": ok * 3,
            "errors": ["x"] * failed,
        }

    def _assert_exits_non_zero(self, stats: dict, why: str) -> None:
        with self.assertRaises(SystemExit) as caught:
            _run_extract(stats)
        self.assertNotEqual(caught.exception.code, 0, why)

    def test_total_failure_exits_non_zero(self) -> None:
        self._assert_exits_non_zero(
            self._stats(2, 0, 2), "Total extraction failure must exit non-zero."
        )

    def test_single_failure_exits_non_zero(self) -> None:
        self._assert_exits_non_zero(
            self._stats(1, 0, 1), "One dispatched episode that failed is a failed run."
        )

    def test_all_empty_exits_non_zero(self) -> None:
        self._assert_exits_non_zero(
            self._stats(2, 0, 0, empty=2),
            "Two or more episodes that all produced nothing look like a backend "
            "printing an error with exit 0.",
        )

    def test_failed_plus_empty_exits_non_zero(self) -> None:
        self._assert_exits_non_zero(
            self._stats(3, 0, 2, empty=1), "No episode produced a pair."
        )

    def test_single_empty_succeeds(self) -> None:
        _run_extract(self._stats(1, 0, 0, empty=1))

    def test_empty_plus_ok_succeeds(self) -> None:
        _run_extract(self._stats(3, 1, 0, empty=2))

    def test_partial_failure_succeeds(self) -> None:
        _run_extract(self._stats(3, 2, 1))

    def test_all_succeeded(self) -> None:
        _run_extract(self._stats(3, 3, 0))

    def test_nothing_pending_succeeds(self) -> None:
        _run_extract(self._stats(0, 0, 0))


if __name__ == "__main__":
    unittest.main()
