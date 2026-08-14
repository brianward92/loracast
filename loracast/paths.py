"""Data-root resolution. All pipeline state lives under $LORACAST_DATA."""

import os
from pathlib import Path


def data_root() -> Path:
    """Return the data root, creating it if needed."""
    root = Path(os.environ.get("LORACAST_DATA", "~/.loracast")).expanduser()
    root.mkdir(parents=True, exist_ok=True)
    return root
