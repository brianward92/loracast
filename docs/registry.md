# The source registry

The registry is a TOML file that lists the podcasts LoRACast ingests. Each
show is one `[[sources]]` block.

## Where the registry comes from

LoRACast resolves the registry in this order:

1. `loracast ingest --registry PATH` — the file you name, if you name one.
   LoRACast exits with an error if that file does not exist.
2. `$LORACAST_DATA/registry.toml` — your own registry, if that file exists.
   `$LORACAST_DATA` defaults to `~/.loracast`.
3. The example registry packaged inside loracast
   (`loracast/ingest/registry_default.toml`), which holds two NPR shows.

To get an editable copy of the example:

```sh
loracast ingest init-registry
```

It writes `$LORACAST_DATA/registry.toml` and prints the path. It refuses to
overwrite an existing file unless you pass `--force`.

```sh
loracast ingest init-registry --force   # replace my registry with the example
```

After you edit the file, run `loracast ingest run`. You never need to be in
the repository directory, and you never need to pass `--registry`.

## File shape

```toml
[[sources]]
slug = "example-show"
name = "The Example Show"
adapter = "rss_html"
feed_url = "https://feeds.example.com/example-show.xml"

[[sources]]
slug = "another-show"
# ...
```

Only `slug`, `name`, `adapter`, and `feed_url` are required. A source that is
missing any of them makes the whole registry fail to load with
`registry source missing required key: <key>`.

Restrict a run to one source with `loracast ingest run --source example-show`
(repeatable).

## Every key

### Required

| key | type | default | what it does |
|---|---|---|---|
| `slug` | string | — | Stable identifier for the show. It names the source's directories under `$LORACAST_DATA/podcasts/`, keys every row in the state database, and is the value `--source` matches. Changing it later orphans the existing state for that show, so pick one and keep it. Use a filesystem-safe string: lowercase letters, digits, and hyphens. |
| `name` | string | — | Human-readable show name. Used in status and manifest reports only. |
| `adapter` | string | — | The feed and page parser. **Only `"rss_html"` is implemented.** Any other value raises `unknown adapter: <value>` when the source is ingested. |
| `feed_url` | string | — | The RSS feed. `rss_html` reads `channel/item` elements from it and builds one episode per item: title, `guid`, `link`, `pubDate`, the `enclosure` URL for audio, and `itunes:duration`. |

### Episode discovery

| key | required | type | default | what it does |
|---|---|---|---|---|
| `homepage_url` | optional | string | unset | Fallback episode URL, used when an RSS item has no `<link>` and no usable embedded URL. Without it those episodes get a null episode URL, and `official_site` has no page to read. |
| `preferred_episode_url_domains` | optional | list of strings | `[]` | Only used when an RSS item has no `<link>`. LoRACast then scans the item's text for `http(s)` URLs and takes the first one whose hostname is in this list. An empty list disables the scan entirely. |
| `require_title_match_for_embedded_episode_url` | optional | boolean | `false` | Tightens the scan above. When true, a candidate URL is accepted only if the last segment of its path shares at least two tokens of 4 or more characters with the episode title. |

### Transcript acquisition

| key | required | type | default | what it does |
|---|---|---|---|---|
| `strategy_order` | optional | list of strings | see "Strategy order" below | Explicit acquisition order for this source. It replaces the default order completely. Valid names: `official_site`, `official_youtube`, `apple_podcasts`, `asr`. An unrecognized name is silently skipped, so a typo quietly removes a strategy. |
| `official_youtube_only` | optional | boolean | `false` | Drops `official_site` from the default order, leaving `official_youtube` then `asr`. It also makes `official_site` return nothing even if your own `strategy_order` names it. Use it for shows that publish no transcript page. |
| `transcript_link_keywords` | optional | list of strings | `[]` | Lowercase substrings matched against the visible text of `<a>` links on the episode page. The first link whose text contains any of them, and whose host passes `allowed_domains`, is fetched as the transcript. **With an empty list nothing ever matches, so `official_site` can never succeed.** Links whose final path segment is `transcript`, `transcripts`, or empty are skipped as site landing pages rather than per-episode transcripts. |
| `allowed_domains` | optional | list of strings | `[]` (no restriction) | Exact hostname allowlist. Transcript link candidates on other hosts are skipped during link resolution, and a transcript URL outside the list aborts that episode with `domain not allowed by source policy`. Matching is an exact string comparison against the URL host, so it is not a suffix match: list `example.com` and `www.example.com` separately if you need both. |
| `forbidden_terms` | optional | list of strings | `[]` | Case-insensitive substrings searched in the fetched episode page HTML and transcript page HTML. If any term is present, that episode's acquisition aborts with `source policy violation`. This runs on the `official_site` strategy only. |

### YouTube captions

Both keys below are optional, but `official_youtube` returns nothing unless
at least one of them is set. The strategy also needs the `youtube` extra
(`pip install 'loracast[youtube]'`); without `yt-dlp` installed it returns
nothing.

| key | required | type | default | what it does |
|---|---|---|---|---|
| `youtube_channel_handle` | optional | string | unset | The show's channel handle, for example `"@ExampleShow"`. LoRACast lists `https://www.youtube.com/<handle>/videos`, also runs a per-episode `ytsearch5` query for the episode title, and uses the handle to confirm a candidate video really belongs to that channel. A leading `@` is added if you omit it. |
| `youtube_playlist_url` | optional | string | unset | A playlist URL, for example `"https://www.youtube.com/playlist?list=PL..."`. Every entry in the playlist counts as official. Useful when the show posts episodes to a shared or multi-show channel. |

A candidate video is accepted when its title similarity to the RSS episode
title is at least 0.72. A video whose duration is within 60 seconds of the
feed's `itunes:duration` is promoted to a confident match, provided its title
similarity is already at least 0.55.

### Apple Podcasts (opt-in)

| key | required | type | default | what it does |
|---|---|---|---|---|
| `apple_podcast_id` | optional | string or integer | unset | The iTunes collection id of the show — the `id...` number in its Apple Podcasts URL. The `apple_podcasts` strategy returns nothing without it. |

`apple_podcasts` is **not** in the default strategy order. To use it you must
set both `apple_podcast_id` and a `strategy_order` that names it. Episodes
are matched to Apple's episode index by title similarity (at least 0.72) and
a release date within 2 days. Apple transcripts are machine-generated, so
LoRACast records them as such and keeps looking for an official transcript.

### Whisper ASR

| key | required | type | default | what it does |
|---|---|---|---|---|
| `asr_model` | optional | string | `"base.en"` | faster-whisper model name for this source, for example `"small.en"` or `"medium.en"`. Larger models are more accurate and much slower. |
| `language` | optional | string | `"en"` | Language code passed to faster-whisper. |

ASR needs the `asr` extra (`pip install 'loracast[asr]'`) and, for sane
performance on long episodes, `ffmpeg` on `PATH`.

## Strategy order

With no `strategy_order` key, the order is:

| `official_youtube_only` | order |
|---|---|
| `false` (default) | `official_site`, `official_youtube`, `asr` |
| `true` | `official_youtube`, `asr` |

LoRACast tries each strategy in turn and stops at the first that returns a
transcript. `apple_podcasts` is never in a default order.

Machine-generated transcripts (YouTube captions, Apple transcripts, ASR) are
provisional. For 21 days after an episode is first served that way, LoRACast
re-checks the official strategies — the same order with `asr` removed — and
repoints the canonical transcript if a genuine publisher transcript appears.

## Worked example

```toml
# A show that publishes transcript pages on its own site, has an official
# YouTube channel as a backstop, and falls back to local ASR.
[[sources]]
slug = "example-show"
name = "The Example Show"
adapter = "rss_html"
feed_url = "https://feeds.example.com/example-show.xml"
homepage_url = "https://example.com/podcasts/example-show"
allowed_domains = ["example.com", "www.example.com", "transcripts.example.com"]
transcript_link_keywords = ["transcript", "read the transcript", "full transcript"]
forbidden_terms = ["do not scrape", "automated access prohibited", "not for redistribution"]
youtube_channel_handle = "@ExampleShow"
asr_model = "small.en"
language = "en"

# A video-first show with no transcript page: skip official_site, and opt in
# to Apple Podcasts transcripts ahead of local ASR.
[[sources]]
slug = "example-video-show"
name = "The Example Video Show"
adapter = "rss_html"
feed_url = "https://feeds.example.com/example-video-show.xml"
homepage_url = "https://example.com/podcasts/example-video-show"
official_youtube_only = true
youtube_playlist_url = "https://www.youtube.com/playlist?list=PLexamplePlaylistId"
apple_podcast_id = "1234567890"
strategy_order = ["official_youtube", "apple_podcasts", "asr"]
```

## Adding a show: a checklist

1. Find the show's RSS feed URL and confirm it returns XML.
2. Open one episode page and find the transcript link. Note the exact
   visible text of that link — that text is what
   `transcript_link_keywords` has to match — and the hostname the link
   points at, which goes in `allowed_domains`.
3. Write the `[[sources]]` block into `$LORACAST_DATA/registry.toml`.
4. Test on a few episodes before you commit to a full backfill:

   ```sh
   loracast ingest run --source example-show --limit-per-source 3 --skip-asr
   loracast ingest status --source example-show
   ```

   `--skip-asr` keeps the first test cheap: no audio downloads and no
   transcription.
5. If `status` shows no transcripts, the usual causes are a
   `transcript_link_keywords` list that does not match the real link text,
   an `allowed_domains` list that is missing the transcript host, or a
   `forbidden_terms` hit.
6. Before you add a source, check that show's terms of service and
   copyright. See the "Content and rights" section of the README.
