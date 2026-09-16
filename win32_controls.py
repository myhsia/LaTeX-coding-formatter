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
WM_COMMAND = 0x0111
WM_CTLCOLORBTN = 0x0135
WM_CTLCOLORSTATIC = 0x0138
WM_NCDESTROY = 0x0082

# button
BS_PUSHBUTTON = 0x00000000
BS_AUTOCHECKBOX = 0x00000003
BM_GETCHECK = 0x00F0
BM_SETCHECK = 0x00F1
BST_UNCHECKED = 0x0000
BST_CHECKED = 0x0001
BN_CLICKED = 0

# combo box
CBS_DROPDOWNLIST = 0x0003
CB_ADDSTRING = 0x0143
CB_GETCURSEL = 0x0147
CB_GETLBTEXT = 0x0148
CB_GETLBTEXTLEN = 0x0149
CB_SETCURSEL = 0x014E
CB_GETITEMHEIGHT = 0x0154
CB_FINDSTRINGEXACT = 0x0158
CB_SETMINVISIBLE = 0x1701
CBN_SELCHANGE = 1

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


WNDPROC = ctypes.WINFUNCTYPE(LRESULT, wintypes.HWND, wintypes.UINT,
                             WPARAM, LPARAM)

_NEXT_ID = [100]
_FONT = None


def _as_ptr(obj):
    """A ``c_void_p`` for a ctypes instance (safe for pointer-sized params)."""
    return ctypes.cast(ctypes.byref(obj), ctypes.c_void_p)


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

    def __init__(self, window, slot, on_change=None):
        self.window = window
        self.slot = slot
        self.on_change = on_change
        self.active = False
        self._hwnd = None
        self._host = None
        self._old_proc = None
        self._proc_ref = None
        self._font = None
        self._null_brush = None
        self._fg = 0
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
        setter = getattr(u, 'SetWindowLongPtrW', None) or u.SetWindowLongW
        setter.restype = ctypes.c_void_p
        setter.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_void_p]

        g.CreateFontIndirectW.restype = ctypes.c_void_p
        g.CreateFontIndirectW.argtypes = [ctypes.c_void_p]
        g.SetTextColor.restype = wintypes.DWORD
        g.SetTextColor.argtypes = [ctypes.c_void_p, wintypes.DWORD]
        g.SetBkMode.restype = ctypes.c_int
        g.SetBkMode.argtypes = [ctypes.c_void_p, ctypes.c_int]
        g.GetStockObject.restype = ctypes.c_void_p
        g.GetStockObject.argtypes = [ctypes.c_int]

        self._user32 = u
        self._gdi32 = g
        self._setter = setter
        self._null_brush = g.GetStockObject(NULL_BRUSH)

    def _slot_size(self):
        """The slot's client area in device pixels.

        The host HWND's ``GetClientRect`` is authoritative at any DPI;
        mixing Qt's logical rect with a manual ``devicePixelRatioF`` scale
        drifts (and leaves stale sizes when the ratio changes).
        """
        try:
            rect = wintypes.RECT()
            if self._user32.GetClientRect(self._host, _as_ptr(rect)):
                width = rect.right - rect.left
                height = rect.bottom - rect.top
                if width > 0 and height > 0:
                    return (width, height)
        except Exception:
            pass
        rect = self.slot.rect()
        return (max(1, rect.width()), max(1, rect.height()))

    def _text_colour(self):
        try:
            from PySide6.QtGui import QPalette

            colour = self.slot.palette().color(QPalette.ColorRole.WindowText)
            return colour.red() | (colour.green() << 8) | (colour.blue() << 16)
        except Exception:
            return 0

    def _frame(self, width, height):
        """Top-left/width/height for ``MoveWindow`` (combos override)."""
        return (0, 0, width, height)

    def _create(self, class_name, style, text=''):
        self._load()
        self._host = int(self.slot.winId())      # forces the slot native
        self._font = make_control_font(self.slot, self._user32, self._gdi32)
        self._fg = self._text_colour()
        width, height = self._slot_size()
        hwnd = self._user32.CreateWindowExW(
            0, class_name, text, WS_CHILD | WS_VISIBLE | style,
            0, 0, width, height, self._host, self._id, None, None)
        if not hwnd:
            return False
        self._hwnd = hwnd
        self.active = True
        if self._font:
            self._user32.SendMessageW(hwnd, WM_SETFONT, self._font, True)
        self._subclass_host()
        self._hwnd_hook()
        self.place()
        self._user32.ShowWindow(self._hwnd,
                                5 if self.slot.isVisible() else 0)
        return True

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
            width, height = self._slot_size()
            x, y, w, h = self._frame(width, height)
            self._user32.MoveWindow(self._hwnd, x, y, w, h, True)
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

    # ---------- host subclass ----------
    def _subclass_host(self):
        self._proc_ref = WNDPROC(self._host_proc)
        self._old_proc = self._setter(
            self._host, GWLP_WNDPROC,
            ctypes.cast(self._proc_ref, ctypes.c_void_p))

    def _host_proc(self, hwnd, msg, wparam, lparam):
        try:
            if msg == WM_COMMAND:
                if (int(wparam) & 0xFFFF) == self._id:
                    self._command((int(wparam) >> 16) & 0xFFFF)
            elif (self.transparent
                    and msg in (WM_CTLCOLORSTATIC, WM_CTLCOLORBTN)
                    and self._hwnd is not None
                    and int(lparam or 0) == self._hwnd):
                self._gdi32.SetTextColor(wparam, self._fg)
                self._gdi32.SetBkMode(wparam, TRANSPARENT)
                return int(self._null_brush or 0)
            elif msg == WM_NCDESTROY:
                self._restore_host()
        except Exception:
            pass
        return self._user32.CallWindowProcW(self._old_proc, hwnd, msg,
                                            WPARAM(wparam), LPARAM(lparam))

    def _restore_host(self):
        try:
            if self._old_proc:
                self._setter(self._host, GWLP_WNDPROC, self._old_proc)
        except Exception:
            pass

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
    """``COMBOBOX`` + ``CBS_DROPDOWNLIST`` over a Qt slot."""

    transparent = False

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
        return ('ComboBox', CBS_DROPDOWNLIST | WS_VSCROLL | WS_TABSTOP, '')

    def _hwnd_hook(self):
        for title in self._titles:
            self._user32.SendMessageW(self._hwnd, CB_ADDSTRING, 0,
                                      ctypes.c_wchar_p(title))
        # decouple the drop-down height from the (closed) control height
        self._user32.SendMessageW(self._hwnd, CB_SETMINVISIBLE, 8, 0)
        self.setCurrentText(self._current)

    def _frame(self, width, height):
        closed = int(self._user32.SendMessageW(
            self._hwnd, CB_GETITEMHEIGHT, -1, 0))
        if closed <= 0:
            closed = height
        y = max(0, (height - closed) // 2)
        return (0, y, width, closed)

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
