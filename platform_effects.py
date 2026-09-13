#!/usr/bin/env python3
r"""Native window chrome per OS, for the Qt GUI.

macOS  A custom 52 pt title band drawn with an NSVisualEffectView
       (sidebar blur); the native titlebar chrome (traffic lights and
       window title) is shifted down so it is vertically centred in the
       band, and the Qt content is expected to leave a matching
       transparent strip at the top (see band_height()).
Windows 11  DWM Mica (with legacy-attribute and solid fallbacks)
Linux  system Qt platform theme (no override)

IMPORTANT: Qt renders the whole widget tree into a single top-level
NSView and its show() path only orders the window on screen while that
view is the window's contentView. Never reparent or re-wrap Qt's view.

Usage
-----
    from platform_effects import (band_height, prepare_qt, apply_effects,
                                  reposition_materials, notes)
    prepare_qt(window)                  # before show(): Qt attributes
    apply_effects(window, dark=...)     # after show(): band + chrome
    reposition_materials(window)        # on resize/activate/full-screen
    notes()  -> list of fallback/failure messages for logging
"""

import os
import sys

_NOTES = []

BAND_HEIGHT = 52.0
LIGHTS_INSET = 19.0      # native unified-toolbar inset (Finder/Notes)
TITLE_GAP = 8.0          # gap between the traffic lights and the title
_BAND_VIEW = None


def _note(msg):
    _NOTES.append(str(msg))


def notes():
    return list(_NOTES)


def band_height():
    """Height of the top band the GUI must leave transparent (0 when the
    platform does not use one)."""
    return BAND_HEIGHT if sys.platform == 'darwin' else 0.0


def lights_inset():
    """Left gap between the window edge and the traffic lights. Defaults
    to the native unified-toolbar inset (19 pt); override for fine-tuning
    with the FORMAT_TEX_LIGHTS_INSET environment variable."""
    try:
        return float(os.environ.get('FORMAT_TEX_LIGHTS_INSET',
                                    LIGHTS_INSET))
    except (TypeError, ValueError):
        return LIGHTS_INSET


def title_gap():
    """Gap between the traffic lights and the left-aligned window title
    (default 8 pt); override with FORMAT_TEX_TITLE_GAP."""
    try:
        return float(os.environ.get('FORMAT_TEX_TITLE_GAP', TITLE_GAP))
    except (TypeError, ValueError):
        return TITLE_GAP


def last_material_view():
    """The band view inserted on macOS (for tests), or None."""
    return _BAND_VIEW


def prepare_qt(window):
    """Set Qt attributes that must precede the native window being
    shown (safe to call from __init__)."""
    from PySide6.QtCore import Qt
    window.setAttribute(Qt.WA_TranslucentBackground, True)


def apply_effects(window, dark=False):
    """Apply the native window chrome for the current OS. Returns a
    short description (for logging); failures degrade to the platform
    default instead of raising."""
    try:
        if sys.platform == 'darwin':
            return _macos(window, dark)
        if sys.platform == 'win32':
            return _windows(window, dark)
        return 'system theme'
    except Exception as exc:
        _note('native effects failed: {}: {}'.format(type(exc).__name__, exc))
        return 'system theme (fallback)'


def reposition_materials(window):
    """Re-fit the band and re-centre the native titlebar chrome. AppKit
    resets both on resize/activation, so call this from the window's
    resize/change handlers."""
    if sys.platform != 'darwin' or _BAND_VIEW is None:
        return
    try:
        import objc
        qt_view = objc.objc_object(c_void_p=int(window.winId()))
        nswin = qt_view.window()
        _place_band(nswin, qt_view.superview())
        _align_titlebar(nswin)
    except Exception as exc:
        _note('reposition failed: {}: {}'.format(type(exc).__name__, exc))


def _macos(window, dark):
    import objc
    import AppKit

    global _BAND_VIEW

    qt_view = objc.objc_object(c_void_p=int(window.winId()))
    nswin = qt_view.window()
    theme = qt_view.superview()

    # We draw the band ourselves - drop any native toolbar.
    try:
        if nswin.toolbar() is not None:
            nswin.setToolbar_(None)
    except Exception as exc:
        _note('toolbar removal failed: {}'.format(exc))

    mask = nswin.styleMask()
    if not (mask & AppKit.NSWindowStyleMaskFullSizeContentView):
        nswin.setStyleMask_(mask | AppKit.NSWindowStyleMaskFullSizeContentView)
    nswin.setTitlebarAppearsTransparent_(True)
    nswin.setTitleVisibility_(AppKit.NSWindowTitleVisible)
    try:
        nswin.setTitlebarSeparatorStyle_(AppKit.NSTitlebarSeparatorStyleNone)
    except Exception:
        pass

    if _BAND_VIEW is None:
        band = AppKit.NSVisualEffectView.alloc().init()
        band.setMaterial_(AppKit.NSVisualEffectMaterialSidebar)
        band.setBlendingMode_(AppKit.NSVisualEffectBlendingModeBehindWindow)
        band.setState_(AppKit.NSVisualEffectStateFollowsWindowActiveState)
        _BAND_VIEW = band
    try:
        _BAND_VIEW.removeFromSuperview()
    except Exception:
        pass
    theme.addSubview_positioned_relativeTo_(_BAND_VIEW, AppKit.NSWindowBelow,
                                            qt_view)
    _place_band(nswin, theme)
    _align_titlebar(nswin)
    return '52 pt sidebar-blur band'


def _place_band(nswin, theme):
    import AppKit
    if _BAND_VIEW is None:
        return
    if nswin.styleMask() & AppKit.NSWindowStyleMaskFullScreen:
        _BAND_VIEW.setHidden_(True)      # no title bar in full screen
        return
    bounds = theme.bounds()
    _BAND_VIEW.setHidden_(False)
    _BAND_VIEW.setFrame_(((0.0, bounds.size.height - BAND_HEIGHT),
                          (bounds.size.width, BAND_HEIGHT)))


def _align_titlebar(nswin):
    """Centre the native titlebar container in the band, give the traffic
    lights the native left inset and left-align the window title just
    after them.

    AppKit resets the chrome on layout, so instead of storing a baseline
    we correct from the measured offsets - each correction moves the
    chrome 1:1, so one step is exact and re-running is a no-op."""
    import AppKit

    close = nswin.standardWindowButton_(AppKit.NSWindowCloseButton)
    if close is None:
        return
    frame = nswin.frame()
    top = frame.origin.y + frame.size.height
    left = frame.origin.x

    def screen_rect(view):
        rect = view.convertRect_toView_(view.bounds(), None)
        return nswin.convertRectToScreen_(rect)

    # vertical: centre the container in the band
    rect = screen_rect(close)
    current = top - (rect.origin.y + rect.size.height / 2)
    delta_y = current - BAND_HEIGHT / 2.0
    if abs(delta_y) >= 0.5:
        box = close.superview()
        bf = box.frame()
        flipped = bool(box.superview().isFlipped())
        box.setFrameOrigin_((bf.origin.x,
                             bf.origin.y + (-delta_y if flipped else delta_y)))

    # horizontal: inset the three buttons natively
    rect = screen_rect(close)
    delta_x = lights_inset() - (rect.origin.x - left)
    if abs(delta_x) >= 0.5:
        for kind in (AppKit.NSWindowCloseButton,
                     AppKit.NSWindowMiniaturizeButton,
                     AppKit.NSWindowZoomButton):
            button = nswin.standardWindowButton_(kind)
            if button is None:
                continue
            bf = button.frame()
            button.setFrameOrigin_((bf.origin.x + delta_x, bf.origin.y))

    # title: left-align it just after the traffic lights
    title = nswin.toolbarTitlebarTitleTextField()
    zoom = nswin.standardWindowButton_(AppKit.NSWindowZoomButton)
    if title is None or zoom is None:
        return
    zoom_rect = screen_rect(zoom)
    target = zoom_rect.origin.x + zoom_rect.size.width + title_gap()
    delta = target - screen_rect(title).origin.x
    if abs(delta) >= 0.5:
        tf = title.frame()
        title.setFrameOrigin_((tf.origin.x + delta, tf.origin.y))


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
