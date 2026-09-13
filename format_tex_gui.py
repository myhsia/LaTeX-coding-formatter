#!/usr/bin/env python3
r"""PySide6 GUI for format_tex.py: insert CJK/Latin spacing in TeX files
with a file picker, directory scanning, rule toggles, per-file encoding
auto-detection, and a colorized diff preview pane.

Native window materials: macOS Liquid Glass / vibrancy, Windows Mica
(see platform_effects.py).

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

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QFontDatabase, QPalette, QTextCharFormat, \
    QTextCursor
from PySide6.QtWidgets import (QApplication, QCheckBox, QComboBox,
                               QFileDialog, QHBoxLayout, QLabel, QListWidget,
                               QMainWindow, QMessageBox, QPlainTextEdit,
                               QPushButton, QVBoxLayout, QWidget)

from format_tex import FormatOptions, format_file, scan_directory
from platform_effects import apply_effects, notes

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


class MainWindow(QMainWindow):

    def __init__(self):
        super().__init__()
        self.setWindowTitle('TeX 中英文混排格式化工具')
        self.resize(980, 700)
        self.setMinimumSize(760, 560)

        self.dark = detect_dark(QApplication.instance())
        self.pal = DARK if self.dark else LIGHT

        central = QWidget()
        self.setCentralWidget(central)
        central.setStyleSheet('background: transparent;')
        layout = QVBoxLayout(central)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(8)

        top = QHBoxLayout()
        pick = QPushButton('选择文件…')
        pick.clicked.connect(self.add_files)
        scan = QPushButton('扫描目录…')
        scan.clicked.connect(self.scan_dir)
        self.btn_clear = QPushButton('清空列表')
        self.btn_clear.clicked.connect(self.clear_files)
        top.addWidget(pick)
        top.addWidget(scan)
        top.addWidget(QLabel('扩展名'))
        self.ext_edit = QComboBox()
        self.ext_edit.setEditable(True)
        self.ext_edit.addItems(EXTENSIONS)
        self.ext_edit.setEditText('.tex')
        top.addWidget(self.ext_edit)
        self.chk_recursive = QCheckBox('含子目录')
        top.addWidget(self.chk_recursive)
        top.addWidget(self.btn_clear)
        top.addStretch(1)
        layout.addLayout(top)

        self.file_list = QListWidget()
        layout.addWidget(self.file_list)

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
        self.enc_out = QComboBox()
        self.enc_out.setEditable(True)
        self.enc_out.addItems(ENCODINGS)
        self.enc_out.setCurrentText('同输入')
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

        self.effect_note = apply_effects(self, self.dark)
        for msg in notes():
            self.show_error(msg + '\n')

    # ---------- helpers ----------

    def log_path(self):
        cwd = Path.cwd()
        if os.access(cwd, os.W_OK):
            return cwd / 'format_tex_gui.log'
        return Path.home() / 'format_tex_gui.log'

    def show_error(self, text):
        try:
            self.append('[内部错误]\n{}\n'.format(text))
            last = text.splitlines()[-1] if text else ''
            self.statusBar().showMessage('内部错误: {}'.format(last))
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
        self.statusBar().showMessage('已选择 {} 个文件 (新增 {} 个)'.format(
            self.file_list.count(), added))

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
                                     self.chk_recursive.isChecked())
        except Exception:
            self.show_error(traceback.format_exc())
            return
        self._add_paths([str(p) for p in matches])

    def clear_files(self):
        self.file_list.clear()
        self.clear_output()
        self.statusBar().showMessage('列表已清空')

    def run(self, write):
        try:
            self._run(write)
        except Exception:
            self.show_error(traceback.format_exc())

    def _run(self, write):
        paths = [Path(self.file_list.item(i).text())
                 for i in range(self.file_list.count())]
        if not paths:
            self.statusBar().showMessage('请先选择 TeX 文件')
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
        self.statusBar().showMessage('需修改: {}  已符合: {}  错误: {}{}'.format(
            n_change, unchanged, errors, suffix))


def main():
    app = QApplication(sys.argv)
    app.setApplicationName('TeX 中英文混排格式化工具')
    if sys.platform.startswith('linux') and detect_dark(app):
        app.setStyle('Fusion')
    window = MainWindow()
    sys.excepthook = lambda t, v, tb: window.show_error(
        ''.join(traceback.format_exception(t, v, tb)))
    window.statusBar().showMessage(
        '就绪 (窗口效果: {})'.format(window.effect_note))
    window.show()
    return app.exec()


if __name__ == '__main__':
    sys.exit(main())
