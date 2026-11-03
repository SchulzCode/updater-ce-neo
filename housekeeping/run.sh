#!/bin/sh
set -eu

ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
PYTHON=${PYTHON:-"$ROOT/.venv-build/bin/python"}
if [ ! -x "$PYTHON" ]; then
    PYTHON=$(command -v python3)
fi
if [ -z "$PYTHON" ]; then
    echo "Python 3 was not found. Install Python 3.10+ first." >&2
    exit 1
fi

cd "$ROOT"
exec "$PYTHON" -m housekeeping "$@"
