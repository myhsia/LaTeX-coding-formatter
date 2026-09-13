#!/usr/bin/env python3
r"""Native window materials per OS, for the Qt GUI.

macOS 26+   NSGlassEffectView (Liquid Glass), NSVisualEffectView below 26
Windows 11  DWM Mica (with legacy-attribute and solid fallbacks)
Linux       system Qt platform theme (no override)

IMPORTANT: Qt renders the whole widget tree into a single top-level
NSView and its show() path only orders the window on screen while that
view is the window's contentView. Never reparent or re-wrap Qt's view -
insert the material as a SIBLING in the window's theme frame instead.

Usage
-----
    from platform_effects import prepare_qt, apply_effects, notes
    prepare_qt(window)                    # before show(): translucency
    applied = apply_effects(window, dark=...)   # after show()
    notes()  -> list of fallback/failure messages for logging
"""

import sys

_NOTES = []
_LAST_VIEW = None


def _note(msg):
    _NOTES.append(str(msg))


def notes():
    return list(_NOTES)


def last_material_view():
    """The material view inserted by the last apply_effects call (or
    None), so callers/tests can verify its placement."""
    return _LAST_VIEW


def prepare_qt(window):
    """Set Qt attributes that must precede the native window being
    shown (safe to call from __init__)."""
    from PySide6.QtCore import Qt
    window.setAttribute(Qt.WA_TranslucentBackground, True)


def apply_effects(window, dark=False):
    """Insert the best native window material for the current OS.
    Must be called AFTER the window is shown. Returns a short
    description (for logging); failures degrade to the system theme
    instead of raising."""
    try:
        if sys.platform == 'darwin':
            return _macos(window, dark)
        if sys.platform == 'win32':
            return _windows(window, dark)
        return 'system theme'
    except Exception as exc:
        _note('native effects failed: {}: {}'.format(type(exc).__name__, exc))
        return 'system theme (fallback)'


def titlebar_height(nswin):
    """Height of the title bar strip in points (0 when absent)."""
    try:
        frame = nswin.frame()
        content = nswin.contentRectForFrameRect_(frame)
        return max(0.0, frame.size.height - content.size.height)
    except Exception:
        return 0.0


def reposition_material(window):
    """Re-fit the material view to the current title bar strip (call on
    window resize); hides it when there is no title bar (full screen)."""
    view = _LAST_VIEW
    if view is None:
        return
    try:
        import objc
        qt_view = objc.objc_object(c_void_p=int(window.winId()))
        nswin = qt_view.window()
        theme = qt_view.superview()
        height = titlebar_height(nswin)
        width = theme.bounds().size.width
        total = theme.bounds().size.height
        if height <= 0:
            view.setHidden_(True)
            return
        view.setHidden_(False)
        view.setFrame_(((0.0, total - height), (width, height)))
    except Exception as exc:
        _note('reposition failed: {}: {}'.format(type(exc).__name__, exc))


def _macos(window, dark):
    global _LAST_VIEW
    import objc
    import AppKit

    qt_view = objc.objc_object(c_void_p=int(window.winId()))
    nswin = qt_view.window()
    try:
        nswin.setTitlebarAppearsTransparent_(True)
    except Exception as exc:
        _note('titlebar transparency failed: {}'.format(exc))

    try:
        nswin.setTitlebarSeparatorStyle_(
            AppKit.NSTitlebarSeparatorStyleLine)
    except Exception as exc:
        _note('titlebar separator unavailable: {}'.format(exc))

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

    theme = qt_view.superview()
    if _LAST_VIEW is not None:       # idempotent re-apply (WinIdChange)
        try:
            _LAST_VIEW.removeFromSuperview()
        except Exception:
            pass
    # The material covers only the title bar strip, not the content
    # area (which the GUI paints with an opaque window color).
    height = titlebar_height(nswin)
    width = theme.bounds().size.width
    total = theme.bounds().size.height
    effect.setFrame_(((0.0, total - height), (width, height)))
    effect.setAutoresizingMask_(AppKit.NSViewWidthSizable
                                | AppKit.NSViewMinYMargin)
    theme.addSubview_positioned_relativeTo_(effect, AppKit.NSWindowBelow,
                                            qt_view)
    _LAST_VIEW = effect
    return applied


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
