# Contributing to LoRACast

Thanks for your interest. Bug reports, small fixes, and new source-registry
recipes are all welcome.

## Getting set up

LoRACast needs Python 3.12 or newer.

```sh
git clone https://github.com/brianward92/loracast
cd loracast
python3 -m venv .venv
source .venv/bin/activate
pip install -e '.[dev]'
```

Add extras when you work on the parts that need them:

```sh
pip install -e '.[dev,asr,youtube]'   # ingest fallbacks
pip install -e '.[dev,train]'         # LoRA training, Apple Silicon only
pip install -e '.[dev,api]'           # Anthropic API extraction backend
```

## Running the tests

```sh
pytest
```

**The test suite must pass offline.** It runs with no network access and no
API credentials, on a machine that has neither Claude Code, an
`ANTHROPIC_API_KEY`, `ffmpeg`, `yt-dlp`, nor `faster-whisper` installed. CI
runs exactly `pip install -e '.[dev]'` and `pytest`, so anything the base
`dev` install does not provide has to be stubbed or skipped.

That means a new test must not:

- open a socket, resolve a hostname, or fetch a URL;
- read or write anything outside `tmp_path` — in particular nothing under
  `$LORACAST_DATA` or `~/.loracast`;
- shell out to `claude`, `ffmpeg`, `yt-dlp`, or any other external binary;
- need an API key, a login, or a paid quota.

Fake the network at the seam instead. The existing tests do this: they pass
canned feed XML and canned HTML straight into the adapter, and they replace
the pipeline's fetch methods. Follow that pattern rather than adding a
recorded-HTTP layer.

## Changing behavior

- Keep the core ingest path stdlib-only. New third-party dependencies belong
  behind an optional extra in `pyproject.toml`.
- A missing optional dependency or a missing system binary should degrade,
  not crash, and the degradation should be visible in the logs.
- If you add a registry key, document it in `docs/registry.md`. That table is
  meant to be complete.
- If you change a CLI flag, update the README, and check `docs/registry.md`
  and `docs/writeup.md` for commands that mention it.
- The docs must not describe behavior the code does not have. If a change
  makes a documented claim untrue, fix the doc in the same pull request.

## Pull requests

- One logical change per pull request.
- Say what you ran. If you could not run part of the pipeline — training
  needs Apple Silicon, extraction needs a paid LLM backend — say so instead
  of implying you did.
- Do not commit transcripts, audio, datasets, adapters, or anything else
  derived from a podcast feed. All of that belongs under `$LORACAST_DATA`,
  outside the repository.
- Do not commit personal paths, hostnames, email addresses, API keys, or any
  other credential. Use `$LORACAST_DATA`, `~/.loracast`, and
  `/path/to/loracast` as placeholders in docs and examples.
- Before you add a podcast to the example registry, check that show's terms
  of service and copyright. See the "Content and rights" section of the
  README.

## Reporting problems

Open an issue at
https://github.com/brianward92/loracast/issues for bugs and feature
requests. For anything security-sensitive, follow
[SECURITY.md](SECURITY.md) instead — do not open a public issue.

## License

By contributing, you agree that your contributions are licensed under the
Apache License 2.0, the same license as the project.
