#!/bin/sh
set -eu

ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
PYTHON=${PYTHON:-"$ROOT/.venv-build/bin/python"}
if [ ! -x "$PYTHON" ]; then
    PYTHON=$(command -v python3)
fi
if [ -z "$PYTHON" ]; then
    echo "Python 3 was not found." >&2
    exit 1
fi
if ! "$PYTHON" -c "import PyInstaller" >/dev/null 2>&1; then
    echo "PyInstaller is not installed in $PYTHON." >&2
    echo "Install it with: $PYTHON -m pip install pyinstaller" >&2
    exit 1
fi

cd "$ROOT"
rm -rf "$ROOT/housekeeping/build" "$ROOT/housekeeping/dist"
"$PYTHON" -m PyInstaller \
    --noconfirm \
    --clean \
    --windowed \
    --name "Mirror Housekeeping" \
    --paths "$ROOT" \
    --hidden-import housekeeping.app \
    --osx-bundle-identifier "app.innioasis.mirror-housekeeping" \
    --distpath "$ROOT/housekeeping/dist" \
    --workpath "$ROOT/housekeeping/build" \
    --specpath "$ROOT/housekeeping/build" \
    "$ROOT/housekeeping/__main__.py"

APP="$ROOT/housekeeping/dist/Mirror Housekeeping.app"
if [ ! -d "$APP" ]; then
    echo "Build did not produce $APP" >&2
    exit 1
fi
echo "Built $APP"
