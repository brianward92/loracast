from __future__ import annotations

import unittest

from loracast.ingest.normalize import has_transcript_body, html_to_transcript_text


class NormalizeTests(unittest.TestCase):
    def test_strips_tags_and_collapses_whitespace(self) -> None:
        html = "<p>Hello   <b>world</b></p><p>Second line</p>"
        self.assertEqual(html_to_transcript_text(html), "Hello\nworld\nSecond line")

    def test_keeps_only_the_transcript_container_when_present(self) -> None:
        html = (
            "<html><head><script>function OptanonWrapper() {}</script></head><body>"
            "<nav>Skip to main content</nav><h1 class=\"transcript\">Title</h1>"
            "<div class=\"transcript storytext\">"
            "<p>HOST, BYLINE: First <em>turn</em>.</p>"
            "<div class=\"ad\"><p>Advert inside</p></div>"
            "<p>GUEST: Second&nbsp;turn.</p></div>"
            "<footer><p>Sponsor message</p></footer></body></html>"
        )
        self.assertEqual(
            html_to_transcript_text(html),
            "HOST, BYLINE: First turn.\nAdvert inside\nGUEST: Second turn.",
        )

    def test_unclosed_paragraphs_still_break_lines(self) -> None:
        html = (
            "<div class=\"transcript storytext\">"
            "<p>(SOUNDBITE OF MUSIC)<p>HOST: First turn.<p>GUEST: Second turn.</div>"
        )
        self.assertEqual(
            html_to_transcript_text(html),
            "(SOUNDBITE OF MUSIC)\nHOST: First turn.\nGUEST: Second turn.",
        )

    def test_container_without_paragraphs_means_no_transcript(self) -> None:
        html = (
            "<body><nav>Skip to main content</nav>"
            "<div class=\"transcript storytext\">   </div>"
            "<footer>Sponsor message</footer></body>"
        )
        self.assertEqual(html_to_transcript_text(html), "")

    def test_has_transcript_body_rejects_page_chrome(self) -> None:
        chrome = "Title : NPR\nAccessibility links\nSkip to main content\n" + "Menu item\n" * 200
        self.assertFalse(has_transcript_body(chrome))
        self.assertFalse(has_transcript_body(""))
        spoken = "\n".join(f"HOST, BYLINE: Turn number {i} of the show." for i in range(30))
        self.assertTrue(has_transcript_body(spoken))
        no_speakers = "Transcript\n" + "Narration without speaker labels. " * 40
        self.assertTrue(has_transcript_body(no_speakers), "no chrome and long enough")

    def test_pages_without_a_container_use_the_whole_page(self) -> None:
        html = "<p>Only</p><p>paragraphs</p>"
        self.assertEqual(html_to_transcript_text(html), "Only\nparagraphs")

    def test_drops_script_and_style_content(self) -> None:
        html = (
            "<script>function OptanonWrapper() { consent(); }</script>"
            "<style>.a { color: red; }</style>"
            "<p>Actual transcript text</p>"
        )
        self.assertEqual(html_to_transcript_text(html), "Actual transcript text")


if __name__ == "__main__":
    unittest.main()
