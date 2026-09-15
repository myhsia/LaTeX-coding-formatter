#!/usr/bin/env python3
r"""Win32 file list hosted inside the Qt window (Windows only).

A real ``SysListView32`` (report view, full-row select, shell icons) is
created with ``CreateWindowExW`` and parented into the Qt list widget's
HWND (calling ``winId()`` forces that widget to be a native child, so the
control paints above the Qt-drawn placeholder). Explorer drag & drop is
enabled with ``DragAcceptFiles``/``WM_DROPFILES``, selection is observed
through a WndProc subclass on the host, and the colours follow the Qt
palette so the list matches the application in light and dark mode.

Every step is defensive: if anything fails, ``build()`` returns False and
the caller keeps the Qt list (so Windows can never regress because of
this module).
"""

import ctypes
import sys
from ctypes import wintypes
from pathlib import Path

# 64-bit correct aliases (ctypes.wintypes.LPARAM is 32 bits on Windows)
LRESULT = ctypes.c_ssize_t
LPARAM = ctypes.c_ssize_t
WPARAM = ctypes.c_size_t

WM_SIZE = 0x0005
WM_NOTIFY = 0x004E
WM_DROPFILES = 0x0233
WM_NCDESTROY = 0x0082

LVS_REPORT = 0x0001
LVS_SHOWSELALWAYS = 0x0008
LVS_NOCOLUMNHEADER = 0x4000
LVS_EX_FULLROWSELECT = 0x00000020
LVS_EX_DOUBLEBUFFER = 0x00010000
LVS_EX_LABELTIP = 0x00004000

LVM_FIRST = 0x1000
LVM_INSERTCOLUMNW = LVM_FIRST + 97
LVM_INSERTITEMW = LVM_FIRST + 77
LVM_DELETEITEM = LVM_FIRST + 8
LVM_DELETEALLITEMS = LVM_FIRST + 9
LVM_GETNEXTITEM = LVM_FIRST + 12
LVM_SETIMAGELIST = LVM_FIRST + 3
LVM_SETEXTENDEDLISTVIEWSTYLE = LVM_FIRST + 54
LVM_SETITEMSTATE = LVM_FIRST + 43
LVM_SETCOLUMNWIDTH = LVM_FIRST + 30
LVM_SETBKCOLOR = LVM_FIRST + 1
LVM_SETTEXTCOLOR = LVM_FIRST + 36
LVM_SETTEXTBKCOLOR = LVM_FIRST + 38
LVIF_STATE = 0x0008
LVIS_SELECTED = 0x0002
LVIS_FOCUSED = 0x0001

LVNI_SELECTED = 0x0002
LVSIL_SMALL = 1
LVIF_TEXT = 0x0001
LVIF_IMAGE = 0x0002
LVCF_FMT = 0x0001
LVCF_WIDTH = 0x0002
LVCF_SUBITEM = 0x0008
LVCFMT_LEFT = 0x0000
LVN_ITEMCHANGED = -101            # LVN_FIRST - 1 (compared signed)

SHGFI_ICON = 0x000000100
SHGFI_SMALLICON = 0x000000001
SHGFI_USEFILEATTRIBUTES = 0x000000010
FILE_ATTRIBUTE_NORMAL = 0x00000080
FILE_ATTRIBUTE_DIRECTORY = 0x00000010
ILC_COLOR32 = 0x00000020
ILC_MASK = 0x00000001

GWLP_WNDPROC = -4


class LVITEMW(ctypes.Structure):
    _fields_ = [
        ('mask', wintypes.UINT), ('iItem', ctypes.c_int),
        ('iSubItem', ctypes.c_int), ('state', wintypes.UINT),
        ('stateMask', wintypes.UINT), ('pszText', wintypes.LPWSTR),
        ('cchTextMax', ctypes.c_int), ('iImage', ctypes.c_int),
        ('lParam', LPARAM), ('iIndent', ctypes.c_int),
        ('iGroupId', ctypes.c_int), ('cColumns', wintypes.UINT),
        ('puColumns', ctypes.c_void_p), ('piColFmt', ctypes.c_void_p),
        ('iGroup', ctypes.c_int),
    ]


class LVCOLUMNW(ctypes.Structure):
    _fields_ = [
        ('mask', wintypes.UINT), ('fmt', ctypes.c_int),
        ('cx', ctypes.c_int), ('pszText', wintypes.LPWSTR),
        ('cchTextMax', ctypes.c_int), ('iSubItem', ctypes.c_int),
        ('iImage', ctypes.c_int), ('iOrder', ctypes.c_int),
        ('cxMin', ctypes.c_int), ('cxDefault', ctypes.c_int),
        ('cxIdeal', ctypes.c_int),
    ]


class NMHDR(ctypes.Structure):
    _fields_ = [('hwndFrom', wintypes.HWND), ('idFrom', ctypes.c_void_p),
                ('code', wintypes.UINT)]


class NMITEMACTIVATE(ctypes.Structure):
    _fields_ = [('hdr', NMHDR), ('iItem', ctypes.c_int),
                ('iSubItem', ctypes.c_int), ('uNewState', wintypes.UINT),
                ('uOldState', wintypes.UINT), ('uChanged', wintypes.UINT)]


class SHFILEINFOW(ctypes.Structure):
    _fields_ = [('hIcon', wintypes.HICON), ('iIcon', ctypes.c_int),
                ('dwAttributes', wintypes.DWORD),
                ('szDisplayName', wintypes.WCHAR * 260),
                ('szTypeName', wintypes.WCHAR * 80)]


WNDPROC = ctypes.WINFUNCTYPE(LRESULT, wintypes.HWND, wintypes.UINT,
                             WPARAM, LPARAM)


def _as_ptr(obj):
    """A ``c_void_p`` for a ctypes instance (safe for pointer-sized params)."""
    return ctypes.cast(ctypes.byref(obj), ctypes.c_void_p)


class Win32FileList:
    """A ``SysListView32`` file list hosted in a Qt widget's HWND."""

    native = True

    def __init__(self, window, slot, on_selection=None, on_drop=None,
                 colours=None):
        self.window = window
        self.slot = slot
        self.on_selection = on_selection
        self.on_drop = on_drop
        self.colours = dict(colours or {})
        self.items = []                 # [(path, root)]
        self._hwnd = None
        self._host = None
        self._old_proc = None
        self._proc_ref = None
        self._icon_list = None
        self._icons = {}                # (is_dir, suffix) -> image index
        self._in_notify = False

    # ---------- creation ----------
    def _load(self):
        """Load the DLLs and declare signatures.

        Declaring ``argtypes`` is essential: without it ctypes widens every
        Python int to a 32-bit ``c_int`` and the 64-bit HWNDs/pointers we
        pass (notably ``CallWindowProcW``'s old proc) are truncated, which
        faults the process.
        """
        u = ctypes.WinDLL('user32', use_last_error=True)
        s = ctypes.WinDLL('shell32', use_last_error=True)
        c = ctypes.WinDLL('comctl32', use_last_error=True)
        g = ctypes.WinDLL('gdi32', use_last_error=True)
        try:
            t = ctypes.WinDLL('uxtheme', use_last_error=True)
        except Exception:
            t = None

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
        u.CallWindowProcW.restype = LRESULT
        u.CallWindowProcW.argtypes = [ctypes.c_void_p, wintypes.HWND,
                                      wintypes.UINT, WPARAM, LPARAM]
        u.DestroyIcon.restype = wintypes.BOOL
        u.DestroyIcon.argtypes = [wintypes.HICON]

        setter = getattr(u, 'SetWindowLongPtrW', None) or u.SetWindowLongW
        setter.restype = ctypes.c_void_p
        setter.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_void_p]

        s.DragAcceptFiles.restype = None
        s.DragAcceptFiles.argtypes = [wintypes.HWND, wintypes.BOOL]
        s.DragQueryFileW.restype = wintypes.UINT
        s.DragQueryFileW.argtypes = [ctypes.c_void_p, wintypes.UINT,
                                     wintypes.LPWSTR, wintypes.UINT]
        s.DragFinish.restype = None
        s.DragFinish.argtypes = [ctypes.c_void_p]
        s.SHGetFileInfoW.restype = ctypes.c_size_t
        s.SHGetFileInfoW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD,
                                     ctypes.c_void_p, wintypes.UINT,
                                     wintypes.UINT]

        c.InitCommonControls.restype = None
        c.InitCommonControls.argtypes = []
        c.ImageList_Create.restype = ctypes.c_void_p
        c.ImageList_Create.argtypes = [ctypes.c_int, ctypes.c_int,
                                       wintypes.UINT, ctypes.c_int,
                                       ctypes.c_int]
        c.ImageList_AddIcon.restype = ctypes.c_int
        c.ImageList_AddIcon.argtypes = [ctypes.c_void_p, wintypes.HICON]

        if t is not None:
            t.SetWindowTheme.restype = ctypes.c_long
            t.SetWindowTheme.argtypes = [wintypes.HWND, wintypes.LPCWSTR,
                                         wintypes.LPCWSTR]

        self._user32 = u
        self._shell32 = s
        self._comctl32 = c
        self._gdi32 = g
        self._uxtheme = t
        self._setter = setter

    def build(self):
        if sys.platform != 'win32':
            return False
        try:
            self._load()

            host = int(self.slot.winId())      # forces the widget native
            self._host = host
            hwnd = self._user32.CreateWindowExW(
                0, 'SysListView32', '',
                wintypes.DWORD(0x40000000 | 0x10000000 | LVS_REPORT
                               | LVS_SHOWSELALWAYS | LVS_NOCOLUMNHEADER),
                0, 0, 100, 100, host, None, None, None)
            if not hwnd:
                return False
            self._hwnd = hwnd
            self._comctl32.InitCommonControls()

            self._user32.SendMessageW(
                hwnd, LVM_SETEXTENDEDLISTVIEWSTYLE, 0,
                LVS_EX_FULLROWSELECT | LVS_EX_DOUBLEBUFFER | LVS_EX_LABELTIP)

            column = LVCOLUMNW()
            column.mask = LVCF_FMT | LVCF_WIDTH | LVCF_SUBITEM
            column.fmt = LVCFMT_LEFT
            column.cx = 400
            column.iSubItem = 0
            column.pszText = ctypes.c_wchar_p('')
            self._user32.SendMessageW(hwnd, LVM_INSERTCOLUMNW, 0,
                                      _as_ptr(column))

            self._apply_colours()
            self._shell32.DragAcceptFiles(hwnd, True)
            self._subclass_host()
            # the control must fill the widget before it is shown
            self.place()
            self._user32.ShowWindow(hwnd, 5)      # SW_SHOW
            return True
        except Exception as exc:
            self._note('win32 file list failed: {}: {}'.format(
                type(exc).__name__, exc))
            return False

    def _apply_colours(self):
        """Match the Qt palette (light/dark) instead of the system theme."""
        try:
            from PySide6.QtGui import QPalette

            palette = self.slot.palette()
            window = palette.color(QPalette.ColorRole.Window)
            text = palette.color(QPalette.ColorRole.WindowText)
            bg = window.red() | (window.green() << 8) | (window.blue() << 16)
            fg = text.red() | (text.green() << 8) | (text.blue() << 16)
            self._user32.SendMessageW(self._hwnd, LVM_SETBKCOLOR, 0, bg)
            self._user32.SendMessageW(self._hwnd, LVM_SETTEXTBKCOLOR, 0, bg)
            self._user32.SendMessageW(self._hwnd, LVM_SETTEXTCOLOR, 0, fg)
            if self._uxtheme is not None:
                dark = window.lightness() < 128
                theme = 'DarkMode_Explorer' if dark else 'Explorer'
                try:
                    self._uxtheme.SetWindowTheme(self._hwnd, theme, None)
                except Exception:
                    pass
        except Exception:
            pass

    # ---------- placement ----------
    def place(self):
        if self._hwnd is None:
            return
        try:
            rect = self.slot.rect()
            client = self.slot.mapTo(self.slot, rect.topLeft())
            width = max(1, rect.width())
            height = max(1, rect.height())
            self._user32.MoveWindow(self._hwnd, client.x(), client.y(),
                                    width, height, True)
            self._user32.ShowWindow(self._hwnd,
                                    5 if self.slot.isVisible() else 0)
            # keep the single column as wide as the control
            self._user32.SendMessageW(
                self._hwnd, LVM_SETCOLUMNWIDTH, 0, max(40, width - 4))
        except Exception:
            pass

    # ---------- model ----------
    def add(self, rows):
        added = 0
        seen = {path for path, _r in self.items}
        for path, root in rows:
            key = str(path)
            if key in seen:
                continue
            seen.add(key)
            index = len(self.items)
            self.items.append((key, str(root) if root else None))
            self._insert(index)
            added += 1
        if added:
            self._sync_visibility()
        return added

    def _insert(self, index):
        path = self.items[index][0]
        item = LVITEMW()
        item.mask = LVIF_TEXT | LVIF_IMAGE
        item.iItem = index
        item.iSubItem = 0
        item.pszText = ctypes.c_wchar_p(Path(path).name)
        item.cchTextMax = len(Path(path).name) + 1
        item.iImage = self._icon_index(path)
        self._user32.SendMessageW(self._hwnd, LVM_INSERTITEMW, 0,
                                  _as_ptr(item))

    def _icon_index(self, path):
        """Shell icon index for a row (cached per kind)."""
        try:
            entry = Path(path)
            key = ('dir',) if entry.is_dir() else ('file',
                                                   entry.suffix.lower())
            if key in self._icons:
                return self._icons[key]
            if self._icon_list is None:
                self._icon_list = self._comctl32.ImageList_Create(
                    16, 16, ILC_COLOR32 | ILC_MASK, 0, 8)
                self._user32.SendMessageW(self._hwnd, LVM_SETIMAGELIST,
                                          LVSIL_SMALL, self._icon_list)
            info = SHFILEINFOW()
            flags = SHGFI_ICON | SHGFI_SMALLICON
            attributes = FILE_ATTRIBUTE_NORMAL
            if entry.is_dir():
                attributes = FILE_ATTRIBUTE_DIRECTORY
                flags |= SHGFI_USEFILEATTRIBUTES
            self._shell32.SHGetFileInfoW(
                str(entry), attributes, _as_ptr(info), ctypes.sizeof(info),
                flags)
            index = -1
            if info.hIcon:
                index = self._comctl32.ImageList_AddIcon(self._icon_list,
                                                         info.hIcon)
                self._user32.DestroyIcon(info.hIcon)
            self._icons[key] = index
            return index
        except Exception:
            return -1

    def clear(self):
        if self._hwnd is None:
            return
        self.items = []
        self._user32.SendMessageW(self._hwnd, LVM_DELETEALLITEMS, 0, 0)
        self._sync_visibility()

    def count(self):
        return len(self.items)

    def entries(self):
        return list(self.items)

    def selection_rows(self):
        rows = []
        if self._hwnd is None:
            return rows
        index = -1
        while True:
            index = self._user32.SendMessageW(self._hwnd, LVM_GETNEXTITEM,
                                              index, LVNI_SELECTED)
            if index < 0 or index >= len(self.items):
                break
            rows.append(int(index))
        return rows

    def selected_entries(self):
        out = []
        for row in self.selection_rows():
            path, root = self.items[row]
            out.append((Path(path), Path(root) if root else None))
        return out

    def has_selection(self):
        return bool(self.selection_rows())

    def remove_selected(self):
        rows = sorted(set(self.selection_rows()), reverse=True)
        if not rows:
            return 0
        for row in rows:
            self._user32.SendMessageW(self._hwnd, LVM_DELETEITEM, row, 0)
            if 0 <= row < len(self.items):
                del self.items[row]
        self._sync_visibility()
        return len(rows)

    def select_rows(self, rows):
        """Replace the selection (LVM_SETITEMSTATE needs an LVITEM)."""
        if self._hwnd is None:
            return
        wanted = set(int(row) for row in rows)
        for row in range(len(self.items)):
            selected = row in wanted
            item = LVITEMW()
            item.mask = LVIF_STATE
            item.iItem = row
            item.state = LVIS_SELECTED | (LVIS_FOCUSED if selected else 0)
            item.stateMask = LVIS_SELECTED | LVIS_FOCUSED
            self._user32.SendMessageW(self._hwnd, LVM_SETITEMSTATE, row,
                                      _as_ptr(item))

    def select_index(self, row):
        self.select_rows([row])

    def select_first(self):
        if self.items:
            self.select_index(0)

    # ---------- internals ----------
    def _sync_visibility(self):
        if self._hwnd is None:
            return
        self._user32.ShowWindow(
            self._hwnd, 5 if (self.items and self.slot.isVisible()) else 0)

    def _subclass_host(self):
        """Observe selection changes (WM_NOTIFY) on the hosting widget."""
        self._proc_ref = WNDPROC(self._host_proc)
        self._old_proc = self._setter(
            self._host, GWLP_WNDPROC,
            ctypes.cast(self._proc_ref, ctypes.c_void_p))

    def _host_proc(self, hwnd, msg, wparam, lparam):
        try:
            if msg == WM_NOTIFY and self._hwnd is not None:
                header = ctypes.cast(ctypes.c_void_p(lparam),
                                     ctypes.POINTER(NMHDR)).contents
                if (int(header.hwndFrom or 0) == self._hwnd
                        and ctypes.c_int(header.code).value == LVN_ITEMCHANGED
                        and not self._in_notify):
                    self._in_notify = True
                    try:
                        if callable(self.on_selection):
                            self.on_selection()
                    finally:
                        self._in_notify = False
            elif msg == WM_DROPFILES and callable(self.on_drop):
                paths = self._dropped_paths(wparam)
                self._user32.DragFinish(wparam)
                if paths:
                    self.on_drop(paths)
            elif msg == WM_NCDESTROY:
                self._restore_host()
        except Exception:
            pass
        return self._user32.CallWindowProcW(self._old_proc, hwnd, msg,
                                            WPARAM(wparam), LPARAM(lparam))

    def _dropped_paths(self, hdrop):
        paths = []
        try:
            count = self._shell32.DragQueryFileW(hdrop, 0xFFFFFFFF, None, 0)
            for index in range(count):
                length = self._shell32.DragQueryFileW(hdrop, index, None, 0)
                buffer = ctypes.create_unicode_buffer(length + 1)
                self._shell32.DragQueryFileW(hdrop, index, buffer,
                                             length + 1)
                if buffer.value:
                    paths.append(buffer.value)
        except Exception:
            pass
        return paths

    def _restore_host(self):
        try:
            if self._old_proc:
                self._setter(self._host, GWLP_WNDPROC, self._old_proc)
        except Exception:
            pass

    def _note(self, message):
        try:
            import platform_effects as pe
            pe._note(message)
        except Exception:
            pass
