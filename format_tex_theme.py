#!/usr/bin/env python3
r"""Colour palette for the diff pane and the syntax colours.

Kept free of any GUI toolkit so both the Qt application and the native
macOS application can use it. ``native_dark()`` asks AppKit for the
effective appearance; the Qt app keeps using its own detection.
"""

LIGHT = {
    'text_bg': '#fafafa', 'text_fg': '#1a1a1a',
    'add': '#098658', 'del': '#a31515', 'meta': '#0550ae',
}
DARK = {
    'text_bg': '#1e1e1e', 'text_fg': '#d4d4d4',
    'add': '#4ec9b0', 'del': '#f48771', 'meta': '#569cd6',
}


def palette(dark):
    return DARK if dark else LIGHT


def native_dark():
    """True when the app's effective appearance is dark (macOS only)."""
    try:
        import AppKit

        app = AppKit.NSApplication.sharedApplication()
        appearance = app.effectiveAppearance()
        if appearance is None:
            return False
        name = appearance.bestMatchFromAppearancesWithNames_([
            AppKit.NSAppearanceNameAqua,
            AppKit.NSAppearanceNameDarkAqua,
        ])
        return name == AppKit.NSAppearanceNameDarkAqua
    except Exception:
        return True
