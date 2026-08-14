# LoRACast

Build and evaluate LoRA adapters from podcast transcripts.

LoRACast ingests podcast feeds, extracts training data from transcripts with an
LLM, and fine-tunes LoRA adapters, then scores them by negative log-likelihood
(NLL) on held-out prompts. NLL measures predictive fit on the corpus, not
expertise.

(LoRACast is unrelated to LoRa radio.)

## Install

```sh
pip install -e .[dev]
```

Optional extras: `asr` (faster-whisper), `youtube` (yt-dlp), `train` (mlx-lm;
Apple Silicon), `api` (anthropic).

## Usage

```sh
loracast ingest
loracast extract
loracast train
loracast eval
```

Data lives under `$LORACAST_DATA` (default `~/.loracast`).

## Status

Work in progress — quickstart, results, and writeup to come.

## License

Apache-2.0
