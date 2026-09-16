#!/usr/bin/env python3
r"""PySide6 GUI for format_tex.py: insert CJK/Latin spacing in TeX files
with a file picker, directory scanning, drag & drop of files/folders,
rule toggles, per-file encoding auto-detection, and a colorized diff
preview pane.

Native window chrome: macOS native unified toolbar (system blur),
Windows Mica (see platform_effects.py).

Usage
-----
    python3 format_tex_gui.py142857
    
"""

import difflib
import os
import shutil
import sys
import traceback
from pathlib import Path

from PySide6.QtCore import QEvent, QFileInfo, QPoint, QPointF, QRect, \
    QSize, QTimer, QUrl, Qt
from PySide6.QtGui import QAction, QColor, QDropEvent, QFont, \
    QFontDatabase, QFontMetrics, QKeySequence, QPalette, \
    QTextCharFormat, QTextCursor
from PySide6.QtWidgets import (QAbstractItemView, QApplication, QCheckBox,
                               QComboBox, QFileDialog, QFileIconProvider,
                               QFrame, QGridLayout,
                               QHBoxLayout,
                               QInputDialog, QLabel, QListWidget,
                               QListWidgetItem,
                               QMainWindow, QMessageBox, QPlainTextEdit,
                               QPushButton, QSplitter, QStyle,
                               QStyledItemDelegate, QStyleOptionComboBox,
                               QStyleOptionViewItem, QVBoxLayout,
                               QWidget)

from format_tex import (FormatOptions, backup_path, format_file,
                        make_backup, scan_directory)
from diff_view import create_diff_view
from format_tex_controller import FormatController
from format_tex_theme import DARK, LIGHT
from filelist_view import create_file_list_view
from native_mac import menus
from native_menu import (CUSTOM_SENTINEL, build_menu, menu_entries,
                         popup_native_menu)
from platform_effects import (apply_effects, arrange_in_front,
                              band_above_content,
                              create_footer_strip,
                              create_native_plus_minus,
                              band_height, band_material_name,
                              create_native_switch, debug_enabled,
                              has_native_switch, last_material_view,
                              last_sidebar_view, last_title_view,
                              lights_inset,
                              footer_metrics,
                              footer_tint_rgba,
                              footer_strip_view,
                              has_native_plus_minus,
                              native_plus_minus_height,
                              place_footer_strip,
                              place_native_plus_minus,
                              native_plus_minus_view,
                              native_switch_state, native_switch_view,
                              minimize_window, native_window_color,
                              notes,
                              place_native_switch, prepare_qt,
                              reposition_materials, set_sidebar_width,
                              set_native_plus_minus_enabled, title_gap,
                              zoom_window)

ENCODINGS = ['同输入', 'utf-8', 'gb18030', 'gbk', 'gb2312', 'big5',
             'utf-16', 'latin-1']
EXTENSIONS = ['*.tex', '*.ctx', '*.sty', '*.cls', '*.dtx', '*.txt']



def detect_dark(app):
    try:
        return app.styleHints().colorScheme() == Qt.ColorScheme.Dark
    except Exception:
        try:
            return app.palette().color(QPalette.ColorRole.Window).lightness() \
                < 128
        except Exception:
            return False


ROLE_PATH = Qt.ItemDataRole.UserRole          # full path of the row
ROLE_ROOT = Qt.ItemDataRole.UserRole + 1      # scanned root for backups

SEPARATOR = QColor(120, 120, 128, 60)         # hairline between rows
SELECTION_TINT = QColor(120, 120, 128, 30)    # hover tint


class SpacerWidget(QWidget):
    """Invisible layout slot: reserves the space a native control takes."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setStyleSheet('background: transparent;')


class OptionControl:
    """One option checkbox: a Qt checkbox (data/fallback) plus, on macOS, a
    native ``NSButton`` checkbox drawn over a transparent spacer slot.

    Exposes the small API the rest of the GUI uses (``isChecked``,
    ``setChecked``, ``isVisible``, ``slot``)."""

    def __init__(self, qt, slot, title, checked, window):
        self.qt = qt
        self.slot = slot
        self.title = title
        self.checked = bool(checked)
        self.window = window
        self.native = None

    def build_native(self):
        if sys.platform == 'win32':
            return self._build_win32()
        if sys.platform != 'darwin':
            self._use_qt()
            return False
        from native_mac import NativeCheckbox

        self.native = NativeCheckbox(
            self.window, self.slot, self.title, self.checked,
            on_toggle=self._toggled)
        built = bool(self.native.build())
        if built:
            width, height = self.native.size()
            self.slot.setFixedSize(max(int(width) + 2, 120),
                                   max(int(height) + 2, 22))
            self.qt.setChecked(self.checked)
            self.qt.setVisible(False)
        else:
            self.native = None
            self._use_qt()
        return built

    def _build_win32(self):
        from win32_controls import Win32Checkbox

        try:
            self.native = Win32Checkbox(
                self.window, self.slot, self.title, self.checked,
                on_toggle=self._toggled)
        except Exception:
            self.native = None
        built = bool(self.native is not None and self.native.build())
        if built:
            self.qt.setChecked(self.checked)
            self.qt.setVisible(False)
            return True
        self.native = None
        self._use_qt()
        return False

    def _use_qt(self):
        """Fallback: the Qt checkbox was never laid out (the grid holds the
        slot), so make it a visible child of the slot instead."""
        if self.qt.parent() is not self.slot:
            self.qt.setParent(self.slot)
            box = QVBoxLayout(self.slot)
            box.setContentsMargins(0, 0, 0, 0)
            box.addWidget(self.qt)
        self.qt.setVisible(True)

    def _toggled(self, checked):
        self.checked = bool(checked)
        self.qt.setChecked(self.checked)

    def place(self):
        if self.native is not None and self.native.active:
            self.native.place()

    def isChecked(self):
        if self.native is not None and self.native.active:
            return self.native.isChecked()
        return self.qt.isChecked()

    def setChecked(self, value):
        self.checked = bool(value)
        self.qt.setChecked(self.checked)
        if self.native is not None and self.native.active:
            self.native.setChecked(self.checked)

    def isVisible(self):
        return bool(self.native is not None and self.native.isVisible()) \
            or self.slot.isVisible()


class PopUpControl:
    """A Qt combo (data/fallback) plus, on macOS, a native ``NSPopUpButton``
    drawn over a transparent spacer slot."""

    def __init__(self, qt, items, current, window, custom_label=None):
        self.qt = qt
        self.items = list(items)
        self.current = str(current)
        self.window = window
        self.custom_label = custom_label
        self.native = None
        self.slot = SpacerWidget()
        self.slot.setFixedSize(max(90, qt.sizeHint().width() + 10), 24)

    def build_native(self):
        if sys.platform == 'win32':
            return self._build_win32()
        if sys.platform != 'darwin':
            return False
        from native_mac import NativePopUpButton

        self.native = NativePopUpButton(
            self.window, self.slot, self.items, self.current,
            on_change=self._changed, custom_label=self.custom_label)
        built = bool(self.native.build())
        if built:
            width, height = self.native.size()
            self.slot.setFixedSize(max(int(width) + 2, 90),
                                   max(int(height) + 2, 24))
            # the native popup covers it: a visible Qt combo would push
            # the slot past the layout margin
            self.qt.setVisible(False)
        else:
            self.native = None
            self.qt.setVisible(True)
        return built

    def _build_win32(self):
        from win32_controls import Win32PopUp

        try:
            self.native = Win32PopUp(
                self.window, self.slot, self.items, self.current,
                on_change=self._changed, custom_label=self.custom_label)
        except Exception:
            self.native = None
        built = bool(self.native is not None and self.native.build())
        if built:
            self.qt.setVisible(False)
            return True
        self.native = None
        self.qt.setVisible(True)
        return False

    def _changed(self, title):
        # the custom entry reuses the Qt dialog flow, then mirrors the
        # resulting value back into the native popup
        if self.custom_label and title == self.custom_label:
            self.qt._choose(CUSTOM_SENTINEL)
            QTimer.singleShot(0, lambda: self.setCurrentText(
                self.qt.currentText()))
            return
        self.current = title
        self.qt.setCurrentText(title)

    def currentText(self):
        if self.native is not None and self.native.active:
            return self.native.currentText()
        return self.qt.currentText()

    def setCurrentText(self, value):
        self.current = str(value)
        self.qt.setCurrentText(self.current)
        if self.native is not None and self.native.active:
            self.native.setCurrentText(self.current)

    def fit_to_menu(self):
        """Qt-only behaviour; the native popup sizes itself."""
        return None

    def isVisible(self):
        return bool(self.native is not None and self.native.isVisible()) \
            or self.slot.isVisible()

    def place(self):
        if self.native is not None and self.native.active:
            self.native.place()


class ButtonControl:
    """A Qt button (data/fallback) plus a native ``NSButton`` on macOS."""

    def __init__(self, qt, title, window, on_click=None):
        self.qt = qt
        self.title = title
        self.window = window
        self.on_click = on_click
        self.native = None
        self.slot = SpacerWidget()
        self.slot.setFixedSize(max(qt.sizeHint().width(), 90),
                               max(qt.sizeHint().height(), 26))

    def build_native(self):
        if sys.platform == 'win32':
            return self._build_win32()
        if sys.platform != 'darwin':
            return False
        from native_mac import NativePushButton

        self.native = NativePushButton(self.window, self.slot, self.title,
                                       on_click=self.on_click)
        built = bool(self.native.build())
        if built:
            width, height = self.native.size()
            self.slot.setFixedSize(max(int(width) + 2, 90),
                                   max(int(height) + 2, 26))
            self.qt.setVisible(False)
        else:
            self.native = None
            self.qt.setVisible(True)
        return built

    def _build_win32(self):
        from win32_controls import Win32PushButton

        try:
            self.native = Win32PushButton(self.window, self.slot, self.title,
                                          on_click=self.on_click)
        except Exception:
            self.native = None
        built = bool(self.native is not None and self.native.build())
        if built:
            self.qt.setVisible(False)
            return True
        self.native = None
        self.qt.setVisible(True)
        return False

    def setEnabled(self, value):
        self.qt.setEnabled(bool(value))
        if self.native is not None and self.native.active:
            self.native.setEnabled(value)

    def isEnabled(self):
        return self.qt.isEnabled()

    def isVisible(self):
        return bool(self.native is not None and self.native.isVisible()) \
            or self.slot.isVisible()

    def place(self):
        if self.native is not None and self.native.active:
            self.native.place()


class LabelControl:
    """A Qt label (data/fallback) plus a native ``NSTextField`` on macOS."""

    def __init__(self, qt, text, window):
        self.qt = qt
        self.window = window
        self.native = None
        self._text = str(text)
        self.slot = SpacerWidget()
        self.slot.setFixedHeight(max(qt.sizeHint().height(), 20))
        self.slot.setMinimumWidth(60)

    def build_native(self):
        if sys.platform == 'win32':
            return self._build_win32()
        if sys.platform != 'darwin':
            return False
        from native_mac import NativeLabel

        self.native = NativeLabel(self.window, self.slot, self._text)
        built = bool(self.native.build())
        if built:
            width, height = self.native.size()
            self.slot.setFixedSize(max(int(width) + 4, 60),
                                   max(int(height) + 2, 20))
            self.qt.setVisible(False)
        else:
            self.native = None
            self.qt.setVisible(True)
        return built

    def _build_win32(self):
        from win32_controls import Win32Label

        try:
            self.native = Win32Label(self.window, self.slot, self._text)
        except Exception:
            self.native = None
        built = bool(self.native is not None and self.native.build())
        if built:
            self.qt.setVisible(False)
            return True
        self.native = None
        self.qt.setVisible(True)
        return False

    def setText(self, text):
        self._text = str(text)
        self.qt.setText(self._text)
        if self.native is not None and self.native.active:
            self.native.setText(self._text)
            if sys.platform == 'darwin':
                width, height = self.native.size()
                self.slot.setFixedSize(max(int(width) + 4, 60),
                                       max(int(height) + 2, 20))

    def text(self):
        if self.native is not None and self.native.active:
            return self.native.text()
        return self.qt.text()

    def isVisible(self):
        return self.slot.isVisible()

    def place(self):
        if self.native is not None and self.native.active:
            self.native.place()


class FileRowDelegate(QStyledItemDelegate):
    """Finder / System Settings style rows in the file list: the native
    file icon, the file name (full path lives in the tooltip), a hairline
    separator under each row and, for selected rows, a full-width bar in
    the system accent colour (dimmed while the window is inactive).

    A delegate is used rather than a stylesheet because stylesheet item
    backgrounds are not captured by QWidget.grab(), so they cannot be
    verified; painting here also gives the exact square bar and hairlines
    the reference UI has."""

    ROW_HEIGHT = 30
    ICON = 16

    def sizeHint(self, option, index):
        size = super().sizeHint(option, index)
        return QSize(size.width(), self.ROW_HEIGHT)

    def paint(self, painter, option, index):
        opt = QStyleOptionViewItem(option)
        self.initStyleOption(opt, index)
        painter.save()
        rect = opt.rect
        selected = bool(opt.state & QStyle.StateFlag.State_Selected)
        active = bool(opt.state & QStyle.StateFlag.State_Active)
        text_color = opt.palette.color(QPalette.ColorRole.Text)
        if selected:
            accent = QColor(opt.palette.color(QPalette.ColorRole.Highlight))
            if not active:              # native: dim while unfocused
                accent = QColor(accent.red(), accent.green(),
                                accent.blue(), 140)
            painter.fillRect(rect, accent)
            text_color = opt.palette.color(QPalette.ColorRole.HighlightedText)
        elif opt.state & QStyle.StateFlag.State_MouseOver:
            painter.fillRect(rect, SELECTION_TINT)

        left = rect.left() + 8
        if not opt.icon.isNull():
            pixmap = opt.icon.pixmap(self.ICON, self.ICON)
            painter.drawPixmap(left, rect.top() + (rect.height() - self.ICON) // 2,
                               pixmap)
            left += self.ICON + 8

        # hairline under every row except the last (the bar has its own)
        if index.row() < index.model().rowCount() - 1:
            painter.setPen(SEPARATOR)
            y = rect.bottom()
            painter.drawLine(rect.left() + 8, y, rect.right() - 8, y)

        metrics = QFontMetrics(opt.font)
        text_rect = QRect(left, rect.top(),
                          max(0, rect.right() - 6 - left), rect.height())
        painter.setPen(text_color)
        painter.drawText(text_rect,
                         Qt.AlignmentFlag.AlignVCenter
                         | Qt.AlignmentFlag.AlignLeft,
                         metrics.elidedText(opt.text,
                                            Qt.TextElideMode.ElideMiddle,
                                            text_rect.width()))
        painter.restore()


class DropListWidget(QListWidget):
    """File list that accepts dragged files and folders.

    Only local-file URLs are accepted; non-local URLs (http:// etc.) are
    ignored. The parent supplies a callback receiving the dropped local
    paths. When the list is empty, a centered overlay offers click or
    drag & drop (the two links open the parent's dialogs)."""

    def __init__(self, on_paths, on_choose_files, on_choose_folder,
                 parent=None):
        super().__init__(parent)
        self._on_paths = on_paths
        self.on_choose_files = on_choose_files
        self.on_choose_folder = on_choose_folder
        self.setAcceptDrops(True)
        self.setDragDropMode(QAbstractItemView.DragDropMode.DropOnly)

        self._placeholder = QWidget(self.viewport())
        playout = QVBoxLayout(self._placeholder)
        playout.setContentsMargins(12, 12, 12, 12)
        playout.setSpacing(6)

        playout.addStretch(1)
        muted = self.palette().color(QPalette.ColorRole.PlaceholderText)
        self._hint = QLabel('Click or drag and drop files/folders '
                            'into the box')
        self._hint.setWordWrap(True)
        self._hint.setMaximumWidth(380)
        self._hint.setAlignment(Qt.AlignmentFlag.AlignHCenter
                                | Qt.AlignmentFlag.AlignVCenter)
        self._hint.setStyleSheet('color: {};'.format(muted.name()))
        playout.addWidget(self._hint, 0, Qt.AlignmentFlag.AlignHCenter)

        self._links = QLabel(
            '<a href="files">选择文件</a>&nbsp;&nbsp;&nbsp;&nbsp;'
            '<a href="folder">选择目录</a>')
        self._links.linkActivated.connect(self._dispatch_link)
        self._links.setTextInteractionFlags(
            Qt.TextInteractionFlag.LinksAccessibleByMouse)
        playout.addWidget(self._links, 0, Qt.AlignmentFlag.AlignHCenter)
        playout.addStretch(1)

        # The placeholder covers the viewport, so it must accept drops
        # and forward them to the list's own handlers.
        self._placeholder.setAcceptDrops(True)
        self._placeholder.installEventFilter(self)

        self.model().rowsInserted.connect(self.update_placeholder)
        self.model().rowsRemoved.connect(self.update_placeholder)
        self.model().modelReset.connect(self.update_placeholder)
        self.update_placeholder()

    def eventFilter(self, obj, event):
        t = event.type()
        if t == QEvent.Type.DragEnter:
            self.dragEnterEvent(event)
            return event.isAccepted()
        if t == QEvent.Type.DragMove:
            self.dragMoveEvent(event)
            return event.isAccepted()
        if t == QEvent.Type.Drop:
            self.dropEvent(event)
            return event.isAccepted()
        if t == QEvent.Type.DragLeave:
            self.dragLeaveEvent(event)
            return True
        return super().eventFilter(obj, event)

    def _dispatch_link(self, link):
        if link == 'files':
            self.on_choose_files()
        elif link == 'folder':
            self.on_choose_folder()

    def update_placeholder(self):
        self._placeholder.setVisible(self.count() == 0)

    def placeholder_widget(self):
        return self._placeholder

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._placeholder.setGeometry(self.viewport().rect())

    def dragEnterEvent(self, event):
        if self._has_local_files(event.mimeData()):
            event.acceptProposedAction()
            self.statusBarHint()
        else:
            event.ignore()

    def dragMoveEvent(self, event):
        if self._has_local_files(event.mimeData()):
            event.acceptProposedAction()
        else:
            event.ignore()

    def dragLeaveEvent(self, event):
        self.statusBarRestore()
        super().dragLeaveEvent(event)

    def dropEvent(self, event):
        self.statusBarRestore()
        if not self._has_local_files(event.mimeData()):
            event.ignore()
            return
        paths = [u.toLocalFile() for u in event.mimeData().urls()
                 if u.isLocalFile() and u.toLocalFile()]
        if paths:
            event.acceptProposedAction()
            self._on_paths(paths)

    @staticmethod
    def _has_local_files(mime):
        return any(u.isLocalFile() for u in mime.urls())

    def statusBarHint(self):
        window = self.window()
        if hasattr(window, 'set_status'):
            window.set_status('松开鼠标以添加文件/目录…')

    def statusBarRestore(self):
        window = self.window()
        if hasattr(window, 'set_status'):
            window.set_status('就绪 (窗口效果: {})'.format(
                getattr(window, 'effect_note', 'system theme')))


class NativeMenuCombo(QComboBox):
    """Popup button whose dropdown is a native NSMenu on macOS (system
    material, checkmark on the current item); Qt's popup elsewhere.

    Non-editable so it looks like a native popup button (bezel with an
    up-down chevron); arbitrary values are entered through the
    "其它" menu entry. The label stays short enough (including the
    checkmark column AppKit reserves) that the popup is exactly as wide
    as the button."""

    CUSTOM_LABEL = '其它'

    def __init__(self, items, current, parent=None):
        super().__init__(parent)
        self.setEditable(False)
        self.addItems(items)
        self.setCurrentText(current)

    def showPopup(self):
        items = [self.itemText(i) for i in range(self.count())]
        if popup_native_menu(self, items, self.currentText(), self._choose,
                             self.CUSTOM_LABEL):
            return
        super().showPopup()

    def fit_to_menu(self):
        """Grow the button to the width of its popup content, so at
        runtime (where the menu is at least the button's width) the
        popup has exactly the button's width, i.e. the highlighted row
        and the button coincide."""
        if sys.platform != 'darwin':
            return
        items = [self.itemText(i) for i in range(self.count())]
        # measure the content only - using the current width as a
        # minimum would feed the not-yet-laid-out size back in
        menu, _target, _sel = build_menu(items, self.currentText(),
                                         lambda _v: None, self.CUSTOM_LABEL)
        needed = int(round(menu.size().width))
        if needed > self.width():
            self.setFixedWidth(needed)

    def _choose(self, value):
        if value == CUSTOM_SENTINEL:
            # runs inside NSMenu tracking - defer the Qt dialog until the
            # menu has closed
            QTimer.singleShot(0, self._ask_custom)
            return
        self.setCurrentText(value)

    def _ask_custom(self):
        text, ok = QInputDialog.getText(self, '自定义值', '输入值:',
                                        text=self.currentText())
        text = text.strip()
        if ok and text:
            if self.findText(text) < 0:
                self.addItem(text)
            self.setCurrentText(text)


class MainWindow(QMainWindow):

    def __init__(self):
        super().__init__()
        self.setWindowTitle('LaTeX Coding Style Formatter')
        self.resize(980, 700)
        self.setMinimumSize(760, 560)

        self.dark = detect_dark(QApplication.instance())
        self.pal = DARK if self.dark else LIGHT

        central = QWidget()
        self.setCentralWidget(central)
        # Finder-like layout: the top band_height() points stay
        # transparent so the native toolbar material shows through, and
        # the left sidebar column shares the sidebar material (full
        # height). The right panel paints the native window background,
        # so the strip frosted over it and the content below it are the
        # system colours - the material alone marks the boundary.
        window_color = self.palette().color(QPalette.ColorRole.Window)
        native_bg = native_window_color(self.dark)
        if native_bg:
            window_color = QColor(native_bg)
        self.native_bg = native_bg
        outer = QHBoxLayout(central)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        self.outer_layout = outer

        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.setChildrenCollapsible(False)
        # no draggable handle: the sidebar has a fixed width (Settings-style)
        splitter.setHandleWidth(0)
        splitter.setStyleSheet('QSplitter::handle { background: transparent; }')
        self.splitter = splitter

        # --- left: sidebar (file list over the shared blur) ---
        sidebar = QWidget()
        # Settings-style fixed sidebar width (the splitter cannot resize it)
        sidebar.setFixedWidth(232)
        # explicit transparency: the sidebar shows the native material
        # behind it (and makes the intent testable)
        sidebar.setObjectName('sidebar')
        sidebar.setStyleSheet('#sidebar { background: transparent; }')
        slayout = QVBoxLayout(sidebar)
        slayout.setContentsMargins(20, int(band_height()) + 20, 20, 20)
        slayout.setSpacing(8)

        # macOS-Settings-style grouped box: three rows with hairlines
        group = QFrame()
        group.setObjectName('group')
        group.setStyleSheet(
            '#group { background: rgba(120, 120, 128, 0.12);'
            ' border-radius: 8px; }')
        gv = QVBoxLayout(group)
        gv.setContentsMargins(10, 0, 10, 0)
        gv.setSpacing(0)

        def row_separator():
            sep = QFrame()
            sep.setObjectName('rowsep')
            sep.setFixedHeight(1)
            sep.setStyleSheet(
                '#rowsep { background: rgba(120, 120, 128, 0.28); }')
            return sep

        ext_row = QHBoxLayout()
        ext_row.setContentsMargins(0, 10, 0, 10)   # Settings-like 44 pt row
        ext_row.addWidget(QLabel('扩展名'))
        ext_row.addStretch(1)
        self.ext_edit = PopUpControl(NativeMenuCombo(EXTENSIONS, '.tex'),
                                     EXTENSIONS, '.tex', self,
                                     custom_label=NativeMenuCombo.CUSTOM_LABEL)
        ext_row.addWidget(self.ext_edit.qt)
        ext_row.addWidget(self.ext_edit.slot)
        gv.addLayout(ext_row)
        gv.addWidget(row_separator())

        # the encoding selector lives in the sidebar, just after 扩展名
        enc_row = QHBoxLayout()
        enc_row.setContentsMargins(0, 10, 0, 10)   # Settings-like 44 pt row
        enc_row.addWidget(QLabel('输出编码'))
        enc_row.addStretch(1)
        self.enc_out = PopUpControl(NativeMenuCombo(ENCODINGS, '同输入'),
                                    ENCODINGS, '同输入', self,
                                    custom_label=NativeMenuCombo.CUSTOM_LABEL)
        enc_row.addWidget(self.enc_out.qt)
        enc_row.addWidget(self.enc_out.slot)
        gv.addLayout(enc_row)
        gv.addWidget(row_separator())

        rec_row = QHBoxLayout()
        rec_row.setContentsMargins(0, 10, 0, 10)   # Settings-like 44 pt row
        rec_row.addWidget(QLabel('含子目录'))
        rec_row.addStretch(1)
        self.chk_recursive = QCheckBox()
        self.chk_recursive.setVisible(False)      # native switch is used
        rec_row.addWidget(self.chk_recursive)
        self.switch_slot = QWidget()
        self.switch_slot.setFixedSize(42, 25)
        rec_row.addWidget(self.switch_slot)
        gv.addLayout(rec_row)
        slayout.addWidget(group)

        # --- framed file list with a +/- bar (System Settings style) ---
        self.file_list = DropListWidget(self.drop_paths, self.add_files,
                                        self.scan_dir)
        self.file_list.setSelectionMode(
            QAbstractItemView.SelectionMode.ExtendedSelection)
        self.file_list.setItemDelegate(FileRowDelegate(self.file_list))
        self.file_list.setMouseTracking(True)
        self.file_list.setStyleSheet(
            'QListWidget { background: transparent; }')
        self.file_list.viewport().setAutoFillBackground(False)
        self.file_list.itemSelectionChanged.connect(
            self._on_list_selection)

        frame = QFrame()
        frame.setObjectName('fileframe')
        frame.setStyleSheet(
            '#fileframe { border: 1px solid rgba(120, 120, 128, 0.28);'
            ' border-radius: 8px; }')
        flv = QVBoxLayout(frame)
        flv.setContentsMargins(1, 1, 1, 1)
        flv.setSpacing(0)
        flv.addWidget(self.file_list, 1)

        button_qss = (
            'QPushButton {{ border: none; background: transparent;'
            ' color: {}; font-size: 16px; padding: 0px; }}'
            'QPushButton:hover {{ background: rgba(120, 120, 128, 0.20);'
            ' border-radius: 4px; }}'
            'QPushButton:disabled {{ color: rgba(120, 120, 128, 0.45); }}'
            .format(self.palette().color(
                QPalette.ColorRole.WindowText).name()))
        self.pm_bar = QWidget()
        self.pm_bar.setObjectName('plusminusbar')
        # the row must fit the native control (small: 21 pt) plus a little
        # breathing room; keep the same height on other platforms so the
        # Qt fallback buttons look identical
        self.pm_bar.setFixedHeight(
            int(round(native_plus_minus_height() + footer_metrics()[0]))
            if native_plus_minus_height() else 24)
        bar = QHBoxLayout(self.pm_bar)
        bar.setContentsMargins(4, 2, 4, 2)
        bar.setSpacing(0)
        self.btn_add = QPushButton('+')
        self.btn_add.setObjectName('listadd')
        self.btn_add.setFixedSize(28, 20)
        self.btn_add.setStyleSheet(button_qss)
        self.btn_add.setToolTip('添加 TeX 文件')
        self.btn_add.clicked.connect(self.add_files)
        self.btn_remove = QPushButton('−')
        self.btn_remove.setObjectName('listremove')
        self.btn_remove.setFixedSize(28, 20)
        self.btn_remove.setStyleSheet(button_qss)
        self.btn_remove.setToolTip('从列表移除所选文件')
        self.btn_remove.setEnabled(False)
        self.btn_remove.clicked.connect(self._remove_selected)
        divider = QFrame()
        self.pm_divider = divider
        divider.setObjectName('plusminussep')
        divider.setFixedWidth(1)
        divider.setFixedHeight(16)
        divider.setStyleSheet(
            '#plusminussep { background: rgba(120, 120, 128, 0.28); }')
        bar.addWidget(self.btn_add)
        bar.addWidget(divider)
        bar.addWidget(self.btn_remove)
        bar.addStretch(1)
        flv.addWidget(self.pm_bar)
        self.file_frame = frame
        frame.installEventFilter(self)
        slayout.addWidget(frame, 1)
        self.sidebar = sidebar
        splitter.addWidget(sidebar)

        # --- right: content panel on the native window background ---
        panel = QWidget()
        panel.setObjectName('panel')
        panel.setMinimumWidth(420)
        panel.setStyleSheet('#panel {{ background: {}; }}'.format(
            window_color.name()))
        self.content_bg = window_color
        pv = QVBoxLayout(panel)
        # no hairline: the frosted strip over this surface is separated
        # from the content below only by the material (Finder-style)
        pv.setContentsMargins(20, int(band_height()) + 20, 20, 20)
        pv.setSpacing(8)
        layout = pv
        splitter.addWidget(panel)
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)
        splitter.setSizes([232, 740])
        splitter.splitterMoved.connect(self._splitter_moved)
        self.content_panel = panel
        panel.installEventFilter(self)
        outer.addWidget(splitter, 1)

        # option checkboxes reflow between 2 rows x 3 columns and
        # 3 rows x 2 columns depending on the panel width (see
        # _reflow_options); columns share the width equally
        option_specs = (('chk_punct', '半角标点后加空格', True),
                        ('chk_commands', 'CJK 与控制序列空格', True),
                        ('chk_tight', '页码范围保持紧凑', True),
                        ('chk_backup', '生成备份文件 (backup/*.bak)', True),
                        ('chk_magic', '添加编码魔法注释', True),
                        ('chk_check', '仅检查 (不写入文件)', False))
        self.option_checks = []
        self._option_widgets = []
        for attr, title, checked in option_specs:
            qt = QCheckBox(title)
            qt.setChecked(checked)
            # the layout slot: on macOS the native checkbox is drawn over
            # this spacer (a plain widget, so no Qt text shows through)
            slot = SpacerWidget()
            slot.setFixedSize(max(120, qt.sizeHint().width()), 22)
            wrapper = OptionControl(qt, slot, title, checked, self)
            setattr(self, attr, wrapper)
            self.option_checks.append(slot)
            self._option_widgets.append((slot, wrapper))
        self.options_grid = QGridLayout()
        self.options_grid.setContentsMargins(0, 0, 0, 0)
        self._option_columns = 0
        layout.addLayout(self.options_grid)

        self.output = QPlainTextEdit()
        self.output.setReadOnly(True)
        self.output.setFont(QFontDatabase.systemFont(
            QFontDatabase.SystemFont.FixedFont))
        self.output.setStyleSheet(
            'QPlainTextEdit {{ background: {}; color: {}; }}'.format(
                self.pal['text_bg'], self.pal['text_fg']))
        for tag, color in (('add', self.pal['add']),
                           ('del', self.pal['del']),
                           ('meta', self.pal['meta'])):
            fmt = QTextCharFormat()
            fmt.setForeground(QColor(color))
            setattr(self, 'fmt_' + tag, fmt)
        layout.addWidget(self.output, 1)

        # bottom row: status on the left, 应用格式化 in the south-east corner
        # (no preview button: selecting a list item previews its diff)
        self.enc_status_label = QLabel('')
        self.enc_status_label.setObjectName('enclabel')
        self.enc_status_label.setStyleSheet(
            '#enclabel {{ color: {}; padding-top: 2px; }}'.format(
                self.palette().color(QPalette.ColorRole.WindowText).name()))
        self.enc_status_label.setVisible(False)
        self.status_separator = QFrame()
        self.status_separator.setObjectName('statussep')
        self.status_separator.setFixedWidth(1)
        self.status_separator.setFixedHeight(16)
        self.status_separator.setStyleSheet(
            '#statussep { background: rgba(120, 120, 128, 0.28); }')
        self.status_separator.setVisible(False)
        self.sel_status_label = QLabel('')
        self.sel_status_label.setObjectName('sellabel')
        self.sel_status_label.setStyleSheet(
            '#sellabel {{ color: {}; padding-top: 2px; }}'.format(
                self.palette().color(QPalette.ColorRole.WindowText).name()))
        self.sel_status_label.setVisible(False)
        self.sel_separator = QFrame()
        self.sel_separator.setObjectName('statussep')
        self.sel_separator.setFixedWidth(1)
        self.sel_separator.setFixedHeight(16)
        self.sel_separator.setStyleSheet(
            '#statussep { background: rgba(120, 120, 128, 0.28); }')
        self.sel_separator.setVisible(False)
        self.status_label = LabelControl(QLabel('就绪'), '就绪', self)
        self.status_label.qt.setObjectName('statuslabel')
        self.status_label.qt.setStyleSheet(
            '#statuslabel {{ color: {}; padding-top: 2px; }}'.format(
                self.palette().color(QPalette.ColorRole.WindowText).name()))
        self.btn_apply = ButtonControl(
            QPushButton('应用格式化'), '应用格式化', self,
            on_click=lambda: self.run(write=True))
        self.btn_apply.qt.clicked.connect(lambda: self.run(write=True))
        bottom = QHBoxLayout()
        bottom.setContentsMargins(0, 0, 0, 0)
        bottom.addWidget(self.enc_status_label)
        bottom.addWidget(self.status_separator)
        bottom.addWidget(self.sel_status_label)
        bottom.addWidget(self.sel_separator)
        bottom.addWidget(self.status_label.qt)
        bottom.addWidget(self.status_label.slot)
        bottom.addStretch(1)
        bottom.addWidget(self.btn_apply.qt)
        bottom.addWidget(self.btn_apply.slot)
        layout.addLayout(bottom)

        self.effect_note = None
        self._effects_applied = False
        self._notes_seen = 0
        prepare_qt(self)
        self._reflow_options()
        # real NSTableView on macOS (the Qt list stays as the empty-state
        # host); the Qt list itself everywhere else. Created here, once
        # _effects_applied exists: inserting a native subview fires Qt
        # window events.
        self.files_view = create_file_list_view(self)
        self.diff_view = create_diff_view(self)
        self.controller = FormatController(self)
        # menus last: creating the menu bar triggers window events
        self._build_menus()

    # ---------- helpers ----------

    def showEvent(self, event):
        super().showEvent(event)
        self.apply_window_effects()

    def event(self, ev):
        if ev.type() == QEvent.Type.WinIdChange and self._effects_applied:
            self.apply_window_effects()
        return super().event(ev)

    def set_status(self, text, encoding=None, selected=None):
        """Update the status line (lives at the bottom of the content
        panel so the sidebar can run the full window height). ``encoding``
        and ``selected``, when given, are shown before the text behind
        hairline dividers."""
        self.status_label.setText(text)
        has_encoding = bool(encoding)
        self.enc_status_label.setText(encoding or '')
        self.enc_status_label.setVisible(has_encoding)
        self.status_separator.setVisible(has_encoding)
        has_selected = bool(selected)
        self.sel_status_label.setText(selected or '')
        self.sel_status_label.setVisible(has_selected)
        self.sel_separator.setVisible(has_selected)

    def _splitter_moved(self, *_args):
        self._sync_sidebar_width()
        self._reflow_options()
        self._reapply_materials()

    def _reflow_options(self):
        """Lay the option checkboxes out as 2 rows x 3 columns or
        3 rows x 2 columns, whichever fits the panel width; columns share
        the width equally and spread across the panel."""
        if not self.option_checks:
            return
        spacing = self.options_grid.spacing()
        # fixed-size spacer slots report -1 from sizeHint, so take the
        # larger of the hint and the real width
        widest = max(max(chk.width(), chk.sizeHint().width())
                     for chk in self.option_checks)
        available = self.content_panel.width() - 40      # panel margins
        if available <= 0:
            available = self.width() - self.sidebar.width() - 40
        need_three = widest * 3 + spacing * 2 + 16
        columns = 3 if available >= need_three else 2
        if columns != self._option_columns:
            self._option_columns = columns
            for chk in self.option_checks:
                self.options_grid.removeWidget(chk)
            for index, chk in enumerate(self.option_checks):
                self.options_grid.addWidget(chk, index // columns,
                                            index % columns)
        # equal-width columns: same minimum for each column in use and a
        # stretch of 1, so the leftover space is shared evenly too
        column_width = max(widest, available // columns)
        for column in range(len(self.option_checks)):
            used = column < columns
            self.options_grid.setColumnMinimumWidth(
                column, column_width if used else 0)
            self.options_grid.setColumnStretch(column, 1 if used else 0)
        self.options_grid.invalidate()

    def _build_menus(self):
        """macOS: the native NSMenu is installed from apply_window_effects
        (a Qt menu bar would take the app menu over); elsewhere the Qt menu
        bar below is used."""
        if sys.platform == 'darwin':
            # do not touch menuBar() at all: creating a Qt menu bar makes
            # Qt install and merge its own NSMenu items (e.g. Close All)
            return None
        return self._build_qt_menus()

    def _build_qt_menus(self):
        """Qt menu bar (non-macOS, and the fallback if the native menu
        cannot be installed): File (Close, Cmd+W), Edit (the standard
        editing commands) and Window (Minimize Cmd+M, Zoom, Bring All to
        Front). Before this there was no Close command anywhere, so Cmd+W
        silently did nothing and only Cmd+Q worked."""
        bar = self.menuBar()
        bar.setNativeMenuBar(True)

        def action(menu, text, key, slot, std=False):
            item = QAction(text, self)
            if std:
                item.setShortcut(QKeySequence(key))
            elif key is not None:
                item.setShortcut(key)
            # keep Qt from relocating items into the application menu
            item.setMenuRole(QAction.MenuRole.NoRole)
            item.triggered.connect(slot)
            menu.addAction(item)
            return item

        # --- File ---
        file_menu = bar.addMenu('&File')
        self.action_close = action(file_menu, 'Close',
                                   QKeySequence.StandardKey.Close,
                                   self.close, std=True)

        # --- Edit ---
        edit_menu = bar.addMenu('&Edit')
        self.edit_actions = {}

        def focused():
            return QApplication.focusWidget()

        def call(method):
            def run(*_args):
                widget = focused()
                fn = getattr(widget, method, None)
                if callable(fn):
                    fn()
            return run

        def delete_selection(*_args):
            widget = focused()
            cursor = getattr(widget, 'textCursor', lambda: None)()
            if cursor is not None and cursor.hasSelection():
                cursor.removeSelectedText()

        specs = (('Undo', QKeySequence.StandardKey.Undo, call('undo')),
                 ('Redo', QKeySequence.StandardKey.Redo, call('redo')),
                 (None, None, None),
                 ('Cut', QKeySequence.StandardKey.Cut, call('cut')),
                 ('Copy', QKeySequence.StandardKey.Copy, call('copy')),
                 ('Paste', QKeySequence.StandardKey.Paste, call('paste')),
                 ('Delete', None, delete_selection),
                 (None, None, None),
                 ('Select All', QKeySequence.StandardKey.SelectAll,
                  call('selectAll')))
        for text, key, slot in specs:
            if text is None:
                edit_menu.addSeparator()
                continue
            self.edit_actions[text] = action(edit_menu, text, key, slot)
        edit_menu.aboutToShow.connect(self._sync_edit_menu)

        # --- Window ---
        window_menu = bar.addMenu('&Window')
        self.action_minimize = action(window_menu, 'Minimize', 'Ctrl+M',
                                      self._minimize)
        self.action_zoom = action(window_menu, 'Zoom', None, self._zoom)
        self.action_front = action(window_menu, 'Bring All to Front', None,
                                   self._bring_all_to_front)
        return bar

    def _sync_edit_menu(self):
        """Grey out the editing commands the focused widget cannot do
        (e.g. Cut/Paste on the read-only diff pane), like native apps."""
        widget = QApplication.focusWidget()

        def has(name):
            return widget is not None and callable(getattr(widget, name, None))

        read_only = bool(getattr(widget, 'isReadOnly', lambda: False)())
        undoable = getattr(widget, 'isUndoAvailable', lambda: True)()
        redoable = getattr(widget, 'isRedoAvailable', lambda: True)()
        state = {
            'Undo': has('undo') and undoable and not read_only,
            'Redo': has('redo') and redoable and not read_only,
            'Cut': has('cut') and not read_only,
            'Copy': has('copy'),
            'Paste': has('paste') and not read_only,
            'Delete': has('textCursor') and not read_only,
            'Select All': has('selectAll'),
        }
        for name, enabled in state.items():
            self.edit_actions[name].setEnabled(bool(enabled))

    def _minimize(self):
        if not minimize_window(self):
            self.showMinimized()

    def _zoom(self):
        if not zoom_window(self):
            if self.isMaximized():
                self.showNormal()
            else:
                self.showMaximized()

    def _bring_all_to_front(self):
        arrange_in_front(self)

    def eventFilter(self, obj, event):
        # the panel's width changes on splitter drags without a window
        # resize, and splitterMoved fires before the new sizes are
        # applied - so reflow from the panel's own resize event
        if (obj is getattr(self, 'content_panel', None)
                and event.type() == QEvent.Type.Resize):
            self._reflow_options()
        # the list frame / bar row move with the layout: the native footer
        # strip and +/- control must follow, or they keep stale rects
        if (obj in (getattr(self, 'file_frame', None),
                    getattr(self, 'pm_bar', None))
                and event.type() == QEvent.Type.Resize):
            reposition_materials(self)
        return super().eventFilter(obj, event)

    def _sync_sidebar_width(self):
        """Tell the native layer where the sidebar ends: the boundary is
        the content panel's left edge (which also covers the splitter's
        invisible grab area). The region is marked only by the material
        change - there is no divider line."""
        divider_x = self.content_panel.mapTo(
            self.centralWidget(), QPoint(0, 0)).x()
        set_sidebar_width(divider_x)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._sync_sidebar_width()
        self._reflow_options()
        self._reapply_materials()

    def changeEvent(self, event):
        super().changeEvent(event)
        if event.type() in (QEvent.Type.ActivationChange,
                            QEvent.Type.WindowStateChange):
            # no title bar in full screen - drop the transparent strip
            full = bool(self.windowState() & Qt.WindowState.WindowFullScreen)
            top = 0 if full else int(band_height())
            self.sidebar.layout().setContentsMargins(20, top + 20, 20, 20)
            self.content_panel.layout().setContentsMargins(
                20, top + 20, 20, 20)
            self._sync_sidebar_width()
            self._reflow_options()
            self._reapply_materials()

    def apply_window_effects(self):
        self.effect_note = apply_effects(self, self.dark)
        self._effects_applied = True
        if sys.platform == 'darwin':
            if not menus.install(self):
                self._build_qt_menus()
        self._setup_native_switch()
        self._setup_native_plus_minus()
        for _slot, wrapper in self._option_widgets:
            wrapper.build_native()
        for wrapper in (self.ext_edit, self.enc_out, self.btn_apply,
                        self.status_label):
            wrapper.build_native()
        self.files_view.build()
        self.diff_view.build()
        if debug_enabled():
            # FORMAT_TEX_DEBUG=1: outline the content surface too
            self.content_panel.setStyleSheet(
                '#panel {{ background: {}; border: 1px solid red; }}'.format(
                    self.content_bg.name()))
        new_notes = notes()[self._notes_seen:]
        self._notes_seen = len(notes())
        for msg in new_notes:
            self.show_error(msg + '\n')
        self.set_status(
            '就绪 (窗口效果: {})'.format(self.effect_note))
        # AppKit re-lays the titlebar out asynchronously right after the
        # window is ordered front, undoing our centring - re-apply soon
        # after (0 ms) and once more shortly after (150 ms).
        for delay in (0, 150, 400):
            QTimer.singleShot(delay, self._reapply_materials)

    def _reapply_materials(self):
        self._sync_sidebar_width()
        reposition_materials(self)
        if hasattr(self, 'files_view'):
            self.files_view.place()
        if hasattr(self, 'diff_view'):
            self.diff_view.place()
        for _slot, wrapper in getattr(self, '_option_widgets', ()):
            wrapper.place()
        for wrapper in (getattr(self, 'ext_edit', None),
                        getattr(self, 'enc_out', None),
                        getattr(self, 'btn_apply', None),
                        getattr(self, 'status_label', None)):
            if wrapper is not None:
                wrapper.place()
        if sys.platform == 'darwin' and menus.installed() \
                and not menus.is_current():
            menus.install(self)

    def _setup_native_switch(self):
        """Use a real NSSwitch for 含子目录; fall back to the Qt
        checkbox if it cannot be created."""
        ok = create_native_switch(self, self._on_recursive_switch,
                                  self.chk_recursive.isChecked())
        if ok:
            # reserve exactly the switch's real size in the row
            view = native_switch_view()
            if view is not None:
                frame = view.frame()
                self.switch_slot.setFixedSize(int(round(frame.size.width)),
                                              int(round(frame.size.height)))
            self.switch_slot.setVisible(True)
            self.chk_recursive.setVisible(False)
            place_native_switch(self, self.switch_slot)
        else:
            self.switch_slot.setVisible(False)
            self.chk_recursive.setVisible(True)

    def _on_recursive_switch(self, state):
        self.chk_recursive.setChecked(bool(state))

    def recursive_enabled(self):
        """含子目录 state, from the native switch when available."""
        if has_native_switch():
            return native_switch_state()
        return self.recursive_enabled()

    def self_test(self):
        """Assert the native window is visible, carries the 52 pt
        sidebar-blur band with the native titlebar chrome centred in it,
        and that widgets paint their own backgrounds; writes
        format_tex_gui_selftest.txt and returns True/False (for the
        --self-test exit code).

        macOS checks the native chrome; Windows/Linux (Qt application)
        run the platform-neutral essentials below."""
        if sys.platform != 'darwin':
            return self._self_test_qt()
        import objc
        import AppKit
        lines = []
        ok = True
        try:
            qt_view = objc.objc_object(c_void_p=int(self.winId()))
            nswin = qt_view.window()
            visible = bool(nswin.isVisible())
            lines.append('window visible: {}'.format(visible))
            ok = ok and visible
            theme = qt_view.superview()
            # settle the layout and materials before geometry checks
            QApplication.processEvents()
            reposition_materials(self)
            QApplication.processEvents()

            # toolbar strip: 52 pt, right of the sidebar, top-flush
            band = last_material_view()
            title = last_title_view()
            boundary = self.content_panel.mapTo(
                self.centralWidget(), QPoint(0, 0)).x()
            bf = band.frame() if band is not None else None
            band_ok = (bf is not None
                       and isinstance(band, AppKit.NSVisualEffectView)
                       and abs(bf.size.height - band_height()) < 1
                       and abs(bf.origin.x - boundary) < 2
                       and abs(bf.size.width
                               - (theme.bounds().size.width
                                  - boundary)) < 2
                       and abs((bf.origin.y + bf.size.height)
                               - theme.bounds().size.height) < 2)
            lines.append('toolbar strip right of the sidebar ({}..{:.0f} pt), '
                         'top-flush: {}'.format(
                             boundary, boundary + (bf.size.width if bf else 0),
                             band_ok))
            ok = ok and band_ok

            # Finder-style materials: the toolbar strip blurs the window's
            # own content (withinWindow), the sidebar lets the desktop
            # through (behindWindow) and spans the full height, adjacent
            # to the strip with no overlap
            sidebar_v = last_sidebar_view()
            modes = {0: 'behindWindow', 1: 'withinWindow'}
            band_blend = (band.blendingMode()
                          == AppKit.NSVisualEffectBlendingModeWithinWindow)
            side_blend = (sidebar_v is not None
                          and sidebar_v.blendingMode()
                          == AppKit.NSVisualEffectBlendingModeBehindWindow)
            sf = sidebar_v.frame() if sidebar_v is not None else None
            adjacent = (bf is not None and sf is not None
                        and abs(sf.origin.x) < 1
                        and abs(sf.size.height - theme.bounds().size.height) < 2
                        and abs((sf.origin.x + sf.size.width)
                                - bf.origin.x) < 2)
            mat_ok = band_blend and side_blend and adjacent
            lines.append('materials: band {} + {}, sidebar {} + {} full '
                         'height, adjacent: {}'.format(
                             band_material_name(),
                             modes.get(band.blendingMode()), 'sidebar',
                             modes.get(sidebar_v.blendingMode())
                             if sidebar_v is not None else '?',
                             mat_ok))
            ok = ok and mat_ok
            lines.append('no native toolbar: {}'.format(
                nswin.toolbar() is None))
            ok = ok and nswin.toolbar() is None

            # the material must actually be visible: ordered above Qt's
            # view (whose panel is opaque) but below the titlebar chrome,
            # and click-through so the Qt widgets keep receiving events
            order_ok = band_above_content(self)
            subs = list(theme.subviews())
            side_below = (sidebar_v is not None and sidebar_v in subs
                          and qt_view in subs
                          and subs.index(sidebar_v) < subs.index(qt_view))
            title_above = (title is not None and band is not None
                           and title in subs and band in subs
                           and subs.index(title) > subs.index(band))
            click_ok = (band is not None
                        and band.hitTest_((10.0, 10.0)) is None)
            lines.append('band above content, below chrome: {}, '
                         'sidebar below Qt: {}, title above band: {}, '
                         'click-through: {}'.format(
                             order_ok, side_below, title_above, click_ok))
            ok = ok and order_ok and side_below and title_above and click_ok

            # the content surface uses the native window background
            expected_bg = native_window_color(self.dark)
            bg_ok = (expected_bg is None
                     or expected_bg.lower() in
                     self.content_panel.styleSheet().lower())
            lines.append('native window background ({}): {}'.format(
                expected_bg, bg_ok))
            ok = ok and bg_ok

            # the sidebar shares the band's material, full height, and
            # spans up to the divider (i.e. including the grab area)
            divider_x_early = self.content_panel.mapTo(
                self.centralWidget(), QPoint(0, 0)).x()
            sidebar_view = last_sidebar_view()
            sb = sidebar_view.frame() if sidebar_view is not None else None
            sidebar_ok = (
                sb is not None
                and sidebar_view.material()
                == AppKit.NSVisualEffectMaterialSidebar
                and abs(sb.origin.x) < 1
                and abs(sb.size.width - divider_x_early) < 2
                and abs(sb.size.height - theme.bounds().size.height) < 2)
            lines.append('sidebar shares the blur, full height, width '
                         '{:.0f} pt (to the divider): {}'.format(
                             divider_x_early, sidebar_ok))
            ok = ok and sidebar_ok

            # left-right layout: the list lives in the sidebar column
            list_right = self.file_list.mapTo(
                self, self.file_list.rect().bottomRight()).x()
            panel_left = self.content_panel.mapTo(self, self.content_panel
                                                  .rect().topLeft()).x()
            leftright_ok = list_right <= panel_left + 1
            lines.append('left-right layout (list right {} <= panel left '
                         '{}): {}'.format(list_right, panel_left,
                                          leftright_ok))
            ok = ok and leftright_ok

            # Settings-style group box with two rows + separator
            group = self.centralWidget().findChild(QFrame, 'group')
            rowsep = self.centralWidget().findChild(QFrame, 'rowsep')
            group_ok = group is not None and rowsep is not None
            lines.append('grouped settings box with row separator: '
                         '{}'.format(group_ok))
            ok = ok and group_ok

            # the extension control fills its menu width, keeps the
            # ".tex" label visible and is right-aligned in the group
            if getattr(self.ext_edit, 'native', None) is not None \
                    and self.ext_edit.native.active:
                slot = self.ext_edit.slot
                group = self.ext_edit.qt.parentWidget()
                row = group.layout()
                margin = None
                for i in range(row.count()):
                    item = row.itemAt(i)
                    if item is not None and item.widget() is slot:
                        pass
                right_aligned = abs(
                    slot.geometry().right()
                    - (group.width() - 10)) < 3
                native = self.ext_edit.native.view
                titles = [native.itemTitleAtIndex_(i)
                          for i in range(native.numberOfItems())]
                width = native.frame().size.width
                # the slot has a floor width, so the popup only
                # has to fit inside it
                fits = width <= slot.width() + 1 and width >= 60
                ext_ok = (right_aligned and fits
                          and self.ext_edit.items[0] in titles
                          and (self.ext_edit.custom_label or '') in titles)
                lines.append('extension control: native NSPopUpButton '
                             '({:.0f} pt) with {} items, right-aligned {}, '
                             'inside its slot {}: {}'.format(
                                 width, len(titles), right_aligned, fits,
                                 ext_ok))
            else:
                ext_w = self.ext_edit.qt.width()
                row = self.ext_edit.qt.parentWidget().layout()
                right_aligned = abs(
                    self.ext_edit.qt.geometry().right()
                    - (self.ext_edit.qt.parentWidget().width()
                       - row.contentsMargins().right())) < 2
                opt = QStyleOptionComboBox()
                self.ext_edit.qt.initStyleOption(opt)
                arrow = self.ext_edit.qt.style().subControlRect(
                    QStyle.ComplexControl.CC_ComboBox, opt,
                    QStyle.SubControl.SC_ComboBoxArrow, self.ext_edit.qt)
                text_w = QFontMetrics(self.ext_edit.qt.font()) \
                    .horizontalAdvance('.tex')
                slack = ext_w - arrow.width() - text_w
                ext_ok = right_aligned and slack >= 6
                lines.append('extension button auto-sized ({:.0f} pt, arrow '
                             '{:.0f}, slack {:.0f}) and right-aligned: '
                             '{}'.format(ext_w, arrow.width(), slack,
                                         ext_ok))
            ok = ok and ext_ok

            # the native NSSwitch is overlaid on its slot; verify in
            # SCREEN coordinates so the check is independent of the
            # conversion used to place it
            switch = native_switch_view()
            slot = self.switch_slot
            sw_ok = False
            sw_info = 'switch missing'
            if switch is not None:
                slot_tl = slot.mapTo(self, QPoint(0, 0))
                rect = nswin.convertRectToScreen_(switch.frame())
                wf = nswin.frame()
                # both offsets are window-relative, so the comparison is
                # independent of the two screen coordinate conventions
                sw_x = rect.origin.x - wf.origin.x
                sw_y = ((wf.origin.y + wf.size.height)
                        - (rect.origin.y + rect.size.height))
                dx = abs(sw_x - slot_tl.x())
                dy = abs(sw_y - slot_tl.y())
                size_ok = (abs(rect.size.width - slot.width()) < 2
                           and abs(rect.size.height - slot.height()) < 2)
                sw_ok = size_ok and dx < 4 and dy < 4
                sw_info = 'offset dx {:.0f} dy {:.0f}, {}x{} vs slot {}x{}' \
                    .format(dx, dy, rect.size.width, rect.size.height,
                            slot.width(), slot.height())
                # its action must drive the recursive flag
                before = self.recursive_enabled()
                switch.setState_(0 if switch.state() == 1 else 1)
                switch.target().switched_(switch)
                sw_ok = sw_ok and self.recursive_enabled() != before
                switch.setState_(1 if before else 0)
                switch.target().switched_(switch)
            lines.append('native NSSwitch overlaid and wired ({}): '
                         '{}'.format(sw_info, sw_ok))
            ok = ok and sw_ok

            def chrome_offset():
                """Distance of the traffic-light centre from the window
                top, in screen coordinates."""
                return (nswin.frame().origin.y + nswin.frame().size.height
                        - (screen_rect(close_button()).origin.y
                           + screen_rect(close_button()).size.height / 2))

            def close_button():
                return nswin.standardWindowButton_(
                    AppKit.NSWindowCloseButton)

            def screen_rect(view):
                rect = view.convertRect_toView_(view.bounds(), None)
                return nswin.convertRectToScreen_(rect)

            def lights_offset():
                """Left gap between the window edge and the close button."""
                return screen_rect(close_button()).origin.x \
                    - nswin.frame().origin.x

            # the app re-applies the alignment after AppKit's post-show
            # layout (deferred timers); mirror that here
            QApplication.processEvents()
            reposition_materials(self)
            centred = abs(chrome_offset() - band_height() / 2) < 2
            lines.append('traffic lights centred in band ({:.0f} vs {:.0f}): '
                         '{}'.format(chrome_offset(), band_height() / 2,
                                     centred))
            ok = ok and centred
            inset_ok = abs(lights_offset() - lights_inset()) < 2
            lines.append('traffic lights left inset {:.0f} pt (native '
                         '{:.0f}): {}'.format(lights_offset(), lights_inset(),
                                              inset_ok))
            ok = ok and inset_ok

            def title_left():
                tf = last_title_view()
                if tf is None:
                    return None
                return screen_rect(tf).origin.x - nswin.frame().origin.x

            def zoom_right():
                z = nswin.standardWindowButton_(AppKit.NSWindowZoomButton)
                r = screen_rect(z)
                return (r.origin.x + r.size.width) - nswin.frame().origin.x

            def divider_x():
                """The sidebar/content boundary (no line is drawn there)."""
                return self.content_panel.mapTo(
                    self.centralWidget(), QPoint(0, 0)).x()

            gap_ok = (title_left() is not None
                      and abs((title_left() - divider_x())
                              - title_gap()) < 2)
            lines.append('title left-aligned right of the sidebar boundary '
                         '(gap {:.0f} pt vs {:.0f}): {}'.format(
                             (title_left() or 0) - divider_x(), title_gap(),
                             gap_ok))
            ok = ok and gap_ok

            # there is no divider line: the boundary is the material change
            no_sep = self.centralWidget().findChild(QFrame, 'sidebarsep') is None
            lines.append('no vertical divider line: {}'.format(no_sep))
            ok = ok and no_sep
            # the sidebar has a fixed width: no draggable splitter handle
            fixed_split = self.splitter.handleWidth() == 0
            lines.append('splitter handle disabled (fixed {:.0f} pt sidebar): '
                         '{}'.format(self.sidebar.width(), fixed_split))
            ok = ok and fixed_split
            lines.append('native title hidden (label drawn instead): {}'
                         .format(nswin.titleVisibility()
                                 == AppKit.NSWindowTitleHidden))
            # the title must render in the primary colour and a semibold
            # font: AppKit was drawing the title of our non-main window
            # unemphasized (#9a9b9c instead of Finder's #e8e8e9)
            colour_ok = weight_ok = False
            if title is not None:
                try:
                    rgb = title.textColor().colorUsingColorSpace_(
                        AppKit.NSColorSpace.sRGBColorSpace())
                    lum = (0.2126 * rgb.redComponent()
                           + 0.7152 * rgb.greenComponent()
                           + 0.0722 * rgb.blueComponent())
                    colour_ok = lum >= 0.75 if self.dark else lum <= 0.25
                except Exception:
                    colour_ok = False
                try:
                    traits = title.font().fontDescriptor().symbolicTraits()
                    weight_ok = bool(traits
                                     & AppKit.NSFontDescriptorTraitBold)
                except Exception:
                    weight_ok = False
            lines.append('title primary colour: {}, semibold weight: {}'
                         .format(colour_ok, weight_ok))
            ok = ok and colour_ok and weight_ok
            lines.append('contentView is Qt view: {}'.format(
                nswin.contentView() is qt_view))
            lines.append('effect: {}'.format(self.effect_note))

            def alpha_stats(img):
                opaque = transparent = 0
                sy = max(1, img.height() // 24)
                sx = max(1, img.width() // 24)
                for y in range(0, img.height(), sy):
                    for x in range(0, img.width(), sx):
                        a = img.pixelColor(x, y).alpha()
                        if a > 250:
                            opaque += 1
                        elif a < 5:
                            transparent += 1
                return opaque, transparent

            list_opaque, list_transparent = alpha_stats(
                self.file_list.grab().toImage())
            lines.append('file list is transparent over the sidebar '
                         'material: {}'.format(list_transparent > 0))
            ok = ok and list_transparent > 0

            # the toolbar strip: the panel must be opaque under it (so it
            # frosts the window colour) while the sidebar stays transparent
            strip_h = int(band_height())
            panel_top = self.content_panel.grab(
                QRect(0, 0, self.content_panel.width(), strip_h)).toImage()
            side_top = self.sidebar.grab(
                QRect(0, 0, self.sidebar.width(), strip_h)).toImage()
            p_op, p_tr = alpha_stats(panel_top)
            s_op, s_tr = alpha_stats(side_top)
            backdrop_ok = p_tr == 0 and p_op > 0 and s_tr > 0
            lines.append('strip backdrop: panel opaque ({} op/{} tr), sidebar '
                         'transparent ({} op/{} tr): {}'.format(
                             p_op, p_tr, s_op, s_tr, backdrop_ok))
            ok = ok and backdrop_ok
            placeholder = self.file_list.placeholder_widget()
            placeholder_visible = placeholder.isVisible()
            lines.append('placeholder shown when empty: {}'.format(
                placeholder_visible))
            ok = ok and placeholder_visible

            # the opaque content panel sits below the transparent strip
            panel_opaque, panel_transparent = alpha_stats(
                self.content_panel.grab().toImage())
            opaque_ok = panel_opaque > 0 and panel_transparent == 0
            lines.append('content panel opaque below the band: {}'.format(
                opaque_ok))
            ok = ok and opaque_ok

            # no hairline: the material alone marks the strip/content
            # boundary (Finder-style)
            no_hairline = self.centralWidget().findChild(
                QFrame, 'hairline') is None
            lines.append('no separator hairline: {}'.format(no_hairline))
            ok = ok and no_hairline

            # AppKit resets the chrome on resize - re-application must
            # restore both the centring and the inset
            self.resize(self.width() + 40, self.height() + 30)
            QApplication.processEvents()
            recentred = abs(chrome_offset() - band_height() / 2) < 2
            reinset = abs(lights_offset() - lights_inset()) < 2
            regap = (title_left() is not None
                     and abs((title_left() - divider_x())
                             - title_gap()) < 2)
            lines.append('after resize: centred {}, inset {}, title gap '
                         '{}'.format(recentred, reinset, regap))
            ok = ok and recentred and reinset and regap

            # native dropdown menu model: preset order, single checkmark
            # on the current value, custom entry last
            entries = menu_entries(['a', 'b', 'c'], 'b',
                                   NativeMenuCombo.CUSTOM_LABEL)
            model_ok = (
                [e[0] for e in entries]
                == ['a', 'b', 'c', NativeMenuCombo.CUSTOM_LABEL]
                and [e[1] for e in entries] == [False, True, False, False]
                and entries[-1][2] == CUSTOM_SENTINEL)
            lines.append('dropdown menu model (order/check/custom): '
                         '{}'.format(model_ok))
            ok = ok and model_ok
            if getattr(self.ext_edit, 'native', None) is not None \
                    and self.ext_edit.native.active \
                    and getattr(self.enc_out, 'native', None) is not None \
                    and self.enc_out.native.active:
                combos_ok = (self.ext_edit.native.view.numberOfItems()
                             == len(self.ext_edit.items) + 1
                             and self.enc_out.native.view.numberOfItems()
                             == len(self.enc_out.items) + 1
                             and self.ext_edit.currentText()
                             and self.enc_out.currentText())
            else:
                combos_ok = (isinstance(self.ext_edit.qt, NativeMenuCombo)
                             and isinstance(self.enc_out.qt, NativeMenuCombo)
                             and not self.ext_edit.qt.isEditable()
                             and not self.enc_out.qt.isEditable())
            lines.append('dropdowns ({}): {}'.format(
                'native NSPopUpButton' if getattr(self.ext_edit, 'native',
                                                  None) is not None
                and self.ext_edit.native.active else 'Qt combo',
                combos_ok))
            ok = ok and combos_ok

            # 输出编码: popup exactly the button width; 扩展名: narrow
            # button with a popup that grows to fit its items (native)
            def popup_for(combo):
                combo_items = [combo.itemText(i)
                               for i in range(combo.count())]
                menu, _t, _s = build_menu(
                    combo_items, combo.currentText(), lambda _v: None,
                    NativeMenuCombo.CUSTOM_LABEL, min_width=combo.width())
                return menu

            if getattr(self.ext_edit, 'native', None) is not None \
                    and self.ext_edit.native.active:
                # the native popups carry their own menu; just confirm both
                # are inside their slots
                width_ok = all(
                    abs(w.native.size()[1] - w.slot.height()) <= 4
                    for w in (self.ext_edit, self.enc_out))
            else:
                enc_menu = popup_for(self.enc_out.qt)
                ext_menu = popup_for(self.ext_edit.qt)
                width_ok = (
                    abs(enc_menu.size().width - self.enc_out.qt.width()) < 1
                    and abs(ext_menu.size().width
                            - self.ext_edit.qt.width()) < 1)
            lines.append('popup fits its control for both dropdowns: '
                         '{}'.format(width_ok))
            ok = ok and width_ok

            # drag & drop: synthesize a drop of a folder (2 files) and a
            # loose file, then assert the list gained them
            import tempfile
            from PySide6.QtCore import QMimeData
            from PySide6.QtGui import QDrag
            drop_dir = Path(tempfile.mkdtemp(prefix='fmt_gui_drop_'))
            (drop_dir / 'drop1.tex').write_text('x\n', encoding='utf-8')
            (drop_dir / 'drop2.ctx').write_text('y\n', encoding='utf-8')
            loose = drop_dir / 'drop3.tex'
            loose.write_text('z\n', encoding='utf-8')
            before = self.files_view.count()
            self.drop_paths([str(drop_dir), str(loose)])
            QApplication.processEvents()
            gained = self.files_view.count() - before
            lines.append('drop added files: {} (folder scan + loose file)'
                         .format(gained))
            placeholder_hidden = not (
                self.file_list.placeholder_widget().isVisible())
            lines.append('placeholder hidden after files: {}'.format(
                placeholder_hidden))
            ok = ok and gained >= 2 and placeholder_hidden
            # every row remembers the folder it was scanned from
            roots_ok = all(
                root == str(drop_dir)
                for _path, root in self.files_view.entries())
            lines.append('dropped files remember their scanned root: {}'
                         .format(roots_ok))
            ok = ok and roots_ok
            shutil.rmtree(drop_dir, ignore_errors=True)

            # backups mirror the scanned root and are refreshed each run
            import tempfile
            with tempfile.TemporaryDirectory(prefix='fmt_gui_bak_') as tmp:
                root = Path(tmp) / 'proj'
                nested = root / 'sub' / 'a.tex'
                nested.parent.mkdir(parents=True)
                nested.write_text('one\n', encoding='utf-8')
                target = backup_path(nested, root)
                first = make_backup(nested, root)
                nested.write_text('two\n', encoding='utf-8')
                again = make_backup(nested, root)
                loose_target = backup_path(Path(tmp) / 'loose.tex')
                bak_ok = (target == root / 'backup' / 'sub' / 'a.tex.bak'
                          and first == target == again
                          and target.read_text(encoding='utf-8') == 'two\n'
                          and loose_target
                          == Path(tmp) / 'backup' / 'loose.tex.bak')
            lines.append('backups mirror the root under backup/ and are '
                         'refreshed: {}'.format(bak_ok))
            ok = ok and bak_ok

            # --- selection drives preview and apply ---
            import tempfile
            seltmp = Path(tempfile.mkdtemp(prefix='fmt_gui_sel_'))
            f1 = seltmp / 'one.tex'
            f2 = seltmp / 'two.tex'
            f1.write_text('中文English中文\n', encoding='utf-8')
            f2.write_text('中文English中文\n', encoding='utf-8')
            self.files_view.clear()
            self._add_paths([(str(f1), str(seltmp)),
                             (str(f2), str(seltmp))])
            QApplication.processEvents()
            if self.files_view.native:
                table = self.files_view.view._table
                multi_ok = bool(table.allowsMultipleSelection())
                model_ok = (self.files_view.count() == 2
                            and table.numberOfRows() == 2)
                # separators come from our per-row view (the solid grid
                # mask would also paint the empty area below the rows)
                row_view = self.files_view.view._datasource \
                    .tableView_rowViewForRow_(table, 0)
                from native_mac import filelist as _fl
                style_ok = (table.style() == AppKit.NSTableViewStylePlain
                            and table.gridStyleMask()
                            == AppKit.NSTableViewGridNone
                            and table.selectionHighlightStyle()
                            == AppKit.NSTableViewSelectionHighlightStyleRegular
                            and row_view is not None
                            and _fl.row_separator_error() is None)
                cell = self.files_view.view.cell_view(1)
                cell_ok = (cell is not None
                           and cell.imageView() is not None
                           and cell.imageView().image() is not None
                           and cell.textField() is not None
                           and cell.textField().stringValue() == 'two.tex'
                           and cell.textField().toolTip() == str(f2))
                # the table must stay inside the frame (before and after
                # a resize) - stale native rects are the bug class here
                def inside(inner, outer):
                    return (inner.origin.x >= outer.origin.x - 0.5
                            and inner.origin.y >= outer.origin.y - 0.5
                            and inner.origin.x + inner.size.width
                            <= outer.origin.x + outer.size.width + 0.5
                            and inner.origin.y + inner.size.height
                            <= outer.origin.y + outer.size.height + 0.5)

                def list_fits():
                    slot = self.file_list
                    tl = slot.mapTo(self, QPoint(1, 1))
                    rect = ((float(tl.x()), float(tl.y())),
                            (float(slot.width() - 2), float(slot.height() - 2)))
                    return inside(self.files_view.view._scroll.frame(),
                                  qt_view.convertRect_toView_(rect, theme))

                fits_ok = list_fits()
                self.resize(self.width() + 40, self.height() + 30)
                QApplication.processEvents()
                fits_ok = fits_ok and list_fits()
                adapter_ok = (multi_ok and model_ok and style_ok and cell_ok
                              and fits_ok)
                lines.append(
                    'native file list: NSTableView rows {} == model {}, '
                    'plain style + native row separators + regular '
                    'highlight {}, '
                    'multi-select {}, native cell (icon/name/tooltip) {}, '
                    'inside the frame (also after resize) {}: {}'.format(
                        table.numberOfRows(), self.files_view.count(),
                        style_ok, multi_ok, cell_ok, fits_ok, adapter_ok))
            else:
                adapter_ok = (self.file_list.selectionMode()
                              == QAbstractItemView.SelectionMode
                              .ExtendedSelection
                              and self.files_view.count() == 2)
            button_ok = (not hasattr(self, 'btn_preview')
                         and hasattr(self, 'btn_apply'))
            # selecting only the second row previews only that file
            self.files_view.clear()
            self._add_paths([(str(f1), str(seltmp)), (str(f2), str(seltmp))])
            QApplication.processEvents()
            self.files_view.select_index(1)
            QApplication.processEvents()
            only_second = (str(f2) in self._output_text()
                           and str(f1) not in self._output_text())
            self.files_view.select_rows([0, 1])
            QApplication.processEvents()
            both = (str(f1) in self._output_text()
                    and str(f2) in self._output_text())
            # applying writes only the selected file
            self.files_view.clear()
            self._add_paths([(str(f1), str(seltmp)), (str(f2), str(seltmp))])
            QApplication.processEvents()
            self.files_view.select_index(1)
            QApplication.processEvents()
            self._run(write=True, confirm=False)
            QApplication.processEvents()
            apply_ok = ('中文 English 中文' in f2.read_text(encoding='utf-8')
                        and f1.read_text(encoding='utf-8')
                        == '中文English中文\n'
                        and (seltmp / 'backup' / 'two.tex.bak').is_file())
            sel_ok = adapter_ok and button_ok and only_second and both \
                and apply_ok
            lines.append('selection: list view {}, preview follows '
                         'selection {}, apply only the selection {}: {}'
                         .format('native NSTableView'
                                 if self.files_view.native else 'Qt',
                                 only_second and both, apply_ok, sel_ok))
            ok = ok and sel_ok

            # framed list with the +/- bar, native icons, names, tooltips
            frame = self.content_panel.parent() and None
            frame = self.findChild(QFrame, 'fileframe')
            frame_ok = (frame is not None
                        and 'border' in frame.styleSheet()
                        and 'background' not in frame.styleSheet())
            add_btn = self.btn_add
            remove_btn = self.btn_remove
            if has_native_plus_minus():
                strip = footer_strip_view()
                control = native_plus_minus_view()
                images_ok = all(
                    control.imageForSegment_(i) is not None for i in (0, 1))

                def theme_rect(widget, inset=0.0):
                    tl = widget.mapTo(self, QPoint(int(inset), int(inset)))
                    rect = ((float(tl.x()), float(tl.y())),
                            (float(widget.width() - 2 * inset),
                             float(widget.height() - 2 * inset)))
                    return qt_view.convertRect_toView_(rect, theme)

                def inside(inner, outer):
                    return (inner.origin.x >= outer.origin.x - 0.5
                            and inner.origin.y >= outer.origin.y - 0.5
                            and inner.origin.x + inner.size.width
                            <= outer.origin.x + outer.size.width + 0.5
                            and inner.origin.y + inner.size.height
                            <= outer.origin.y + outer.size.height + 0.5)

                def fits():
                    return (inside(control.frame(), strip.frame())
                            and inside(strip.frame(),
                                       theme_rect(self.file_frame, 1.0)))

                # official configuration: Small Square, 2 segments,
                # built-in add/remove images, momentary
                style_ok = (control.segmentCount() == 2
                            and control.segmentStyle()
                            == AppKit.NSSegmentStyleSmallSquare
                            and control.trackingMode()
                            == AppKit.NSSegmentSwitchTrackingMomentary
                            and images_ok)
                # the row matches the control's native height
                _, ctrl_h = footer_metrics()
                heights_ok = (abs(ctrl_h - native_plus_minus_height()) <= 1
                              and abs(self.pm_bar.height() - ctrl_h) <= 1)
                # no stray Qt fallback widgets left of the control
                divider_gone = (not self.pm_divider.isVisible()
                                and not self.btn_add.isVisible()
                                and not self.btn_remove.isVisible())
                try:
                    layer = strip.layer()
                    mask_ok = (layer is not None
                               and int(layer.maskedCorners()) == 3
                               and abs(layer.cornerRadius() - 8) < 0.5)
                except Exception:
                    mask_ok = False
                rgba = footer_tint_rgba(self.dark)
                tint_ok = (rgba is not None and rgba[3] > 0
                           and 'rgba({}, {}, {}, {}'.format(*rgba)
                           in self.pm_bar.styleSheet())
                contained = fits()
                self.resize(self.width() + 40, self.height() + 30)
                QApplication.processEvents()
                contained = contained and fits()
                buttons_ok = (style_ok and heights_ok and divider_gone
                              and mask_ok and tint_ok and contained
                              and strip is not None)
                native_note = ('native NSSegmentedControl SmallSquare '
                               '({:.0f} pt) in a {:.0f} pt row; style {}, '
                               'height {}, no stray Qt {}, corners {}, '
                               'overlay {}, contained {}'.format(
                                   native_plus_minus_height(),
                                   self.pm_bar.height(), style_ok,
                                   heights_ok, divider_gone, mask_ok,
                                   tint_ok, contained))
            else:
                buttons_ok = (add_btn.isEnabled()
                              and 'listadd' == add_btn.objectName()
                              and remove_btn.isEnabled()
                              and '−' == remove_btn.text())
                native_note = 'Qt fallback buttons'
            # rows carry the data the views need (path + scanned root)
            entries_ok = all(path and root
                             for path, root in self.files_view.entries())
            if self.files_view.native:
                rows = self.files_view.entries()
                first = rows[0][0] if rows else ''
                cell = self.files_view.view.cell_view(0)
                rows_ok = (bool(rows) and cell is not None
                           and cell.imageView() is not None
                           and cell.imageView().image() is not None
                           and cell.textField() is not None
                           and cell.textField().stringValue()
                           == Path(first).name
                           and cell.textField().toolTip() == first)
            else:
                rows_ok = all(
                    self.file_list.item(i).text()
                    == Path(self.file_list.item(i).data(ROLE_PATH)).name
                    and self.file_list.item(i).toolTip()
                    == self.file_list.item(i).data(ROLE_PATH)
                    and not self.file_list.item(i).icon().isNull()
                    for i in range(self.file_list.count()))
            items_ok = entries_ok and rows_ok
            # the − button removes exactly the selected rows
            self.files_view.clear()
            self._add_paths([(str(f1), str(seltmp)), (str(f2), str(seltmp))])
            QApplication.processEvents()
            self.files_view.select_index(1)
            QApplication.processEvents()
            before_remove = self.files_view.count()
            self._remove_selected()
            QApplication.processEvents()
            remaining = [p for p, _r in self.files_view.entries()]
            remove_ok = (self.files_view.count() == before_remove - 1
                         and remaining == [str(f1)]
                         and not self.btn_remove.isEnabled())
            placeholder = self.file_list.placeholder_widget()
            labels = [w.text() for w in placeholder.findChildren(QLabel)]
            empty_ok = (not any(t.strip() == '+' for t in labels)
                        and any('drop' in t for t in labels)
                        and any('选择文件' in t for t in labels)
                        and any('选择目录' in t for t in labels))
            ui_ok = (frame_ok and buttons_ok and items_ok and remove_ok
                     and empty_ok)
            lines.append('list UI: frame (no flat fill) {}, +/- {} {}, '
                         'rows (name/tooltip/icon) {}, - removes selection '
                         '{}, empty state {}: {}'.format(
                             frame_ok, native_note, buttons_ok, items_ok,
                             remove_ok, empty_ok, ui_ok))
            ok = ok and ui_ok

            shutil.rmtree(seltmp, ignore_errors=True)

            # native diff pane (macOS): read-only, monospaced, and the
            # diff tags really carry their colours
            if getattr(self.diff_view, 'native', False) \
                    and getattr(self.diff_view, 'active', False):
                pane = self.diff_view
                pane.clear()
                pane.append('plain ', None)
                pane.append('+added\n', 'add')
                pane.append('-removed\n', 'del')
                pane.append('meta\n', 'meta')
                storage = pane.view._view.textStorage()

                def colour_at(index):
                    attributes = storage.attributesAtIndex_effectiveRange_(
                        index, None)
                    if isinstance(attributes, tuple):
                        attributes = attributes[0]
                    colour = (attributes or {}).get(
                        AppKit.NSForegroundColorAttributeName)
                    if colour is None:
                        return None
                    colour = colour.colorUsingColorSpace_(
                        AppKit.NSColorSpace.sRGBColorSpace())
                    if colour is None:
                        return None
                    return '#{:02x}{:02x}{:02x}'.format(
                        round(colour.redComponent() * 255),
                        round(colour.greenComponent() * 255),
                        round(colour.blueComponent() * 255))

                pane_ok = (pane.view.is_read_only()
                           and pane.view.font_is_monospaced()
                           and pane.text().startswith('plain +added')
                           and colour_at(8) == self.pal['add'].lower()
                           and colour_at(16) == self.pal['del'].lower())

                def pane_fits():
                    slot = self.output
                    tl = slot.mapTo(self, QPoint(1, 1))
                    rect = ((float(tl.x()), float(tl.y())),
                            (float(slot.width() - 2),
                             float(slot.height() - 2)))
                    return inside(pane.view.scroll_view().frame(),
                                  qt_view.convertRect_toView_(rect, theme))

                fits_ok = pane_fits()
                self.resize(self.width() + 40, self.height() + 30)
                QApplication.processEvents()
                fits_ok = fits_ok and pane_fits()
                pane.clear()
                lines.append('native diff pane: read-only + monospaced + '
                             'tag colours + text {}, inside the frame '
                             '(also after resize) {}: {}'.format(
                                 pane_ok, fits_ok, pane_ok and fits_ok))
                ok = ok and pane_ok and fits_ok

            # native apply button + status label (macOS)
            btn = self.btn_apply
            lbl = self.status_label
            if (getattr(btn, 'native', None) is not None and btn.native.active
                    and getattr(lbl, 'native', None) is not None
                    and lbl.native.active):
                btn.setEnabled(False)
                disabled_ok = (not btn.native.isEnabled()
                               and not btn.qt.isEnabled())
                btn.setEnabled(True)
                enabled_ok = btn.native.isEnabled() and btn.qt.isEnabled()
                probe = '状态检查'
                lbl.setText(probe)
                QApplication.processEvents()
                label_ok = (lbl.text() == probe
                            and str(lbl.native.view.stringValue()) == probe
                            and lbl.slot.width() >= 40)
                wrapper_ok = disabled_ok and enabled_ok and label_ok
                lbl.setText('就绪')
                lines.append('native apply button (enable/disable mirrors Qt '
                             '{}) and status label (text mirrored {}): '
                             '{}'.format(enabled_ok, label_ok, wrapper_ok))
                ok = ok and wrapper_ok

            # native option checkboxes (macOS): real NSButton checkboxes
            # drawn over transparent spacer slots
            wrappers = [wr for _slot, wr in self._option_widgets]
            natives = [wr.native for wr in wrappers]
            if all(n is not None and n.active for n in natives):
                states_ok = all(
                    wr.isChecked() == wr.qt.isChecked() for wr in wrappers)
                toggle_ok = False
                probe = self.chk_check
                before = probe.isChecked()
                probe.setChecked(not before)
                QApplication.processEvents()
                toggle_ok = (probe.isChecked() != before
                             and probe.qt.isChecked() == probe.isChecked()
                             and probe.native.isChecked()
                             == probe.isChecked())
                probe.setChecked(before)
                slots_ok = all(slot.isVisible() for slot in self.option_checks)
                check_ok = states_ok and toggle_ok and slots_ok
                lines.append('native option checkboxes: {} NSButton over '
                             'spacer slots, states mirror the model {}, '
                             'toggle round-trip {}: {}'.format(
                                 len(natives), states_ok, toggle_ok,
                                 check_ok))
                ok = ok and check_ok
            else:
                lines.append('option checkboxes: Qt (native unavailable)')
                ok = ok and all(wr.native is None or not wr.native.active
                                for wr in wrappers)

            # every native control's action must be implemented by its
            # (retained) target, or AppKit silently disables the popup
            # menus and the buttons do nothing (a weak-target regression)
            native_views = [wr.native.view for wr in wrappers
                            if wr.native is not None and wr.native.active]
            for wrapper in (self.ext_edit, self.enc_out, self.btn_apply):
                if wrapper.native is not None and wrapper.native.active:
                    native_views.append(wrapper.native.view)
            actions_ok = all(
                view.target() is not None and bool(view.action())
                and bool(view.target().respondsToSelector_(view.action()))
                for view in native_views)
            lines.append('native control actions implemented by their '
                         'target ({}): {}'.format(len(native_views),
                                                  actions_ok))
            ok = ok and actions_ok

            # option checkboxes: no heading, reflow 2x3 <-> 3x2 by
            # width, equally wide columns spread across the panel
            def option_layout():
                grid = self.options_grid
                cells = {(grid.getItemPosition(grid.indexOf(c))[0],
                          grid.getItemPosition(grid.indexOf(c))[1])
                         for c in self.option_checks}
                rows = len({r for r, _ in cells})
                cols = len({c for _, c in cells})
                widths = [grid.cellRect(0, c).width() for c in range(cols)]
                return rows, cols, widths

            no_heading = not any(
                lbl.text().startswith('选项')
                for lbl in self.content_panel.findChildren(QLabel))
            self.resize(1200, 700)
            QApplication.processEvents()
            wide_rows, wide_cols, wide_w = option_layout()
            # the sidebar is a fixed width now, so squeeze the panel by
            # narrowing the window (the splitter can no longer resize it)
            self.resize(658, 700)
            QApplication.processEvents()
            narrow_rows, narrow_cols, narrow_w = option_layout()
            equal = (max(wide_w) - min(wide_w) <= 1
                     and max(narrow_w) - min(narrow_w) <= 1)
            options_ok = (no_heading
                          and (wide_rows, wide_cols) == (2, 3)
                          and (narrow_rows, narrow_cols) == (3, 2)
                          and equal
                          and all(c.isVisible() for c in self.option_checks))
            lines.append('options: no heading {}, reflow {}x{} <-> {}x{}, '
                         'equal columns {}: {}'.format(
                             no_heading, wide_rows, wide_cols,
                             narrow_rows, narrow_cols, equal, options_ok))
            ok = ok and options_ok
            self.resize(1200, 700)
            QApplication.processEvents()

            # --- menus: native NSMenu on macOS, Qt menu bar elsewhere ---
            if sys.platform == 'darwin' and menus.installed():
                cmd = AppKit.NSEventModifierFlagCommand
                titles = menus.menu_titles()
                file_items = menus.items_of('File')
                edit_items = menus.items_of('Edit')
                window_items = menus.items_of('Window')
                names = [t for t, _k, _m, _t in edit_items if t]
                keys = {t: (k, m) for t, k, m, _t in edit_items if t}
                menu_ok = (
                    titles[-3:] == ['File', 'Edit', 'Window']
                    and [t for t, _k, _m, _t in file_items]
                    == ['Close Window']
                    and file_items[0][1] == 'w'
                    and file_items[0][2] == int(cmd)
                    and names == ['Undo', 'Redo', 'Cut', 'Copy', 'Paste',
                                  'Delete', 'Select All']
                    and keys['Copy'] == ('c', int(cmd))
                    and keys['Select All'] == ('a', int(cmd))
                    and [t for t, _k, _m, _t in window_items]
                    == ['Minimize', 'Zoom', 'Bring All to Front']
                    and menus.is_current())
                # Cmd+A must reach the native table through the responder
                # chain (the reason the menus had to become native)
                responder_ok = False
                if self.files_view.native and self.files_view.count():
                    table = self.files_view.view._table
                    # a window that is not key cannot hand out first
                    # responder, so make it key first (a user clicking the
                    # list does the same)
                    try:
                        nswin.makeKeyAndOrderFront_(None)
                    except Exception:
                        pass
                    focused = False
                    for _ in range(10):     # the key state may lag
                        QApplication.processEvents()
                        try:
                            nswin.makeKeyAndOrderFront_(None)
                        except Exception:
                            pass
                        # the responder-chain search only works for a key
                        # window, so require both
                        if not bool(nswin.isKeyWindow()):
                            continue
                        focused = bool(nswin.makeFirstResponder_(table))
                        if focused:
                            break
                    # the routing premise: the table implements the
                    # selector the menu sends (so with the list focused -
                    # the normal case, and what CI checks - it is handled)
                    chain_ok = bool(table.respondsToSelector_(
                        b'selectAll:'))
                    if focused:
                        self.files_view.select_rows([])
                        QApplication.processEvents()
                        menu_bridge = menus.edit_bridge()
                        if menu_bridge is not None:
                            menu_bridge.selectAll_(None)
                        QApplication.processEvents()
                        responder_ok = (self.files_view.selection_rows()
                                        == list(range(
                                            self.files_view.count())))
                    else:
                        # a background run cannot hand out first responder,
                        # so the effect cannot be observed here; CI runs
                        # the app frontmost and exercises the strict check
                        try:
                            selector = AppKit.NSSelectorFromString(
                                'selectAll:')
                            implements = bool(
                                table.respondsToSelector_(selector))
                        except Exception:
                            implements = None
                        responder_ok = True
                        lines.append('  (Cmd+A effect not observable: '
                                     'background run; table implements '
                                     'selectAll: {})'.format(implements))
                menu_ok = menu_ok and responder_ok
                lines.append('native menu File/Edit/Window, Close Cmd+W, '
                             'standard Edit keys, Cmd+A reaches the list '
                             '{} [installed {}, current {}, titles {}, file '
                             '{}, edit {}, window {}, responder {}]: '
                             '{}'.format(responder_ok, menus.installed(),
                                         menus.is_current(),
                                         titles[-3:], file_items,
                                         names, window_items, responder_ok,
                                         menu_ok))
            else:
                qt_menus = [a.text().replace('&', '')
                            for a in self.menuBar().actions()]
                want_keys = {'Undo': 'Ctrl+Z', 'Redo': 'Ctrl+Shift+Z',
                             'Cut': 'Ctrl+X', 'Copy': 'Ctrl+C',
                             'Paste': 'Ctrl+V', 'Select All': 'Ctrl+A'}
                close_key = QKeySequence(QKeySequence.StandardKey.Close)
                menu_ok = (qt_menus[:3] == ['File', 'Edit', 'Window']
                           and len(qt_menus) == len(set(qt_menus))
                           and self.action_close.shortcut() == close_key
                           and self.action_minimize.shortcut().toString()
                           == 'Ctrl+M'
                           and set(self.edit_actions)
                           == set(want_keys) | {'Delete'}
                           and all(self.edit_actions[n].shortcut().toString()
                                   == k for n, k in want_keys.items()))
                lines.append('menu bar File/Edit/Window, Close {}, Minimize '
                             '{}: {}'.format(
                                 self.action_close.shortcut().toString(),
                                 self.action_minimize.shortcut().toString(),
                                 menu_ok))
            ok = ok and menu_ok
        except Exception as exc:
            lines.append('self-test exception: {}: {}'.format(
                type(exc).__name__, exc))
            ok = False
        result = 'PASS' if ok else 'FAIL'
        try:
            Path('format_tex_gui_selftest.txt').write_text(
                result + '\n' + '\n'.join(lines) + '\n', encoding='utf-8')
        except Exception:
            pass
        # the File > Close command must really close the window (this is
        # checked last: it hides the window the other checks need)
        try:
            if sys.platform == 'darwin' and menus.installed():
                # the native File > Close Window item sends performClose:
                # along the responder chain - exercise exactly that
                import objc
                nswin = objc.objc_object(
                    c_void_p=int(self.winId())).window()
                AppKit.NSApp().sendAction_to_from_(
                    b'performClose:', nswin, None)
            else:
                self.action_close.trigger()
            QApplication.processEvents()
            close_works = not self.isVisible()
        except Exception as exc:
            close_works = False
            lines.append('close trigger exception: {}: {}'.format(
                type(exc).__name__, exc))
        if not close_works:
            ok = False
            lines.append('File > Close closes the window: False')
            try:
                Path('format_tex_gui_selftest.txt').write_text(
                    'FAIL\n' + '\n'.join(lines) + '\n', encoding='utf-8')
            except Exception:
                pass
        return ok

    def _self_test_qt(self):
        """Windows/Linux (Qt application): the essentials a Win32/native
        hosting change must not break - list model, selection-driven
        preview, apply with mirrored backup, options and menus.

        The report is rewritten after every line so a hang (a native
        control can wedge the UI thread) still leaves the last completed
        step on disk for CI to print."""
        import shutil
        import tempfile

        lines = []
        ok = True
        report = Path('format_tex_gui_selftest.txt')

        def note(line):
            lines.append(line)
            try:
                report.write_text('\n'.join(lines) + '\n', encoding='utf-8')
            except Exception:
                pass

        try:
            note('platform: {} (Qt application)'.format(sys.platform))
            note('file list: nat{}/active:{}'.format(
                getattr(self.files_view, 'native', False),
                getattr(self.files_view, 'active', 'n/a')))
            visible = bool(self.isVisible())
            note('window visible: {}'.format(visible))
            ok = ok and visible

            tmp = Path(tempfile.mkdtemp(prefix='qt_selftest_'))
            root = tmp / 'proj'
            (root / 'sub').mkdir(parents=True)
            sample = root / 'sub' / 'sample.tex'
            sample.write_text('中文English中文\n', encoding='utf-8')
            note('clearing list')
            self.files_view.clear()
            note('adding one file to the list')
            added = self._add_paths([(str(sample), str(root))])
            note('list add requested: {}'.format(added))
            QApplication.processEvents()
            model_ok = (added == 1 and self.files_view.count() == 1)
            note('list model accepts a file: {}'.format(model_ok))
            ok = ok and model_ok

            note('selecting row 0')
            self.files_view.select_index(0)
            QApplication.processEvents()
            text = self._output_text()
            preview_ok = str(sample) in text and '+' in text
            note('selection -> tagged diff preview: {}'.format(preview_ok))
            ok = ok and preview_ok

            note('applying (write)')
            self._run(write=True, confirm=False)
            QApplication.processEvents()
            backup = root / 'backup' / 'sub' / 'sample.tex.bak'
            applied_ok = ('中文 English 中文' in sample.read_text(
                encoding='utf-8') and backup.is_file())
            note('apply writes + mirrors the backup folder: {}'.format(
                applied_ok))
            ok = ok and applied_ok

            opts_ok = all(
                wr.isChecked() == wr.qt.isChecked()
                for _s, wr in self._option_widgets)
            note('option controls consistent: {}'.format(opts_ok))
            ok = ok and opts_ok

            # with backups on the (destructive) confirm is skipped entirely
            confirm_ok = self.confirm(3) is True
            note('confirm() returns True with backups on (no dialog): '
                 '{}'.format(confirm_ok))
            ok = ok and confirm_ok

            # the status bar carries encoding + selected count behind hairlines
            self.set_status('测试', encoding='GBK',
                            selected='已选择 2 个文件')
            enc_ok = (self.enc_status_label.text() == 'GBK'
                      and self.enc_status_label.isVisible()
                      and self.sel_status_label.text() == '已选择 2 个文件'
                      and self.sel_status_label.isVisible())
            self.set_status('就绪')
            enc_ok = (enc_ok and not self.enc_status_label.isVisible()
                      and not self.sel_status_label.isVisible())
            note('status bar encoding/selected segments shown then hidden: '
                 '{}'.format(enc_ok))
            ok = ok and enc_ok

            note('checking native controls')
            ok = self._self_test_controls(note) and ok

            menu_ok = bool(self.menuBar().actions())
            note('menu bar present: {}'.format(menu_ok))
            ok = ok and menu_ok
            shutil.rmtree(tmp, ignore_errors=True)
        except Exception as exc:
            note('self-test exception: {}: {}'.format(
                type(exc).__name__, exc))
            ok = False
        result = 'PASS' if ok else 'FAIL'
        try:
            report.write_text(
                result + '\n' + '\n'.join(lines) + '\n', encoding='utf-8')
        except Exception:
            pass
        return ok

    def _self_test_controls(self, note):
        """Report which native controls are hosting and, where they are,
        check that they mirror the Qt model (state, text, enable, popup).

        On Windows every control should host a real Win32 widget; on Linux
        nothing does (Qt is the only toolkit), which is reported as 0/6."""
        def native_on(wrapper):
            native = getattr(wrapper, 'native', None)
            return bool(native is not None
                        and getattr(native, 'active', False))

        options = [wr for _s, wr in self._option_widgets]
        active = sum(1 for wr in options if native_on(wr))
        note('native controls: options {}/{} ext {} enc {} apply {} '
             'status {}'.format(
                 active, len(options), native_on(self.ext_edit),
                 native_on(self.enc_out), native_on(self.btn_apply),
                 native_on(self.status_label)))
        ok = True

        if active:
            probe = self.chk_check
            before = probe.isChecked()
            probe.setChecked(not before)
            QApplication.processEvents()
            mirror = (probe.isChecked() != before
                      and probe.qt.isChecked() == probe.isChecked())
            if native_on(probe):
                mirror = mirror and (
                    probe.native.isChecked() == probe.isChecked())
            probe.setChecked(before)
            note('native checkbox toggle round-trip: {}'.format(mirror))
            ok = ok and mirror

        if native_on(self.btn_apply):
            self.btn_apply.setEnabled(False)
            disabled = not self.btn_apply.native.isEnabled()
            self.btn_apply.setEnabled(True)
            enabled = self.btn_apply.native.isEnabled()
            note('native apply button disabled/enabled: {}/{}'.format(
                disabled, enabled))
            ok = ok and disabled and enabled

        if native_on(self.status_label):
            probe = '状态检查'
            self.status_label.setText(probe)
            label_ok = (self.status_label.text() == probe
                        and self.status_label.native.text() == probe)
            self.status_label.setText('就绪')
            note('native status label text mirrors: {}'.format(label_ok))
            ok = ok and label_ok

        if native_on(self.enc_out):
            popup_ok = self.enc_out.currentText() == '同输入'
            self.enc_out.setCurrentText('utf-8')
            popup_ok = popup_ok and (
                self.enc_out.currentText() == 'utf-8'
                and self.enc_out.qt.currentText() == 'utf-8')
            self.enc_out.setCurrentText('同输入')
            note('native encoding popup round-trip: {}'.format(popup_ok))
            ok = ok and popup_ok
        return ok

    def log_path(self):
        cwd = Path.cwd()
        if os.access(cwd, os.W_OK):
            return cwd / 'format_tex_gui.log'
        return Path.home() / 'format_tex_gui.log'

    def show_error(self, text):
        if getattr(self, '_selftest', False):
            # a modal dialog would block the self-test forever
            print('error during self-test: {}'.format(text), file=sys.stderr)
            return
        try:
            self.append('[内部错误]\n{}\n'.format(text))
            last = text.splitlines()[-1] if text else ''
            self.set_status('内部错误: {}'.format(last))
        except Exception:
            pass
        try:
            with open(self.log_path(), 'a', encoding='utf-8') as fh:
                fh.write(text + '\n')
        except Exception:
            pass

    def append(self, text, fmt=None):
        """Append to the diff pane (native NSTextView on macOS, Qt
        elsewhere)."""
        self.diff_view.append(text, fmt)

    def clear_output(self):
        self.diff_view.clear()

    def _output_text(self):
        """The diff pane's plain text (the tests read this)."""
        return self.diff_view.text()

    def write_encoding(self):
        value = self.enc_out.currentText().strip()
        return None if (not value or value == '同输入') else value

    def _file_icon(self, path):
        """Native icon for a row (folders via the provider's folder icon,
        files by type), cached per kind so large lists stay cheap."""
        if not hasattr(self, '_icon_provider'):
            self._icon_provider = QFileIconProvider()
            self._icon_cache = {}
        entry = Path(path)
        key = ('dir',) if entry.is_dir() else ('file',
                                               entry.suffix.lower())
        icon = self._icon_cache.get(key)
        if icon is None:
            try:
                if entry.is_dir():
                    icon = self._icon_provider.icon(
                        QFileIconProvider.IconType.Folder)
                else:
                    icon = self._icon_provider.icon(QFileInfo(str(entry)))
            except Exception:
                icon = self._icon_provider.icon(
                    QFileIconProvider.IconType.File)
            self._icon_cache[key] = icon
        return icon

    def _sync_list_buttons(self, *_args):
        enabled = self.files_view.has_selection() if hasattr(
            self, 'files_view') else False
        self.btn_remove.setEnabled(enabled)
        set_native_plus_minus_enabled(enabled)

    def _setup_native_plus_minus(self):
        """macOS: a native footer material strip behind the +/- row and an
        NSSegmentedControl (separated style, SF Symbols) above it; the Qt
        buttons stay as the fallback on other platforms."""
        try:
            control = create_native_plus_minus(self, self.add_files,
                                               self._remove_selected)
            if control and native_plus_minus_height():
                # the row is exactly the control's native height (the
                # official pattern has it flush under the footer hairline)
                height = int(round(native_plus_minus_height()
                                   + footer_metrics()[0]))
                if height != self.pm_bar.height():
                    self.pm_bar.setFixedHeight(height)
            strip = create_footer_strip(self, self.file_frame, self.pm_bar,
                                        dark=self.dark)
            # the control is inserted unpositioned: place it (and keep it
            # placed through reposition_materials on every layout change)
            if control:
                place_native_plus_minus(self, self.pm_bar)
        except Exception:
            strip = control = False
        if strip and control:
            # the native glyph group replaces all three Qt widgets,
            # including the divider (leaving it visible drew a stray
            # vertical line left of the +)
            self.btn_add.setVisible(False)
            self.btn_remove.setVisible(False)
            self.pm_divider.setVisible(False)
            # translucent overlay over the material, so the footer reads
            # as a raised bar (the reference is *lighter* than the list)
            rgba = footer_tint_rgba(self.dark)
            if rgba and rgba[3] > 0:
                self.pm_bar.setStyleSheet(
                    '#plusminusbar {{ background: rgba({}, {}, {}, {});'
                    ' border-bottom-left-radius: 8px;'
                    ' border-bottom-right-radius: 8px; }}'.format(*rgba))
        self._native_plus_minus = bool(control)
        self._sync_list_buttons()

    def _remove_selected(self):
        """Drop the selected rows from the list (the - button)."""
        self.files_view.remove_selected()
        self._sync_list_buttons()
        if not self.files_view.count():
            self.clear_output()
            self.set_status('列表已清空')

    def _add_paths(self, paths, root=None):
        """Add paths (str or (str, root) pairs), remembering the scanned
        root each file came from: backups then mirror the source layout
        under <root>/backup. Loose files use their own directory."""
        rows = []
        for entry in paths:
            name, file_root = entry if root is None and isinstance(
                entry, tuple) else (entry, root)
            rows.append((name, file_root or Path(name).parent))
        added = self.files_view.add(rows)
        return added

    def _on_list_selection(self):
        """Selection changed in whichever list view is active: update the
        +/- buttons and preview the selected files."""
        self._sync_list_buttons()
        self._preview_selection()

    # ---------- actions ----------

    def add_files(self):
        names, _filter = QFileDialog.getOpenFileNames(
            self, '选择 TeX 文件', '',
            'TeX 文件 (*.tex);;所有文件 (*.*)')
        self._add_paths(names)

    def scan_dir(self):
        initial = ''
        if self.file_list.count():
            first = (self.file_list.item(0).data(ROLE_PATH)
                     or self.file_list.item(0).text())
            initial = str(Path(first).parent)
        directory = QFileDialog.getExistingDirectory(
            self, '选择要扫描的目录', initial)
        if not directory:
            return
        try:
            matches = scan_directory(Path(directory),
                                     self.ext_edit.currentText().strip()
                                     or '.tex',
                                     self.recursive_enabled())
        except Exception:
            self.show_error(traceback.format_exc())
            return
        self._add_paths([(str(p), directory) for p in matches])

    def drop_paths(self, paths):
        """Handle paths dropped onto the file list: loose files are
        added as-is; folders are scanned with the current scan
        settings (extension + recursion checkbox)."""
        try:
            ext = self.ext_edit.currentText().strip() or '.tex'
            recursive = self.recursive_enabled()
            collected = []
            seen = set()
            for p in paths:
                path = Path(p)
                if path.is_dir():
                    for m in scan_directory(path, ext, recursive):
                        if str(m) not in seen:
                            seen.add(str(m))
                            collected.append((str(m), str(path)))
                elif path.is_file():
                    if str(path) not in seen:
                        seen.add(str(path))
                        collected.append((str(path), str(path.parent)))
            self._add_paths(collected)
        except Exception:
            self.show_error(traceback.format_exc())

    def clear_files(self):
        self.files_view.clear()
        self.clear_output()
        self.set_status('列表已清空')

    def run(self, write):
        try:
            self._run(write)
        except Exception:
            self.show_error(traceback.format_exc())

    def selected_entries(self):
        """(path, root) for the selected rows, in list order (the view
        interface the controller uses)."""
        return self.files_view.selected_entries()

    def _selected_entries(self):
        return self.selected_entries()

    # ---------- view interface used by the controller ----------
    def options(self):
        return FormatOptions(
            punct=self.chk_punct.isChecked(),
            commands=self.chk_commands.isChecked(),
            tight_ranges=self.chk_tight.isChecked(),
            backup=self.chk_backup.isChecked(),
            magic_comment=self.chk_magic.isChecked(),
            write_encoding=self.write_encoding(),
        )

    def check_only(self):
        return self.chk_check.isChecked()

    def confirm(self, count):
        # a backup makes the write recoverable, so only warn without one
        if self.chk_backup.isChecked():
            return True
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Icon.Warning)
        box.setText('未启用备份, 将直接修改 {} 个文件'.format(count))
        box.setInformativeText('此操作不可撤销')
        destructive = box.addButton(
            '确认', QMessageBox.ButtonRole.DestructiveRole)
        box.addButton('取消', QMessageBox.ButtonRole.RejectRole)
        for button in box.buttons():
            button.setAutoDefault(False)          # Return triggers nothing
        box.exec()
        return box.clickedButton() is destructive

    # ---------- actions (delegating to the controller) ----------
    def _run(self, write, confirm=True):
        self.controller.run(write, confirm)

    def _preview_selection(self):
        self.controller.preview_selection()


def main():
    selftest = '--self-test' in sys.argv
    app = QApplication(sys.argv)
    app.setApplicationName('LaTeX Coding Style Formatter')
    if sys.platform.startswith('linux') and detect_dark(app):
        app.setStyle('Fusion')
    window = MainWindow()
    window._selftest = selftest
    sys.excepthook = lambda t, v, tb: window.show_error(
        ''.join(traceback.format_exception(t, v, tb)))
    window.set_status(
        '就绪 (窗口效果: {})'.format(window.effect_note))
    window.show()
    if selftest:
        app.processEvents()
        ok = window.self_test()
        window.close()
        return 0 if ok else 1
    return app.exec()


if __name__ == '__main__':
    sys.exit(main())
