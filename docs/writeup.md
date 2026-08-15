# LoRACast: design notes

LoRACast turns podcast feeds into evaluated LoRA adapters in four stages —
ingest, extract, train, eval — each usable on its own through the
`loracast` CLI. This document explains the design decisions that aren't
obvious from the code.

## Ingest

**State machine over a SQLite ledger.** Every episode moves through
`discovered → acquiring → transcript_ready | asr_pending | no_transcript_found
| acquire_error`, with a parallel `asr_pending → asr_in_progress →
transcript_ready | asr_error` track. All transitions, and every acquisition
attempt (including failures and rejections), are recorded in
`state.sqlite3`, so a run is resumable and auditable. A flock-based writer
lock keeps concurrent runs from interleaving; stale in-progress rows are
requeued after 30 minutes.

**Strategy ordering.** Official transcript pages are preferred over YouTube
captions, which are preferred over Apple Podcasts transcripts, with local
Whisper ASR as the last resort. The ordering encodes trust: publisher
transcripts are human-edited; captions and Apple transcripts are usually
machine-generated; ASR is ours. A per-source `strategy_order` in the
registry overrides the default.

**Machine → official upgrades.** Publishers often post transcripts days
after an episode ships. Episodes served by a machine transcript are
re-checked (for 21 days) against the official strategies, and the canonical
transcript is repointed when a genuine human transcript appears — without
deleting the ASR artifact or its attempt history, and never laterally
swapping one machine transcript for another.

**Watchdogs, not just timeouts.** `urlopen`'s timeout bounds a single
socket read, so a slow-trickling CDN can hold a connection open forever. A
SIGALRM watchdog caps each episode's wall-clock budget, and it raises a
`BaseException` subclass so the broad `except Exception` guards inside
strategies can't swallow it — a hung fetch unwinds to the per-episode
handler instead of being mistaken for a merely-failed strategy.

**Source policy.** Each registry entry carries `allowed_domains` and
`forbidden_terms`; a transcript page whose domain falls outside the
allowlist, or whose text contains a publisher's do-not-scrape marker,
aborts that episode's acquisition.

## Extract

**Backends are dumb, the runner is smart.** The `ExtractorBackend` protocol
is one method: prompt in, raw text out. The Claude Code CLI backend and the
Anthropic API backend are therefore interchangeable; prompt assembly, JSONL
parsing, validation, source metadata, and file writes all live in the
runner. Model output is treated as untrusted: only lines that parse as a
valid two-turn messages object survive, capped at 10 pairs per episode.

**Idempotency by construction.** One output file per episode; if it exists,
the episode is skipped. LLM non-determinism across runs never compounds —
each episode is extracted exactly once, and an empty file is a valid,
terminal result ("this episode had nothing worth keeping").

**The spec is the product.** `loracast/extract/prompt.md` defines the
quality bar: reasoning pairs (claim → mechanism → evidence → implication),
standalone questions with no dangling referents, time-anchored when
time-sensitive, grounded in the transcript with explicit anti-hallucination
rules (no fabricated quotes, no precision drift). Silence beats slop.

## Train and eval

**Deterministic splits.** An example's split is
`sha256(seed, example_id)` bucketed by ratio — not a shuffle. Re-building
over a grown corpus never moves an existing example between train, valid,
and test, so eval numbers stay comparable as the corpus grows. Optional
downsampling ranks by `sha256(seed, sample_key, example_id)`, letting an
orchestrator rotate fresh samples per run without touching the split
assignment.

**Sequence filtering with the real tokenizer.** Examples longer than
`max_seq_length` are dropped using the training model's own tokenizer, not
a character heuristic, before mlx-lm ever sees the data.

**Honest scoring.** Eval computes mean NLL per token over only the final
assistant turn — everything through the generation prompt is masked,
matching the `mask_prompt: true` training convention. Base and adapter are
scored on the same held-out test split, and the report is the difference.
NLL is predictive fit on this corpus; it is evidence the adapter learned
the distribution, not that it became an expert.
