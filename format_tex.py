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

* Tie mode (``tie=True`` / ``--tie``) makes *every* separator a
  non-breaking tie '~': the ones the formatter inserts *and* whitespace
  already present at those boundaries, collapsed to a single '~'. Applies
  to CJK <-> Latin (广义~Fibonacci~行列式), CJK <-> $...$ (如~$\Gamma$~排正体),
  CJK <-> control words (无需写标题~\cite{1}), the space after \verb, and
  the half-width punctuation gaps (式~(1)、2)~字体). The '~' before \verb
  is used either way. A control word stays attached to a following
  math/verb (``\cite $x$`` is left as is, matching the non-tie rules);
  protected regions keep their internal spaces.

Never modified: verbatim environments, % comments, ``...'' quoted spans.

The CLI forces UTF-8 on stdout/stderr so CJK output works on Windows
consoles and pipes (cp1252).

Usage
-----
    python3 format_tex.py file.tex [more.tex ...]          format in place
                          (original kept as backup/<name>.bak next to
                          the scanned root; see <root>/backup/...)
    python3 format_tex.py --check file.tex [more.tex ...]  report only
    python3 format_tex.py --no-punct --no-commands --loose-ranges
                          --no-backup file.tex             toggle rule sets
    python3 format_tex.py --tie file.tex                    use '~' instead
                          of every inserted space (see above)
    python3 format_tex.py --input-encoding gb2312
                          --output-encoding utf-8 file.tex choose encodings
                          (input is auto-detected per file by default;
                          --input-encoding overrides it; output defaults
                          to each file's detected encoding)
    python3 format_tex.py --no-magic-comment file.tex
                          skip "% !TeX encoding" magic comments
                          (default: add when missing)
    python3 format_tex.py --extension .ctx .     scan the directory for
                          *.ctx files (--recursive to include subdirs)
"""

import argparse
import codecs
import difflib
import re
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

try:
    import chardet
except ImportError:
    chardet = None

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
MAGIC_COMMENT_RE = re.compile(r'^\s*%\s*!\s*tex\s+encoding',
                              re.IGNORECASE | re.MULTILINE)
KNOWN_ENCODINGS = ('gbk', 'gb18030', 'utf-8', 'utf-16', 'ascii',
                   'utf-8-sig', 'big5')


def _score_cjk(text):
    """Fraction of non-ASCII characters landing in common CJK ranges."""
    good = bad = 0
    for ch in text:
        cp = ord(ch)
        if cp < 0x80:
            continue
        if (0x4E00 <= cp <= 0x9FFF or 0x3400 <= cp <= 0x4DBF
                or 0x3000 <= cp <= 0x303F or 0xFF00 <= cp <= 0xFFEF):
            good += 1
        else:
            bad += 1
    total = good + bad
    return good / total if total else 1.0


def _heuristic_encoding(data):
    """Dependency-free fallback: BOM / strict UTF-8 trial / scored CJK."""
    if data.startswith(codecs.BOM_UTF8):
        return 'utf-8-sig'
    if data[:2] in (codecs.BOM_UTF16_LE, codecs.BOM_UTF16_BE):
        return 'utf-16'
    try:
        data.decode('utf-8')
        return 'utf-8'
    except UnicodeDecodeError:
        pass
    best = None
    for enc in ('gb18030', 'big5'):
        try:
            ratio = _score_cjk(data.decode(enc))
        except UnicodeDecodeError:
            continue
        if best is None or ratio > best[1]:
            best = (enc, ratio)
    if best is not None:
        return best[0]
    for enc in ('gb18030', 'latin-1'):
        try:
            data.decode(enc)
            return enc
        except (UnicodeDecodeError, LookupError):
            continue
    return 'latin-1'


def detect_encoding(data):
    """Auto-detect the encoding of ``data`` (bytes): BOM sniff, then
    chardet (when available) normalized to a known set, validated by a
    trial decode, with a stdlib heuristic as fallback."""
    if data.startswith(codecs.BOM_UTF8):
        return 'utf-8-sig'
    if data[:2] in (codecs.BOM_UTF16_LE, codecs.BOM_UTF16_BE):
        return 'utf-16'
    candidate = None
    if chardet is not None:
        enc = chardet.detect(data)['encoding'] or 'utf-8'
        low = enc.lower().replace('_', '-')
        if low == 'gb2312':
            candidate = 'gb18030'
        elif low.startswith('iso-8859'):
            candidate = 'gbk'
        elif low in KNOWN_ENCODINGS:
            candidate = low
        else:
            candidate = 'gb18030'
        try:
            data.decode(candidate)
            return candidate
        except (UnicodeDecodeError, LookupError):
            pass
    return _heuristic_encoding(data)


def add_magic_comment(text, encoding):
    """Prepend a ``% !TeX encoding`` comment when none exists; returns
    (text, added)."""
    if MAGIC_COMMENT_RE.search(text):
        return text, False
    nl = '\r\n' if '\r\n' in text[:200] else '\n'
    return '% !TeX encoding = {}{}'.format(encoding, nl) + text, True


BACKUP_DIRNAME = 'backup'


@dataclass
class FormatOptions:
    """Formatting toggles; the defaults reproduce the rules as applied.

    ``read_encoding=None`` means auto-detect per file;
    ``write_encoding=None`` means "write back in the detected encoding".
    """

    punct: bool = True
    commands: bool = True
    tight_ranges: bool = True
    backup: bool = True
    magic_comment: bool = True
    tie: bool = False
    read_encoding: Optional[str] = None
    write_encoding: Optional[str] = None

    def effective_write_encoding(self, read_encoding):
        return self.write_encoding or read_encoding


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


def apply_spacing(text, punct=True, commands=True, tie=False):
    cjk = '[' + HAN + ']'
    lat = '[A-Za-z0-9]'
    math_ph = '\x00M\\d+\x01'
    verb_ph = '\x00V\\d+\x01'
    # every space this function inserts uses ``sep``: '~' (a tie) when the
    # tie option is on, a plain space otherwise
    sep = '~' if tie else ' '

    def latin_math(m):
        if m.group(1).startswith('\\'):
            return m.group(0)
        return m.group(1) + sep + m.group(2)

    def latin_verb(m):
        if m.group(1).startswith('\\'):
            return m.group(0)
        return m.group(1) + sep + m.group(2)

    rules = [
        (f'({cjk})({lat})', r'\1' + sep + r'\2'),
        (f'({lat})({cjk})', r'\1' + sep + r'\2'),
    ]
    if punct:
        rules += [
            (f'({cjk})\\(', r'\1' + sep + r'('),
            (f'\\)({cjk})', r')' + sep + r'\1'),
            (f'({cjk})([{SENT}])(?={lat})', r'\1' + sep + r'\2'),
            (f'({lat})\\.({cjk})', r'\1.' + sep + r'\2'),
        ]
    rules += [
        (f'({cjk})({math_ph})', r'\1' + sep + r'\2'),
        (f'({math_ph})({cjk})', r'\1' + sep + r'\2'),
        (f'({math_ph})({lat})', r'\1' + sep + r'\2'),
        (f'({cjk})({verb_ph})', r'\1~\2'),
        (f'({cjk})[ \\t]+({verb_ph})', r'\1~\2'),
        (f'({verb_ph})([{HAN}A-Za-z0-9])', r'\1' + sep + r'\2'),
    ]
    if commands:
        rules.append(
            (f'({cjk})(\\\\(?!textsuperscript)[A-Za-z]+)',
             r'\1' + sep + r'\2'))

    if tie:
        # Tie mode is consistent: whitespace already in the source at any
        # of the boundaries above is collapsed to a single '~' too, not
        # just the separators we insert. Protected regions (math, \verb,
        # verbatim, comments, quotes) are placeholders here and untouched.

        def latin_math_space(m):
            if m.group(1).startswith('\\'):
                return m.group(0)          # a command stays attached
            return m.group(1) + '~' + m.group(2)

        def latin_verb_space(m):
            if m.group(1).startswith('\\'):
                return m.group(0)
            return m.group(1) + '~' + m.group(2)

        rules += [
            (f'({cjk})[ \\t]+({lat})', r'\1~\2'),
            (f'({lat})[ \\t]+({cjk})', r'\1~\2'),
            (f'({cjk})[ \\t]+({math_ph})', r'\1~\2'),
            (f'({math_ph})[ \\t]+({cjk})', r'\1~\2'),
            (f'({math_ph})[ \\t]+({lat})', r'\1~\2'),
            (f'({verb_ph})[ \\t]+([{HAN}A-Za-z0-9])', r'\1~\2'),
            (f'((?:\\\\[A-Za-z]+)|{lat})[ \\t]+({math_ph})',
             latin_math_space),
            (f'((?:\\\\[A-Za-z]+)|{lat})[ \\t]+({verb_ph})',
             latin_verb_space),
        ]
        if punct:
            rules += [
                (f'({cjk})[ \\t]+\\(', r'\1~('),
                (f'\\)[ \\t]+({cjk})', r')~\1'),
                (f'({cjk})([{SENT}])[ \\t]+(?={lat})', r'\1\2~'),
                (f'({lat})\\.[ \\t]+({cjk})', r'\1.~\2'),
            ]
        if commands:
            rules.append(
                (f'({cjk})[ \\t]+(\\\\(?!textsuperscript)[A-Za-z]+)',
                 r'\1~\2'))

    total = 0
    for pattern, repl in rules:
        text, n = re.subn(pattern, repl, text)
        total += n

    text, n = re.subn(
        r'((?:\\[A-Za-z]+)|[A-Za-z0-9])(' + math_ph + ')',
        latin_math, text)
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
        protected, punct=opts.punct, commands=opts.commands, tie=opts.tie)
    return prot.restore(spaced), count


def format_file(path, opts=None):
    """Read a file (auto-detect its encoding unless overridden), format
    it, and return (source, result, count, read_encoding); raises on IO
    or unbalanced-$ errors."""
    opts = opts or FormatOptions()
    with open(path, 'rb') as fh:
        raw = fh.read()
    read_enc = (opts.read_encoding if opts.read_encoding is not None
                else detect_encoding(raw))
    source = raw.decode(read_enc)
    result, count = format_source(source, opts)
    if opts.magic_comment:
        out_enc = opts.effective_write_encoding(read_enc)
        result, _added = add_magic_comment(result, out_enc)
    return source, result, count, read_enc


def backup_path(path, root=None):
    """Where the backup copy of ``path`` goes: ``<root>/backup/<relative
    path>.bak``, mirroring subdirectories. ``root`` is the scanned root
    the file came from (a directory argument, or a dropped/selected
    folder); it defaults to the file's own directory, so a loose file
    gets ``<dir>/backup/<name>.bak``."""
    path = Path(path)
    root = Path(root) if root is not None else path.parent
    try:
        rel = path.relative_to(root)
    except ValueError:
        rel = Path(path.name)
    return root / BACKUP_DIRNAME / rel.parent / (path.name + '.bak')


def make_backup(path, root=None):
    """Refresh the backup copy of ``path`` (created on demand, always
    overwritten), mirroring the source layout inside the backup folder.
    Returns the backup path; raises OSError on failure."""
    target = backup_path(path, root)
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(path, target)
    return target


def process_file(path, check, opts=None, root=None):
    opts = opts or FormatOptions()
    try:
        source, result, count, read_enc = format_file(path, opts)
    except (ValueError, OSError, LookupError) as exc:
        print('{}: ERROR: {}'.format(path, exc))
        return True, False
    out_enc = opts.effective_write_encoding(read_enc)
    enc_info = read_enc if read_enc == out_enc else '{} -> {}'.format(
        read_enc, out_enc)

    orig = source.splitlines()
    new = result.splitlines()
    diff = list(difflib.unified_diff(
        orig, new,
        fromfile=str(path) + ' (original)',
        tofile=str(path) + ' (formatted)',
        lineterm=''))

    if not diff:
        print('{}: already formatted, no changes ({})'.format(path, enc_info))
        return False, False

    changed = sum(1 for a, b in zip(orig, new) if a != b)
    changed += abs(len(orig) - len(new))
    print('{}: {} spacing insertion(s) on {} line(s) ({})'.format(
        path, count, changed, enc_info))
    print('\n'.join(diff))

    if not check:
        try:
            data = result.encode(out_enc)
        except (ValueError, LookupError) as exc:
            print('{}: ERROR: cannot encode output as {}: {}'.format(
                path, out_enc, exc))
            return True, False
        try:
            if opts.backup:
                backup = make_backup(path, root)
                print('{}: original saved to {}'.format(path, backup))
            with open(path, 'wb') as fh:
                fh.write(data)
        except OSError as exc:
            # never modify a file we could not back up; keep the batch going
            print('{}: ERROR: cannot write (backup failed?): {}'.format(
                path, exc))
            return True, False
        if read_enc == out_enc:
            print('{}: written as {}'.format(path, out_enc))
        else:
            print('{}: written as {} (detected {})'.format(
                path, out_enc, read_enc))
    return False, True


def _use_utf8_stdio():
    for stream in (sys.stdout, sys.stderr):
        try:
            if stream is not None and hasattr(stream, 'reconfigure'):
                stream.reconfigure(encoding='utf-8')
        except (OSError, ValueError):
            pass


def scan_directory(directory, ext, recursive=False):
    """Return files under ``directory`` whose suffix matches ``ext``
    (case-insensitive), sorted. ``ext`` may be a suffix (``tex``,
    ``.tex``) or a glob (``*.tex``, which is what the GUI's extension
    menu holds); ``recursive`` includes subdirectories."""
    ext = ext.strip()
    if not ext:
        ext = '.tex'
    if ext.startswith('*'):
        ext = ext[1:]              # '*.tex' -> '.tex'
    if not ext.startswith('.'):
        ext = '.' + ext
    entries = directory.rglob('*') if recursive else directory.iterdir()
    matches = [p for p in entries
               if p.is_file() and p.suffix.lower() == ext.lower()]
    return sorted(matches)


def expand_targets(paths, ext, recursive=False, with_roots=False):
    """Expand directories among ``paths`` into scanned files, keeping
    plain files as-is; deduplicates while preserving order.

    With ``with_roots`` the result is a list of ``(path, root)`` pairs,
    where ``root`` is the directory the file was scanned from (a
    directory argument, or the file's own directory for plain files).
    Backups mirror the source layout under ``<root>/backup``."""
    targets = []
    seen = set()
    for path in paths:
        if path.is_dir():
            root = path
            candidates = scan_directory(path, ext, recursive)
        else:
            root = path.parent
            candidates = [path]
        for cand in candidates:
            key = str(cand)
            if key not in seen:
                seen.add(key)
                targets.append((cand, root) if with_roots else cand)
    return targets


def main(argv=None):
    _use_utf8_stdio()
    parser = argparse.ArgumentParser(
        description='Insert CJK/Latin spacing in TeX files '
                    '(run with --help to see the full rule set).')
    parser.add_argument('files', nargs='+', type=Path,
                        help='TeX file(s) to format; directories are '
                             'scanned for files matching --extension')
    parser.add_argument('--check', action='store_true',
                        help='report needed changes without writing files')
    parser.add_argument('--no-punct', action='store_true',
                        help='do not add spaces around half-width punctuation')
    parser.add_argument('--no-commands', action='store_true',
                        help='do not add spaces between CJK and control words')
    parser.add_argument('--loose-ranges', action='store_true',
                        help='space page-range dashes too (1820 $-$ 1830)')
    parser.add_argument('--tie', action='store_true',
                        help="insert '~' instead of a space for every "
                             'separator the formatter adds (CJK/Latin, math, '
                             'control words, punctuation)')
    parser.add_argument('--no-backup', action='store_true',
                        help='do not write backups into the backup/ '
                             'folder')
    parser.add_argument('--no-magic-comment', action='store_true',
                        help='do not add "%% !TeX encoding" magic comments '
                             '(default: add when missing)')
    parser.add_argument('--input-encoding', metavar='NAME', default=None,
                        help='override the auto-detected input encoding '
                             '(default: auto-detect)')
    parser.add_argument('--output-encoding', metavar='NAME', default=None,
                        help='encoding for the written file(s) '
                             '(default: same as each file\'s detected '
                             'encoding)')
    parser.add_argument('--extension', metavar='EXT', default='.tex',
                        help='file extension used when scanning directories '
                             '(default: .tex)')
    parser.add_argument('--recursive', action='store_true',
                        help='scan directories recursively (includes '
                             'subdirectories)')
    args = parser.parse_args(argv)
    opts = FormatOptions(
        punct=not args.no_punct,
        commands=not args.no_commands,
        tight_ranges=not args.loose_ranges,
        backup=not args.no_backup,
        magic_comment=not args.no_magic_comment,
        tie=args.tie,
        read_encoding=args.input_encoding,
        write_encoding=args.output_encoding,
    )

    targets = expand_targets(args.files, args.extension, args.recursive,
                             with_roots=True)
    if not targets:
        print('no files found (check --extension / --recursive)')
        return 1

    code = 0
    for path, root in targets:
        if not path.is_file():
            print('{}: ERROR: no such file'.format(path))
            code = 1
            continue
        error, changed = process_file(path, args.check, opts, root=root)
        if error or (changed and args.check):
            code = 1
    return code


if __name__ == '__main__':
    sys.exit(main())
