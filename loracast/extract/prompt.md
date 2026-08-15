# Podcast Q&A Extraction Spec

This document is the single source of truth for how an LLM extracts training
Q&A pairs from podcast transcripts for LoRA fine-tuning.

## Purpose

Produce training data that teaches a model to **reason** — to chain claims,
mechanisms, and implications — not to retrieve factoids. Each pair is a
standalone worked example of substantive thought on news, markets, technology,
or geopolitics.

## Quality bar

- **Target:** 3 A+ pairs per episode.
- **Acceptable ceiling:** up to 10 pairs, each ≥ B+.
- **Hard floor:** NEVER produce C+ or below. Cut ruthlessly.
- **Empty is valid.** An episode that yields zero qualifying pairs yields
  zero output lines. Silence is better than slop.

An "A+ pair" is one a subject-matter expert would read and say "yes, that's a
crisp, non-obvious piece of reasoning I'd want my trainee to internalize."

## Anti-patterns (reject on sight)

- **Meta-trivia about the podcast itself:** "Who hosts X?", "What is X
  about?", "How long has X been running?", "Who are the guests?"
- **Pure factoids / retrieval:** "Who is the author of Y?", "When was Z
  founded?", "How much does W cost?" — anything a search engine resolves in
  one hop.
- **Unanswerable without context:** questions with pronouns or references
  lacking antecedents — "the guest", "this episode", "the conversation", "the
  book", "the speaker". If you'd need to listen to the episode to know what
  the question is about, kill it.
- **News-ticker noise:** "How many U.S. personnel were injured in the
  strike?", "Which country offered to host talks?" — these come from newscast
  preambles baked into transcripts. Not podcast content. Kill.
- **Sponsor reads, intros, outros, credits, show promos.**
- **One-word or phrase answers.** Answers are multi-sentence reasoning, not
  lookups.
- **Opinion-without-reasoning:** "So-and-so thinks X is overvalued" with no
  chain of why. We want the argument, not the conclusion.
- **Near-duplicates** within an episode (e.g., same claim stated in two
  overlapping chunks). Keep the strongest one.
- **Fabricated attributed quotes.** Inventing a quote from a real person
  ("X said Y") when the transcript does not contain that quote is a
  hallucination, even if the sentiment is plausible. Paraphrase or drop.
- **Invented specificity.** Claiming a precise number, date, or name that
  the transcript does not contain — even when the broader claim is
  supported — corrupts downstream reasoning. The model that trains on this
  pair will treat the fabricated specifics as ground truth.

## Required shape of an A+ pair

### Question
- **Standalone.** Names every entity. No "the", "this", "that" referencing
  unstated antecedents. A reader with zero episode context can understand what
  is being asked.
- **Time-anchored when the answer is time-sensitive.** If the question is
  about a specific Fed meeting, earnings print, policy announcement, market
  move, or any event whose meaning depends on *when* it happened, the date or
  named regime must appear in the question itself — not only in the answer.
  E.g. "Why did the Fed leave policy modestly restrictive at the January 2026
  meeting?" not "Why did the Fed leave policy modestly restrictive?". The
  rule of thumb: a reader skimming only the question should know the temporal
  context. Generic structural questions ("Why does a steepening yield curve
  precede commodity rallies?") need no date.
- **Reasoning-oriented.** Asks *why*, *how*, *what are the implications of*,
  *what mechanism connects*, *what does X imply about Y*. NOT *who*, *when*,
  *where*, *how many*.
- **Non-trivial.** A smart generalist would not know the answer by default.
  The answer requires synthesis or domain-specific framing.

### Answer
- **2-5 sentences** (roughly 40-150 words). Not a single phrase, not a wall.
- **Claim → mechanism → recent-event evidence → implication** structure.
  State the thesis, explain the causal or economic chain that makes it true,
  anchor it to a specific recent event from the transcript (dates, named
  actors, concrete numbers), then name what it implies for markets, policy,
  technology, or behavior. The recent-event anchor is load-bearing — it
  turns an abstract mechanism into a worked example the model can later
  pattern-match against new events.
- **Grounded in the transcript** — do not invent facts, statistics, or
  dates. You may synthesize across multiple statements in the same episode,
  but every supporting claim (especially numbers) must appear in the
  source. Three specific failure modes to watch for:
    - **No fabricated quotes.** Never put words in someone's mouth that
      aren't in the transcript. If you want to attribute a claim, paraphrase
      what they actually said, or drop the attribution. A quote you "kind of
      remember they would say" is a hallucination.
    - **No precision drift.** If the transcript says "Big Tech", do not
      write "four or five buyers." If it says "tens of millions", do not
      write "23 million." Match the transcript's level of specificity. When
      in doubt, use the looser phrasing.
    - **Episode publish date is allowed as a fact.** The episode metadata
      passed in the prompt is authoritative — you may cite it directly
      (e.g., "in the September 2025 episode" or "as of late 2025") even if
      that exact date doesn't appear in the transcript text. But do not
      invent any *other* date. Inferred dates that aren't in the transcript
      or the metadata are hallucinations.
- **Self-contained.** Answer reads as a complete thought even if someone
  has never heard the episode.

### Example of the target shape

```
Q: What does it mean for capital markets when a sharp equity selloff
   coincides with rising long-term Treasury yields instead of falling ones?
A: In a typical risk-off episode, capital flees equities and flows into
   Treasuries, which drives prices up and yields down; when the yield rises
   instead, it signals that investors are exiting both the stock market and
   the debt market simultaneously — a vote of no confidence in the issuing
   nation rather than a standard rotation. This played out in early April
   2025 when sweeping US reciprocal-tariff announcements tipped the S&P
   into bear-market territory, but the 10-year Treasury yield, after a
   brief dip below 4%, climbed back to roughly 4.2% — higher than before
   the tariff shock. Capital wasn't rotating to safety; it was leaving the
   country. For a reserve-currency issuer this implies the loss of
   safe-haven status itself: without inelastic demand for its sovereign
   debt, the country loses the fiscal capacity to run large deficits
   cheaply, and the currency premium compresses. Historically the joint
   exit from a country's stocks and bonds has preceded sovereign debt
   crises in emerging markets; seeing it in a developed-market reserve
   currency implies a structural repricing of global capital allocation.
```

This is the bar. The answer is an argument anchored to a specific recent
event, not a textbook lookup.

## Process

1. **Read the full transcript.** Note where intros/sponsors/newscasts end and
   where substantive content begins.
2. **Identify substantive threads.** Arguments, frameworks, causal claims,
   predictions-with-reasoning, non-obvious observations.
3. **For each thread, draft a Q in standalone reasoning shape.** If you
   cannot name all entities without referring to "the guest" / "the episode",
   the thread is not extraction-ready — skip it.
4. **Compose an A.** Claim → mechanism → implication. 2-5 sentences.
5. **Self-review each pair.** Would an expert label this A+? If not B+ or
   above, cut.
6. **Dedup.** If two pairs cover the same core insight, keep the stronger.
7. **Cap at 10.** Keep the top-N by quality, not the first-N by order.

## Output format

Output ONLY JSONL — one JSON object per line, nothing else (no prose, no
code fences). Zero lines is valid when no pair qualifies.

Each line:

```json
{"messages": [{"role": "user", "content": "<Q>"}, {"role": "assistant", "content": "<A>"}]}
```

ASCII-safe content (escape non-ASCII characters). The runner adds source
metadata and writes the output file; you only emit the pairs.
