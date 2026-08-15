from __future__ import annotations

import hashlib
import re
import unicodedata
from dataclasses import dataclass
from datetime import timezone
from email.utils import parsedate_to_datetime
from html.parser import HTMLParser
from urllib.parse import urljoin, urlparse
from xml.etree import ElementTree


@dataclass(frozen=True)
class EpisodeSeed:
    podcast_slug: str
    episode_id: str
    title: str
    published_at: str | None
    episode_url: str
    audio_url: str | None
    guid: str | None
    duration_seconds: int | None = None


_ITUNES_NS = "{http://www.itunes.com/dtds/podcast-1.0.dtd}"


class LinkCollector(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.links: list[tuple[str, str]] = []
        self._current_href: str | None = None
        self._text_parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag != "a":
            return
        attrs_map = dict(attrs)
        self._current_href = attrs_map.get("href")
        self._text_parts = []

    def handle_data(self, data: str) -> None:
        if self._current_href:
            self._text_parts.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag == "a" and self._current_href:
            text = " ".join(part.strip() for part in self._text_parts if part.strip())
            self.links.append((self._current_href, text))
            self._current_href = None
            self._text_parts = []


class RSSHTMLAdapter:
    name = "rss_html"

    def discover(self, source: dict, feed_xml: str) -> list[EpisodeSeed]:
        root = ElementTree.fromstring(feed_xml)
        channel = root.find("channel")
        if channel is None:
            return []

        episodes: list[EpisodeSeed] = []
        for item in channel.findall("item"):
            title = self._text(item.findtext("title")) or "untitled"
            guid = self._text(item.findtext("guid"))
            link = self._episode_url(source, item, title) or source.get("homepage_url")
            enclosure = item.find("enclosure")
            audio_url = enclosure.get("url") if enclosure is not None else None
            published_at = self._parse_pub_date(self._text(item.findtext("pubDate")))
            duration_seconds = self._parse_duration(
                self._text(item.findtext(f"{_ITUNES_NS}duration"))
            )
            seed_basis = (
                guid or link or f"{source['slug']}:{title}:{published_at or ''}"
            )
            episode_id = hashlib.sha1(seed_basis.encode("utf-8")).hexdigest()[:16]
            episodes.append(
                EpisodeSeed(
                    podcast_slug=source["slug"],
                    episode_id=episode_id,
                    title=title,
                    published_at=published_at,
                    episode_url=link,
                    audio_url=audio_url,
                    guid=guid,
                    duration_seconds=duration_seconds,
                )
            )
        return episodes

    def _episode_url(
        self, source: dict, item: ElementTree.Element, title: str
    ) -> str | None:
        direct_link = self._text(item.findtext("link"))
        if direct_link:
            return direct_link

        preferred_domains = source.get("preferred_episode_url_domains", [])
        if not preferred_domains:
            return None
        candidate_urls = self._candidate_urls(item)
        for candidate in candidate_urls:
            parsed = urlparse(candidate)
            if preferred_domains and parsed.netloc not in preferred_domains:
                continue
            if source.get(
                "require_title_match_for_embedded_episode_url"
            ) and not self._candidate_matches_title(candidate, title):
                continue
            return candidate
        return None

    def resolve_transcript_url(
        self, source: dict, episode_url: str, episode_html: str
    ) -> tuple[str | None, str | None]:
        parser = LinkCollector()
        parser.feed(episode_html)
        keywords = [
            value.lower() for value in source.get("transcript_link_keywords", [])
        ]
        allowed_domains = set(source.get("allowed_domains", []))
        for href, text in parser.links:
            candidate = urljoin(episode_url, href)
            lower_text = text.lower()
            if not any(keyword in lower_text for keyword in keywords):
                continue
            parsed = urlparse(candidate)
            if allowed_domains and parsed.netloc not in allowed_domains:
                continue
            # A real per-episode transcript URL carries an ID segment after
            # `/transcripts/`. The bare landing page (matched from a footer
            # "Transcripts" nav link, since "transcript" is a substring of
            # "Transcripts") is a site listing, not a transcript -- skip it so
            # the episode stays genuinely pending instead of saving garbage.
            if parsed.path.rstrip("/").rsplit("/", 1)[-1] in {
                "transcripts",
                "transcript",
                "",
            }:
                continue
            return candidate, f"matched transcript link text: {text or href}"
        return None, "no transcript link found"

    def _candidate_urls(self, item: ElementTree.Element) -> list[str]:
        values: list[str] = []
        for child in item:
            text = self._text(child.text)
            if text:
                values.append(text)
        urls: list[str] = []
        seen = set()
        for value in values:
            for match in re.findall(r"https?://[^\s<>()\"']+", value):
                normalized = "".join(
                    ch for ch in match if unicodedata.category(ch) != "Cf"
                )
                normalized = normalized.replace("&amp;", "&").rstrip(".,)")
                if normalized in seen:
                    continue
                seen.add(normalized)
                urls.append(normalized)
        return urls

    def _candidate_matches_title(self, candidate: str, title: str) -> bool:
        path = urlparse(candidate).path.rsplit("/", 1)[-1]
        candidate_tokens = set(re.findall(r"[a-z0-9]+", path.lower()))
        title_tokens = {
            token
            for token in re.findall(r"[a-z0-9]+", title.lower())
            if len(token) >= 4
        }
        overlap = candidate_tokens & title_tokens
        return len(overlap) >= 2

    @staticmethod
    def _text(value: str | None) -> str | None:
        if value is None:
            return None
        stripped = value.strip()
        return stripped or None

    @staticmethod
    def _parse_duration(value: str | None) -> int | None:
        # Megaphone publishes integer seconds; some feeds use HH:MM:SS or
        # MM:SS. Accept both.
        if not value:
            return None
        value = value.strip()
        if not value:
            return None
        if value.isdigit():
            return int(value)
        if ":" in value:
            try:
                parts = [int(p) for p in value.split(":")]
            except ValueError:
                return None
            seconds = 0
            for part in parts:
                seconds = seconds * 60 + part
            return seconds
        return None

    @staticmethod
    def _parse_pub_date(value: str | None) -> str | None:
        if not value:
            return None
        try:
            dt = parsedate_to_datetime(value)
        except (TypeError, ValueError, IndexError):
            return None
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc).isoformat()


ADAPTERS = {RSSHTMLAdapter.name: RSSHTMLAdapter()}


def get_adapter(name: str) -> RSSHTMLAdapter:
    try:
        return ADAPTERS[name]
    except KeyError as exc:
        raise ValueError(f"unknown adapter: {name}") from exc
