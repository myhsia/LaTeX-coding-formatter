#!/usr/bin/env python3
r"""Format TeX files for Chinese journal typesetting (《自动化学报》 style):
insert spaces between CJK characters and Latin text, inline math, and
\verb commands.

Rules
-----
* CJK <-> [A-Za-z0-9]        生成pdf文件       -> 生成 pdf 文件
* CJK/Latin <-> $...$        如$\Gamma$排正体  -> 如 $\Gamma$ 排正体
  Math whose content is only '-', e.g. page ranges 1820$-$1830, stays tight.
* CJK <-> control words      无需写标题\cite{1} -> 无需写标题 \cite{1}
  \textsuperscript stays attached: 尚书林\textsuperscript{1,\,2}
* \verb                      对于\verb|..|标题 -> 对于~\verb|..| 标题
  A tie '~' is used between CJK and a following \verb (existing same-line
  spaces are normalized to '~'), a plain space after \verb. \verb|...|
  contents and \verb|..|\linebreak\verb|..| constructs are never modified.
* Half-width punctuation     式(1)、(2)是 -> 式 (1)、(2) 是
  2)字体 -> 2) 字体, 的.fig格式 -> 的 .fig 格式, 1.中国科学院 -> 1. 中国科学院
  Sentence punctuation directly after CJK and followed by whitespace, CJK
  or end of line stays attached (使用说明.); Chinese-style ',;' before CJK
  stays attached; '-' is never spaced (E-mail, CPM-Nets, XXXX-XX-XX);
  full-width punctuation (、。) always stays attached.

Never modified: verbatim environments, % comments, ``...'' quoted spans.

Usage
-----
    python3 format_tex.py file.tex [more.tex ...]          format in place
    python3 format_tex.py --check file.tex [more.tex ...]  report only
    python3 format_tex.py --no-punct --no-commands --loose-ranges
                          --no-backup file.tex             toggle rule sets
"""

import argparse
import difflib
import re
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path

HAN = '\u4e00-\u9fff\u3400-\u4dbf\uf900-\ufaff'
SENT = '.,;:!?'
PH = '\x00{}{}\x01'

VERBATIM_RE = re.compile(r'\\begin\{(verbatim\*?)\}.*?\\end\{\1\}', re.DOTALL)
COMMENT_RE = re.compile(r'(?<!\\)%[^\n]*')
VERB_RE = re.compile(r'\\verb\*?([^a-zA-Z\s*])(.*?)\1')
MATH_RE = re.compile(r'(?<!\\)\$(?:\\.|[^\\$])*?(?<!\\)\$', re.DOTALL)
QUOTE_RE = re.compile(r"``.*?''", re.DOTALL)
DOLLAR_RE = re.compile(r'(?<!\\)\$')
PLACEHOLDER_RE = re.compile('\x00([SVMT])(\\d+)\x01')


@dataclass
class FormatOptions:
    """Formatting toggles; the defaults reproduce the rules as applied."""

    punct: bool = True
    commands: bool = True
    tight_ranges: bool = True
    backup: bool = True


class Protector:
    """Swaps sensitive regions for inert placeholders, and back again."""

    def __init__(self):
        self.items = []

    def add(self, cls, snippet):
        self.items.append(snippet)
        return PH.format(cls, len(self.items) - 1)

    def restore(self, text):
        while True:
            text, n = PLACEHOLDER_RE.subn(
                lambda m: self.items[int(m.group(2))], text)
            if not n:
                return text


def protect(text, tight_ranges=True):
    prot = Protector()

    def take(cls, rx, s):
        return rx.sub(lambda m: prot.add(cls, m.group(0)), s)

    text = take('S', VERBATIM_RE, text)
    text = take('S', COMMENT_RE, text)
    text = take('V', VERB_RE, text)

    dollars = len(DOLLAR_RE.findall(text))
    if dollars % 2:
        raise ValueError(
            'odd number of unescaped $ ({}) - refusing to guess math spans'
            .format(dollars))

    def take_math(m):
        cls = 'T' if (tight_ranges and m.group(0)[1:-1].strip() == '-') else 'M'
        return prot.add(cls, m.group(0))

    text = MATH_RE.sub(take_math, text)
    text = take('S', QUOTE_RE, text)
    return text, prot


def apply_spacing(text, punct=True, commands=True):
    cjk = '[' + HAN + ']'
    lat = '[A-Za-z0-9]'
    math_ph = '\x00M\\d+\x01'
    verb_ph = '\x00V\\d+\x01'

    def latin_verb(m):
        if m.group(1).startswith('\\'):
            return m.group(0)
        return m.group(1) + ' ' + m.group(2)

    rules = [
        (f'({cjk})({lat})', r'\1 \2'),
        (f'({lat})({cjk})', r'\1 \2'),
    ]
    if punct:
        rules += [
            (f'({cjk})\\(', r'\1 ('),
            (f'\\)({cjk})', r') \1'),
            (f'({cjk})([{SENT}])(?={lat})', r'\1 \2'),
            (f'({lat})\\.({cjk})', r'\1. \2'),
        ]
    rules += [
        (f'({cjk})({math_ph})', r'\1 \2'),
        (f'({math_ph})({cjk})', r'\1 \2'),
        (f'({lat})({math_ph})', r'\1 \2'),
        (f'({math_ph})({lat})', r'\1 \2'),
        (f'({cjk})({verb_ph})', r'\1~\2'),
        (f'({cjk})[ \\t]+({verb_ph})', r'\1~\2'),
        (f'({verb_ph})([{HAN}A-Za-z0-9])', r'\1 \2'),
    ]
    if commands:
        rules.append((f'({cjk})(\\\\(?!textsuperscript)[A-Za-z]+)', r'\1 \2'))

    total = 0
    for pattern, repl in rules:
        text, n = re.subn(pattern, repl, text)
        total += n

    text, n = re.subn(
        r'((?:\\[A-Za-z]+)|[A-Za-z0-9])(' + verb_ph + ')',
        latin_verb, text)
    total += n
    return text, total


def format_source(source, opts=None):
    opts = opts or FormatOptions()
    protected, prot = protect(source, tight_ranges=opts.tight_ranges)
    spaced, count = apply_spacing(
        protected, punct=opts.punct, commands=opts.commands)
    return prot.restore(spaced), count


def format_file(path, opts=None):
    """Read a file and return (source, result, count); raises on IO,
    encoding or unbalanced-$ errors."""
    with open(path, encoding='utf-8', newline='') as fh:
        source = fh.read()
    result, count = format_source(source, opts)
    return source, result, count


def process_file(path, check, opts=None):
    opts = opts or FormatOptions()
    try:
        source, result, count = format_file(path, opts)
    except (ValueError, OSError) as exc:
        print('{}: ERROR: {}'.format(path, exc))
        return True, False

    orig = source.splitlines()
    new = result.splitlines()
    diff = list(difflib.unified_diff(
        orig, new,
        fromfile=str(path) + ' (original)',
        tofile=str(path) + ' (formatted)',
        lineterm=''))

    if not diff:
        print('{}: already formatted, no changes'.format(path))
        return False, False

    changed = sum(1 for a, b in zip(orig, new) if a != b)
    changed += abs(len(orig) - len(new))
    print('{}: {} spacing insertion(s) on {} line(s)'.format(path, count, changed))
    print('\n'.join(diff))

    if not check:
        if opts.backup:
            backup = path.with_name(path.name + '.bak')
            if not backup.exists():
                shutil.copy2(path, backup)
                print('{}: original saved to {}'.format(path, backup))
        with open(path, 'w', encoding='utf-8', newline='') as fh:
            fh.write(result)
    return False, True


def main(argv=None):
    parser = argparse.ArgumentParser(
        description='Insert CJK/Latin spacing in TeX files '
                    '(run with --help to see the full rule set).')
    parser.add_argument('files', nargs='+', type=Path,
                        help='TeX file(s) to format')
    parser.add_argument('--check', action='store_true',
                        help='report needed changes without writing files')
    parser.add_argument('--no-punct', action='store_true',
                        help='do not add spaces around half-width punctuation')
    parser.add_argument('--no-commands', action='store_true',
                        help='do not add spaces between CJK and control words')
    parser.add_argument('--loose-ranges', action='store_true',
                        help='space page-range dashes too (1820 $-$ 1830)')
    parser.add_argument('--no-backup', action='store_true',
                        help='do not create a .bak backup before writing')
    args = parser.parse_args(argv)
    opts = FormatOptions(
        punct=not args.no_punct,
        commands=not args.no_commands,
        tight_ranges=not args.loose_ranges,
        backup=not args.no_backup,
    )

    code = 0
    for path in args.files:
        if not path.is_file():
            print('{}: ERROR: no such file'.format(path))
            code = 1
            continue
        error, changed = process_file(path, args.check, opts)
        if error or (changed and args.check):
            code = 1
    return code


if __name__ == '__main__':
    sys.exit(main())
