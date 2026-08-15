"""Load the podcast source registry from TOML."""

from __future__ import annotations

import tomllib
from pathlib import Path

DEFAULT_REGISTRY = Path("configs/registry.toml")


def load_registry(path: Path | str | None = None) -> dict:
    """Read a registry TOML file into the config shape the pipeline expects:
    ``{"sources": [{slug, name, adapter, feed_url, ...}, ...]}``.
    """
    registry_path = Path(path) if path else DEFAULT_REGISTRY
    with registry_path.open("rb") as handle:
        data = tomllib.load(handle)
    sources = data.get("sources", [])
    for source in sources:
        for key in ("slug", "name", "adapter", "feed_url"):
            if key not in source:
                raise ValueError(f"registry source missing required key: {key}")
    return {"sources": sources}
