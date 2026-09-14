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

from PySide6.QtCore import QEvent, QPoint, QPointF, QRect, \
    QTimer, QUrl, Qt
from PySide6.QtGui import QAction, QColor, QDropEvent, QFont, \
    QFontDatabase, QFontMetrics, QKeySequence, QPalette, \
    QTextCharFormat, QTextCursor
from PySide6.QtWidgets import (QAbstractItemView, QApplication, QCheckBox,
                               QComboBox, QFileDialog, QFrame, QHBoxLayout,
                               QInputDialog, QLabel, QListWidget,
                               QMainWindow, QMessageBox, QPlainTextEdit,
                               QPushButton, QSplitter, QStyle,
                               QStyleOptionComboBox, QVBoxLayout,
                               QWidget)

from format_tex import FormatOptions, format_file, scan_directory
from native_menu import (CUSTOM_SENTINEL, build_menu, menu_entries,
                         popup_native_menu)
from platform_effects import (apply_effects, arrange_in_front,
                              band_above_content,
                              band_height, band_material_name,
                              create_native_switch, debug_enabled,
                              has_native_switch, last_material_view,
                              last_sidebar_view, last_title_view,
                              lights_inset,
                              native_switch_state, native_switch_view,
                              minimize_window, native_window_color,
                              notes,
                              place_native_switch, prepare_qt,
                              reposition_materials, set_sidebar_width,
                              title_gap, zoom_window)

ENCODINGS = ['同输入', 'utf-8', 'gb18030', 'gbk', 'gb2312', 'big5',
             'utf-16', 'latin-1']
EXTENSIONS = ['.tex', '.ctx', '.sty', '.cls', '.txt']

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

        self._plus = QLabel('+')
        plus_font = self._plus.font()
        plus_font.setPointSize(54)
        plus_font.setWeight(QFont.Weight.Light)
        self._plus.setFont(plus_font)
        self._plus.setAlignment(Qt.AlignmentFlag.AlignCenter)
        muted = self.palette().color(QPalette.ColorRole.PlaceholderText)
        self._plus.setStyleSheet('color: {};'.format(muted.name()))
        playout.addWidget(self._plus)

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

        self.btn_clear = QPushButton('清空列表')
        self.btn_clear.clicked.connect(self.clear_files)
        slayout.addWidget(self.btn_clear)
        self.file_list = DropListWidget(self.drop_paths, self.add_files,
                                        self.scan_dir)
        self.file_list.setStyleSheet(
            'QListWidget { background: transparent; }'
            'QListWidget::item { background: transparent; }')
        self.file_list.viewport().setAutoFillBackground(False)
        slayout.addWidget(self.file_list, 1)
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
        outer.addWidget(splitter, 1)

        options = QLabel('选项:')
        layout.addWidget(options)
        grid = QHBoxLayout()
        col1 = QVBoxLayout()
        col2 = QVBoxLayout()
        col3 = QVBoxLayout()
        self.chk_punct = QCheckBox('半角标点前后加空格')
        self.chk_punct.setChecked(True)
        self.chk_commands = QCheckBox('CJK 与命令之间加空格')
        self.chk_commands.setChecked(True)
        self.chk_tight = QCheckBox('页码范围保持紧凑')
        self.chk_tight.setChecked(True)
        self.chk_backup = QCheckBox('生成备份文件 (.bak)')
        self.chk_backup.setChecked(True)
        self.chk_magic = QCheckBox('添加编码魔法注释')
        self.chk_magic.setChecked(True)
        self.chk_check = QCheckBox('仅检查 (不写入文件)')
        for w in (self.chk_punct, self.chk_tight, self.chk_magic):
            col1.addWidget(w)
        for w in (self.chk_commands, self.chk_backup, self.chk_check):
            col2.addWidget(w)
        grid.addLayout(col1)
        grid.addLayout(col2)
        grid.addStretch(1)
        layout.addLayout(grid)

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
        self.btn_preview = QPushButton('预览差异')
        self.btn_preview.clicked.connect(lambda: self.run(write=False))
        self.btn_apply = QPushButton('应用格式化')
        self.btn_apply.clicked.connect(lambda: self.run(write=True))
        actions.addWidget(self.btn_preview)
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
        reposition_materials(self)

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
            reposition_materials(self)

    def apply_window_effects(self):
        self.effect_note = apply_effects(self, self.dark)
        self._effects_applied = True
        self._setup_native_switch()
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
            shutil.rmtree(drop_dir, ignore_errors=True)

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

    def _add_paths(self, paths):
        existing = {self.file_list.item(i).text()
                    for i in range(self.file_list.count())}
        added = 0
        for name in paths:
            if name not in existing:
                self.file_list.addItem(name)
                added += 1
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
            initial = str(Path(self.file_list.item(0).text()).parent)
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
        self._add_paths([str(p) for p in matches])

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
                            collected.append(str(m))
                elif path.is_file():
                    n_files += 1
                    if str(path) not in seen:
                        seen.add(str(path))
                        collected.append(str(path))
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

    def _run(self, write):
        paths = [Path(self.file_list.item(i).text())
                 for i in range(self.file_list.count())]
        if not paths:
            self.set_status('请先选择 TeX 文件')
            return
        opts = FormatOptions(
            punct=self.chk_punct.isChecked(),
            commands=self.chk_commands.isChecked(),
            tight_ranges=self.chk_tight.isChecked(),
            backup=self.chk_backup.isChecked(),
            magic_comment=self.chk_magic.isChecked(),
            write_encoding=self.write_encoding(),
        )

        results = []
        for path in paths:
            entry = {'path': path, 'count': 0, 'changed': False,
                     'error': None, 'diff': [], 'result': None,
                     'read_enc': None}
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

        will_write = write and not self.chk_check.isChecked()
        n_change = sum(1 for e in results if e['changed'])
        if will_write and n_change and QMessageBox.question(
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
                if opts.backup:
                    backup = e['path'].with_name(e['path'].name + '.bak')
                    if not backup.exists():
                        shutil.copy2(e['path'], backup)
                with open(e['path'], 'wb') as fh:
                    fh.write(data)
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
