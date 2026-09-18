#!/usr/bin/env python3
"""Windows 11 look for the Qt-drawn controls (Windows only).

The option checkboxes, the two drop-downs, the apply button and the status
text are drawn by Qt on Windows (the file list stays a native
``SysListView32``). Qt's own ``windows11`` style is used when the Qt build
provides it; on top of that - or as a fallback on older Qt - these helpers
apply a small dark/light stylesheet plus a ``QProxyStyle`` that draws the
checkbox indicator and the combo chevron, so the result matches the
Windows 11 control fill, rounded corners and the system accent colour
instead of the classic Win32 chrome.

Everything is scoped per widget (no application-wide stylesheet), so the
rest of the carefully styled layout is untouched.
"""

import sys

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import (QColor, QPainter, QPainterPath, QPalette, QPen)
from PySide6.QtWidgets import QApplication, QProxyStyle, QStyle

# Windows 11 dark / light control colours
_DARK = {
    'fill': '#2d2d2d',
    'fill_hover': '#333333',
    'fill_press': '#292929',
    'border': '#3a3a3a',
    'text': '#ffffff',
    'muted': 'rgba(255, 255, 255, 0.40)',
    'popup': '#2b2b2b',
    'hairline': '#3a3a3a',
}
_LIGHT = {
    'fill': '#ffffff',
    'fill_hover': '#f7f7f7',
    'fill_press': '#ededed',
    'border': '#d6d6d6',
    'text': '#1a1a1a',
    'muted': 'rgba(0, 0, 0, 0.35)',
    'popup': '#ffffff',
    'hairline': '#e2e2e2',
}


def install(app):
    """Use Qt's Windows 11 style when this Qt build offers it."""
    if sys.platform != 'win32':
        return False
    try:
        from PySide6.QtWidgets import QStyleFactory

        keys = [str(key).lower() for key in QStyleFactory.keys()]
        if 'windows11' in keys:
            style = QStyleFactory.create('windows11')
            if style is not None:
                app.setStyle(style)
                return True
    except Exception:
        pass
    return False


def _accent(dark):
    try:
        import platform_effects as pe

        value = pe.native_accent_color(dark)
        if value:
            return QColor(value)
    except Exception:
        pass
    try:
        return QApplication.palette().color(QPalette.ColorRole.Highlight)
    except Exception:
        return QColor('#4cc2ff')


def _accent_text(accent):
    try:
        import platform_effects as pe

        return QColor(pe.accent_text_color(
            (accent.red(), accent.green(), accent.blue())))
    except Exception:
        return QColor('#ffffff')


class _Win11Style(QProxyStyle):
    """Draws the checkbox indicator and combo chevron in the Win11 idiom."""

    def __init__(self, accent, accent_text, base=None):
        super().__init__(base)
        self._accent = QColor(accent)
        self._accent_text = QColor(accent_text)

    def drawPrimitive(self, element, option, painter, widget=None):
        if element == QStyle.PrimitiveElement.PE_IndicatorCheckBox:
            self._draw_check(painter, option)
            return
        if element == QStyle.PrimitiveElement.PE_IndicatorArrowDown:
            self._draw_arrow(painter, option)
            return
        super().drawPrimitive(element, option, painter, widget)

    def _draw_check(self, painter, option):
        rect = option.rect
        size = float(min(rect.width(), rect.height()))
        if size <= 0:
            return
        box = QRectF(rect.center().x() - size / 2.0,
                     rect.center().y() - size / 2.0, size, size)
        radius = max(2.0, size * 0.24)
        checked = bool(option.state & QStyle.StateFlag.State_On)
        enabled = bool(option.state & QStyle.StateFlag.State_Enabled)
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        inner = box.adjusted(1.0, 1.0, -1.0, -1.0)
        if checked:
            fill = QColor(self._accent) if enabled \
                else self._accent.darker(170)
            painter.setPen(QPen(fill, max(1.0, size * 0.08)))
            painter.setBrush(fill)
            painter.drawRoundedRect(inner, radius, radius)
            pen = QPen(self._accent_text if enabled
                       else QColor(self._accent_text).darker(140))
            pen.setWidthF(max(1.4, size * 0.13))
            pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
            painter.setPen(pen)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            path = QPainterPath()
            path.moveTo(box.left() + size * 0.27, box.top() + size * 0.53)
            path.lineTo(box.left() + size * 0.44, box.top() + size * 0.70)
            path.lineTo(box.left() + size * 0.74, box.top() + size * 0.31)
            painter.drawPath(path)
        else:
            border = QColor(255, 255, 255, 150) if enabled \
                else QColor(255, 255, 255, 60)
            painter.setPen(QPen(border, max(1.0, size * 0.08)))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawRoundedRect(inner, radius, radius)
        painter.restore()

    def _draw_arrow(self, painter, option):
        rect = option.rect
        size = float(min(rect.width(), rect.height()))
        if size <= 0:
            return
        span = max(6.0, size * 0.42)
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        pen = QPen(QColor(255, 255, 255, 200))
        pen.setWidthF(max(1.2, span * 0.15))
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        painter.setPen(pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        cx = float(rect.center().x())
        cy = float(rect.center().y())
        path = QPainterPath()
        path.moveTo(cx - span * 0.5, cy - span * 0.18)
        path.lineTo(cx, cy + span * 0.30)
        path.lineTo(cx + span * 0.5, cy - span * 0.18)
        painter.drawPath(path)
        painter.restore()


def _palette(dark):
    return dict(_DARK if dark else _LIGHT)


def _install_style(widget, dark):
    """Wrap the widget's style once (draws the indicator/chevron).

    ``QProxyStyle`` defaults its base to the application style, so we do
    not hand it the widget's shared style object (ownership stays with us
    only for the proxy itself)."""
    if getattr(widget, '_win11_styled', False):
        return
    try:
        accent = _accent(dark)
        widget.setStyle(_Win11Style(accent, _accent_text(accent)))
        widget._win11_styled = True
    except Exception:
        pass


def apply_checkbox(widget, dark=True):
    """Win11 checkbox: transparent background, rounded accent indicator."""
    _install_style(widget, dark)


def apply_combo(widget, dark=True):
    """Win11 combo: rounded dark field and a dark accent popup."""
    _install_style(widget, dark)
    colours = _palette(dark)
    accent = _accent(dark).name()
    text = _accent_text(_accent(dark)).name()
    widget.setStyleSheet(
        'QComboBox {{'
        ' background-color: {fill}; color: {text};'
        ' border: 1px solid {border}; border-radius: 5px;'
        ' padding: 3px 26px 3px 9px; min-height: 20px; }}'
        'QComboBox:hover {{ background-color: {fill_hover}; }}'
        'QComboBox:on {{ background-color: {fill_press}; }}'
        'QComboBox:disabled {{ color: {muted}; }}'
        'QComboBox::drop-down {{ border: none; width: 24px; }}'
        .format(accent=accent, text=colours['text'], fill=colours['fill'],
                fill_hover=colours['fill_hover'],
                fill_press=colours['fill_press'],
                border=colours['border'], muted=colours['muted']))
    view = None
    try:
        view = widget.view()
    except Exception:
        view = None
    if view is not None:
        view.setStyleSheet(
            'QAbstractItemView {{'
            ' background-color: {popup}; color: {text};'
            ' border: 1px solid {hairline}; border-radius: 5px;'
            ' padding: 4px; outline: none;'
            ' selection-background-color: {accent};'
            ' selection-color: {accent_text}; }}'
            'QAbstractItemView::item {{ min-height: 22px;'
            ' padding: 2px 8px; border-radius: 4px; }}'
            'QAbstractItemView::item:selected {{'
            ' background-color: {accent}; color: {accent_text}; }}'
            .format(popup=colours['popup'], text=colours['text'],
                    hairline=colours['hairline'], accent=accent,
                    accent_text=text))


def apply_button(widget, dark=True):
    """Accent-filled Win11 primary button."""
    colours = _palette(dark)
    accent = _accent(dark)
    hover = accent.lighter(118).name()
    press = accent.darker(118).name()
    text = _accent_text(accent).name()
    widget.setStyleSheet(
        'QPushButton {{'
        ' background-color: {accent}; color: {text};'
        ' border: 1px solid {accent}; border-radius: 4px;'
        ' padding: 6px 18px; min-height: 20px; }}'
        'QPushButton:hover {{ background-color: {hover};'
        ' border-color: {hover}; }}'
        'QPushButton:pressed {{ background-color: {press};'
        ' border-color: {press}; }}'
        'QPushButton:disabled {{ background-color: {fill};'
        ' color: {muted}; border-color: {border}; }}'
        .format(accent=accent.name(), text=text, hover=hover, press=press,
                fill=colours['fill'], muted=colours['muted'],
                border=colours['border']))


def apply_label(widget, dark=True):
    """Transparent status text (no gray rectangle behind it)."""
    colours = _palette(dark)
    widget.setStyleSheet(
        'background: transparent; border: none; color: {text};'
        ' padding-top: 2px;'.format(text=colours['text']))
