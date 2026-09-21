#!/usr/bin/env python3
r"""Diff-pane adapters for the GUI.

Same idea as ``filelist_view``: one small interface with a native
implementation (a read-only ``NSTextView`` on macOS) and the existing Qt
``QPlainTextEdit`` elsewhere, so the application and the self-test do not
care which is active.

The ``+``/``-``/space that prefixes every diff line is a gutter marker: it
is drawn outside the text (a Qt "line marker area" / an AppKit
``NSRulerView``), so selecting and copying the diff yields the code only.

Interface: ``clear``, ``append(text, tag, marker=None)``, ``text``,
``place``, ``native``.
"""

import sys

from PySide6.QtCore import QRect, QSize, Qt
from PySide6.QtGui import QPainter, QPalette, QTextCursor
from PySide6.QtWidgets import QPlainTextEdit, QWidget


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


class _MarkerGutter(QWidget):
    """The non-selectable strip that paints the diff line markers."""

    def __init__(self, editor):
        super().__init__(editor)
        self._editor = editor

    def sizeHint(self):
        return QSize(int(round(self._editor.gutter_width())), 0)

    def paintEvent(self, event):
        self._editor.paint_gutter(event)


class DiffTextEdit(QPlainTextEdit):
    """A ``QPlainTextEdit`` whose diff markers live in a gutter.

    The text holds only the code; the ``+``/``-``/space prefix of each diff
    line is stored separately and painted in the left gutter, which is not
    part of the selection, so copying never includes it."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._advance = 0.0
        self._markers = {}              # block number -> (marker, QColor|None)
        self._gutter = None
        self._configured = False

    # ---------- gutter plumbing ----------
    def gutter_width(self):
        return self._advance

    def configure_gutter(self, advance):
        """Reserve one cell on the left for the markers."""
        self._advance = float(advance)
        self.document().setDocumentMargin(0)
        self.setViewportMargins(int(round(self._advance)), 0, 0, 0)
        if self._gutter is None:
            self._gutter = _MarkerGutter(self)
        if not self._configured:
            self._configured = True
            self.updateRequest.connect(self._on_update_request)
            self.blockCountChanged.connect(lambda _n: self._gutter.update())
            self.verticalScrollBar().valueChanged.connect(
                lambda _v: self._gutter.update())
        self._gutter.show()
        self._gutter.update()

    def _on_update_request(self, rect, dy):
        if self._gutter is None:
            return
        if dy:
            self._gutter.scroll(0, dy)
        else:
            self._gutter.update(0, rect.y(), self._gutter.width(),
                                rect.height())

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if self._gutter is not None:
            cr = self.contentsRect()
            self._gutter.setGeometry(
                QRect(cr.left(), cr.top(), int(round(self._advance)),
                      cr.height()))

    # ---------- content ----------
    def clear(self):
        super().clear()
        self._markers = {}
        if self._gutter is not None:
            self._gutter.update()

    def append_text(self, text, fmt=None, marker=None, colour=None):
        cursor = self.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.End)
        block = self.blockCount() - 1
        if fmt is None:
            cursor.insertText(text)
        else:
            cursor.insertText(text, fmt)
        self.setTextCursor(cursor)
        if marker:
            self._markers[block] = (marker, colour)
        if self._gutter is not None:
            self._gutter.update()

    def markers(self):
        """The gutter markers in block order (for tests)."""
        return [self._markers[b][0] for b in sorted(self._markers)]

    def marker_at(self, block):
        entry = self._markers.get(block)
        return entry[0] if entry is not None else None

    def paint_gutter(self, event):
        if self._gutter is None:
            return
        painter = QPainter(self._gutter)
        painter.fillRect(event.rect(), Qt.BrushStyle.NoBrush)
        metrics = self.fontMetrics()
        default = self.palette().color(QPalette.ColorRole.Text)
        block = self.firstVisibleBlock()
        top = self.blockBoundingGeometry(block).translated(
            self.contentOffset()).top()
        bottom = top + self.blockBoundingRect(block).height()
        while block.isValid() and top <= event.rect().bottom():
            if block.isVisible() and bottom >= event.rect().top():
                entry = self._markers.get(block.blockNumber())
                if entry is not None:
                    marker, colour = entry
                    painter.setPen(colour if colour is not None else default)
                    painter.drawText(
                        0, int(top), self._gutter.width(), metrics.height(),
                        int(Qt.AlignmentFlag.AlignLeft), marker)
            block = block.next()
            top = bottom
            bottom = top + self.blockBoundingRect(block).height()
        painter.end()


class QtDiffViewAdapter:
    """The Qt ``DiffTextEdit`` (other platforms, and fallback)."""

    native = False

    def __init__(self, window):
        self.window = window
        self.output = window.output
        self.formats = {'add': getattr(window, 'fmt_add', None),
                        'del': getattr(window, 'fmt_del', None),
                        'meta': getattr(window, 'fmt_meta', None)}
        try:
            from PySide6.QtGui import QFontMetricsF

            self.advance = QFontMetricsF(
                self.output.font()).horizontalAdvance('M')
            if hasattr(self.output, 'configure_gutter'):
                self.output.configure_gutter(self.advance)
        except Exception:
            self.advance = 0.0

    def clear(self):
        self.output.clear()

    def append(self, text, fmt=None, marker=None):
        tag = _tag_of(fmt, self.formats)
        if tag is not None:
            fmt = self.formats.get(tag)
        colour = None
        try:
            if fmt is not None and not isinstance(fmt, str):
                colour = fmt.foreground().color()
        except Exception:
            colour = None
        if hasattr(self.output, 'append_text'):
            self.output.append_text(text, fmt, marker=marker, colour=colour)
            return
        # plain QPlainTextEdit fallback (no gutter)
        cursor = self.output.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.End)
        if fmt is None or isinstance(fmt, str):
            cursor.insertText(text)
        else:
            cursor.insertText(text, fmt)
        self.output.setTextCursor(cursor)

    def text(self):
        return self.output.toPlainText()

    def markers(self):
        if hasattr(self.output, 'markers'):
            return self.output.markers()
        return []

    def place(self):
        pass

    def build(self):
        """Nothing to build: the Qt pane is always ready."""
        return False


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

    def append(self, text, fmt=None, marker=None):
        if self.active:
            self.view.append(text, _tag_of(fmt, self.formats), marker=marker)
        else:
            QtDiffViewAdapter(self.window).append(
                text, fmt, marker=marker)

    def text(self):
        return self.view.text() if self.active else self.output.toPlainText()

    def markers(self):
        return self.view.markers() if self.active else []

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
