#!/usr/bin/env python3
r"""Native window chrome per OS, for the Qt GUI.

macOS  Native unified NSToolbar (tall bar, system blur material,
       centred traffic lights, separator) - no custom material views
Windows 11  DWM Mica (with legacy-attribute and solid fallbacks)
Linux  system Qt platform theme (no override)

IMPORTANT: Qt renders the whole widget tree into a single top-level
NSView and its show() path only orders the window on screen while that
view is the window's contentView. Never reparent or re-wrap Qt's view.

Usage
-----
    from platform_effects import prepare_qt, apply_effects, notes
    prepare_qt(window)                    # before show(): Qt attributes +
                                          # native toolbar (sets geometry)
    applied = apply_effects(window, dark=...)   # after show()
    notes()  -> list of fallback/failure messages for logging
"""

import sys

_NOTES = []


def _note(msg):
    _NOTES.append(str(msg))


def notes():
    return list(_NOTES)


def prepare_qt(window):
    """Set things that must precede the native window being shown
    (safe to call from __init__): Qt translucency and, on macOS, the
    unified toolbar - installing it before show() lets Qt compute its
    content geometry below the taller title bar."""
    from PySide6.QtCore import Qt
    window.setAttribute(Qt.WA_TranslucentBackground, True)
    if sys.platform == 'darwin':
        try:
            _install_toolbar(window)
        except Exception as exc:
            _note('toolbar install failed: {}: {}'.format(
                type(exc).__name__, exc))


def apply_effects(window, dark=False):
    """Apply the best native window chrome for the current OS. Returns
    a short description (for logging); failures degrade to the system
    theme instead of raising."""
    try:
        if sys.platform == 'darwin':
            return _macos(window, dark)
        if sys.platform == 'win32':
            return _windows(window, dark)
        return 'system theme'
    except Exception as exc:
        _note('native effects failed: {}: {}'.format(type(exc).__name__, exc))
        return 'system theme (fallback)'


def _install_toolbar(window):
    """Give the window a native unified toolbar: a tall system-drawn bar
    with the toolbar blur material, centred traffic lights and the
    automatic separator. Idempotent."""
    import objc
    import AppKit

    qt_view = objc.objc_object(c_void_p=int(window.winId()))
    nswin = qt_view.window()
    tb = nswin.toolbar()
    if tb is None:
        tb = AppKit.NSToolbar.alloc().initWithIdentifier_(
            'latex-coding-style-formatter')
        try:
            tb.setAllowsUserCustomization_(False)
            tb.setAutosavesConfiguration_(False)
        except Exception as exc:
            _note('toolbar options failed: {}'.format(exc))
        nswin.setToolbar_(tb)
    nswin.setToolbarStyle_(AppKit.NSWindowToolbarStyleUnified)
    try:
        nswin.setShowsToolbarButton_(False)
    except Exception as exc:
        _note('toolbar button hide failed: {}'.format(exc))
    try:
        nswin.setTitlebarSeparatorStyle_(
            AppKit.NSTitlebarSeparatorStyleLine)
    except Exception as exc:
        _note('titlebar separator unavailable: {}'.format(exc))


def _macos(window, dark):
    _install_toolbar(window)
    return 'unified toolbar (system blur)'


def titlebar_height(nswin):
    """Height of the title bar + toolbar region in points (0 when
    absent); used by tests to confirm the bar is toolbar-tall."""
    try:
        frame = nswin.frame()
        content = nswin.contentRectForFrameRect_(frame)
        return max(0.0, frame.size.height - content.size.height)
    except Exception:
        return 0.0


def _windows(window, dark):
    import ctypes

    hwnd = int(window.winId())
    dwm = ctypes.windll.dwmapi

    def set_attr(attr, value):
        value = ctypes.c_int(int(value))
        return dwm.DwmSetWindowAttribute(ctypes.c_void_p(hwnd), attr,
                                         ctypes.byref(value),
                                         ctypes.sizeof(value))

    set_attr(20, 1 if dark else 0)   # DWMWA_USE_IMMERSIVE_DARK_MODE

    class MARGINS(ctypes.Structure):
        _fields_ = [('left', ctypes.c_int), ('right', ctypes.c_int),
                    ('top', ctypes.c_int), ('bottom', ctypes.c_int)]

    margins = MARGINS(-1, -1, -1, -1)
    if dwm.DwmExtendFrameIntoClientArea(ctypes.c_void_p(hwnd),
                                       ctypes.byref(margins)) != 0:
        return 'solid (extend frame failed)'
    if set_attr(38, 2) == 0:         # DWMWA_SYSTEMBACKDROP_TYPE = Mica
        return 'mica'
    if set_attr(1029, 1) == 0:       # DWMWA_MICA_EFFECT (Windows 11 21H2)
        return 'mica (legacy attribute)'
    _note('system backdrop attributes rejected (Windows 10?)')
    return 'acrylic/solid fallback'
