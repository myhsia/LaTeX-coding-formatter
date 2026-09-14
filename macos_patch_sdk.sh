#!/usr/bin/env bash
# Patch the macOS GUI executables to claim the macOS 27.0 SDK.
#
# Why this is needed: PyInstaller ships a prebuilt bootloader, so our
# binaries inherit an ancient SDK claim (measured: sdk 12.1) regardless of
# the toolchain used. macOS renders the legacy title bar / traffic lights
# for apps whose main executable is linked against an older SDK, so the
# modern (Liquid Glass) appearance is gated on the LC_BUILD_VERSION sdk
# field. Patching it to the current SDK and re-signing ad-hoc opts the app
# into the modern appearance without changing minos (11.0). vtool does not
# validate the number, so CI also installs the macOS 27 SDK (GitHub's
# `xcode-27` image) and asserts xcrun reports 27.0.
#
# Usage: ./macos_patch_sdk.sh [dist_dir]   (default: ./dist)
#        MACOS_SDK_TARGET=26.0 to opt down (e.g. to test older macOS).
#        No-op on non-macOS. Idempotent.
set -euo pipefail

if [[ "$(uname)" != "Darwin" ]]; then
    echo "not macOS, nothing to patch"
    exit 0
fi

DIST="${1:-./dist}"
SDK_TARGET="${MACOS_SDK_TARGET:-27.0}"
MINOS="${MACOS_MINOS:-11.0}"

# informational: what SDK the active toolchain actually provides
INSTALLED_SDK="$(xcrun --show-sdk-version --sdk macosx 2>/dev/null || true)"
if [[ -n "$INSTALLED_SDK" && "$INSTALLED_SDK" != "$SDK_TARGET" ]]; then
    echo "note: claiming SDK $SDK_TARGET while the active SDK is $INSTALLED_SDK"
fi

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
