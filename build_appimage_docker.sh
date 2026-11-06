#!/usr/bin/env bash
# ==============================================================================
# build_appimage_docker.sh — build (and test) the Linux AppImages inside Docker
#
# build_appimage.sh packages with Linux tools (appimagetool, sha256sum, tar,
# sed), so its --container mode can only containerise PyInstaller: the AppImage
# assembly still runs on the host and therefore fails on macOS/Windows. This
# wrapper runs the *whole* build in a Debian Bookworm container of the target
# architecture, which is how both Linux AppImages are produced from a Mac.
#
# Each architecture builds in its own build/linux-<arch> tree (the cached
# appimagetool inside it belongs to one CPU architecture), while the finished
# artifacts land in dist/linux exactly as a native run writes them.
#
# Usage:
#   ./build_appimage_docker.sh                  # x86_64 and aarch64
#   ./build_appimage_docker.sh --arch aarch64   # one architecture
#   ./build_appimage_docker.sh --clean          # drop the per-arch build trees first
#   ./build_appimage_docker.sh --no-test        # build only
#   ./build_appimage_docker.sh --rebuild-image  # rebuild the builder image
#
# Requires a reachable Docker daemon (Docker Desktop or `colima start`).
# ==============================================================================

set -euo pipefail
cd "$(dirname "$0")"

DOCKER="${DOCKER:-docker}"
IMAGE_PREFIX="${IMAGE_PREFIX:-updater-linux-builder}"
BRAND="${BUILD_BRAND:-updater_ce}"
DO_TEST=1
CLEAN=0
REBUILD_IMAGE=0
ARCH_LIST=()

die() {
    echo "ERROR: $*" >&2
    exit 1
}

usage() {
    sed -n '2,26p' "$0" | sed -e 's/^# \{0,1\}//'
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --arch)
            [[ $# -ge 2 ]] || die "--arch needs a value (x86_64, aarch64 or all)"
            case "$2" in
                all) ARCH_LIST=(x86_64 aarch64) ;;
                x86_64|amd64) ARCH_LIST+=(x86_64) ;;
                aarch64|arm64) ARCH_LIST+=(aarch64) ;;
                *) die "unsupported --arch '$2' (x86_64, aarch64 or all)" ;;
            esac
            shift 2
            ;;
        --brand)
            [[ $# -ge 2 ]] || die "--brand needs a value (updater_ce or mediatek_installer)"
            BRAND="$(printf '%s' "$2" | tr '[:upper:]-' '[:lower:]_')"
            shift 2
            ;;
        --clean)
            CLEAN=1
            shift
            ;;
        --no-test)
            DO_TEST=0
            shift
            ;;
        --rebuild-image)
            REBUILD_IMAGE=1
            shift
            ;;
        -h|--help)
            usage
            exit 0
            ;;
        *)
            die "unknown option: $1 (see --help)"
            ;;
    esac
done

case "$BRAND" in
    updater_ce) BRAND_SLUG="InnioasisUpdater"; BINARY_NAME="InnioasisUpdater" ;;
    mediatek_installer) BRAND_SLUG="MediaTekInstaller"; BINARY_NAME="MediaTekInstaller" ;;
    *) die "unknown brand '$BRAND' (updater_ce or mediatek_installer)" ;;
esac

[[ ${#ARCH_LIST[@]} -gt 0 ]] || ARCH_LIST=(x86_64 aarch64)

# The release version decides the artifact names; read it the same way
# build_appimage.sh does, minus GNU-only `grep -P` (the host may be macOS).
VERSION="$(sed -n 's/^APP_VERSION[[:space:]]*=[[:space:]]*"\([^"]*\)".*/\1/p' src/config.py | head -n 1)"
[[ -n "$VERSION" ]] || die "could not read APP_VERSION from src/config.py"

platform_for() {
    case "$1" in
        x86_64) echo "linux/amd64" ;;
        aarch64) echo "linux/arm64" ;;
        *) die "unsupported architecture '$1'" ;;
    esac
}

# --- Docker daemon ------------------------------------------------------------
# Colima publishes its socket under the user's home and only wires the docker
# context up when its status check succeeds, so fall back to the socket itself.
if ! "$DOCKER" info >/dev/null 2>&1; then
    for _sock in "${DOCKER_HOST:-}" "$HOME/.colima/default/docker.sock" "$HOME/.colima/docker.sock"; do
        [[ -n "$_sock" ]] || continue
        case "$_sock" in unix://*) ;; *) _sock="unix://$_sock" ;; esac
        if DOCKER_HOST="$_sock" "$DOCKER" info >/dev/null 2>&1; then
            export DOCKER_HOST="$_sock"
            echo "  -> Using Docker socket: $DOCKER_HOST"
            break
        fi
    done
fi
"$DOCKER" info >/dev/null 2>&1 \
    || die "Docker is not reachable. Start Docker Desktop, or 'colima start', and try again."

ensure_image() {
    local arch="$1" platform="$2" image="$IMAGE_PREFIX:$1"
    if [[ "$REBUILD_IMAGE" -eq 0 ]] && "$DOCKER" image inspect "$image" >/dev/null 2>&1; then
        echo "  -> Reusing builder image $image"
        return
    fi
    echo "  -> Building builder image $image ($platform) from Dockerfile.linux_build..."
    "$DOCKER" build --platform "$platform" -t "$image" -f Dockerfile.linux_build .
}

# Runs inside the builder image, in the container's own architecture.
run_test() {
    local arch="$1" platform="$2" image="$IMAGE_PREFIX:$1" appimage="$3"

    echo ""
    echo "--- Testing $(basename "$appimage") natively in the $arch container ---"
    "$DOCKER" run --rm --platform "$platform" \
        -v "$PWD:/workspace" \
        -w /workspace \
        -e QT_QPA_PLATFORM=offscreen \
        "$image" -lc '
set -uo pipefail
img="$1"; arch="$2"; binary="$3"
fail=0
step() { echo "  -> $*"; }
bad() { echo "     FAIL: $*"; fail=1; }

img="$(realpath "$img")"

step "sha256 checksum"
if ( cd "$(dirname "$img")" && sha256sum -c "$(basename "$img").sha256" >/dev/null ); then
    echo "     checksum matches"
else
    bad "checksum does not match"
fi

step "AppImage runtime architecture"
runtime_line="$(file -b "$img")"
echo "     $runtime_line"
case "$arch" in
    x86_64) grep -q "x86-64" <<<"$runtime_line" || bad "runtime is not x86-64" ;;
    aarch64) grep -q "aarch64" <<<"$runtime_line" || bad "runtime is not aarch64" ;;
esac

step "extracting AppDir"
work="$(mktemp -d)"
( cd "$work" && "$img" --appimage-extract >/dev/null 2>&1 ) || bad "extraction failed"
appdir="$work/squashfs-root"
[[ -d "$appdir/usr/bin" ]] || bad "no usr/bin in the AppDir"

payload="$appdir/usr/bin/$binary"
if [[ -f "$payload" ]]; then
    payload_line="$(file -b "$payload")"
    echo "     $payload_line"
    case "$arch" in
        x86_64) grep -q "x86-64" <<<"$payload_line" || bad "app binary is not x86-64" ;;
        aarch64) grep -q "aarch64" <<<"$payload_line" || bad "app binary is not aarch64" ;;
    esac
    [[ -d "$appdir/usr/bin/_internal" ]] || bad "missing _internal (onedir payload)"
else
    bad "missing $binary"
fi

step "bundled payloads"
for tool in adb unrar; do
    if [[ -f "$appdir/usr/bin/$tool" ]]; then
        tool_line="$(file -b "$appdir/usr/bin/$tool")"
        case "$arch" in
            x86_64) grep -q "x86-64" <<<"$tool_line" || bad "$tool is not x86-64: $tool_line" ;;
            aarch64) grep -q "aarch64" <<<"$tool_line" || bad "$tool is not aarch64: $tool_line" ;;
        esac
        echo "     $tool: bundled, matches $arch"
    else
        bad "$tool is not bundled"
    fi
done
if compgen -G "$appdir/usr/bin/_internal/libusb-1.0.so*" >/dev/null \
   || compgen -G "$appdir/usr/lib/libusb-1.0.so*" >/dev/null; then
    echo "     libusb-1.0: bundled"
else
    bad "libusb-1.0 is not bundled"
fi
if [[ "$arch" = "x86_64" ]]; then
    [[ -x "$appdir/usr/bin/SP_Flash_Tool/flash_tool" ]] \
        && echo "     SP Flash Tool: bundled (x86_64 only)" \
        || bad "SP Flash Tool payload missing on x86_64"
else
    [[ -e "$appdir/usr/bin/SP_Flash_Tool" ]] \
        && bad "x86-only SP Flash Tool should not be bundled on $arch" \
        || echo "     SP Flash Tool: omitted (x86_64 only), MTKClient is the engine"
fi

step "environment diagnostics (mtkclient + libusb + mtk_api)"
if timeout 300 env QT_QPA_PLATFORM=offscreen APPIMAGE_EXTRACT_AND_RUN=1 "$img" --diagnostics; then
    echo "     diagnostics passed"
else
    bad "--diagnostics failed"
fi

step "headless launch of the AppImage runtime (10s)"
set +e
APPIMAGE_EXTRACT_AND_RUN=1 timeout 10 "$img" >/tmp/appimage-launch.log 2>&1
rc=$?
set -e
if [[ $rc -eq 124 ]]; then
    echo "     still running after 10s (Qt window is up)"
else
    bad "exited early with status $rc"
    tail -n 20 /tmp/appimage-launch.log
fi

rm -rf "$work"

if [[ $fail -ne 0 ]]; then
    echo "  RESULT: FAILED"
    exit 1
fi
echo "  RESULT: PASSED"
' _ "$appimage" "$arch" "$BINARY_NAME"
}

# --- Build --------------------------------------------------------------------
echo "======================================================================"
echo " Building Linux AppImages in Docker: ${ARCH_LIST[*]} (v$VERSION, $BRAND)"
echo "======================================================================"

for arch in "${ARCH_LIST[@]}"; do
    platform="$(platform_for "$arch")"
    image="$IMAGE_PREFIX:$arch"
    build_dir="build/linux-$arch"

    echo ""
    echo "===================================================================="
    echo "  $arch ($platform)"
    echo "===================================================================="

    ensure_image "$arch" "$platform"

    if [[ "$CLEAN" -eq 1 ]]; then
        echo "  -> Removing $build_dir"
        rm -rf "$build_dir"
    fi
    mkdir -p "$build_dir"

    # /opt/venv is the interpreter Dockerfile.linux_build provisioned, so the
    # script verifies it instead of bootstrapping a host virtualenv. --no-container
    # keeps PyInstaller in this container (the nested Docker call would fail).
    # build/linux is bind-mounted to the per-arch tree so the cached
    # appimagetool never crosses architectures.
    "$DOCKER" run --rm --platform "$platform" \
        --user "$(id -u):$(id -g)" \
        -e HOME=/tmp/builder-home \
        -e XDG_CACHE_HOME=/tmp/builder-home/.cache \
        -e PYTHON=/opt/venv/bin/python3 \
        -e ARCH="$arch" \
        -v "$PWD:/workspace" \
        -v "$PWD/$build_dir:/workspace/build/linux" \
        -w /workspace \
        "$image" -lc "mkdir -p /tmp/builder-home/.cache && ./build_appimage.sh --arch $arch --brand $BRAND --no-container"

    appimage="dist/linux/${BRAND_SLUG}-${VERSION}-${arch}.AppImage"
    [[ -f "$appimage" ]] || die "build finished but $appimage is missing"
    echo "  -> Built $appimage ($(du -h "$appimage" | cut -f1))"

    if [[ "$DO_TEST" -eq 1 ]]; then
        run_test "$arch" "$platform" "$appimage"
    fi
done

echo ""
echo "======================================================================"
echo " Done"
echo "======================================================================"
ls -1 dist/linux/*.AppImage 2>/dev/null || true
