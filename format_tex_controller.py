#!/usr/bin/env python3
r"""Qt-free application logic, shared by the Qt GUI and the native app.

The controller owns the formatting workflow (collect diffs, render them
with add/del/meta tags, write files with backups, summarise the status)
and talks to the界面 through a tiny view interface, so the same code
drives the Qt widgets and the AppKit views:

    view.selected_entries() -> [(Path, Path | None), ...]
    view.append(text, tag)               # tag: 'add' | 'del' | 'meta' | None
    view.clear_output()
    view.set_status(text, encoding=None, selected=None)  # status-bar segments
    view.options() -> FormatOptions
    view.check_only() -> bool            # the "仅检查" option
    view.confirm(count) -> bool          # ask before writing
"""

import difflib
from pathlib import Path

from format_tex import format_file, make_backup


class FormatController:
    """Asks the view for what it needs; never imports a GUI toolkit."""

    def __init__(self, view):
        self.view = view

    # ---------- entry points ----------
    def run(self, write, confirm=True):
        """Preview (write=False) or apply (write=True) the selection."""
        entries = self.view.selected_entries()
        if not entries:
            self.view.set_status('请先选择文件')
            return
        opts = self.view.options()
        self.render(self.collect(entries, opts), write, opts, confirm)

    def preview_selection(self):
        """Show the diff of the current selection (never writes)."""
        entries = self.view.selected_entries()
        if not entries:
            self.view.clear_output()
            self.view.set_status('点击文件列表条目即可预览差异')
            return
        opts = self.view.options()
        self.render(self.collect(entries, opts), False, opts)

    # ---------- work ----------
    def collect(self, entries, opts):
        """Format each entry and gather its diff (no writes)."""
        results = []
        for path, root in entries:
            path = Path(path)
            entry = {'path': path, 'root': Path(root) if root else None,
                     'count': 0, 'changed': False, 'error': None,
                     'diff': [], 'result': None, 'read_enc': None}
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

    def render(self, results, write, opts, confirm=True):
        """Render the collected results and, when writing, apply them."""
        check_only = bool(self.view.check_only())
        will_write = write and not check_only
        n_change = sum(1 for e in results if e['changed'])
        if will_write and n_change and confirm and not self.view.confirm(
                n_change):
            will_write = False

        self.view.clear_output()
        write_errors = 0
        for e in results:
            header = '== {} =='.format(e['path'])
            self.view.append(header + '\n', None)
            if e['error']:
                self.view.append('错误: {}\n\n'.format(e['error']), None)
                continue
            if not e['changed']:
                self.view.append('已符合格式, 无需修改\n\n', None)
                continue
            for line in e['diff']:
                tag = None
                if line.startswith(('+++', '---', '@@')):
                    tag = 'meta'
                elif line.startswith('+'):
                    tag = 'add'
                elif line.startswith('-'):
                    tag = 'del'
                self.view.append(line + '\n', tag)
            self.view.append('\n', None)
            if will_write:
                out_enc = opts.effective_write_encoding(e.get('read_enc'))
                try:
                    data = e['result'].encode(out_enc)
                except (ValueError, LookupError) as exc:
                    self.view.append(
                        '错误: 无法以 {} 编码输出: {}\n\n'.format(
                            out_enc, exc), None)
                    write_errors += 1
                    continue
                try:
                    if opts.backup:
                        backup = make_backup(e['path'], e.get('root'))
                        self.view.append('>>> 备份: {}\n'.format(backup),
                                         None)
                    with open(e['path'], 'wb') as fh:
                        fh.write(data)
                except OSError as exc:
                    # never modify a file we could not back up
                    self.view.append(
                        '错误: 无法写入 (备份失败?): {}\n\n'.format(exc), None)
                    write_errors += 1
                    continue
                self.view.append('>>> 已写入 ({}, {} 处插入)\n\n'.format(
                    out_enc, e['count']), None)
            else:
                self.view.append('>>> 需 {} 处修改 [未写入]\n\n'.format(
                    e['count']), None)

        errors = sum(1 for e in results if e['error']) + write_errors
        unchanged = sum(1 for e in results
                        if not e['error'] and not e['changed'])
        suffix = '  [仅检查模式]' if check_only else ''
        # the status bar carries the encoding: one value when every selected
        # file shares it, otherwise a generic label
        encodings = {e['read_enc'] for e in results if e.get('read_enc')}
        if len(encodings) == 1:
            encoding = next(iter(encodings))
        elif encodings:
            encoding = '多种编码'
        else:
            encoding = None
        self.view.set_status(
            '需修改: {}  已符合: {}  错误: {}{}'.format(
                n_change, unchanged, errors, suffix),
            encoding=encoding,
            selected='已选择 {} 个文件'.format(len(results)))
