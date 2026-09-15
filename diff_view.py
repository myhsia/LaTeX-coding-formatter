#!/usr/bin/env python3
r"""Diff-pane adapters for the GUI.

Same idea as ``filelist_view``: one small interface with a native
implementation (a read-only ``NSTextView`` on macOS) and the existing Qt
``QPlainTextEdit`` elsewhere, so the application and the self-test do not
care which is active.

Interface: ``clear``, ``append(text, tag)``, ``text``, ``place``,
``native``.
"""

import sys


def _tag_of(fmt, fmts):
    """Map a QTextCharFormat (or a tag name) to its tag name (or None)."""
    if fmt is None:
        return None
    if isinstance(fmt, str):
        return fmt
    for tag, candidate in fmts.items():
        if candidate is not None and fmt is candidate:
            return tag
    return None


class QtDiffViewAdapter:
    """The existing ``QPlainTextEdit`` (other platforms, and fallback)."""

    native = False

    def __init__(self, window):
        self.window = window
        self.output = window.output
        self.formats = {'add': getattr(window, 'fmt_add', None),
                        'del': getattr(window, 'fmt_del', None),
                        'meta': getattr(window, 'fmt_meta', None)}

    def clear(self):
        self.output.clear()

    def append(self, text, fmt=None):
        from PySide6.QtGui import QTextCursor

        tag = _tag_of(fmt, self.formats)
        if tag is not None:
            fmt = self.formats.get(tag)
        cursor = self.output.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.End)
        if fmt is None or isinstance(fmt, str):
            cursor.insertText(text)
        else:
            cursor.insertText(text, fmt)
        self.output.setTextCursor(cursor)

    def text(self):
        return self.output.toPlainText()

    def place(self):
        pass


class NativeDiffViewAdapter:
    """A real ``NSTextView``; the Qt pane stays as the geometry slot."""

    native = True

    def __init__(self, window):
        from native_mac import NativeDiffView

        self.window = window
        self.output = window.output
        self.formats = {'add': getattr(window, 'fmt_add', None),
                        'del': getattr(window, 'fmt_del', None),
                        'meta': getattr(window, 'fmt_meta', None)}
        self.view = NativeDiffView(window, self.output,
                                   colours=self._colours())
        self.active = False

    def _colours(self):
        pal = getattr(self.window, 'pal', {}) or {}
        return {'add': pal.get('add'), 'del': pal.get('del'),
                'meta': pal.get('meta')}

    def build(self):
        if self.active:
            return True
        try:
            self.active = bool(self.view.build())
        except Exception:
            self.active = False
        if self.active:
            self.place()
        return self.active

    def clear(self):
        if self.active:
            self.view.clear()
        else:
            self.output.clear()

    def append(self, text, fmt=None):
        if self.active:
            self.view.append(text, _tag_of(fmt, self.formats))
        else:
            QtDiffViewAdapter(self.window).append(text, fmt)

    def text(self):
        return self.view.text() if self.active else self.output.toPlainText()

    def place(self):
        if self.active:
            self.view.place()


def create_diff_view(window):
    """Native on macOS (Qt fallback if it cannot be created)."""
    if sys.platform == 'darwin':
        try:
            return NativeDiffViewAdapter(window)
        except Exception:
            pass
    return QtDiffViewAdapter(window)
