#!/bin/sh
# Idempotent environment setup: create (or reuse) a dedicated venv and
# install loracast into it in editable mode.
#
# Usage:
#   scripts/setup.sh                 # venv at $LORACAST_ENV or ~/.loracast/env
#   LORACAST_ENV=/path/to/env scripts/setup.sh
#   LORACAST_EXTRAS=asr,youtube,train scripts/setup.sh
set -eu

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
ENV_DIR="${LORACAST_ENV:-$HOME/.loracast/env}"
EXTRAS="${LORACAST_EXTRAS:-asr,youtube}"
PYTHON="${PYTHON:-python3}"

if [ ! -x "$ENV_DIR/bin/python" ]; then
    echo "creating venv at $ENV_DIR"
    "$PYTHON" -m venv "$ENV_DIR"
else
    echo "reusing venv at $ENV_DIR"
fi

"$ENV_DIR/bin/pip" install --upgrade pip >/dev/null
"$ENV_DIR/bin/pip" install -e "$REPO_ROOT[$EXTRAS]"

"$ENV_DIR/bin/loracast" ingest status --registry "$REPO_ROOT/configs/registry.toml" >/dev/null
echo "ok: $ENV_DIR/bin/loracast"
