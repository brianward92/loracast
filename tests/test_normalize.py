from __future__ import annotations

import unittest

from loracast.ingest.normalize import html_to_transcript_text


class NormalizeTests(unittest.TestCase):
    def test_strips_tags_and_collapses_whitespace(self) -> None:
        html = "<p>Hello   <b>world</b></p><p>Second line</p>"
        self.assertEqual(html_to_transcript_text(html), "Hello\nworld\nSecond line")

    def test_drops_script_and_style_content(self) -> None:
        html = (
            "<script>function OptanonWrapper() { consent(); }</script>"
            "<style>.a { color: red; }</style>"
            "<p>Actual transcript text</p>"
        )
        self.assertEqual(html_to_transcript_text(html), "Actual transcript text")


if __name__ == "__main__":
    unittest.main()
