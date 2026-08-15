from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from loracast.ingest import fetch, reports
from loracast.ingest.db import connect
from loracast.ingest.official_apple import find_apple_episode, fetch_apple_transcript
from loracast.ingest.official_video import parse_vtt, title_similarity
from loracast.ingest.pipeline import PodcastPipeline


def _insert_episode(conn, **values) -> None:
    columns = ", ".join(values)
    placeholders = ", ".join("?" for _ in values)
    conn.execute(
        f"INSERT INTO episodes ({columns}) VALUES ({placeholders})",
        tuple(values.values()),
    )


class PipelineTests(unittest.TestCase):
    def test_fetch_text_retries_transient_url_errors(self) -> None:
        attempts = {"count": 0}

        class FakeResponse:
            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb):
                return False

            @property
            def headers(self):
                class Headers:
                    @staticmethod
                    def get_content_charset():
                        return "utf-8"

                return Headers()

            @staticmethod
            def read():
                return b"ok"

        def fake_urlopen(request, timeout=30):
            attempts["count"] += 1
            if attempts["count"] < 3:
                raise OSError("connection reset")
            return FakeResponse()

        with patch("loracast.ingest.fetch.urlopen", side_effect=fake_urlopen):
            with patch("loracast.ingest.fetch.time.sleep"):
                value = fetch.fetch_text("https://example.com")
        self.assertEqual(value, "ok")
        self.assertEqual(attempts["count"], 3)

    def test_status_reports_transcript_ready_counts(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config = {"sources": [{"slug": "planet-money", "name": "Planet Money"}]}
            pipeline = PodcastPipeline(config=config, root_dir=Path(tmp) / "state")

            with connect(pipeline.db_path) as conn:
                _insert_episode(
                    conn,
                    episode_id="ep1",
                    podcast_slug="planet-money",
                    title="Episode 1",
                    episode_url="https://example.com/ep1",
                    pull_status="transcript_ready",
                )

            status = reports.status(pipeline)
            self.assertEqual(status["sources"][0]["statuses"]["transcript_ready"], 1)

    def test_parse_vtt_removes_timing_and_tags(self) -> None:
        vtt = """WEBVTT

00:00:00.000 --> 00:00:02.000
<c.colorE5E5E5>Hello world</c>

00:00:02.000 --> 00:00:04.000
Hello world
"""
        self.assertEqual(parse_vtt(vtt), "Hello world")

    def test_title_similarity_rewards_near_match(self) -> None:
        score = title_similarity(
            "Is the Oil Crisis About to Break Global Supply Chains?",
            "Is the Oil Crisis About to Break Global Supply Chains",
        )
        self.assertGreaterEqual(score, 0.92)

    def test_apple_episode_matching(self) -> None:
        """find_apple_episode title and date matching logic."""
        index = [
            {
                "trackId": 1001,
                "trackName": "Marriage Is the Biggest Financial Risk",
                "releaseDate": "2026-04-03T08:15:00Z",
                "trackTimeMillis": 2400000,
            },
            {
                "trackId": 1002,
                "trackName": "Why Everyone is Broke",
                "releaseDate": "2026-04-02T09:00:00Z",
                "trackTimeMillis": 2400000,
            },
        ]

        # Exact title match
        match = find_apple_episode(
            index, "Marriage Is the Biggest Financial Risk", "2026-04-03T08:15:00Z"
        )
        self.assertIsNotNone(match)
        self.assertEqual(match["trackId"], 1001)

        # Title match outside date tolerance
        match = find_apple_episode(
            index, "Marriage Is the Biggest Financial Risk", "2026-04-10T08:15:00Z"
        )
        self.assertIsNone(match)

        # No matching title
        match = find_apple_episode(
            index, "Totally Different Episode", "2026-04-03T08:15:00Z"
        )
        self.assertIsNone(match)

    def test_apple_transcript_parsing(self) -> None:
        """fetch_apple_transcript parsing of serverData and transcript JSON."""
        episode_page = """
        <html>
        <script type="application/json" id="serialized-server-data">
        {"d": [{"data": {"attributes": {"transcriptUrls": [{"url": "https://example.com/transcript.json"}]}}}]}
        </script>
        </html>
        """
        transcript_json = json.dumps(
            {
                "segments": [
                    {"startTime": 0, "text": "Hello"},
                    {"startTime": 5000, "text": "world"},
                ]
            }
        )

        def fake_urlopen(req, timeout=10):
            response = MagicMock()
            if "podcasts.apple.com" in req.full_url:
                response.read.return_value = episode_page.encode("utf-8")
            else:
                response.read.return_value = transcript_json.encode("utf-8")
            response.__enter__.return_value = response
            response.__exit__.return_value = False
            return response

        with patch(
            "loracast.ingest.official_apple.urlopen", side_effect=fake_urlopen
        ):
            text = fetch_apple_transcript("123456", "999")

        self.assertIsNotNone(text)
        self.assertIn("Hello", text)
        self.assertIn("world", text)

    def test_coverage_metrics(self) -> None:
        """contiguous_streak_from_latest is calculated correctly."""
        with tempfile.TemporaryDirectory() as tmp:
            config = {"sources": [{"slug": "test-pod", "name": "Test"}]}
            pipeline = PodcastPipeline(config=config, root_dir=Path(tmp) / "state")

            with connect(pipeline.db_path) as conn:
                for episode_id, published_at, pull_status in (
                    ("ep1", "2026-04-10T00:00:00Z", "transcript_ready"),
                    ("ep2", "2026-04-09T00:00:00Z", "transcript_ready"),
                    ("ep3", "2026-04-08T00:00:00Z", "asr_pending"),
                ):
                    _insert_episode(
                        conn,
                        episode_id=episode_id,
                        podcast_slug="test-pod",
                        title=f"Episode {episode_id}",
                        episode_url=f"https://example.com/{episode_id}",
                        published_at=published_at,
                        pull_status=pull_status,
                    )

            status = reports.status(pipeline, source_slugs=["test-pod"])
            # Streak of 2: ep1 and ep2 are ready, ep3 breaks it.
            self.assertEqual(status["sources"][0]["contiguous_streak_from_latest"], 2)

    def test_acquire_sets_asr_pending(self) -> None:
        """Episodes with audio but no official transcript become asr_pending."""
        with tempfile.TemporaryDirectory() as tmp:
            config = {
                "sources": [
                    {
                        "slug": "test-pod",
                        "name": "Test",
                        "adapter": "rss_html",
                        "feed_url": "http://example.com/feed.xml",
                        "homepage_url": "http://example.com",
                    }
                ]
            }
            pipeline = PodcastPipeline(config=config, root_dir=Path(tmp) / "state")

            with connect(pipeline.db_path) as conn:
                _insert_episode(
                    conn,
                    episode_id="ep1",
                    podcast_slug="test-pod",
                    title="Test Episode",
                    episode_url="http://example.com/ep1",
                    audio_url="http://example.com/audio/ep1.mp3",
                    pull_status="discovered",
                )

            with patch.object(
                PodcastPipeline, "fetch_text", side_effect=lambda url: "<html></html>"
            ):
                pipeline.acquire(source_slugs=["test-pod"])

            with connect(pipeline.db_path) as conn:
                episode = conn.execute(
                    "SELECT pull_status FROM episodes WHERE episode_id = ?", ("ep1",)
                ).fetchone()
            self.assertEqual(episode["pull_status"], "asr_pending")


if __name__ == "__main__":
    unittest.main()
