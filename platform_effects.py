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
_SIDEBAR_VIEW = None
_SIDEBAR_WIDTH = 0.0
_SWITCH = None
_SWITCH_TARGET = None
_SWITCH_SLOT = None
_SWITCH_TARGET_CLASS = None


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


def last_sidebar_view():
    """The sidebar material view inserted on macOS (for tests)."""
    return _SIDEBAR_VIEW


def band_material_name():
    """Material for the title band. Finder-style toolbars use a toolbar
    material with `withinWindow` blending (blurring the window's own
    content, so over the opaque content it looks like a frosted layer),
    while the sidebar keeps `behindWindow` (the desktop blurs through).
    Override for A/B testing with FORMAT_TEX_BAND_MATERIAL
    (sidebar|headerView|titlebar|underWindowBackground)."""
    return os.environ.get('FORMAT_TEX_BAND_MATERIAL',
                          'headerView').strip()


def _material_constant(AppKit, name):
    table = {
        'sidebar': AppKit.NSVisualEffectMaterialSidebar,
        'headerView': AppKit.NSVisualEffectMaterialHeaderView,
        'titlebar': AppKit.NSVisualEffectMaterialTitlebar,
        'underwindowbackground':
            AppKit.NSVisualEffectMaterialUnderWindowBackground,
    }
    return table.get(name.lower(), AppKit.NSVisualEffectMaterialHeaderView)


def set_sidebar_width(width):
    """Tell the native layer how wide the Qt sidebar column is, so the
    sidebar material can be framed to match (Finder-style full-height
    sidebar sharing the title bar's blur layer)."""
    global _SIDEBAR_WIDTH
    try:
        _SIDEBAR_WIDTH = max(0.0, float(width))
    except (TypeError, ValueError):
        _SIDEBAR_WIDTH = 0.0


def _switch_target_class():
    """pyobjc NSObject subclass acting as the switch's action target."""
    global _SWITCH_TARGET_CLASS
    if _SWITCH_TARGET_CLASS is None:
        from AppKit import NSObject

        class _SwitchTarget(NSObject):
            def switched_(self, sender):
                self.callback(int(sender.state()) == 1)

        _SWITCH_TARGET_CLASS = _SwitchTarget
    return _SWITCH_TARGET_CLASS


def create_native_switch(window, callback, on=False):
    """Create/insert a native NSSwitch above the Qt view (so it draws and
    behaves natively in the sidebar). Returns True on success; callers
    fall back to a Qt checkbox otherwise."""
    global _SWITCH, _SWITCH_TARGET
    if sys.platform != 'darwin':
        return False
    try:
        import objc
        import AppKit

        if _SWITCH is None:
            target = _switch_target_class().alloc().init()
            target.callback = callback
            switch = AppKit.NSSwitch.alloc().init()
            switch.setControlSize_(AppKit.NSControlSizeRegular)
            switch.setTarget_(target)
            switch.setAction_(b'switched:')
            switch.sizeToFit()
            _SWITCH, _SWITCH_TARGET = switch, target
        _SWITCH.setState_(AppKit.NSControlStateValueOn if on
                          else AppKit.NSControlStateValueOff)

        qt_view = objc.objc_object(c_void_p=int(window.winId()))
        theme = qt_view.superview()
        try:
            _SWITCH.removeFromSuperview()
        except Exception:
            pass
        theme.addSubview_positioned_relativeTo_(_SWITCH, AppKit.NSWindowAbove,
                                               qt_view)
        # once in the window the switch settles on its real size
        _SWITCH.sizeToFit()
        return True
    except Exception as exc:
        _note('native switch failed: {}: {}'.format(type(exc).__name__, exc))
        return False


def place_native_switch(window, slot):
    """Centre the native switch over the Qt ``slot`` widget."""
    global _SWITCH_SLOT
    if sys.platform != 'darwin' or _SWITCH is None:
        return
    _SWITCH_SLOT = slot
    try:
        import objc
        from PySide6.QtCore import QPoint

        qt_view = objc.objc_object(c_void_p=int(window.winId()))
        theme = qt_view.superview()
        top_left = slot.mapTo(window, QPoint(0, 0))
        # The Qt view is flipped (top-left origin), the theme frame is
        # not - let AppKit do the conversion so both are handled.
        rect = ((float(top_left.x()), float(top_left.y())),
                (float(slot.width()), float(slot.height())))
        target = qt_view.convertRect_toView_(rect, theme)
        sw_w = _SWITCH.frame().size.width
        sw_h = _SWITCH.frame().size.height
        x = target.origin.x + (target.size.width - sw_w) / 2.0
        y = target.origin.y + (target.size.height - sw_h) / 2.0
        _SWITCH.setFrame_(((x, y), (sw_w, sw_h)))
        _SWITCH.setHidden_(False)
    except Exception as exc:
        _note('native switch placement failed: {}: {}'.format(
            type(exc).__name__, exc))


def native_switch_state():
    try:
        return bool(_SWITCH is not None and _SWITCH.state() == 1)
    except Exception:
        return False


def set_native_switch_state(on):
    try:
        if _SWITCH is not None:
            _SWITCH.setState_(1 if on else 0)
    except Exception:
        pass


def has_native_switch():
    return _SWITCH is not None


def native_switch_view():
    """The NSSwitch instance (for tests), or None."""
    return _SWITCH


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
    """Re-fit the materials and re-centre the native titlebar chrome.
    AppKit resets the chrome on resize/activation, so call this from the
    window's resize/change handlers (and after the splitter moves)."""
    if sys.platform != 'darwin' or _BAND_VIEW is None:
        return
    try:
        import objc
        qt_view = objc.objc_object(c_void_p=int(window.winId()))
        nswin = qt_view.window()
        _place_band(nswin, qt_view.superview())
        _place_sidebar(nswin, qt_view.superview())
        _align_titlebar(nswin)
        if _SWITCH is not None and _SWITCH_SLOT is not None:
            place_native_switch(window, _SWITCH_SLOT)
    except Exception as exc:
        _note('reposition failed: {}: {}'.format(type(exc).__name__, exc))


def _make_material_view(AppKit, material, blending):
    view = AppKit.NSVisualEffectView.alloc().init()
    view.setMaterial_(material)
    view.setBlendingMode_(blending)
    view.setState_(AppKit.NSVisualEffectStateFollowsWindowActiveState)
    return view


def _macos(window, dark):
    import objc
    import AppKit

    global _BAND_VIEW, _SIDEBAR_VIEW

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
        # toolbar-like: blur the window's own content behind the band
        _BAND_VIEW = _make_material_view(
            AppKit, _material_constant(AppKit, band_material_name()),
            AppKit.NSVisualEffectBlendingModeWithinWindow)
    if _SIDEBAR_VIEW is None:
        # sidebar-like: let the desktop blur through
        _SIDEBAR_VIEW = _make_material_view(
            AppKit, AppKit.NSVisualEffectMaterialSidebar,
            AppKit.NSVisualEffectBlendingModeBehindWindow)
    # order matters: the band must sit above the sidebar so that, over the
    # sidebar column, it blurs the sidebar's material (desktop shows through)
    for view in (_SIDEBAR_VIEW, _BAND_VIEW):
        try:
            view.removeFromSuperview()
        except Exception:
            pass
        theme.addSubview_positioned_relativeTo_(view, AppKit.NSWindowBelow,
                                                qt_view)
    _place_band(nswin, theme)
    _place_sidebar(nswin, theme)
    _align_titlebar(nswin)
    return '52 pt band + sidebar blur'


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


def _place_sidebar(nswin, theme):
    """Frame the sidebar material as the left column. It spans the full
    window height so it merges with the top band into one continuous
    blur (Finder-style), and stays visible in full screen."""
    if _SIDEBAR_VIEW is None:
        return
    bounds = theme.bounds()
    if _SIDEBAR_WIDTH <= 0:
        _SIDEBAR_VIEW.setHidden_(True)
        return
    _SIDEBAR_VIEW.setHidden_(False)
    _SIDEBAR_VIEW.setFrame_(((0.0, 0.0),
                             (_SIDEBAR_WIDTH, bounds.size.height)))


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

    # title: left-align it just right of the sidebar divider (falling
    # back to just after the traffic lights when there is no sidebar)
    title = nswin.toolbarTitlebarTitleTextField()
    if title is None:
        return
    if _SIDEBAR_WIDTH > 0:
        target = left + _SIDEBAR_WIDTH + title_gap()
    else:
        zoom = nswin.standardWindowButton_(AppKit.NSWindowZoomButton)
        if zoom is None:
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
