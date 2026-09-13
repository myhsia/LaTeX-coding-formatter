#!/usr/bin/env python3
r"""Native window materials per OS, for the Qt GUI.

macOS 26+   NSGlassEffectView (Liquid Glass), NSVisualEffectView below 26
Windows 11  DWM Mica (with legacy-attribute and solid fallbacks)
Linux       system Qt platform theme (no override)

Usage
-----
    from platform_effects import apply_effects, notes
    applied = apply_effects(window, dark=...)
    notes()  -> list of fallback/failure messages for logging
"""

import sys

_NOTES = []


def _note(msg):
    _NOTES.append(str(msg))


def notes():
    return list(_NOTES)


def apply_effects(window, dark=False):
    """Apply the best native window material for the current OS and
    return a short description (for logging). Failures degrade to the
    system theme instead of raising."""
    try:
        if sys.platform == 'darwin':
            return _macos(window, dark)
        if sys.platform == 'win32':
            return _windows(window, dark)
        return 'system theme'
    except Exception as exc:
        _note('native effects failed: {}: {}'.format(type(exc).__name__, exc))
        return 'system theme (fallback)'


def _macos(window, dark):
    from PySide6.QtCore import Qt
    window.setAttribute(Qt.WA_TranslucentBackground, True)

    import objc
    import AppKit
    from Foundation import NSMakeRect  # noqa: F401  (ensures Foundation loads)

    content = objc.objc_object(c_void_p=int(window.winId()))
    nswin = content.window()
    try:
        nswin.setTitlebarAppearsTransparent_(True)
    except Exception as exc:
        _note('titlebar transparency failed: {}'.format(exc))

    try:
        glass = objc.lookUpClass('NSGlassEffectView')
    except objc.nosuchclass_error:
        glass = None

    if glass is not None:
        effect = glass.alloc().init()
        applied = 'liquid glass'
    else:
        effect = AppKit.NSVisualEffectView.alloc().init()
        material = (AppKit.NSVisualEffectMaterialUnderWindowBackground
                    if not dark else AppKit.NSVisualEffectMaterialDark)
        effect.setMaterial_(material)
        effect.setBlendingMode_(AppKit.NSVisualEffectBlendingModeBehindWindow)
        effect.setState_(AppKit.NSVisualEffectStateFollowsWindowActiveState)
        applied = 'vibrancy'

    effect.setFrame_(content.bounds())
    effect.setAutoresizingMask_(AppKit.NSViewWidthSizable
                                 | AppKit.NSViewHeightSizable)
    content.addSubview_positioned_relativeTo_(effect, AppKit.NSWindowBelow,
                                              None)
    return applied


def _windows(window, dark):
    from PySide6.QtCore import Qt
    window.setAttribute(Qt.WA_TranslucentBackground, True)

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
