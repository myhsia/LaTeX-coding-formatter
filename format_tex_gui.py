#!/usr/bin/env python3
r"""PySide6 GUI for format_tex.py: insert CJK/Latin spacing in TeX files
with a file picker, directory scanning, drag & drop of files/folders,
rule toggles, per-file encoding auto-detection, and a colorized diff
preview pane.

Native window chrome: macOS native unified toolbar (system blur),
Windows Mica (see platform_effects.py).

Usage
-----
    python3 format_tex_gui.py
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
                              footer_strip_view, has_native_plus_minus,
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

LIGHT = {
    'text_bg': '#fafafa', 'text_fg': '#1a1a1a',
    'add': '#098658', 'del': '#a31515', 'meta': '#0550ae',
}
DARK = {
    'text_bg': '#1e1e1e', 'text_fg': '#d4d4d4',
    'add': '#4ec9b0', 'del': '#f48771', 'meta': '#569cd6',
}


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
        # comfortable grab area, but Qt draws no line - our own separator
        # (sidebar_sep) is the visible divider and runs the full height
        splitter.setHandleWidth(6)
        splitter.setStyleSheet('QSplitter::handle { background: transparent; }')
        self.splitter = splitter

        # --- left: sidebar (file list over the shared blur) ---
        sidebar = QWidget()
        sidebar.setMinimumWidth(180)
        # explicit transparency: the sidebar shows the native material
        # behind it (and makes the intent testable)
        sidebar.setObjectName('sidebar')
        sidebar.setStyleSheet('#sidebar { background: transparent; }')
        slayout = QVBoxLayout(sidebar)
        slayout.setContentsMargins(12, int(band_height()) + 12, 12, 12)
        slayout.setSpacing(8)

        # macOS-Settings-style grouped box: two rows with a hairline
        group = QFrame()
        group.setObjectName('group')
        group.setStyleSheet(
            '#group { background: rgba(120, 120, 128, 0.12);'
            ' border-radius: 8px; }')
        gv = QVBoxLayout(group)
        gv.setContentsMargins(10, 6, 10, 6)
        gv.setSpacing(0)

        ext_row = QHBoxLayout()
        ext_row.addWidget(QLabel('扩展名'))
        ext_row.addStretch(1)
        self.ext_edit = NativeMenuCombo(EXTENSIONS, '.tex')
        self.ext_edit.fit_to_menu()
        ext_row.addWidget(self.ext_edit)
        gv.addLayout(ext_row)

        rowsep = QFrame()
        rowsep.setObjectName('rowsep')
        rowsep.setFixedHeight(1)
        rowsep.setStyleSheet(
            '#rowsep { background: rgba(120, 120, 128, 0.28); }')
        gv.addWidget(rowsep)

        rec_row = QHBoxLayout()
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
        self.file_list.itemSelectionChanged.connect(self._preview_selection)
        self.file_list.itemSelectionChanged.connect(self._sync_list_buttons)

        frame = QFrame()
        frame.setObjectName('fileframe')
        frame.setStyleSheet(
            '#fileframe { border: 1px solid rgba(120, 120, 128, 0.28);'
            ' border-radius: 8px; }')
        flv = QVBoxLayout(frame)
        flv.setContentsMargins(1, 1, 1, 1)
        flv.setSpacing(0)
        flv.addWidget(self.file_list, 1)
        barsep = QFrame()
        barsep.setObjectName('listbarsep')
        barsep.setFixedHeight(1)
        barsep.setStyleSheet(
            '#listbarsep { background: rgba(120, 120, 128, 0.28); }')
        flv.addWidget(barsep)

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
            int(round(native_plus_minus_height())) + 8 if
            native_plus_minus_height() else 28)
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
        pv.setContentsMargins(12, int(band_height()) + 12, 12, 12)
        pv.setSpacing(8)
        layout = pv
        splitter.addWidget(panel)
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)
        splitter.setSizes([240, 740])
        splitter.splitterMoved.connect(self._splitter_moved)
        self.content_panel = panel
        panel.installEventFilter(self)
        outer.addWidget(splitter, 1)

        # option checkboxes reflow between 2 rows x 3 columns and
        # 3 rows x 2 columns depending on the panel width (see
        # _reflow_options); columns share the width equally
        self.chk_punct = QCheckBox('半角标点后加空格')
        self.chk_punct.setChecked(True)
        self.chk_commands = QCheckBox('CJK 与控制序列空格')
        self.chk_commands.setChecked(True)
        self.chk_tight = QCheckBox('页码范围保持紧凑')
        self.chk_tight.setChecked(True)
        self.chk_backup = QCheckBox('生成备份文件 (backup/*.bak)')
        self.chk_backup.setChecked(True)
        self.chk_magic = QCheckBox('添加编码魔法注释')
        self.chk_magic.setChecked(True)
        self.chk_check = QCheckBox('仅检查 (不写入文件)')
        self.option_checks = [self.chk_punct, self.chk_commands,
                              self.chk_tight, self.chk_backup,
                              self.chk_magic, self.chk_check]
        self.options_grid = QGridLayout()
        self.options_grid.setContentsMargins(0, 0, 0, 0)
        self._option_columns = 0
        layout.addLayout(self.options_grid)

        enc = QHBoxLayout()
        enc.addWidget(QLabel('输出编码'))
        self.enc_out = NativeMenuCombo(ENCODINGS, '同输入')
        self.enc_out.fit_to_menu()
        enc.addWidget(self.enc_out)
        enc.addWidget(QLabel('(输入编码自动检测; 输出默认同输入编码, '
                             '也可输入任意编码名)'))
        enc.addStretch(1)
        layout.addLayout(enc)

        actions = QHBoxLayout()
        # no preview button: selecting an item in the file list previews
        # its diff automatically, and "应用格式化" writes the selection
        self.btn_apply = QPushButton('应用格式化')
        self.btn_apply.clicked.connect(lambda: self.run(write=True))
        actions.addWidget(self.btn_apply)
        actions.addStretch(1)
        layout.addLayout(actions)

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

        self.status_label = QLabel('就绪')
        self.status_label.setObjectName('statuslabel')
        self.status_label.setStyleSheet(
            '#statuslabel {{ color: {}; padding-top: 2px; }}'.format(
                self.palette().color(QPalette.ColorRole.WindowText).name()))
        layout.addWidget(self.status_label)

        self.effect_note = None
        self._effects_applied = False
        self._notes_seen = 0
        prepare_qt(self)
        self._reflow_options()
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

    def set_status(self, text):
        """Update the status line (lives at the bottom of the content
        panel so the sidebar can run the full window height)."""
        self.status_label.setText(text)

    def _splitter_moved(self, *_args):
        self._sync_sidebar_width()
        self._reflow_options()
        reposition_materials(self)

    def _reflow_options(self):
        """Lay the option checkboxes out as 2 rows x 3 columns or
        3 rows x 2 columns, whichever fits the panel width; columns share
        the width equally and spread across the panel."""
        if not self.option_checks:
            return
        spacing = self.options_grid.spacing()
        widest = max(chk.sizeHint().width() for chk in self.option_checks)
        available = self.content_panel.width() - 24      # panel margins
        if available <= 0:
            available = self.width() - self.sidebar.width() - 24
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
        """macOS menu bar, so the standard shortcuts work and are
        discoverable: File (Close, Cmd+W), Edit (the standard editing
        commands) and Window (Minimize Cmd+M, Zoom, Bring All to Front).
        Qt supplies the application menu (About/Services/Hide/Quit Cmd+Q)
        automatically. Before this there was no Close command anywhere, so
        Cmd+W silently did nothing and only Cmd+Q worked."""
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
        reposition_materials(self)

    def changeEvent(self, event):
        super().changeEvent(event)
        if event.type() in (QEvent.Type.ActivationChange,
                            QEvent.Type.WindowStateChange):
            # no title bar in full screen - drop the transparent strip
            full = bool(self.windowState() & Qt.WindowState.WindowFullScreen)
            top = 0 if full else int(band_height())
            self.sidebar.layout().setContentsMargins(12, top + 12, 12, 12)
            self.content_panel.layout().setContentsMargins(
                12, top + 12, 12, 12)
            self._sync_sidebar_width()
            self._reflow_options()
            reposition_materials(self)

    def apply_window_effects(self):
        self.effect_note = apply_effects(self, self.dark)
        self._effects_applied = True
        self._setup_native_switch()
        self._setup_native_plus_minus()
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
        --self-test exit code)."""
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

            # the extension button is auto-sized to its menu (so the
            # popup highlight coincides with it), right-aligned, and
            # leaves the ".tex" label + arrow room
            ext_w = self.ext_edit.width()
            row = self.ext_edit.parentWidget().layout()
            right_aligned = abs(
                self.ext_edit.geometry().right()
                - (self.ext_edit.parentWidget().width()
                   - row.contentsMargins().right())) < 2
            opt = QStyleOptionComboBox()
            self.ext_edit.initStyleOption(opt)
            arrow = self.ext_edit.style().subControlRect(
                QStyle.ComplexControl.CC_ComboBox, opt,
                QStyle.SubControl.SC_ComboBoxArrow, self.ext_edit)
            text_w = QFontMetrics(self.ext_edit.font()).horizontalAdvance(
                '.tex')
            slack = ext_w - arrow.width() - text_w
            ext_ok = right_aligned and slack >= 6
            lines.append('extension button auto-sized ({:.0f} pt, arrow '
                         '{:.0f}, slack {:.0f}) and right-aligned: '
                         '{}'.format(ext_w, arrow.width(), slack, ext_ok))
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
            lines.append('splitter grab width: {} pt'.format(
                self.splitter.handleWidth()))
            ok = ok and self.splitter.handleWidth() >= 4
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
            combos_ok = (isinstance(self.ext_edit, NativeMenuCombo)
                         and isinstance(self.enc_out, NativeMenuCombo)
                         and not self.ext_edit.isEditable()
                         and not self.enc_out.isEditable())
            lines.append('both dropdowns are native popup buttons: '
                         '{}'.format(combos_ok))
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

            enc_menu = popup_for(self.enc_out)
            ext_menu = popup_for(self.ext_edit)
            width_ok = (abs(enc_menu.size().width - self.enc_out.width()) < 1
                        and abs(ext_menu.size().width
                                - self.ext_edit.width()) < 1)
            lines.append('popup width == button width for both dropdowns: '
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
            before = self.file_list.count()
            mime = QMimeData()
            mime.setUrls([QUrl.fromLocalFile(str(drop_dir)),
                          QUrl.fromLocalFile(str(loose))])
            event = QDropEvent(QPointF(5, 5), Qt.DropAction.CopyAction,
                               mime, Qt.MouseButton.LeftButton,
                               Qt.KeyboardModifier.NoModifier)
            self.file_list.dropEvent(event)
            gained = self.file_list.count() - before
            lines.append('drop added files: {} (folder scan '
                         '+ loose file)'.format(gained))
            placeholder_hidden = not placeholder.isVisible()
            lines.append('placeholder hidden after files: {}'.format(
                placeholder_hidden))
            ok = ok and gained >= 2 and placeholder_hidden
            # dropped files remember the folder they were scanned from,
            # so backups can mirror it under <root>/backup
            roots_ok = all(
                self.file_list.item(i).data(ROLE_ROOT) == str(drop_dir)
                for i in range(self.file_list.count()))
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
            self.file_list.clear()
            self.file_list.clearSelection()
            self._add_paths([(str(f1), str(seltmp)),
                             (str(f2), str(seltmp))])
            QApplication.processEvents()
            mode_ok = (self.file_list.selectionMode()
                       == QAbstractItemView.SelectionMode.ExtendedSelection)
            button_ok = (not hasattr(self, 'btn_preview')
                         and hasattr(self, 'btn_apply'))
            # select only the second item: only its diff is shown
            self.file_list.clearSelection()
            self.file_list.item(1).setSelected(True)
            QApplication.processEvents()
            only_second = (str(f2) in self.output.toPlainText()
                           and str(f1) not in self.output.toPlainText())
            # selecting both shows both (Cmd/Shift-style multi-select)
            self.file_list.item(0).setSelected(True)
            QApplication.processEvents()
            both = (str(f1) in self.output.toPlainText()
                    and str(f2) in self.output.toPlainText())
            # rows are painted by our delegate: the selected row must
            # differ from an unselected one (the accent bar). Delegates are
            # captured by grab(), unlike stylesheet item backgrounds.
            self.file_list.clearSelection()
            QApplication.processEvents()
            plain = self.file_list.grab().toImage()
            self.file_list.item(1).setSelected(True)
            QApplication.processEvents()
            painted = self.file_list.grab().toImage()
            row = self.file_list.visualItemRect(self.file_list.item(1))
            off = self.file_list.viewport().mapTo(self.file_list,
                                                  QPoint(0, 0))
            # grabs are in device pixels (Retina), rects in logical points
            ratio = painted.devicePixelRatio()
            tx = int((off.x() + row.right() - 20) * ratio)
            ty = int((off.y() + row.center().y()) * ratio)
            sel_px = painted.pixelColor(tx, ty)
            unsel_px = plain.pixelColor(tx, ty)
            delta = (abs(sel_px.red() - unsel_px.red())
                     + abs(sel_px.green() - unsel_px.green())
                     + abs(sel_px.blue() - unsel_px.blue()))
            delegate_ok = isinstance(self.file_list.itemDelegate(),
                                     FileRowDelegate)
            highlight_ok = delegate_ok and delta > 12
            # applying writes only the selected file
            self.file_list.clearSelection()
            self.file_list.item(1).setSelected(True)
            QApplication.processEvents()
            self._run(write=True, confirm=False)
            QApplication.processEvents()
            apply_ok = ('中文 English 中文' in f2.read_text(encoding='utf-8')
                        and f1.read_text(encoding='utf-8')
                        == '中文English中文\n'
                        and (seltmp / 'backup' / 'two.tex.bak').is_file())
            sel_ok = (mode_ok and button_ok and only_second and both
                      and highlight_ok and apply_ok)
            # framed list with the +/- bar, native icons, names, tooltips
            frame = self.content_panel.parent() and None
            frame = self.findChild(QFrame, 'fileframe')
            frame_ok = (frame is not None
                        and 'border' in frame.styleSheet()
                        and 'background' not in frame.styleSheet())
            add_btn = self.btn_add
            remove_btn = self.btn_remove
            if has_native_plus_minus():
                control = native_plus_minus_view()
                strip = footer_strip_view()
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

                # the control must sit inside the strip, and the strip
                # inside the frame's inner bottom band; re-check after a
                # resize, because stale native rects are exactly the bug
                # this guards against
                contained = (inside(control.frame(), strip.frame())
                             and inside(strip.frame(),
                                        theme_rect(self.file_frame, 1.0)))
                self.resize(self.width() + 40, self.height() + 30)
                QApplication.processEvents()
                contained = (contained
                             and inside(control.frame(), strip.frame())
                             and inside(strip.frame(),
                                        theme_rect(self.file_frame, 1.0)))
                small_ok = native_plus_minus_height() <= 22
                buttons_ok = (control.segmentCount() == 2 and images_ok
                              and strip is not None and contained and small_ok
                              and not self.btn_add.isVisible())
                native_note = 'native NSSegmentedControl + footer strip'
            else:
                buttons_ok = (add_btn.isEnabled()
                              and 'listadd' == add_btn.objectName()
                              and remove_btn.isEnabled()
                              and '−' == remove_btn.text())
                native_note = 'Qt fallback buttons'
            items_ok = all(
                self.file_list.item(i).data(ROLE_PATH)
                and self.file_list.item(i).text()
                == Path(self.file_list.item(i).data(ROLE_PATH)).name
                and self.file_list.item(i).toolTip()
                == self.file_list.item(i).data(ROLE_PATH)
                and not self.file_list.item(i).icon().isNull()
                for i in range(self.file_list.count()))
            # the − button removes exactly the selected rows
            self.file_list.clearSelection()
            self.file_list.item(1).setSelected(True)
            QApplication.processEvents()
            before_remove = self.file_list.count()
            self._remove_selected()
            QApplication.processEvents()
            remaining = [self.file_list.item(i).data(ROLE_PATH)
                         for i in range(self.file_list.count())]
            remove_ok = (self.file_list.count() == before_remove - 1
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

            lines.append('selection: multi-select {}, no preview button {}, '
                         'preview follows selection {}, accent row bar {}, '
                         'apply only the selection {}: {}'.format(
                             mode_ok, button_ok, only_second and both,
                             highlight_ok, apply_ok, sel_ok))
            ok = ok and sel_ok and highlight_ok
            shutil.rmtree(seltmp, ignore_errors=True)
            self.file_list.clear()

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
            self.splitter.setSizes([720, 480])       # squeeze the panel
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
            self.splitter.setSizes([240, 740])
            QApplication.processEvents()

            # --- menu bar: standard commands, so Cmd+W etc. work ---
            menus = [a.text().replace('&', '')
                     for a in self.menuBar().actions()]
            want_keys = {'Undo': 'Ctrl+Z', 'Redo': 'Ctrl+Shift+Z',
                         'Cut': 'Ctrl+X', 'Copy': 'Ctrl+C',
                         'Paste': 'Ctrl+V', 'Select All': 'Ctrl+A'}
            close_key = QKeySequence(QKeySequence.StandardKey.Close)
            menu_ok = (menus[:3] == ['File', 'Edit', 'Window']
                       and len(menus) == len(set(menus))
                       and self.action_close.shortcut() == close_key
                       and self.action_minimize.shortcut().toString()
                       == 'Ctrl+M'
                       and set(self.edit_actions)
                       == set(want_keys) | {'Delete'}
                       and all(self.edit_actions[n].shortcut().toString() == k
                               for n, k in want_keys.items()))
            lines.append('menu bar File/Edit/Window, Close {} ({}), Minimize '
                         '{}: {}'.format(
                             self.action_close.shortcut().toString(),
                             'std' if close_key.toString() == 'Ctrl+W'
                             else '?',
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

    def log_path(self):
        cwd = Path.cwd()
        if os.access(cwd, os.W_OK):
            return cwd / 'format_tex_gui.log'
        return Path.home() / 'format_tex_gui.log'

    def show_error(self, text):
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
        cursor = self.output.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.End)
        if fmt is None:
            cursor.insertText(text)
        else:
            cursor.insertText(text, fmt)
        self.output.setTextCursor(cursor)

    def clear_output(self):
        self.output.clear()

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
        enabled = bool(self.file_list.selectedItems())
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
                # size the row to the control that was really built
                height = int(round(native_plus_minus_height())) + 8
                if height != self.pm_bar.height():
                    self.pm_bar.setFixedHeight(height)
            strip = create_footer_strip(self, self.file_frame, self.pm_bar)
            # the control is inserted unpositioned: place it (and keep it
            # placed through reposition_materials on every layout change)
            if control:
                place_native_plus_minus(self, self.pm_bar)
        except Exception:
            strip = control = False
        if strip and control:
            self.btn_add.setVisible(False)
            self.btn_remove.setVisible(False)
        self._native_plus_minus = bool(control)
        self._sync_list_buttons()

    def _remove_selected(self):
        """Drop the selected rows from the list (the − button)."""
        rows = sorted({self.file_list.row(item)
                       for item in self.file_list.selectedItems()},
                      reverse=True)
        for row in rows:
            self.file_list.takeItem(row)
        self._sync_list_buttons()
        if not self.file_list.count():
            self.clear_output()
            self.set_status('列表已清空')

    def _add_paths(self, paths, root=None):
        """Add paths (str or (str, root) pairs), remembering the scanned
        root each file came from: backups then mirror the source layout
        under <root>/backup. Loose files use their own directory."""
        existing = {self.file_list.item(i).data(ROLE_PATH)
                    for i in range(self.file_list.count())}
        added = 0
        first_added = None
        for entry in paths:
            name, file_root = entry if root is None and isinstance(
                entry, tuple) else (entry, root)
            if name in existing:
                continue
            item = QListWidgetItem(self._file_icon(name), Path(name).name)
            item.setData(ROLE_PATH, name)
            item.setData(ROLE_ROOT, str(file_root or Path(name).parent))
            item.setToolTip(name)
            self.file_list.addItem(item)
            existing.add(name)
            added += 1
            if first_added is None:
                first_added = item
        if first_added is not None and not self.file_list.selectedItems():
            self.file_list.setCurrentItem(first_added)   # triggers preview
        self.set_status('已选择 {} 个文件 (新增 {} 个)'.format(
            self.file_list.count(), added))
        return added

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
            n_dirs = n_files = 0
            seen = set()
            for p in paths:
                path = Path(p)
                if path.is_dir():
                    n_dirs += 1
                    for m in scan_directory(path, ext, recursive):
                        if str(m) not in seen:
                            seen.add(str(m))
                            collected.append((str(m), str(path)))
                elif path.is_file():
                    n_files += 1
                    if str(path) not in seen:
                        seen.add(str(path))
                        collected.append((str(path), str(path.parent)))
            added = self._add_paths(collected)
            self.set_status(
                '拖入 {} 个文件、{} 个目录: 新增 {} 个文件 (共 {} 个)'.format(
                    n_files, n_dirs, added, self.file_list.count()))
        except Exception:
            self.show_error(traceback.format_exc())

    def clear_files(self):
        self.file_list.clear()
        self.clear_output()
        self.set_status('列表已清空')

    def run(self, write):
        try:
            self._run(write)
        except Exception:
            self.show_error(traceback.format_exc())

    def _selected_entries(self):
        """(path, root) for the selected list items, in list order."""
        entries = []
        for i in range(self.file_list.count()):
            item = self.file_list.item(i)
            if not item.isSelected():
                continue
            path = item.data(ROLE_PATH) or item.text()
            root = item.data(ROLE_ROOT)
            entries.append((Path(path), Path(root) if root else None))
        return entries

    def _format_options(self):
        return FormatOptions(
            punct=self.chk_punct.isChecked(),
            commands=self.chk_commands.isChecked(),
            tight_ranges=self.chk_tight.isChecked(),
            backup=self.chk_backup.isChecked(),
            magic_comment=self.chk_magic.isChecked(),
            write_encoding=self.write_encoding(),
        )

    def _run(self, write, confirm=True):
        entries = self._selected_entries()
        if not entries:
            self.set_status('请先选择文件')
            return
        opts = self._format_options()
        self._render(self._collect(entries, opts), write, opts, confirm)

    def _preview_selection(self):
        """Show the diff of the current selection (never writes). Called
        whenever the file list selection changes."""
        entries = self._selected_entries()
        if not entries:
            self.clear_output()
            self.set_status('点击文件列表条目即可预览差异')
            return
        opts = self._format_options()
        self._render(self._collect(entries, opts), False, opts)

    def _collect(self, entries, opts):
        results = []
        for path, root in entries:
            entry = {'path': path, 'root': root, 'count': 0,
                     'changed': False, 'error': None, 'diff': [],
                     'result': None, 'read_enc': None}
            if not path.is_file():
                entry['error'] = '文件不存在'
            else:
                try:
                    source, result, count, read_enc = format_file(path, opts)
                    entry['read_enc'] = read_enc
                    entry['result'] = result
                    entry['count'] = count
                    entry['diff'] = list(difflib.unified_diff(
                        source.splitlines(), result.splitlines(),
                        fromfile=str(path) + ' (原文件)',
                        tofile=str(path) + ' (格式化后)', lineterm=''))
                    entry['changed'] = bool(entry['diff'])
                except Exception as exc:
                    entry['error'] = '{}: {}'.format(type(exc).__name__, exc)
            results.append(entry)
        return results

    def _render(self, results, write, opts, confirm=True):
        will_write = write and not self.chk_check.isChecked()
        n_change = sum(1 for e in results if e['changed'])
        if will_write and n_change and confirm and QMessageBox.question(
                self, '确认', '将修改 {} 个文件, 是否继续?'.format(
                    n_change)) != QMessageBox.StandardButton.Yes:
            will_write = False

        self.clear_output()
        write_errors = 0
        for e in results:
            header = '== {} =='.format(e['path'])
            if e.get('read_enc'):
                header += '  [检测编码: {}]'.format(e['read_enc'])
            self.append(header + '\n')
            if e['error']:
                self.append('错误: {}\n\n'.format(e['error']))
                continue
            if not e['changed']:
                self.append('已符合格式, 无需修改\n\n')
                continue
            for line in e['diff']:
                tag = None
                if line.startswith(('+++', '---', '@@')):
                    tag = 'meta'
                elif line.startswith('+'):
                    tag = 'add'
                elif line.startswith('-'):
                    tag = 'del'
                fmt = getattr(self, 'fmt_' + tag) if tag else None
                self.append(line + '\n', fmt)
            self.append('\n')
            if will_write:
                out_enc = opts.effective_write_encoding(e.get('read_enc'))
                try:
                    data = e['result'].encode(out_enc)
                except (ValueError, LookupError) as exc:
                    self.append('错误: 无法以 {} 编码输出: {}\n\n'.format(
                        out_enc, exc))
                    write_errors += 1
                    continue
                try:
                    if opts.backup:
                        backup = make_backup(e['path'], e.get('root'))
                        self.append('>>> 备份: {}\n'.format(backup))
                    with open(e['path'], 'wb') as fh:
                        fh.write(data)
                except OSError as exc:
                    # never modify a file we could not back up
                    self.append('错误: 无法写入 (备份失败?): {}\n\n'.format(
                        exc))
                    write_errors += 1
                    continue
                self.append('>>> 已写入 ({}, {} 处插入)\n\n'.format(
                    out_enc, e['count']))
            else:
                self.append('>>> 需 {} 处修改 [未写入]\n\n'.format(e['count']))

        errors = sum(1 for e in results if e['error']) + write_errors
        unchanged = sum(1 for e in results
                        if not e['error'] and not e['changed'])
        suffix = '  [仅检查模式]' if self.chk_check.isChecked() else ''
        self.set_status('需修改: {}  已符合: {}  错误: {}{}'.format(
            n_change, unchanged, errors, suffix))


def main():
    selftest = '--self-test' in sys.argv
    app = QApplication(sys.argv)
    app.setApplicationName('LaTeX Coding Style Formatter')
    if sys.platform.startswith('linux') and detect_dark(app):
        app.setStyle('Fusion')
    window = MainWindow()
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
