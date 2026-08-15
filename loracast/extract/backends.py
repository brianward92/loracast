"""LLM backends for extraction.

Both backends take a fully-built prompt and return the model's raw text
response; the runner owns parsing and file writes, so backends stay
interchangeable.
"""

from __future__ import annotations

import shutil
import subprocess
from typing import Protocol

DEFAULT_CLI_MODEL = "sonnet"
DEFAULT_API_MODEL = "claude-opus-5"


class ExtractorBackend(Protocol):
    name: str

    def complete(self, prompt: str, timeout_s: int) -> str: ...


class ClaudeCLIBackend:
    """Drive the Claude Code CLI in print mode. Uses the local subscription;
    no API key needed."""

    name = "cli"

    def __init__(self, model: str | None = None) -> None:
        claude_bin = shutil.which("claude")
        if not claude_bin:
            raise RuntimeError(
                "claude CLI not found on PATH; install Claude Code or use --backend api"
            )
        self.claude_bin = claude_bin
        self.model = model or DEFAULT_CLI_MODEL

    def complete(self, prompt: str, timeout_s: int) -> str:
        result = subprocess.run(
            [self.claude_bin, "-p", prompt, "--model", self.model],
            capture_output=True,
            text=True,
            timeout=timeout_s,
        )
        if result.returncode != 0:
            tail = ((result.stderr or "") + (result.stdout or "")).strip().splitlines()[-3:]
            raise RuntimeError(f"claude exited {result.returncode}: {tail}")
        return result.stdout


class AnthropicAPIBackend:
    """Call the Anthropic API directly. Requires the `anthropic` package
    (`pip install loracast[api]`) and ANTHROPIC_API_KEY in the environment."""

    name = "api"

    def __init__(self, model: str | None = None) -> None:
        try:
            import anthropic
        except ModuleNotFoundError as exc:
            raise RuntimeError(
                "anthropic package not installed; pip install 'loracast[api]'"
            ) from exc
        self.client = anthropic.Anthropic()
        self.model = model or DEFAULT_API_MODEL

    def complete(self, prompt: str, timeout_s: int) -> str:
        response = self.client.messages.create(
            model=self.model,
            max_tokens=8192,
            messages=[{"role": "user", "content": prompt}],
            timeout=timeout_s,
        )
        return "".join(
            block.text for block in response.content if block.type == "text"
        )


def get_backend(name: str, model: str | None = None) -> ExtractorBackend:
    if name == "cli":
        return ClaudeCLIBackend(model)
    if name == "api":
        return AnthropicAPIBackend(model)
    raise ValueError(f"unknown backend: {name}")
