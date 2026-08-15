# LoRACast

Build and evaluate LoRA adapters from podcast transcripts.

LoRACast is a small end-to-end pipeline: it discovers podcast episodes from
RSS, acquires transcripts (official transcript pages first, YouTube captions
and Apple Podcasts next, local Whisper ASR as a fallback), extracts reasoning
Q&A pairs from them with an LLM, fine-tunes a LoRA adapter with
[mlx-lm](https://github.com/ml-explore/mlx-lm) on Apple Silicon, and scores
the adapter against the base model by negative log-likelihood on a held-out
split.

NLL measures predictive fit on the corpus — how much better the adapter
predicts held-out transcript-derived text than the base model — not
expertise. (And LoRACast is unrelated to LoRa radio.)

## Install

```sh
bash scripts/setup.sh   # venv at ~/.loracast/env + PATH, asr/youtube extras
```

or plain pip:

```sh
pip install -e '.[dev]'
```

The core ingest path is stdlib-only. Optional extras:

| extra | enables |
|---|---|
| `asr` | Whisper transcription fallback (faster-whisper) |
| `youtube` | Official-channel caption acquisition (yt-dlp) |
| `train` | LoRA fine-tuning on Apple Silicon (mlx-lm, pyyaml) |
| `api` | Anthropic API extraction backend (anthropic) |
| `dev` | Test suite (pytest) |

## Quickstart

```sh
# 1. Pull transcripts for the two shipped NPR shows into $LORACAST_DATA
loracast ingest run

# 2. Extract Q&A training pairs (uses the Claude Code CLI by default;
#    --backend api uses the Anthropic API via ANTHROPIC_API_KEY)
loracast extract --limit 20

# 3. Build the dataset and fine-tune an adapter (Apple Silicon)
loracast train

# 4. Score adapter vs base NLL on the held-out test split
loracast eval
```

`loracast ingest status` and `loracast ingest manifest` report coverage and
export the transcript manifest. Sources live in `configs/registry.toml`; add
a `[[sources]]` block to ingest another show.

All state lives under `$LORACAST_DATA` (default `~/.loracast`) — nothing is
written inside the repo tree.

## Scheduling

Two crontab lines keep the corpus fresh and extraction caught up:

```cron
0 6 * * *  cd $HOME/src/loracast && loracast ingest run >> ~/.loracast/cron.log 2>&1
0 7 * * *  cd $HOME/src/loracast && loracast extract --limit 20 >> ~/.loracast/cron.log 2>&1
```

## Results

Numbers from the first end-to-end adapter run will land here: base vs
adapter NLL per token on the held-out test split, token counts, and the
training configuration used. Watch this space.

## Design notes

See [docs/writeup.md](docs/writeup.md) for the architecture: acquisition
strategy ordering and upgrade semantics, extraction quality bar, and the
deterministic hash-based dataset splits.

## License

Apache-2.0
