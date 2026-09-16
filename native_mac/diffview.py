#!/usr/bin/env python3
r"""Native macOS diff pane.

A read-only, selectable ``NSTextView`` in a transparent ``NSScrollView``,
placed over the Qt slot widget that provides the geometry (the same
mechanism as the file list, footer, title and menu). AppKit supplies the
monospaced text rendering, selection, scrolling and the standard editing
commands (⌘C/⌘A arrive through the native Edit menu's responder chain).

Usage
-----
    view = NativeDiffView(window, slot, colours)
    view.build()
    view.clear(); view.append('+ added line\n', 'add')
"""

import sys
from pathlib import Path

# the diff pane's monospace font and the code column width it must fit
DIFF_FONT_SIZE = 12.0
CODE_COLUMNS = 80


def diff_font():
    import AppKit

    return AppKit.NSFont.monospacedSystemFontOfSize_weight_(
        DIFF_FONT_SIZE, AppKit.NSFontWeightRegular)


def char_advance():
    """Width of one monospace cell in the diff font (points)."""
    import AppKit

    sample = AppKit.NSAttributedString.alloc().initWithString_attributes_(
        'M', {AppKit.NSFontAttributeName: diff_font()})
    return float(sample.size().width)


def code_column_width(columns=CODE_COLUMNS):
    return char_advance() * columns


def _ns_color(AppKit, value):
    """NSColor from '#rrggbb' (or None)."""
    try:
        text = str(value).lstrip('#')
        if len(text) == 3:
            text = ''.join(ch * 2 for ch in text)
        red = int(text[0:2], 16) / 255.0
        green = int(text[2:4], 16) / 255.0
        blue = int(text[4:6], 16) / 255.0
        return AppKit.NSColor.colorWithSRGBRed_green_blue_alpha_(
            red, green, blue, 1.0)
    except Exception:
        return None


class NativeDiffView:
    """``NSTextView``-backed diff pane (macOS only)."""

    def __init__(self, window, slot, colours=None):
        self.window = window
        self.slot = slot
        self.colours = dict(colours or {})
        self._scroll = None
        self._view = None
        self._font = None
        self._style = None
        self._colours = {}

    @staticmethod
    def available():
        return sys.platform == 'darwin'

    # ---------- creation ----------
    def build(self):
        """Create the text view and insert it above the Qt view."""
        if not self.available():
            return False
        try:
            import AppKit

            import platform_effects as pe

            if self._scroll is None:
                font = diff_font()
                # hanging indent: the +/-/space marker hangs in the left
                # gutter, the code column aligns one cell to its right
                style = AppKit.NSMutableParagraphStyle.alloc().init()
                style.setFirstLineHeadIndent_(0.0)
                style.setHeadIndent_(char_advance())
                view = AppKit.NSTextView.alloc().initWithFrame_(
                    ((0.0, 0.0), (400.0, 300.0)))
                view.setRichText_(True)
                view.setEditable_(False)
                view.setSelectable_(True)
                view.setDrawsBackground_(False)
                view.setFont_(font)
                view.setDefaultParagraphStyle_(style)
                view.setTextContainerInset_((0.0, 4.0))
                view.setVerticallyResizable_(True)
                view.setHorizontallyResizable_(False)
                view.setAutoresizingMask_(
                    AppKit.NSViewWidthSizable)
                container = view.textContainer()
                if container is not None:
                    container.setWidthTracksTextView_(True)
                    container.setLineFragmentPadding_(0.0)
                view.setMinSize_((0.0, 0.0))
                view.setMaxSize_((1.0e7, 1.0e7))
                self._style = style

                scroll = AppKit.NSScrollView.alloc().init()
                scroll.setDocumentView_(view)
                scroll.setDrawsBackground_(False)
                scroll.setBorderType_(AppKit.NSNoBorder)
                scroll.setHasVerticalScroller_(True)
                scroll.setAutohidesScrollers_(True)
                self._view, self._scroll, self._font = view, scroll, font
                self._colours = {
                    tag: colour for tag, colour in
                    ((tag, _ns_color(AppKit, value))
                     for tag, value in self.colours.items())
                    if colour is not None
                }

            self._target = pe.as_target(self.window, self.slot, inset=1.0)
            if not pe.place_in(self.window, self._target, self._scroll):
                return False
            self.place()
            return True
        except Exception as exc:
            try:
                import platform_effects as pe
                pe._note('native diff view failed: {}: {}'.format(
                    type(exc).__name__, exc))
            except Exception:
                pass
            return False

    def place(self):
        if self._scroll is None:
            return
        try:
            rect = self._target.rect()
            self._scroll.setFrame_((rect.origin, rect.size))
            # the text view must track the clip's width so the code column
            # wraps at the pane, not at a stale size
            view = self._view
            if view is not None:
                width = self._scroll.contentView().bounds().size.width
                height = view.frame().size.height
                view.setFrame_(((0.0, 0.0), (width, height)))
            self._scroll.setHidden_(not self._target.visible())
        except Exception:
            pass

    # ---------- content ----------
    def clear(self):
        if self._view is not None:
            self._view.setString_('')

    def append(self, text, tag=None):
        if self._view is None:
            return
        import AppKit
        import Foundation

        attributes = {}
        if self._font is not None:
            attributes[AppKit.NSFontAttributeName] = self._font
        if self._style is not None:
            attributes[AppKit.NSParagraphStyleAttributeName] = self._style
        colour = self._colours.get(tag) if tag else None
        if colour is not None:
            attributes[AppKit.NSForegroundColorAttributeName] = colour
        piece = Foundation.NSAttributedString.alloc() \
            .initWithString_attributes_(text, attributes)
        self._view.textStorage().appendAttributedString_(piece)

    def text(self):
        """The pane's plain text (for tests)."""
        if self._view is None:
            return ''
        try:
            return str(self._view.string())
        except Exception:
            return ''

    def is_read_only(self):
        return bool(self._view is not None and not self._view.isEditable())

    def font_is_monospaced(self):
        try:
            return bool(self._font is not None
                        and self._font.isFixedPitch())
        except Exception:
            return False

    def scroll_view(self):
        return self._scroll
