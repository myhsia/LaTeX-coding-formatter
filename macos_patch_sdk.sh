#!/usr/bin/env bash
# Patch the macOS GUI executables to claim the SDK that introduced the
# Liquid Glass design (macOS 26). macOS 26+ renders the legacy title
# bar / traffic lights for apps whose main executable is linked against
# an older SDK, regardless of the code. Patching LC_BUILD_VERSION and
# re-signing ad-hoc opts the app into the modern appearance.
#
# Usage: ./macos_patch_sdk.sh [dist_dir]   (default: ./dist)
#        No-op on non-macOS. Idempotent.
set -euo pipefail

if [[ "$(uname)" != "Darwin" ]]; then
    echo "not macOS, nothing to patch"
    exit 0
fi

DIST="${1:-./dist}"
SDK_TARGET="${MACOS_SDK_TARGET:-26.0}"
MINOS="${MACOS_MINOS:-11.0}"

VTOOL="$(command -v vtool || true)"
[[ -z "$VTOOL" && -x "$(xcrun -f vtool 2>/dev/null)" ]] && VTOOL="$(xcrun -f vtool)"
if [[ -z "$VTOOL" ]]; then
    echo "ERROR: vtool not found (install Xcode Command Line Tools)" >&2
    exit 1
fi

TARGETS=(
    "$DIST/format-tex-gui-macos-arm64"
    "$DIST/format-tex-gui.app/Contents/MacOS/format-tex-gui"
)

patched=0
for bin in "${TARGETS[@]}"; do
    if [[ ! -f "$bin" ]]; then
        echo "skip (missing): $bin"
        continue
    fi
    tmp="$bin.sdkpatch"
    if "$VTOOL" -set-build-version macos "$MINOS" "$SDK_TARGET" -replace \
            -output "$tmp" "$bin" && mv "$tmp" "$bin"; then
        codesign --force --sign - "$bin" >/dev/null 2>&1 || true
        echo "patched: $bin (sdk -> $SDK_TARGET)"
        patched=$((patched + 1))
    else
        echo "ERROR: vtool failed for $bin" >&2
        rm -f "$tmp"
        exit 1
    fi
done

# Re-sign the app bundle after its inner executable changed.
if [[ -d "$DIST/format-tex-gui.app" ]]; then
    codesign --force --sign - "$DIST/format-tex-gui.app" >/dev/null 2>&1 || true
fi

if [[ $patched -eq 0 ]]; then
    echo "ERROR: no artifacts were patched (build the GUI first)" >&2
    exit 1
fi

echo "done: $patched artifact(s) patched to sdk $SDK_TARGET"
