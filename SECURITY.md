# Security policy

## Supported versions

LoRACast is pre-1.0 and under active development. Only the latest commit on
`main` is supported. Fixes land there; there are no backports.

## Reporting a vulnerability

**Do not open a public issue for a security problem.**

Report it privately through GitHub private vulnerability reporting:

1. Go to https://github.com/brianward92/loracast/security/advisories/new
   (or the repository's **Security** tab, then **Report a vulnerability**).
2. Describe the problem, the version or commit you tested, and the steps to
   reproduce it.
3. Include the impact as you see it, and a proof of concept if you have one.

Only the maintainers can see a report filed this way. You will get an
acknowledgement, and the report stays private until a fix is available. If
you want credit in the advisory, say so.

Please give the maintainers a reasonable chance to ship a fix before you
disclose publicly.

## In scope

- Remote code execution, command injection, or path traversal reachable from
  data LoRACast fetches: feed XML, episode pages, transcript pages, YouTube
  or Apple metadata, or audio files.
- A registry file, a feed, or a fetched page that can make LoRACast write
  outside `$LORACAST_DATA`, overwrite an unrelated file, or escape the
  intended directory layout.
- Leakage of credentials or secrets — `ANTHROPIC_API_KEY`, Claude Code
  session material, or anything else in the environment — into logs, the
  state database, extraction prompts, or an outbound request.
- SQL injection or state-database corruption reachable from feed or page
  content.
- A flaw that lets a malicious feed cause LoRACast to send arbitrary requests
  on the operator's behalf, or to fetch from hosts outside the source's
  `allowed_domains`.
- Anything that sends local file contents to an LLM backend that the
  operator did not select.

## Out of scope

- Vulnerabilities in third-party packages. Report those upstream:
  `faster-whisper`, `yt-dlp`, `mlx-lm`, `transformers`, `anthropic`. Do tell
  us if LoRACast uses one of them in a way that makes a known issue worse.
- Anything that needs an attacker who already has write access to
  `$LORACAST_DATA`, to the registry file, or to the account running
  LoRACast. LoRACast trusts its own data root and its own registry.
- Resource exhaustion from a source you added yourself: a long feed, a huge
  audio file, or a slow server.
- Copyright, licensing, or terms-of-service concerns about a podcast feed.
  Those are real, but they are not security reports — see the "Content and
  rights" section of the README.
- Missing security hardening with no demonstrated impact.

## Non-sensitive bugs

For a crash, a wrong result, or anything else that is not
security-sensitive, open a normal issue:
https://github.com/brianward92/loracast/issues

When you file a report or an issue, redact your own secrets first. Log
output can carry file paths, URLs, and environment details.
