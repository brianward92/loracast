#!/bin/sh
# Idempotent setup: venv at ~/.loracast/env with loracast installed
# (extras: asr, youtube), and ~/.loracast/env/bin added to PATH via your
# shell rc. Safe to re-run any time:
#
#   bash scripts/setup.sh
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

echo "installing loracast[$EXTRAS]"
"$ENV_DIR/bin/pip" install --quiet --upgrade pip
"$ENV_DIR/bin/pip" install --quiet -e "$REPO_ROOT[$EXTRAS]"

"$ENV_DIR/bin/loracast" ingest status --registry "$REPO_ROOT/configs/registry.toml" >/dev/null
echo "installed: $ENV_DIR/bin/loracast"

# Put loracast on PATH for future shells (idempotent: guarded by marker).
PATH_LINE="export PATH=\"\$HOME/.loracast/env/bin:\$PATH\"  # loracast"
case "${SHELL:-}" in
    */zsh) RC="$HOME/.zshrc" ;;
    */bash) RC="$HOME/.bashrc" ;;
    *) RC="" ;;
esac

RESOLVED="$(command -v loracast 2>/dev/null || true)"
if [ "$RESOLVED" = "$ENV_DIR/bin/loracast" ]; then
    echo "ok: loracast is on PATH"
else
    if [ -n "$RESOLVED" ]; then
        echo "warning: another loracast shadows this env: $RESOLVED"
        echo "         remove it or ensure $ENV_DIR/bin comes first in PATH"
    fi
    if [ -n "$RC" ]; then
        if ! grep -qF "# loracast" "$RC" 2>/dev/null; then
            printf '\n%s\n' "$PATH_LINE" >> "$RC"
            echo "added loracast to PATH in $RC"
        fi
        echo "open a new terminal or run: source $RC"
    else
        echo "add to your shell profile: $PATH_LINE"
    fi
fi
