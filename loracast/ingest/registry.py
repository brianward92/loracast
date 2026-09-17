"""Load the podcast source registry from TOML."""

from __future__ import annotations

import tomllib
from importlib.resources import files
from pathlib import Path

from ..paths import data_root

_REQUIRED_KEYS = ("slug", "name", "adapter", "feed_url")


def default_registry_text() -> str:
    """Return the text of the example registry shipped inside the package."""
    return (files("loracast.ingest") / "registry_default.toml").read_text(
        encoding="utf-8"
    )


def user_registry_path() -> Path:
    """Return the per-user registry location: ``$LORACAST_DATA/registry.toml``."""
    return data_root() / "registry.toml"


def _validate(sources: list) -> None:
    for source in sources:
        for key in _REQUIRED_KEYS:
            if key not in source:
                raise ValueError(f"registry source missing required key: {key}")


def load_registry(path: Path | str | None = None) -> dict:
    """Read a registry TOML file into the config shape the pipeline expects:
    ``{"sources": [{slug, name, adapter, feed_url, ...}, ...]}``.

    With no ``path``, use ``$LORACAST_DATA/registry.toml`` when it exists and
    otherwise fall back to the example registry packaged with loracast.
    """
    if path is not None:
        registry_path = Path(path).expanduser()
        if not registry_path.exists():
            raise FileNotFoundError(
                f"registry file not found: {registry_path}\n"
                "Pass an existing file with --registry, or run "
                "'loracast ingest init-registry' to write an editable copy of "
                "the built-in registry to $LORACAST_DATA/registry.toml."
            )
        text = registry_path.read_text(encoding="utf-8")
    else:
        user_path = user_registry_path()
        text = (
            user_path.read_text(encoding="utf-8")
            if user_path.is_file()
            else default_registry_text()
        )

    data = tomllib.loads(text)
    sources = data.get("sources", [])
    _validate(sources)
    return {"sources": sources}
