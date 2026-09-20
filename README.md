# LoRACast

Build and evaluate LoRA adapters from podcast transcripts.

LoRACast is a small end-to-end pipeline: it discovers podcast episodes from
RSS, acquires transcripts (official transcript pages first, official YouTube
captions next, local Whisper ASR as a fallback), extracts reasoning Q&A pairs
from them with an LLM, fine-tunes a LoRA adapter with
[mlx-lm](https://github.com/ml-explore/mlx-lm) on Apple Silicon, and scores
the adapter against the base model by negative log-likelihood on a held-out
split.

NLL measures predictive fit on the corpus — how much better the adapter
predicts held-out transcript-derived text than the base model — not
expertise. (And LoRACast is unrelated to LoRa radio.)

## Install

LoRACast needs Python 3.12 or newer.

From a clone:

```sh
git clone https://github.com/brianward92/loracast
cd loracast
pip install -e '.[asr,youtube]'
```

Without a clone:

```sh
pip install 'loracast[asr,youtube] @ git+https://github.com/brianward92/loracast.git'
```

The clone also carries `scripts/setup.sh`, which creates a virtualenv at
`~/.loracast/env`, installs the `asr` and `youtube` extras into it, and adds
that virtualenv's `bin` directory to your `PATH`.

Once installed, the `loracast` command works from any directory. No command
in this README needs you to be inside the repository.

The core ingest path is stdlib-only. Optional extras:

| extra | enables | extra system requirement |
|---|---|---|
| `asr` | Whisper transcription fallback (faster-whisper) | `ffmpeg` on `PATH` (see below) |
| `youtube` | Official-channel caption acquisition (yt-dlp) | — |
| `train` | LoRA fine-tuning (mlx-lm, transformers) | Apple Silicon |
| `api` | Anthropic API extraction backend (anthropic) | `ANTHROPIC_API_KEY` |
| `dev` | Test suite (pytest) | — |

### ffmpeg

The ASR fallback splits audio into short chunks before transcribing it. It
uses `ffmpeg` to do that. If `ffmpeg` is not on `PATH`, LoRACast does not
fail: it transcribes each episode as a single unsplit file, which is slower
and uses much more memory on long episodes, and it prints no warning. Install
it if you plan to use ASR:

```sh
brew install ffmpeg            # macOS
sudo apt install ffmpeg        # Debian / Ubuntu
```

## Quickstart

All state lives under `$LORACAST_DATA` (default `~/.loracast`) — nothing is
written inside the repo tree.

```sh
# Optional: put the data somewhere other than ~/.loracast
export LORACAST_DATA="$HOME/.loracast"

# 1. Write an editable copy of the built-in example registry to
#    $LORACAST_DATA/registry.toml. The command prints the path it wrote.
#    Edit that file to add your own shows — see docs/registry.md.
loracast ingest init-registry

# 2. Pull transcripts for the sources in the registry
loracast ingest run --limit-per-source 5

# 3. Extract Q&A training pairs. This calls an LLM — read "LLM backends"
#    below before you run it.
loracast extract --limit 20

# 4. Build the dataset and fine-tune an adapter (Apple Silicon only)
pip install -e '.[train]'   # or: pip install 'loracast[train] @ git+https://github.com/brianward92/loracast.git'
loracast train

# 5. Score adapter vs base NLL on the held-out test split
loracast eval
```

Step 1 is optional. If `$LORACAST_DATA/registry.toml` does not exist,
`loracast ingest run` falls back to the example registry that ships inside
the package — the two NPR shows, Planet Money and The Indicator. Run
`init-registry` when you want to edit the source list. It refuses to
overwrite an existing registry unless you pass `--force`.

`loracast ingest --registry PATH` reads any registry file you name and takes
precedence over both the per-user file and the built-in example.

`loracast ingest status` and `loracast ingest manifest` report coverage and
export the transcript manifest.

### Adding your own shows

See [docs/registry.md](docs/registry.md) for every registry key, its default,
and a worked example.

## Transcript acquisition

For each episode, LoRACast tries acquisition strategies in order and stops at
the first one that returns a transcript. The default order is:

1. `official_site` — the transcript page linked from the episode page.
2. `official_youtube` — captions from the show's own YouTube channel or
   playlist (needs the `youtube` extra and a YouTube key in the registry).
3. `asr` — local Whisper transcription of the episode audio (needs the `asr`
   extra).

A fourth strategy, `apple_podcasts`, exists but is **not** in the default
order. It runs only if a source sets `strategy_order` explicitly and names
it, and it also needs that source's `apple_podcast_id`.

The valid strategy names are `official_site`, `official_youtube`,
`apple_podcasts`, and `asr`.

## LLM backends

`loracast extract` sends each transcript to an LLM. Two backends are
available.

**`cli` (the default).** LoRACast runs the `claude` binary — Claude Code — as
a subprocess. This backend needs Claude Code installed, on your `PATH`, and
already signed in. It draws on a paid Claude subscription, and a large
extraction run can consume a meaningful part of that quota. Extraction fails
straight away with `claude CLI not found on PATH` if the binary is missing.
Default model: `sonnet`.

**`api`.** LoRACast calls the Anthropic API directly. This backend needs
`pip install 'loracast[api]'` and an `ANTHROPIC_API_KEY` in the environment.
It is billed per token against that API key. Default model:
`claude-opus-5`.

```sh
loracast extract --backend api --limit 20
```

`--model` overrides the model for either backend. `--effort` sets the
reasoning effort and applies to the `cli` backend only. `--parallel`
(default 4) and `--timeout` (default 600 seconds) control concurrency and the
per-episode time budget.

Extraction is idempotent: one output file per episode, and an episode that
already has one is skipped, so a re-run does not spend quota twice. An
episode whose reply contains no usable pairs gets no output file. It gets a
`<episode_id>.empty.json` marker that keeps the start of the reply, and it is
skipped until you pass `--retry-empty`. A run in which no episode produces a
pair exits non-zero, unless it dispatched a single episode.

## Training

`loracast train` builds the dataset and fine-tunes with mlx-lm, so it needs
Apple Silicon. The default base model is
`mlx-community/gemma-4-26b-a4b-it-4bit`, about 15 GB to download on first use.
Fine-tuning needs noticeably more unified memory than inference does, so on a
smaller machine use `--model` to pick a lighter base model, for example a
4-bit 2B or 4B model from the same `mlx-community` collection.

Other flags: `--config` (LoRA config YAML override), `--adapter-dir`,
`--max-seq-length` (default 2048), and `--seed` (default 42). Unrecognized
arguments pass straight through to mlx-lm.

## Scheduling

Two crontab lines keep the corpus fresh and extraction caught up. The
registry now resolves from `$LORACAST_DATA` (or from the packaged example),
so cron no longer needs to `cd` into a clone:

```cron
LORACAST_DATA=/path/to/loracast-data
0 6 * * *  /path/to/loracast/env/bin/loracast ingest run >> "$LORACAST_DATA/cron.log" 2>&1
0 7 * * *  /path/to/loracast/env/bin/loracast extract --limit 20 >> "$LORACAST_DATA/cron.log" 2>&1
```

Use the absolute path to the `loracast` binary in your virtualenv: cron runs
with a minimal `PATH`.

## Results

No end-to-end run has been published yet. When one is, the base and adapter
NLL per token on the held-out test split, the token counts, and the training
configuration will be reported here.

## Content and rights

LoRACast ships no transcripts and no model weights. It ships code and an
example registry that names two public podcast feeds.

Everything the tool downloads — feed metadata, transcript pages, captions,
audio, and ASR output — is written to your own machine under
`$LORACAST_DATA`. LoRACast uploads nothing, and publishes nothing. The one
exception is extraction, which sends transcript text to whichever LLM backend
you choose.

You are responsible for the terms of service and the copyright of every feed
you add to your registry, and for what you do with the transcripts, datasets,
and adapters that come out of the pipeline. Podcast transcripts are usually
someone else's copyrighted work, and a publisher's terms may restrict
automated access, redistribution, or use as training data. Check before you
add a source.

The `allowed_domains` and `forbidden_terms` registry fields are convenience
checks, not legal compliance:

- `allowed_domains` is an exact hostname allowlist. It stops the
  official-transcript-page strategy from following a link off the hosts you
  listed. It does not cover YouTube captions, Apple Podcasts, or the audio
  download used by ASR.
- `forbidden_terms` is a case-insensitive substring search over the fetched
  page text. If a term appears, that episode's acquisition aborts. It only
  finds the exact strings you put in the list, it only runs on the
  official-transcript-page strategy, and it has no knowledge of
  `robots.txt`, of any licence, or of any terms of service.

Neither field makes a source safe to scrape, and neither is legal advice.

## Tests

```sh
git clone https://github.com/brianward92/loracast
cd loracast
pip install -e '.[dev]'
pytest
```

The suite must pass offline: no network access and no API credentials.

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md). Security reports go through the
process in [SECURITY.md](SECURITY.md).

## Design notes

See [docs/writeup.md](docs/writeup.md) for the architecture: acquisition
strategy ordering and upgrade semantics, extraction quality bar, and the
deterministic hash-based dataset splits.

## License

Apache-2.0
