#!/usr/bin/env python3
r"""Tkinter GUI for format_tex.py: insert CJK/Latin spacing in TeX files
with a file picker, rule toggles, and a diff preview pane.

Usage
-----
    python3 format_tex_gui.py
"""

import difflib
import shutil
import sys
import tkinter as tk
import tkinter.font as tkfont
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from format_tex import FormatOptions, format_file

ENCODINGS = ['utf-8', 'gb18030', 'gbk', 'gb2312', 'big5', 'utf-16', 'latin-1']


class App:

    def __init__(self, root):
        self.root = root
        root.title('TeX 中英文混排格式化工具')
        root.geometry('940x680')
        root.minsize(720, 520)

        fixed = tkfont.nametofont('TkFixedFont')

        top = tk.Frame(root)
        top.pack(fill='x', padx=8, pady=(8, 4))
        tk.Button(top, text='选择文件…', command=self.add_files,
                  width=12).pack(side='left')
        tk.Button(top, text='清空列表', command=self.clear_files,
                  width=10).pack(side='left', padx=(6, 0))

        list_frame = tk.Frame(root)
        list_frame.pack(fill='x', padx=8)
        self.file_list = tk.Listbox(list_frame, height=6)
        list_scroll = tk.Scrollbar(list_frame, command=self.file_list.yview)
        self.file_list.configure(yscrollcommand=list_scroll.set)
        self.file_list.pack(side='left', fill='x', expand=True)
        list_scroll.pack(side='right', fill='y')

        options = tk.LabelFrame(root, text='选项')
        options.pack(fill='x', padx=8, pady=6)
        self.var_punct = tk.BooleanVar(value=True)
        self.var_commands = tk.BooleanVar(value=True)
        self.var_tight = tk.BooleanVar(value=True)
        self.var_backup = tk.BooleanVar(value=True)
        self.var_check = tk.BooleanVar(value=False)
        for row, cols in enumerate([
            [('半角标点前后加空格', self.var_punct),
             ('CJK 与命令之间加空格', self.var_commands)],
            [('页码范围保持紧凑', self.var_tight),
             ('生成备份文件 (.bak)', self.var_backup)],
            [('仅检查 (不写入文件)', self.var_check), None],
        ]):
            for col, item in enumerate(cols):
                if item is None:
                    continue
                text, var = item
                tk.Checkbutton(options, text=text, variable=var,
                               anchor='w').grid(row=row, column=col,
                                                sticky='w', padx=8, pady=2)
        options.columnconfigure(0, weight=1)
        options.columnconfigure(1, weight=1)

        enc_frame = tk.LabelFrame(root, text='文件编码')
        enc_frame.pack(fill='x', padx=8, pady=(0, 6))
        tk.Label(enc_frame, text='输入编码').pack(side='left', padx=(8, 2))
        self.enc_in = ttk.Combobox(enc_frame, values=ENCODINGS, width=12)
        self.enc_in.set('utf-8')
        self.enc_in.pack(side='left')
        tk.Label(enc_frame, text='输出编码').pack(side='left', padx=(16, 2))
        self.enc_out = ttk.Combobox(enc_frame, values=ENCODINGS, width=12)
        self.enc_out.set('utf-8')
        self.enc_out.pack(side='left')
        tk.Label(enc_frame, text='(默认 utf-8; 也可输入任意编码名, 如 gb18030)').pack(
            side='left', padx=(10, 8))

        actions = tk.Frame(root)
        actions.pack(fill='x', padx=8, pady=(0, 4))
        tk.Button(actions, text='预览差异', command=lambda: self.run(write=False),
                  width=12).pack(side='left')
        tk.Button(actions, text='应用格式化', command=lambda: self.run(write=True),
                  width=12).pack(side='left', padx=(6, 0))

        out_frame = tk.Frame(root)
        out_frame.pack(fill='both', expand=True, padx=8)
        self.output = tk.Text(out_frame, font=fixed, wrap='none',
                              state='disabled', background='#fafafa')
        y_scroll = tk.Scrollbar(out_frame, command=self.output.yview)
        x_scroll = tk.Scrollbar(out_frame, command=self.output.xview,
                                orient='horizontal')
        self.output.configure(yscrollcommand=y_scroll.set,
                              xscrollcommand=x_scroll.set)
        self.output.grid(row=0, column=0, sticky='nsew')
        y_scroll.grid(row=0, column=1, sticky='ns')
        x_scroll.grid(row=1, column=0, sticky='ew')
        out_frame.rowconfigure(0, weight=1)
        out_frame.columnconfigure(0, weight=1)

        self.status = tk.StringVar(value='就绪')
        tk.Label(root, textvariable=self.status, anchor='w',
                 relief='sunken').pack(fill='x', side='bottom')

    def set_status(self, text):
        self.status.set(text)

    def append(self, text):
        self.output.configure(state='normal')
        self.output.insert('end', text)
        self.output.configure(state='disabled')

    def clear_output(self):
        self.output.configure(state='normal')
        self.output.delete('1.0', 'end')
        self.output.configure(state='disabled')

    def add_files(self):
        names = filedialog.askopenfilenames(
            title='选择 TeX 文件',
            filetypes=[('TeX 文件', '*.tex'), ('所有文件', '*.*')])
        existing = set(self.file_list.get(0, tk.END))
        added = 0
        for name in names:
            if name not in existing:
                self.file_list.insert(tk.END, name)
                added += 1
        self.set_status('已选择 {} 个文件 (新增 {} 个)'.format(
            self.file_list.size(), added))

    def clear_files(self):
        self.file_list.delete(0, tk.END)
        self.clear_output()
        self.set_status('列表已清空')

    def run(self, write):
        paths = [Path(p) for p in self.file_list.get(0, tk.END)]
        if not paths:
            self.set_status('请先选择 TeX 文件')
            return
        opts = FormatOptions(
            punct=self.var_punct.get(),
            commands=self.var_commands.get(),
            tight_ranges=self.var_tight.get(),
            backup=self.var_backup.get(),
            read_encoding=self.enc_in.get().strip(),
            write_encoding=self.enc_out.get().strip(),
        )

        results = []
        for path in paths:
            entry = {'path': path, 'count': 0, 'changed': False,
                     'error': None, 'diff': [], 'result': None}
            if not path.is_file():
                entry['error'] = '文件不存在'
            else:
                try:
                    source, result, count = format_file(path, opts)
                    entry['result'] = result
                    entry['count'] = count
                    entry['diff'] = list(difflib.unified_diff(
                        source.splitlines(), result.splitlines(),
                        fromfile=str(path) + ' (原文件)',
                        tofile=str(path) + ' (格式化后)', lineterm=''))
                    entry['changed'] = bool(entry['diff'])
                except (ValueError, OSError, LookupError) as exc:
                    entry['error'] = str(exc)
            results.append(entry)

        will_write = write and not self.var_check.get()
        n_change = sum(1 for e in results if e['changed'])
        if will_write and n_change and not messagebox.askyesno(
                '确认', '将修改 {} 个文件, 是否继续?'.format(n_change)):
            will_write = False

        self.clear_output()
        write_errors = 0
        for e in results:
            self.append('== {} ==\n'.format(e['path']))
            if e['error']:
                self.append('错误: {}\n\n'.format(e['error']))
                continue
            if not e['changed']:
                self.append('已符合格式, 无需修改\n\n')
                continue
            self.append('\n'.join(e['diff']) + '\n')
            if will_write:
                try:
                    data = e['result'].encode(opts.write_encoding)
                except (ValueError, LookupError) as exc:
                    self.append('错误: 无法以 {} 编码输出: {}\n\n'.format(
                        opts.write_encoding, exc))
                    write_errors += 1
                    continue
                if opts.backup:
                    backup = e['path'].with_name(e['path'].name + '.bak')
                    if not backup.exists():
                        shutil.copy2(e['path'], backup)
                with open(e['path'], 'wb') as fh:
                    fh.write(data)
                self.append('>>> 已写入 ({} 处插入)\n\n'.format(e['count']))
            else:
                self.append('>>> 需 {} 处修改 [未写入]\n\n'.format(e['count']))

        errors = sum(1 for e in results if e['error']) + write_errors
        unchanged = sum(1 for e in results if not e['error'] and not e['changed'])
        suffix = '  [仅检查模式]' if self.var_check.get() else ''
        self.set_status('需修改: {}  已符合: {}  错误: {}{}'.format(
            n_change, unchanged, errors, suffix))


def main():
    root = tk.Tk()
    App(root)
    root.mainloop()
    return 0


if __name__ == '__main__':
    sys.exit(main())
