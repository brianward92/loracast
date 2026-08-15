from __future__ import annotations

import unittest

from loracast.ingest.adapters import RSSHTMLAdapter
from loracast.ingest.pipeline import PodcastPipeline


class AdapterTests(unittest.TestCase):
    def test_discover_from_rss(self) -> None:
        adapter = RSSHTMLAdapter()
        source = {"slug": "planet-money", "homepage_url": "https://example.com/show"}
        feed = """
        <rss><channel>
          <item>
            <title>Episode 1</title>
            <guid>guid-1</guid>
            <link>https://example.com/ep1</link>
            <pubDate>Tue, 24 Mar 2026 12:00:00 GMT</pubDate>
            <enclosure url="https://cdn.example.com/ep1.mp3" />
          </item>
        </channel></rss>
        """
        episodes = adapter.discover(source, feed)
        self.assertEqual(len(episodes), 1)
        self.assertEqual(episodes[0].title, "Episode 1")
        self.assertEqual(episodes[0].audio_url, "https://cdn.example.com/ep1.mp3")

    def test_resolve_transcript_link(self) -> None:
        adapter = RSSHTMLAdapter()
        source = {
            "allowed_domains": ["example.com"],
            "transcript_link_keywords": ["transcript"],
        }
        html = '<html><body><a href="/episodes/ep1-transcript">Transcript</a></body></html>'
        transcript_url, note = adapter.resolve_transcript_url(
            source, "https://example.com/ep1", html
        )
        self.assertEqual(transcript_url, "https://example.com/episodes/ep1-transcript")
        self.assertIn("matched transcript link text", note)

    def test_skips_bare_transcripts_landing_link(self) -> None:
        # The footer "Transcripts" nav link points at the generic
        # /transcripts/ landing page. Its text matches the "transcript"
        # keyword, but it is a site listing, not a per-episode transcript.
        # The real per-episode link (with an ID segment) must win.
        adapter = RSSHTMLAdapter()
        source = {
            "allowed_domains": ["www.npr.org"],
            "transcript_link_keywords": ["transcript"],
        }
        html = (
            "<html><body>"
            '<a href="/transcripts/nx-s1-5764334">Transcript</a>'
            '<footer><a href="/transcripts/">Transcripts</a></footer>'
            "</body></html>"
        )
        transcript_url, note = adapter.resolve_transcript_url(
            source, "https://www.npr.org/2025/01/15/1224776144/x", html
        )
        self.assertEqual(
            transcript_url, "https://www.npr.org/transcripts/nx-s1-5764334"
        )

        # When only the footer landing link exists, resolve to nothing rather
        # than saving the listing page.
        only_footer = (
            "<html><body><footer>"
            '<a href="/transcripts/">Transcripts</a>'
            "</footer></body></html>"
        )
        url2, note2 = adapter.resolve_transcript_url(
            source, "https://www.npr.org/2025/01/15/1224776144/x", only_footer
        )
        self.assertIsNone(url2)
        self.assertEqual(note2, "no transcript link found")

    def test_prefers_item_embedded_episode_url_when_item_link_missing(self) -> None:
        adapter = RSSHTMLAdapter()
        source = {
            "slug": "example-show",
            "homepage_url": "https://podcasts.example.com/show/example-show",
            "preferred_episode_url_domains": ["example.org", "www.example.org"],
        }
        feed = """
        <rss xmlns:content="http://purl.org/rss/1.0/modules/content/"><channel>
          <item>
            <title>Episode 1</title>
            <guid>guid-1</guid>
            <pubDate>Tue, 24 Mar 2026 12:00:00 GMT</pubDate>
            <content:encoded><![CDATA[
              <p>Read more: https://www.example.org/podcast/episode-1/</p>
            ]]></content:encoded>
          </item>
        </channel></rss>
        """
        episodes = adapter.discover(source, feed)
        self.assertEqual(
            episodes[0].episode_url, "https://www.example.org/podcast/episode-1/"
        )

    def test_rejects_embedded_url_when_title_does_not_match(self) -> None:
        adapter = RSSHTMLAdapter()
        source = {
            "slug": "example-show",
            "homepage_url": "https://podcasts.example.com/show/example-show",
            "preferred_episode_url_domains": ["example.org", "www.example.org"],
            "require_title_match_for_embedded_episode_url": True,
        }
        feed = """
        <rss xmlns:content="http://purl.org/rss/1.0/modules/content/"><channel>
          <item>
            <title>Is the Oil Crisis About to Break Global Supply Chains?</title>
            <guid>guid-1</guid>
            <pubDate>Tue, 24 Mar 2026 12:00:00 GMT</pubDate>
            <content:encoded><![CDATA[
              <p>Read more: https://www.example.org/p/a-completely-unrelated-episode</p>
            ]]></content:encoded>
          </item>
        </channel></rss>
        """
        episodes = adapter.discover(source, feed)
        self.assertEqual(
            episodes[0].episode_url,
            "https://podcasts.example.com/show/example-show",
        )

    def test_policy_rejects_forbidden_terms(self) -> None:
        pipeline = PodcastPipeline.__new__(PodcastPipeline)
        source = {
            "allowed_domains": ["example.com"],
            "forbidden_terms": ["automated access prohibited"],
        }
        with self.assertRaises(ValueError):
            pipeline.check_policy(
                source,
                "https://example.com/transcript",
                "This page says automated access prohibited.",
            )


if __name__ == "__main__":
    unittest.main()
