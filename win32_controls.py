#!/usr/bin/env python3
r"""Native Win32 controls hosted inside the Qt window (Windows only).

Real Win32 controls - ``BUTTON`` checkbox, ``COMBOBOX`` drop-down,
``BUTTON`` push button and ``STATIC`` label - are created with
``CreateWindowExW`` and parented into a Qt slot widget's HWND (the same
mechanism as the file list, so the Qt layout keeps driving the geometry).
Notifications arrive as ``WM_COMMAND`` and the checkbox/label backgrounds
are made transparent through ``WM_CTLCOLORSTATIC``/``WM_CTLCOLORBTN``;
both are observed with a WndProc subclass on the slot.

Every Win32 call declares ``argtypes``/``restype``: without it ctypes
widens Python ints to 32-bit ``c_int`` and truncates the 64-bit
HWNDs/handles/pointers, which faults the process.

The wrappers expose the small API the application already uses
(``isChecked``/``setChecked``, ``currentText``/``setCurrentText``,
``setEnabled``, ``setText``), so the rest of the GUI does not care which
toolkit draws the control. Anything that fails returns ``False`` from
``build()`` and the caller keeps the Qt widget.
"""

import ctypes
import os
import sys
from ctypes import wintypes

LRESULT = ctypes.c_ssize_t
LPARAM = ctypes.c_ssize_t
WPARAM = ctypes.c_size_t

# window styles
WS_CHILD = 0x40000000
WS_VISIBLE = 0x10000000
WS_TABSTOP = 0x00010000
WS_VSCROLL = 0x00200000

# messages
WM_SETTEXT = 0x000C
WM_GETTEXT = 0x000D
WM_GETTEXTLENGTH = 0x000E
WM_SETFONT = 0x0030
WM_DRAWITEM = 0x002B
WM_MEASUREITEM = 0x002C
WM_COMMAND = 0x0111
WM_CTLCOLORLISTBOX = 0x0134
WM_CTLCOLORBTN = 0x0135
WM_CTLCOLORSTATIC = 0x0138
WM_NCDESTROY = 0x0082

# button / draw text
BS_PUSHBUTTON = 0x00000000
BS_AUTOCHECKBOX = 0x00000003
BM_GETCHECK = 0x00F0
BM_SETCHECK = 0x00F1
BST_UNCHECKED = 0x0000
BST_CHECKED = 0x0001
BN_CLICKED = 0
BCM_GETIDEALSIZE = 0x1601
DT_LEFT = 0x0000
DT_VCENTER = 0x0004
DT_SINGLELINE = 0x0020
DT_NOPREFIX = 0x0800
DT_END_ELLIPSIS = 0x8000

# combo box
CBS_DROPDOWNLIST = 0x0003
CBS_OWNERDRAWFIXED = 0x0010
CBS_HASSTRINGS = 0x0200
CB_ADDSTRING = 0x0143
CB_GETCURSEL = 0x0147
CB_GETLBTEXT = 0x0148
CB_GETLBTEXTLEN = 0x0149
CB_SETCURSEL = 0x014E
CB_SETITEMHEIGHT = 0x0153
CB_GETITEMHEIGHT = 0x0154
CB_FINDSTRINGEXACT = 0x0158
CB_SETMINVISIBLE = 0x1701
CBN_SELCHANGE = 1

# owner-draw state / type
ODT_COMBOBOX = 3
ODS_SELECTED = 0x0001
ODS_COMBOBOXEDIT = 0x1000

# static
SS_LEFT = 0x00000000

SPI_GETNONCLIENTMETRICS = 0x0029
NULL_BRUSH = 5
TRANSPARENT = 1
GWLP_WNDPROC = -4


class LOGFONTW(ctypes.Structure):
    _fields_ = [
        ('lfHeight', ctypes.c_long), ('lfWidth', ctypes.c_long),
        ('lfEscapement', ctypes.c_long), ('lfOrientation', ctypes.c_long),
        ('lfWeight', ctypes.c_long), ('lfItalic', ctypes.c_byte),
        ('lfUnderline', ctypes.c_byte), ('lfStrikeOut', ctypes.c_byte),
        ('lfCharSet', ctypes.c_byte), ('lfOutPrecision', ctypes.c_byte),
        ('lfClipPrecision', ctypes.c_byte), ('lfQuality', ctypes.c_byte),
        ('lfPitchAndFamily', ctypes.c_byte),
        ('lfFaceName', wintypes.WCHAR * 32),
    ]


class NONCLIENTMETRICSW(ctypes.Structure):
    _fields_ = [
        ('cbSize', wintypes.UINT), ('iBorderWidth', ctypes.c_int),
        ('iScrollWidth', ctypes.c_int), ('iScrollHeight', ctypes.c_int),
        ('iCaptionWidth', ctypes.c_int), ('iCaptionHeight', ctypes.c_int),
        ('lfCaptionFont', LOGFONTW),
        ('iSmCaptionWidth', ctypes.c_int),
        ('iSmCaptionHeight', ctypes.c_int),
        ('lfSmCaptionFont', LOGFONTW),
        ('iMenuWidth', ctypes.c_int), ('iMenuHeight', ctypes.c_int),
        ('lfMenuFont', LOGFONTW), ('lfStatusFont', LOGFONTW),
        ('lfMessageFont', LOGFONTW),
        ('iPaddedBorderWidth', ctypes.c_int),
    ]


class SIZE(ctypes.Structure):
    _fields_ = [('cx', ctypes.c_long), ('cy', ctypes.c_long)]


class MEASUREITEMSTRUCT(ctypes.Structure):
    _fields_ = [
        ('CtlType', wintypes.UINT), ('CtlID', wintypes.UINT),
        ('itemID', wintypes.UINT), ('itemWidth', wintypes.UINT),
        ('itemHeight', wintypes.UINT), ('itemData', ctypes.c_size_t),
    ]


class DRAWITEMSTRUCT(ctypes.Structure):
    _fields_ = [
        ('CtlType', wintypes.UINT), ('CtlID', wintypes.UINT),
        ('itemID', wintypes.UINT), ('itemAction', wintypes.UINT),
        ('itemState', wintypes.UINT), ('hwndItem', wintypes.HWND),
        ('hDC', ctypes.c_void_p), ('rcItem', wintypes.RECT),
        ('itemData', ctypes.c_size_t),
    ]


WNDPROC = ctypes.WINFUNCTYPE(LRESULT, wintypes.HWND, wintypes.UINT,
                             WPARAM, LPARAM)

_NEXT_ID = [100]
_FONT = None
# host HWND -> {'proc', 'old', 'controls', 'user32', 'gdi32', 'setter'}
_HOSTS = {}
_DEFWINDOWPROC = [None]
# process-wide Windows dark-mode state (undocumented uxtheme ordinals)
_DARK_APP = [False]
_UXTHEME_CACHE = {}


def _as_ptr(obj):
    """A ``c_void_p`` for a ctypes instance (safe for pointer-sized params)."""
    return ctypes.cast(ctypes.byref(obj), ctypes.c_void_p)


def _uxtheme():
    """The uxtheme DLL (or None) with ``SetWindowTheme`` declared."""
    try:
        dll = ctypes.WinDLL('uxtheme', use_last_error=True)
        dll.SetWindowTheme.restype = ctypes.c_long
        dll.SetWindowTheme.argtypes = [wintypes.HWND, wintypes.LPCWSTR,
                                       wintypes.LPCWSTR]
        return dll
    except Exception:
        return None


def _uxtheme_proc(name, ordinal, restype, argtypes):
    """Resolve an undocumented uxtheme export (by name or ordinal).

    ``SetPreferredAppMode``/``AllowDarkModeForWindow``/``FlushMenuThemes``
    are ordinal-only, so they are looked up with ``GetProcAddress`` and
    wrapped with ``WINFUNCTYPE``. Returns None on any failure."""
    key = (name, ordinal)
    if key in _UXTHEME_CACHE:
        return _UXTHEME_CACHE[key]
    proc = None
    try:
        dll = _uxtheme()
        if dll is not None:
            function = getattr(dll, name, None)
            address = None
            if function is not None:
                address = ctypes.cast(function, ctypes.c_void_p).value
            else:
                kernel32 = ctypes.WinDLL('kernel32', use_last_error=True)
                kernel32.GetProcAddress.restype = ctypes.c_void_p
                kernel32.GetProcAddress.argtypes = [ctypes.c_void_p,
                                                    ctypes.c_void_p]
                address = kernel32.GetProcAddress(
                    ctypes.c_void_p(dll._handle),
                    ctypes.c_void_p(ordinal))
            if address:
                proc = ctypes.WINFUNCTYPE(restype, *argtypes)(address)
    except Exception:
        proc = None
    _UXTHEME_CACHE[key] = proc
    return proc


def _allow_dark_mode(hwnd):
    """Opt one window into the dark common-controls theme (Windows 10+)."""
    if not _DARK_APP[0]:
        return
    proc = _uxtheme_proc('AllowDarkModeForWindow', 133, wintypes.BOOL,
                         [wintypes.HWND, wintypes.BOOL])
    if proc is not None:
        try:
            proc(wintypes.HWND(hwnd), True)
        except Exception:
            pass


def configure_dark(dark):
    """Select the process-wide common-controls theme (call once, before any
    control is created). ForceDark when the app is dark, ForceLight
    otherwise, followed by FlushMenuThemes so already-created chrome
    updates."""
    _DARK_APP[0] = bool(dark)
    mode = _uxtheme_proc('SetPreferredAppMode', 135, ctypes.c_int,
                         [ctypes.c_int])
    if mode is not None:
        try:
            mode(2 if dark else 3)          # ForceDark / ForceLight
        except Exception:
            pass
    flush = _uxtheme_proc('FlushMenuThemes', 136, None, [])
    if flush is not None:
        try:
            flush()
        except Exception:
            pass


def _theme_control(hwnd, kind):
    """Theme one hosted control for the current app mode.

    Checkbox/push button/static use ``DarkMode_Explorer``, combos
    ``DarkMode_CFD``. If the dark-mode entry points are unavailable, the
    visual style is dropped so the classic drawing honours the palette
    brushes/text colours we return from ``WM_CTLCOLOR*``."""
    dll = _uxtheme()
    if dll is None:
        return
    if not _DARK_APP[0]:
        try:
            dll.SetWindowTheme(wintypes.HWND(hwnd), 'Explorer', None)
        except Exception:
            pass
        return
    dark_supported = (
        _uxtheme_proc('SetPreferredAppMode', 135, ctypes.c_int,
                      [ctypes.c_int]) is not None
        and _uxtheme_proc('AllowDarkModeForWindow', 133, wintypes.BOOL,
                          [wintypes.HWND, wintypes.BOOL]) is not None)
    if not dark_supported:
        # old Windows: drop the visual style so the classic control honours
        # the palette brush/text colour from WM_CTLCOLOR*
        try:
            dll.SetWindowTheme(wintypes.HWND(hwnd), '', '')
        except Exception:
            pass
        return
    _allow_dark_mode(hwnd)
    theme = 'DarkMode_CFD' if kind == 'combo' else 'DarkMode_Explorer'
    try:
        dll.SetWindowTheme(wintypes.HWND(hwnd), theme, None)
    except Exception:
        pass


def _install_host(control):
    """Install one shared WndProc on the control's host window.

    Every control on a host shares a single subclass (in ``window`` mode
    they all sit on the top-level window), so notifications are dispatched
    to all of them at once instead of nesting one proc per control."""
    host = control._host
    entry = _HOSTS.get(host)
    if entry is None:
        proc = WNDPROC(_host_proc)
        # register before subclassing so no message can arrive while the
        # shared proc is installed but unknown
        entry = {'proc': proc, 'old': None, 'controls': set(),
                 'user32': control._user32, 'gdi32': control._gdi32,
                 'setter': control._setter}
        _HOSTS[host] = entry
        entry['old'] = control._setter(
            host, GWLP_WNDPROC, ctypes.cast(proc, ctypes.c_void_p))
    entry['controls'].add(control)


def _release_host(control):
    entry = _HOSTS.get(control._host)
    if entry is None:
        return
    entry['controls'].discard(control)
    if entry['controls']:
        return
    # restore first, then forget: our proc must never run with no entry
    try:
        if entry['old']:
            entry['setter'](control._host, GWLP_WNDPROC, entry['old'])
    except Exception:
        pass
    _HOSTS.pop(control._host, None)


def _find_control(host, predicate):
    entry = _HOSTS.get(host)
    if entry is None:
        return None
    for control in entry['controls']:
        try:
            if predicate(control):
                return control
        except Exception:
            continue
    return None


def _host_proc(hwnd, msg, wparam, lparam):
    entry = _HOSTS.get(int(hwnd))
    if entry is not None:
        try:
            if msg == WM_COMMAND:
                control = _find_control(
                    int(hwnd), lambda c: (int(wparam) & 0xFFFF) == c._id)
                if control is not None:
                    control._command((int(wparam) >> 16) & 0xFFFF)
            elif msg in (WM_CTLCOLORSTATIC, WM_CTLCOLORBTN):
                control = _find_control(
                    int(hwnd),
                    lambda c: (c.transparent and c._hwnd is not None
                               and int(lparam or 0) == c._hwnd))
                if control is not None:
                    control._ctlcolor_hits += 1
                    control._gdi32.SetTextColor(wparam, control._fg)
                    control._gdi32.SetBkMode(wparam, TRANSPARENT)
                    return int(control._brush or 0)
            elif msg == WM_CTLCOLORLISTBOX:
                # the list box is a child of its combo box
                control = _find_control(
                    int(hwnd),
                    lambda c: (c._hwnd is not None and int(lparam or 0)
                               and c._user32.GetParent(
                                   wintypes.HWND(int(lparam))) == c._hwnd))
                if control is not None:
                    control._ctlcolor_hits += 1
                    control._gdi32.SetTextColor(wparam, control._fg)
                    control._gdi32.SetBkMode(wparam, TRANSPARENT)
                    return int(control._brush or 0)
            elif msg in (WM_DRAWITEM, WM_MEASUREITEM):
                pointer = int(lparam or 0)
                if pointer:
                    header = ctypes.cast(ctypes.c_void_p(pointer),
                                         ctypes.POINTER(wintypes.UINT))
                    ctl_id = int(header[1])
                    control = _find_control(
                        int(hwnd), lambda c: c._id == ctl_id)
                    if control is not None:
                        if msg == WM_DRAWITEM:
                            control._draw_item(
                                ctypes.cast(
                                    ctypes.c_void_p(pointer),
                                    ctypes.POINTER(
                                        DRAWITEMSTRUCT)).contents)
                        else:
                            control._measure_item(
                                ctypes.cast(
                                    ctypes.c_void_p(pointer),
                                    ctypes.POINTER(
                                        MEASUREITEMSTRUCT)).contents)
                        return 1
            elif msg == WM_NCDESTROY:
                for control in list(entry['controls']):
                    _release_host(control)
        except Exception:
            pass
    if entry is not None and entry['old']:
        return entry['user32'].CallWindowProcW(
            entry['old'], hwnd, msg, WPARAM(wparam), LPARAM(lparam))
    user32 = _DEFWINDOWPROC[0]
    if user32 is not None:
        return user32.DefWindowProcW(hwnd, msg, WPARAM(wparam), LPARAM(lparam))
    return 0


def _next_id():
    _NEXT_ID[0] = 100 if _NEXT_ID[0] >= 0xFF00 else _NEXT_ID[0] + 1
    return _NEXT_ID[0]


def _message_font(user32, gdi32):
    """The shell's message font (Segoe UI on modern Windows)."""
    try:
        metrics = NONCLIENTMETRICSW()
        metrics.cbSize = ctypes.sizeof(NONCLIENTMETRICSW)
        if user32.SystemParametersInfoW(
                SPI_GETNONCLIENTMETRICS, metrics.cbSize, _as_ptr(metrics), 0):
            return gdi32.CreateFontIndirectW(_as_ptr(metrics.lfMessageFont))
    except Exception:
        pass
    return None


def make_control_font(slot, user32, gdi32):
    """A Win32 font matching the slot's Qt font, scaled for the window DPI.

    Win32 controls have no notion of ``devicePixelRatio``: a font has to be
    created with a pixel height. Taking the Qt point size at the *logical*
    DPI makes the text half-size on a 200% display, so the Qt font's pixel
    metrics are multiplied by the slot's device pixel ratio to get the
    physical height. The font is shared (all controls share the app font).
    """
    global _FONT
    if _FONT is not None:
        return _FONT
    try:
        from PySide6.QtGui import QFontMetricsF

        font = slot.font()
        try:
            ratio = float(slot.devicePixelRatioF())
        except Exception:
            ratio = 1.0
        metrics = QFontMetricsF(font)
        height = int(round((metrics.ascent() + metrics.descent()) * ratio))
        logfont = LOGFONTW()
        logfont.lfHeight = -max(1, height)
        logfont.lfWeight = 700 if font.bold() else 400
        logfont.lfCharSet = 1                # DEFAULT_CHARSET
        logfont.lfQuality = 5                # CLEARTYPE_QUALITY
        logfont.lfFaceName = str(font.family())[:31]
        _FONT = gdi32.CreateFontIndirectW(_as_ptr(logfont))
    except Exception:
        _FONT = None
    if _FONT is None:
        _FONT = _message_font(user32, gdi32)
    return _FONT


class _Win32Control:
    """Common plumbing for a Win32 control parented into a Qt slot."""

    native = True
    # checkbox/label paint over the Qt surface; a button/combo keeps its
    # themed background
    transparent = False

    # which uxtheme theme name to request ('' = plain)
    theme_kind = 'explorer'

    def __init__(self, window, slot, on_change=None):
        self.window = window
        self.slot = slot
        self.on_change = on_change
        self.active = False
        self._hwnd = None
        self._host = None
        self._font = None
        self._null_brush = None
        self._fg = 0
        self._bg = 0
        self._hi = 0
        self._hi_fg = 0
        self._brush = None
        self._hi_brush = None
        self._item_height = 0
        self._ctlcolor_hits = 0
        self._id = _next_id()

    # ---------- creation ----------
    def _load(self):
        u = ctypes.WinDLL('user32', use_last_error=True)
        g = ctypes.WinDLL('gdi32', use_last_error=True)

        u.CreateWindowExW.restype = wintypes.HWND
        u.CreateWindowExW.argtypes = [
            wintypes.DWORD, wintypes.LPCWSTR, wintypes.LPCWSTR,
            wintypes.DWORD, ctypes.c_int, ctypes.c_int, ctypes.c_int,
            ctypes.c_int, wintypes.HWND, wintypes.HMENU, wintypes.HINSTANCE,
            wintypes.LPVOID]
        u.SendMessageW.restype = LRESULT
        u.SendMessageW.argtypes = [wintypes.HWND, wintypes.UINT, WPARAM,
                                   ctypes.c_void_p]
        u.MoveWindow.restype = wintypes.BOOL
        u.MoveWindow.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_int,
                                 ctypes.c_int, ctypes.c_int, wintypes.BOOL]
        u.ShowWindow.restype = wintypes.BOOL
        u.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
        u.EnableWindow.restype = wintypes.BOOL
        u.EnableWindow.argtypes = [wintypes.HWND, wintypes.BOOL]
        u.IsWindowEnabled.restype = wintypes.BOOL
        u.IsWindowEnabled.argtypes = [wintypes.HWND]
        u.IsWindow.restype = wintypes.BOOL
        u.IsWindow.argtypes = [wintypes.HWND]
        u.DestroyWindow.restype = wintypes.BOOL
        u.DestroyWindow.argtypes = [wintypes.HWND]
        u.GetClientRect.restype = wintypes.BOOL
        u.GetClientRect.argtypes = [wintypes.HWND, ctypes.c_void_p]
        u.SystemParametersInfoW.restype = wintypes.BOOL
        u.SystemParametersInfoW.argtypes = [wintypes.UINT, wintypes.UINT,
                                            ctypes.c_void_p, wintypes.UINT]
        u.CallWindowProcW.restype = LRESULT
        u.CallWindowProcW.argtypes = [ctypes.c_void_p, wintypes.HWND,
                                      wintypes.UINT, WPARAM, LPARAM]
        u.DefWindowProcW.restype = LRESULT
        u.DefWindowProcW.argtypes = [wintypes.HWND, wintypes.UINT, WPARAM,
                                     LPARAM]
        setter = getattr(u, 'SetWindowLongPtrW', None) or u.SetWindowLongW
        setter.restype = ctypes.c_void_p
        setter.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_void_p]

        u.GetParent.restype = wintypes.HWND
        u.GetParent.argtypes = [wintypes.HWND]
        u.GetDC.restype = ctypes.c_void_p
        u.GetDC.argtypes = [wintypes.HWND]
        u.ReleaseDC.restype = ctypes.c_int
        u.ReleaseDC.argtypes = [wintypes.HWND, ctypes.c_void_p]
        u.FillRect.restype = ctypes.c_int
        u.FillRect.argtypes = [ctypes.c_void_p, ctypes.c_void_p,
                               ctypes.c_void_p]
        u.DrawTextW.restype = ctypes.c_int
        u.DrawTextW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR,
                                ctypes.c_int, ctypes.c_void_p,
                                wintypes.UINT]

        g.CreateFontIndirectW.restype = ctypes.c_void_p
        g.CreateFontIndirectW.argtypes = [ctypes.c_void_p]
        g.SetTextColor.restype = wintypes.DWORD
        g.SetTextColor.argtypes = [ctypes.c_void_p, wintypes.DWORD]
        g.SetBkMode.restype = ctypes.c_int
        g.SetBkMode.argtypes = [ctypes.c_void_p, ctypes.c_int]
        g.GetStockObject.restype = ctypes.c_void_p
        g.GetStockObject.argtypes = [ctypes.c_int]
        g.CreateSolidBrush.restype = ctypes.c_void_p
        g.CreateSolidBrush.argtypes = [wintypes.DWORD]
        g.DeleteObject.restype = wintypes.BOOL
        g.DeleteObject.argtypes = [ctypes.c_void_p]
        g.SelectObject.restype = ctypes.c_void_p
        g.SelectObject.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
        g.GetTextExtentPoint32W.restype = wintypes.BOOL
        g.GetTextExtentPoint32W.argtypes = [ctypes.c_void_p,
                                            wintypes.LPCWSTR, ctypes.c_int,
                                            ctypes.c_void_p]

        self._user32 = u
        self._gdi32 = g
        self._setter = setter
        self._null_brush = g.GetStockObject(NULL_BRUSH)
        _DEFWINDOWPROC[0] = u

    def _host_mode(self):
        """``slot`` (default) hosts in the slot's own HWND; ``window``
        parents the control straight to the top-level window."""
        return os.environ.get('FORMAT_TEX_NATIVE_HOST', 'slot').strip().lower()

    def _target_rect(self):
        """``(x, y, width, height)`` in device pixels for ``MoveWindow``.

        ``window`` mode maps the slot into the top-level window (which is
        never layered, unlike a translucent child host); ``slot`` mode uses
        the slot's own client area. ``GetClientRect`` is authoritative at
        any DPI, so no manual scaling is needed there."""
        if self._host_mode() == 'window':
            try:
                from PySide6.QtCore import QPoint

                ratio = float(self.window.devicePixelRatioF())
                pos = self.slot.mapTo(self.window, QPoint(0, 0))
                width = max(1, self.slot.width())
                height = max(1, self.slot.height())
                return (int(round(pos.x() * ratio)),
                        int(round(pos.y() * ratio)),
                        max(1, int(round(width * ratio))),
                        max(1, int(round(height * ratio))))
            except Exception:
                pass
        try:
            rect = wintypes.RECT()
            if self._user32.GetClientRect(self._host, _as_ptr(rect)):
                width = rect.right - rect.left
                height = rect.bottom - rect.top
                if width > 0 and height > 0:
                    return (0, 0, width, height)
        except Exception:
            pass
        rect = self.slot.rect()
        return (0, 0, max(1, rect.width()), max(1, rect.height()))

    @staticmethod
    def _rgb(colour):
        return (colour.red() | (colour.green() << 8)
                | (colour.blue() << 16))

    def _read_colours(self):
        """Cache the palette colours the controls draw with.

        Background/text from the widget palette; the selection highlight
        follows the OS accent (falling back to the Qt highlight) so the
        owner-drawn combo drop-down matches Windows."""
        try:
            from PySide6.QtGui import QColor, QPalette

            palette = self.slot.palette()
            window = palette.color(QPalette.ColorRole.Window)
            text = palette.color(QPalette.ColorRole.WindowText)
            self._bg = self._rgb(window)
            self._fg = self._rgb(text)
            highlight = palette.color(QPalette.ColorRole.Highlight)
            highlight_text = palette.color(QPalette.ColorRole.HighlightedText)
            try:
                import platform_effects as pe

                accent = pe.native_accent_color(window.lightness() < 128)
                if accent:
                    highlight = QColor(accent)
                    highlight_text = QColor(pe.accent_text_color(
                        (highlight.red(), highlight.green(),
                         highlight.blue())))
            except Exception:
                pass
            self._hi = self._rgb(highlight)
            self._hi_fg = self._rgb(highlight_text)
        except Exception:
            pass

    def _make_brushes(self):
        try:
            if self._brush:
                self._gdi32.DeleteObject(self._brush)
            if self._hi_brush:
                self._gdi32.DeleteObject(self._hi_brush)
        except Exception:
            pass
        self._brush = self._gdi32.CreateSolidBrush(self._bg)
        self._hi_brush = self._gdi32.CreateSolidBrush(self._hi)

    def _frame(self, x, y, width, height):
        """Top-left/width/height for ``MoveWindow`` (combos override)."""
        return (x, y, width, height)

    def _create(self, class_name, style, text=''):
        self._load()
        if self._host_mode() == 'window':
            self._host = int(self.window.winId())
        else:
            self._host = int(self.slot.winId())  # forces the slot native
        self._font = make_control_font(self.slot, self._user32, self._gdi32)
        self._read_colours()
        self._make_brushes()
        x, y, width, height = self._target_rect()
        hwnd = self._user32.CreateWindowExW(
            0, class_name, text, WS_CHILD | WS_VISIBLE | style,
            x, y, width, height, self._host, self._id, None, None)
        if not hwnd:
            return False
        self._hwnd = hwnd
        self.active = True
        if self._font:
            self._user32.SendMessageW(hwnd, WM_SETFONT, self._font, True)
        _theme_control(hwnd, self.theme_kind)
        _install_host(self)
        self._hwnd_hook()
        self.place()
        self._user32.ShowWindow(self._hwnd,
                                5 if self.slot.isVisible() else 0)
        return True

    # ---------- native sizing ----------
    def _extent(self, text):
        """Width/height of ``text`` in the control's own font (device px)."""
        if not text or not self._font:
            return None
        hdc = None
        try:
            hdc = self._user32.GetDC(self._hwnd)
            if not hdc:
                return None
            old = self._gdi32.SelectObject(hdc, self._font)
            size = SIZE()
            ok = self._gdi32.GetTextExtentPoint32W(
                hdc, str(text), len(str(text)), _as_ptr(size))
            if old:
                self._gdi32.SelectObject(hdc, old)
            if ok and size.cx > 0:
                return (int(size.cx), int(size.cy))
        except Exception:
            pass
        finally:
            if hdc:
                try:
                    self._user32.ReleaseDC(self._hwnd, hdc)
                except Exception:
                    pass
        return None

    def _dpr(self):
        try:
            return float(self.slot.devicePixelRatioF()) or 1.0
        except Exception:
            return 1.0

    def ideal_size(self):
        """The size the control needs for its text (device px), or None.

        Used to grow the Qt slot so CJK labels are not clipped when the
        Win32 fallback font is wider than Qt's ``sizeHint``."""
        if not (self.active and self._hwnd):
            return None
        try:
            size = SIZE()
            if self._user32.SendMessageW(self._hwnd, BCM_GETIDEALSIZE, 0,
                                         _as_ptr(size)):
                if size.cx > 0 and size.cy > 0:
                    return (int(size.cx), int(size.cy))
        except Exception:
            pass
        extent = self._extent(getattr(self, '_title', '')
                              or getattr(self, '_text', ''))
        if not extent:
            return None
        # leave room for the control's own chrome (checkbox/button glyph)
        return (extent[0] + int(round(24 * self._dpr())), extent[1])

    # ---------- owner draw (combos override) ----------
    def _draw_item(self, item):
        """Draw one owner-draw item (combos override)."""

    def _measure_item(self, item):
        """Measure one owner-draw item (combos override)."""

    def _hwnd_hook(self):
        """Subclasses may configure the control right after creation."""

    def build(self):
        if sys.platform != 'win32':
            return False
        if self.active and self._hwnd and self._user32.IsWindow(self._hwnd):
            self.place()
            return True
        try:
            if not self._create(*self._spec()):
                return False
            self.active = True
            self.place()
            return True
        except Exception as exc:
            self._note('{} failed: {}: {}'.format(
                type(self).__name__, type(exc).__name__, exc))
            self.active = False
            return False

    def _spec(self):
        raise NotImplementedError

    # ---------- placement ----------
    def place(self):
        if self._hwnd is None:
            return
        try:
            x, y, width, height = self._target_rect()
            fx, fy, fw, fh = self._frame(x, y, width, height)
            self._user32.MoveWindow(self._hwnd, fx, fy, fw, fh, True)
            self._user32.ShowWindow(
                self._hwnd, 5 if self.slot.isVisible() else 0)
        except Exception:
            pass

    def isVisible(self):
        return bool(self.active and self._hwnd
                    and self.slot.isVisible())

    def size(self):
        if not (self.active and self._hwnd):
            return (0.0, 0.0)
        try:
            rect = wintypes.RECT()
            self._user32.GetClientRect(self._hwnd, _as_ptr(rect))
            return (float(rect.right), float(rect.bottom))
        except Exception:
            return (0.0, 0.0)

    def _command(self, code):
        """A notification for this control (override)."""

    def _note(self, message):
        try:
            import platform_effects as pe
            pe._note(message)
        except Exception:
            pass


class Win32Checkbox(_Win32Control):
    """``BUTTON`` + ``BS_AUTOCHECKBOX`` over a Qt slot."""

    transparent = True

    def __init__(self, window, slot, title, checked=False, on_toggle=None):
        super().__init__(window, slot, on_change=on_toggle)
        self._title = str(title)
        self._checked = bool(checked)

    def _spec(self):
        return ('Button', BS_AUTOCHECKBOX | WS_TABSTOP, self._title)

    def _hwnd_hook(self):
        self.setChecked(self._checked)

    def _command(self, code):
        if code == BN_CLICKED:
            self._checked = self.isChecked()
            if callable(self.on_change):
                self.on_change(self._checked)

    def isChecked(self):
        if self.active and self._hwnd:
            return int(self._user32.SendMessageW(
                self._hwnd, BM_GETCHECK, 0, 0)) == BST_CHECKED
        return self._checked

    def setChecked(self, value):
        self._checked = bool(value)
        if self.active and self._hwnd:
            self._user32.SendMessageW(
                self._hwnd, BM_SETCHECK,
                BST_CHECKED if self._checked else BST_UNCHECKED, 0)


class Win32PopUp(_Win32Control):
    """``COMBOBOX`` + ``CBS_DROPDOWNLIST`` over a Qt slot.

    Owner-drawn (``CBS_OWNERDRAWFIXED`` + ``CBS_HASSTRINGS``) so the
    open drop-down can be painted dark: the default list box is always
    light, and ``SetWindowTheme`` alone does not darken its items."""

    transparent = False
    theme_kind = 'combo'

    def __init__(self, window, slot, items, current, on_change=None,
                 custom_label=None):
        super().__init__(window, slot, on_change=on_change)
        self._items = list(items)
        self._current = str(current)
        self.custom_label = custom_label
        self._titles = list(self._items)
        if self.custom_label and self.custom_label not in self._titles:
            self._titles.append(self.custom_label)

    def _spec(self):
        return ('ComboBox',
                CBS_DROPDOWNLIST | CBS_OWNERDRAWFIXED | CBS_HASSTRINGS
                | WS_VSCROLL | WS_TABSTOP, '')

    def _hwnd_hook(self):
        for title in self._titles:
            self._user32.SendMessageW(self._hwnd, CB_ADDSTRING, 0,
                                      ctypes.c_wchar_p(title))
        # decouple the drop-down height from the (closed) control height
        self._user32.SendMessageW(self._hwnd, CB_SETMINVISIBLE, 8, 0)
        extent = self._extent('Ag') or (0, int(round(16 * self._dpr())))
        self._item_height = max(int(round(16 * self._dpr())),
                                extent[1] + int(round(6 * self._dpr())))
        # -1 = the closed field, 0 = all owner-draw list items
        self._user32.SendMessageW(self._hwnd, CB_SETITEMHEIGHT, -1,
                                  self._item_height)
        self._user32.SendMessageW(self._hwnd, CB_SETITEMHEIGHT, 0,
                                  self._item_height)
        self.setCurrentText(self._current)

    def ideal_size(self):
        widest = 0
        height = self._item_height
        for title in self._titles:
            extent = self._extent(title)
            if not extent:
                continue
            widest = max(widest, extent[0])
            height = max(height, extent[1] + int(round(6 * self._dpr())))
        if not widest:
            return super().ideal_size()
        # room for the drop-down arrow and the item padding
        return (widest + int(round(28 * self._dpr())), height)

    def _item_text(self, item):
        if item.itemData:
            try:
                text = ctypes.cast(item.itemData, ctypes.c_wchar_p).value
                if text:
                    return text
            except Exception:
                pass
        index = int(item.itemID)
        if 0 <= index < len(self._titles):
            return self._titles[index]
        return self._current

    def _measure_item(self, item):
        try:
            item.itemHeight = self._item_height or int(
                round(16 * self._dpr()))
            item.itemWidth = 0
        except Exception:
            pass

    def _draw_item(self, item):
        try:
            selected = bool(item.itemState & ODS_SELECTED)
            edit = bool(item.itemState & ODS_COMBOBOXEDIT)
            highlight = selected and not edit
            hdc = item.hDC
            rect = wintypes.RECT(item.rcItem.left, item.rcItem.top,
                                 item.rcItem.right, item.rcItem.bottom)
            brush = self._hi_brush if highlight else self._brush
            if not brush:
                return
            self._user32.FillRect(hdc, _as_ptr(rect), brush)
            if highlight:
                self._gdi32.SetTextColor(hdc, self._hi_fg)
            else:
                self._gdi32.SetTextColor(hdc, self._fg)
            self._gdi32.SetBkMode(hdc, TRANSPARENT)
            rect.left += int(round(6 * self._dpr()))
            flags = (DT_LEFT | DT_VCENTER | DT_SINGLELINE | DT_NOPREFIX
                     | DT_END_ELLIPSIS)
            self._user32.DrawTextW(hdc, self._item_text(item), -1,
                                   _as_ptr(rect), flags)
        except Exception:
            pass

    def _frame(self, x, y, width, height):
        closed = int(self._user32.SendMessageW(
            self._hwnd, CB_GETITEMHEIGHT, -1, 0))
        if closed <= 0:
            closed = height
        y = y + max(0, (height - closed) // 2)
        return (x, y, width, closed)

    def _command(self, code):
        if code != CBN_SELCHANGE:
            return
        title = self.currentText()
        if self.custom_label and title == self.custom_label:
            if callable(self.on_change):
                self.on_change(self.custom_label)
            return
        self._current = title
        if callable(self.on_change):
            self.on_change(title)

    def currentText(self):
        if self.active and self._hwnd:
            index = int(self._user32.SendMessageW(
                self._hwnd, CB_GETCURSEL, 0, 0))
            if index < 0:
                return self._current
            length = int(self._user32.SendMessageW(
                self._hwnd, CB_GETLBTEXTLEN, index, 0))
            if length <= 0:
                return self._current
            buffer = ctypes.create_unicode_buffer(length + 1)
            self._user32.SendMessageW(self._hwnd, CB_GETLBTEXT, index,
                                      ctypes.cast(buffer, ctypes.c_void_p))
            return buffer.value or self._current
        return self._current

    def setCurrentText(self, value):
        self._current = str(value)
        if self.active and self._hwnd:
            index = int(self._user32.SendMessageW(
                self._hwnd, CB_FINDSTRINGEXACT, -1,
                ctypes.c_wchar_p(self._current)))
            if index >= 0:
                self._user32.SendMessageW(self._hwnd, CB_SETCURSEL, index, 0)


class Win32PushButton(_Win32Control):
    """``BUTTON`` + ``BS_PUSHBUTTON`` over a Qt slot."""

    transparent = False

    def __init__(self, window, slot, title, on_click=None):
        super().__init__(window, slot, on_change=on_click)
        self._title = str(title)

    def _spec(self):
        return ('Button', BS_PUSHBUTTON | WS_TABSTOP, self._title)

    def _command(self, code):
        if code == BN_CLICKED and callable(self.on_change):
            self.on_change()

    def setEnabled(self, value):
        if self.active and self._hwnd:
            self._user32.EnableWindow(self._hwnd, bool(value))

    def isEnabled(self):
        if self.active and self._hwnd:
            return bool(self._user32.IsWindowEnabled(self._hwnd))
        return True


class Win32Label(_Win32Control):
    """``STATIC`` label over a Qt slot."""

    transparent = True

    def __init__(self, window, slot, text=''):
        super().__init__(window, slot)
        self._text = str(text)

    def _spec(self):
        return ('Static', SS_LEFT, self._text)

    def setText(self, text):
        self._text = str(text)
        if self.active and self._hwnd:
            self._user32.SendMessageW(self._hwnd, WM_SETTEXT, 0,
                                      ctypes.c_wchar_p(self._text))

    def text(self):
        if self.active and self._hwnd:
            length = int(self._user32.SendMessageW(
                self._hwnd, WM_GETTEXTLENGTH, 0, 0))
            if length > 0:
                buffer = ctypes.create_unicode_buffer(length + 1)
                self._user32.SendMessageW(
                    self._hwnd, WM_GETTEXT, length + 1,
                    ctypes.cast(buffer, ctypes.c_void_p))
                return buffer.value
        return self._text
