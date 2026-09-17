#!/bin/sh
# Idempotent setup: venv at ~/.loracast/env with loracast installed
# (extras: asr, youtube), and that venv's bin directory added to PATH via your
# shell rc. Safe to re-run any time:
#
#   sh scripts/setup.sh
#
# Environment overrides:
#   PYTHON           interpreter used to create the venv (must be >= 3.12)
#   LORACAST_ENV     venv location (default: $HOME/.loracast/env)
#   LORACAST_EXTRAS  extras to install (default: asr,youtube)
set -eu

MIN_PY="3.12"
REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
ENV_DIR="${LORACAST_ENV:-$HOME/.loracast/env}"
EXTRAS="${LORACAST_EXTRAS:-asr,youtube}"
PYTHON="${PYTHON:-python3}"

# Print "major.minor.micro" for an interpreter, or "unknown" if it cannot run.
py_version() {
    "$1" -c 'import sys; print("%d.%d.%d" % sys.version_info[:3])' 2>/dev/null \
        || echo unknown
}

# Succeed only if the interpreter is at least $MIN_PY.
py_is_new_enough() {
    "$1" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 12) else 1)' 2>/dev/null
}

fail_too_old() {
    # $1 = interpreter, $2 = what it is, $3 = extra hint line (may be empty)
    echo "error: $2 is Python $(py_version "$1"); loracast needs >= $MIN_PY." >&2
    if [ -n "${3:-}" ]; then
        echo "       $3" >&2
    fi
    echo "       Install Python $MIN_PY or newer, then re-run with:" >&2
    echo "           PYTHON=/path/to/python$MIN_PY sh scripts/setup.sh" >&2
    exit 1
}

if ! command -v "$PYTHON" >/dev/null 2>&1; then
    echo "error: interpreter not found: $PYTHON" >&2
    echo "       Re-run with: PYTHON=/path/to/python$MIN_PY sh scripts/setup.sh" >&2
    exit 1
fi

if [ ! -x "$ENV_DIR/bin/python" ]; then
    py_is_new_enough "$PYTHON" || fail_too_old "$PYTHON" "$PYTHON" ""
    echo "creating venv at $ENV_DIR ($(py_version "$PYTHON"))"
    "$PYTHON" -m venv "$ENV_DIR"
else
    # Never trust a venv left over from an older interpreter.
    py_is_new_enough "$ENV_DIR/bin/python" || fail_too_old \
        "$ENV_DIR/bin/python" \
        "the existing venv at $ENV_DIR" \
        "Delete it first: rm -rf \"$ENV_DIR\""
    echo "reusing venv at $ENV_DIR ($(py_version "$ENV_DIR/bin/python"))"
fi

echo "installing loracast[$EXTRAS]"
"$ENV_DIR/bin/pip" install --quiet --upgrade pip
"$ENV_DIR/bin/pip" install --quiet -e "$REPO_ROOT[$EXTRAS]"

# Smoke check: no --registry, so this exercises the packaged default registry.
"$ENV_DIR/bin/loracast" ingest status >/dev/null
echo "installed: $ENV_DIR/bin/loracast"

# Put loracast on PATH for future shells (idempotent: guarded by marker).
# Keep the rc line portable when the env is in its default location.
if [ "$ENV_DIR" = "$HOME/.loracast/env" ]; then
    PATH_LINE="export PATH=\"\$HOME/.loracast/env/bin:\$PATH\"  # loracast"
else
    PATH_LINE="export PATH=\"$ENV_DIR/bin:\$PATH\"  # loracast"
fi
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
