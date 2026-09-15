#!/usr/bin/env python3
r"""Native window chrome per OS, for the Qt GUI.

macOS  A custom 52 pt toolbar strip drawn with an NSVisualEffectView
       (headerView material, withinWindow) sits ABOVE the Qt view so it
       frosts the opaque content panel, and is click-through so the Qt
       widgets keep working. The sidebar material (sidebar,
       behindWindow) sits BELOW the Qt view, which shows it wherever the
       sidebar widget is transparent; it spans the full window height, so
       one continuous blur runs from the top edge down (no seam, no line).
       The native titlebar chrome (traffic lights and window title) is
       centred in the strip and the title left-aligned past the sidebar.
       The Qt content is expected to leave a matching transparent strip
       at the top (see band_height()).
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
# footer (+/- bar) metrics, matching macOS Settings' footer proportions
FOOTER_SYMBOL_SIZE = 12.0      # SF Symbol point size for plus/minus
FOOTER_BUTTON_WIDTH = 26.0
FOOTER_BUTTON_HEIGHT = 20.0
FOOTER_ROW_PADDING = 4.0       # row height = button height + this
FOOTER_SEPARATOR_HEIGHT = 12.0
FOOTER_SEPARATOR_GAP = 3.0     # equal gap on each side of the divider
FOOTER_INSET = 10.0            # left inset of the group inside the row
FOOTER_CORNER_RADIUS = 8.0     # matches the list frame's border radius
# The footer bar reads as a *raised* translucent bar in Settings: a
# light/dark overlay on top of the material. Tuned so the band ends up
# slightly lighter than the list in dark mode (and slightly darker in
# light mode), like the native reference.
FOOTER_TINT_DARK = 0.12        # white overlay strength in dark mode
FOOTER_TINT_LIGHT = 0.06       # black overlay strength in light mode
_FOOTER_DARK = True
LIGHTS_INSET = 19.0      # native unified-toolbar inset (Finder/Notes)
TITLE_GAP = 12.0         # gap between the sidebar edge and the title
_BAND_VIEW = None
_SIDEBAR_VIEW = None
_TITLE_VIEW = None
_SIDEBAR_WIDTH = 0.0
_SWITCH = None
_SWITCH_TARGET = None
_SWITCH_SLOT = None
_SWITCH_TARGET_CLASS = None
_CLICK_THROUGH_CLASS = None
_PLUS_MINUS_ADD = None
_PLUS_MINUS_SEP = None
_PLUS_MINUS_REMOVE = None
_PLUS_MINUS_TARGET = None
_PLUS_MINUS_TARGET_CLASS = None
_FOOTER_VIEW = None
_FOOTER_SLOT = None
_FOOTER_ROW = None
_PLUS_MINUS_SLOT = None


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


def last_title_view():
    """The title label we draw ourselves on macOS (for tests)."""
    return _TITLE_VIEW


def title_font_size():
    """Point size for the window title (macOS 26 toolbar titles use the
    standard 13 pt; override with FORMAT_TEX_TITLE_SIZE)."""
    try:
        return float(os.environ.get('FORMAT_TEX_TITLE_SIZE', 13.0))
    except (TypeError, ValueError):
        return 13.0


def band_material_name():
    """Material for the title strip. Finder-style toolbars use a toolbar
    material with `withinWindow` blending (blurring the window's own
    content, so over the opaque content it looks like a frosted layer),
    while the sidebar keeps `behindWindow` (the desktop blurs through).
    Override for A/B testing with FORMAT_TEX_BAND_MATERIAL
    (sidebar|headerView|titlebar|underWindowBackground)."""
    return os.environ.get('FORMAT_TEX_BAND_MATERIAL',
                          'headerView').strip()


def band_is_click_through():
    """True on macOS: the strip material sits above the Qt view but must
    never consume mouse events, so it is a click-through subclass."""
    return sys.platform == 'darwin'


def _material_constant(AppKit, name):
    table = {
        'sidebar': AppKit.NSVisualEffectMaterialSidebar,
        'headerView': AppKit.NSVisualEffectMaterialHeaderView,
        'titlebar': AppKit.NSVisualEffectMaterialTitlebar,
        'underwindowbackground':
            AppKit.NSVisualEffectMaterialUnderWindowBackground,
    }
    return table.get(name.lower(), AppKit.NSVisualEffectMaterialHeaderView)


def native_window_color(dark=False):
    """The platform's native window background as '#rrggbb', so Qt can
    paint its content surface with the real system colour instead of its
    own approximation. Falls back to None when unavailable.

    NSColor.windowBackgroundColor is a dynamic catalog colour, so its
    resolution depends on the process appearance context. Measured in
    dark mode: a real native window renders as #1f1f1f and this call
    returns #1e1e1e in the packaged app - a match. Forcing a synthetic
    Aqua/DarkAqua appearance instead yields #323232, which does NOT match
    a real window, so the call is deliberately made in context here."""
    if sys.platform == 'darwin':
        try:
            import AppKit

            rgb = AppKit.NSColor.windowBackgroundColor() \
                .colorUsingColorSpace_(AppKit.NSColorSpace.sRGBColorSpace())
            if rgb is None:
                return None
            value = '#{:02x}{:02x}{:02x}'.format(
                int(round(rgb.redComponent() * 255)),
                int(round(rgb.greenComponent() * 255)),
                int(round(rgb.blueComponent() * 255)))
            if debug_enabled():
                print('[debug] windowBackgroundColor dark={} -> {}'.format(
                    dark, value), flush=True)
            return value
        except Exception as exc:
            _note('native window colour failed: {}: {}'.format(
                type(exc).__name__, exc))
            return None
    if sys.platform == 'win32':
        return '#1f1f1f' if dark else '#f3f3f3'
    return None


def set_sidebar_width(width):
    """Tell the native layer how wide the Qt sidebar column is, so the
    sidebar material can be framed to match (Finder-style full-height
    sidebar sharing the title bar's blur layer)."""
    global _SIDEBAR_WIDTH
    try:
        _SIDEBAR_WIDTH = max(0.0, float(width))
    except (TypeError, ValueError):
        _SIDEBAR_WIDTH = 0.0


def _nswindow(window):
    import objc
    return objc.objc_object(c_void_p=int(window.winId())).window()


def minimize_window(window):
    """Run the native minimise command (the yellow button's action), so
    the menu item behaves exactly like the traffic light. Returns False
    when unavailable, letting the caller fall back to Qt."""
    if sys.platform != 'darwin':
        return False
    try:
        _nswindow(window).performMiniaturize_(None)
        return True
    except Exception as exc:
        _note('native minimize failed: {}: {}'.format(
            type(exc).__name__, exc))
        return False


def zoom_window(window):
    """Run the native zoom command (the green button's action). Returns
    False when unavailable."""
    if sys.platform != 'darwin':
        return False
    try:
        _nswindow(window).performZoom_(None)
        return True
    except Exception as exc:
        _note('native zoom failed: {}: {}'.format(type(exc).__name__, exc))
        return False


def arrange_in_front(window):
    """Bring all of the app's windows to the front (the Window menu's
    standard 'Bring All to Front'). Returns False when unavailable."""
    if sys.platform != 'darwin':
        return False
    try:
        import AppKit
        AppKit.NSApp().arrangeInFront_(None)
        return True
    except Exception as exc:
        _note('arrangeInFront failed: {}: {}'.format(type(exc).__name__, exc))
        return False


def debug_enabled():
    """FORMAT_TEX_DEBUG=1 outlines the native material views so frames can
    be verified from a screenshot."""
    return os.environ.get('FORMAT_TEX_DEBUG', '').strip() not in ('', '0')


def _outline(view, red, green, blue):
    try:
        import AppKit

        view.setWantsLayer_(True)
        layer = view.layer()
        layer.setBorderWidth_(1.0)
        layer.setBorderColor_(AppKit.NSColor.colorWithSRGBRed_green_blue_alpha_(
            red, green, blue, 1.0).CGColor())
    except Exception as exc:
        _note('debug outline failed: {}: {}'.format(type(exc).__name__, exc))


def _debug_outlines():
    if not debug_enabled():
        return
    if _BAND_VIEW is not None:
        _outline(_BAND_VIEW, 1.0, 0.0, 0.0)      # red: toolbar strip
    if _SIDEBAR_VIEW is not None:
        _outline(_SIDEBAR_VIEW, 0.0, 0.6, 1.0)   # blue: sidebar column
    if _TITLE_VIEW is not None:
        _outline(_TITLE_VIEW, 1.0, 0.9, 0.0)     # yellow: title label


def _footer_tint(AppKit, dark):
    """Overlay colour for the footer bar (None to leave it alone).
    Override the strengths with FORMAT_TEX_FOOTER_TINT[_DARK|_LIGHT]."""
    def env(name, default):
        try:
            return float(os.environ.get(name, default))
        except (TypeError, ValueError):
            return default
    if dark:
        alpha = env('FORMAT_TEX_FOOTER_TINT_DARK',
                    env('FORMAT_TEX_FOOTER_TINT', FOOTER_TINT_DARK))
        base = AppKit.NSColor.whiteColor()
    else:
        alpha = env('FORMAT_TEX_FOOTER_TINT_LIGHT',
                    env('FORMAT_TEX_FOOTER_TINT', FOOTER_TINT_LIGHT))
        base = AppKit.NSColor.blackColor()
    if alpha <= 0:
        return None
    return base.colorWithAlphaComponent_(min(1.0, alpha))


def footer_material_name():
    """Material for the native strip behind the list's +/- row. macOS
    Settings panes give the footer its own subtle material; override for
    A/B testing with FORMAT_TEX_FOOTER_MATERIAL
    (headerView|contentBackground|underWindowBackground)."""
    return os.environ.get('FORMAT_TEX_FOOTER_MATERIAL',
                          'headerView').strip()


def create_footer_strip(window, frame_widget, row_widget, dark=True):
    """A native material strip behind the list's +/- row, so the footer
    reads like a Settings pane's footer rather than a flat tint. Anchored
    to the frame's inner bottom band (square top edge under the hairline,
    bottom corners rounded to match the frame), and placed BELOW the Qt
    view, which is transparent there. Returns True on success."""
    global _FOOTER_VIEW, _FOOTER_DARK
    _FOOTER_DARK = bool(dark)
    if sys.platform != 'darwin':
        return False
    try:
        import objc
        import AppKit

        if _FOOTER_VIEW is None:
            material = _material_constant(AppKit, footer_material_name())
            _FOOTER_VIEW = _make_material_view(
                AppKit, material,
                AppKit.NSVisualEffectBlendingModeWithinWindow)

            # follow the frame's bottom radius (layer masks are numeric:
            # minXMaxY | maxXMaxY = 4 | 8)
            try:
                _FOOTER_VIEW.setWantsLayer_(True)
                layer = _FOOTER_VIEW.layer()
                if layer is not None:
                    layer.setCornerRadius_(FOOTER_CORNER_RADIUS)
                    # the view layer's Y axis is flipped: the *bottom* pair
                    # is minXminY | maxXminY. Only those round, so the
                    # strip is the frame's bottom slice (top edge square,
                    # meeting the hairline).
                    layer.setMaskedCorners_(1 | 2)
            except Exception:
                pass

        qt_view = objc.objc_object(c_void_p=int(window.winId()))
        theme = qt_view.superview()
        try:
            _FOOTER_VIEW.removeFromSuperview()
        except Exception:
            pass
        theme.addSubview_positioned_relativeTo_(_FOOTER_VIEW,
                                               AppKit.NSWindowBelow, qt_view)
        place_footer_strip(window, frame_widget, row_widget)
        return True
    except Exception as exc:
        _note('native footer strip failed: {}: {}'.format(
            type(exc).__name__, exc))
        return False


def place_footer_strip(window, frame_widget, row_widget=None, inset=1.0):
    """Span the frame's inner bottom band: same width as the frame's
    inner area, as tall as the +/- row, bottom-aligned one inset above
    the frame bottom - so the strip's rounded bottom corners coincide
    with the frame's rounded border and its top edge is level under the
    hairline (the native Settings.app footer shape).

    The height is read from ``row_widget`` every time (never cached), so
    it cannot go stale when the row's height changes."""
    global _FOOTER_SLOT, _FOOTER_ROW
    if sys.platform != 'darwin' or _FOOTER_VIEW is None or frame_widget is None:
        return
    if row_widget is not None:
        _FOOTER_ROW = row_widget
    row_widget = _FOOTER_ROW
    height = (float(row_widget.height()) if row_widget is not None
              else float(frame_widget.height()))
    _FOOTER_SLOT = frame_widget
    try:
        import objc
        from PySide6.QtCore import QPoint

        qt_view = objc.objc_object(c_void_p=int(window.winId()))
        theme = qt_view.superview()
        top_left = frame_widget.mapTo(
            window, QPoint(int(inset),
                           int(frame_widget.height() - inset - height)))
        rect = ((float(top_left.x()), float(top_left.y())),
                (float(frame_widget.width() - 2 * inset), float(height)))
        target = qt_view.convertRect_toView_(rect, theme)
        _FOOTER_VIEW.setFrame_((target.origin, target.size))
        _FOOTER_VIEW.setHidden_(frame_widget.isVisible() is False)
    except Exception as exc:
        _note('footer strip placement failed: {}: {}'.format(
            type(exc).__name__, exc))


def has_footer_strip():
    return _FOOTER_VIEW is not None


def footer_strip_view():
    return _FOOTER_VIEW


def footer_button_style():
    """Glyph button style for the footer's +/- controls: ``accessory``
    ``borderless`` (no bezel: plain glyphs on the band, the System
    Settings footer look - the default) or ``accessory`` (the
    NSBezelStyleAccessoryBarAction bezel). Override with
    FORMAT_TEX_FOOTER_BUTTON_STYLE."""
    return os.environ.get('FORMAT_TEX_FOOTER_BUTTON_STYLE',
                          'borderless').strip().lower()


def _plus_minus_target_class():
    """pyobjc target for the footer's +/- buttons (tag 0 = add, 1 = remove)."""
    global _PLUS_MINUS_TARGET_CLASS
    if _PLUS_MINUS_TARGET_CLASS is None:
        from AppKit import NSObject

        class _PlusMinusTarget(NSObject):
            def footerClicked_(self, sender):
                if int(sender.tag()) == 0:
                    self.on_add()
                else:
                    self.on_remove()

        _PLUS_MINUS_TARGET_CLASS = _PlusMinusTarget
    return _PLUS_MINUS_TARGET_CLASS


def _sf_symbol_image(AppKit, symbol, legacy_name,
                     point_size=FOOTER_SYMBOL_SIZE):
    image = AppKit.NSImage.imageWithSystemSymbolName_accessibilityDescription_(
        symbol, symbol)
    if image is not None:
        try:
            config = AppKit.NSImageSymbolConfiguration \
                .configurationWithPointSize_weight_(
                    float(point_size), AppKit.NSFontWeightRegular)
            sized = image.imageWithSymbolConfiguration_(config)
            if sized is not None:
                image = sized
        except Exception:
            pass
    else:
        image = AppKit.NSImage.imageNamed_(legacy_name)
    try:
        image.setTemplate_(True)
    except Exception:
        pass
    return image


def create_native_plus_minus(window, on_add, on_remove):
    """The footer's +/- controls: two plain glyph buttons (SF Symbols)
    with a 1 pt native separator between them, sitting on the footer's
    blur band - the System Settings footer look. There is deliberately no
    segmented-control bezel, which would draw a pill around the glyphs.
    Returns True on success; callers keep the Qt buttons otherwise."""
    global _PLUS_MINUS_ADD, _PLUS_MINUS_SEP, _PLUS_MINUS_REMOVE
    global _PLUS_MINUS_TARGET
    if sys.platform != 'darwin':
        return False
    try:
        import objc
        import AppKit

        if _PLUS_MINUS_ADD is None:
            target = _plus_minus_target_class().alloc().init()
            target.on_add = on_add
            target.on_remove = on_remove
            style = footer_button_style()
            buttons = []
            for tag, (symbol, legacy) in enumerate(
                    (('plus', AppKit.NSImageNameAddTemplate),
                     ('minus', AppKit.NSImageNameRemoveTemplate))):
                button = AppKit.NSButton.alloc().init()
                button.setImage_(_sf_symbol_image(AppKit, symbol, legacy))
                button.setImagePosition_(AppKit.NSImageOnly)
                if style == 'borderless':
                    # plain glyph: no bezel, and no tint override - the
                    # template image already renders in the standard
                    # content colour and dims natively when disabled
                    button.setBordered_(False)
                else:
                    button.setBordered_(True)
                    button.setBezelStyle_(
                        AppKit.NSBezelStyleAccessoryBarAction)
                button.setTarget_(target)
                button.setAction_(b'footerClicked:')
                button.setTag_(tag)
                button.sizeToFit()
                buttons.append(button)
            if style == 'borderless':
                # a borderless button shrinks to the glyph: give both the
                # same hit area so the band keeps its native height
                size = AppKit.NSMakeSize(FOOTER_BUTTON_WIDTH,
                                         FOOTER_BUTTON_HEIGHT)
            else:
                size = AppKit.NSMakeSize(
                    max(b.frame().size.width for b in buttons),
                    max(b.frame().size.height for b in buttons))
            for button in buttons:
                button.setFrameSize_(size)
            width, height = size.width, size.height
            separator = AppKit.NSBox.alloc().init()
            separator.setBoxType_(AppKit.NSBoxSeparator)
            separator.setFrameSize_((1.0, FOOTER_SEPARATOR_HEIGHT))
            _PLUS_MINUS_ADD, _PLUS_MINUS_REMOVE = buttons
            _PLUS_MINUS_SEP = separator
            _PLUS_MINUS_TARGET = target

        qt_view = objc.objc_object(c_void_p=int(window.winId()))
        theme = qt_view.superview()
        for view in (_PLUS_MINUS_ADD, _PLUS_MINUS_SEP, _PLUS_MINUS_REMOVE):
            try:
                view.removeFromSuperview()
            except Exception:
                pass
            theme.addSubview_positioned_relativeTo_(
                view, AppKit.NSWindowAbove, qt_view)
        return True
    except Exception as exc:
        _note('native +/- controls failed: {}: {}'.format(
            type(exc).__name__, exc))
        return False


def place_native_plus_minus(window, slot):
    """Left-align the +/- group inside the bar row, vertically centred and
    clamped so it can never leave the band."""
    global _PLUS_MINUS_SLOT
    if (sys.platform != 'darwin' or _PLUS_MINUS_ADD is None
            or slot is None):
        return
    _PLUS_MINUS_SLOT = slot
    try:
        import objc
        from PySide6.QtCore import QPoint

        qt_view = objc.objc_object(c_void_p=int(window.winId()))
        theme = qt_view.superview()
        top_left = slot.mapTo(window, QPoint(0, 0))
        rect = ((float(top_left.x()), float(top_left.y())),
                (float(slot.width()), float(slot.height())))
        target = qt_view.convertRect_toView_(rect, theme)
        size = _PLUS_MINUS_ADD.frame().size
        sep_size = _PLUS_MINUS_SEP.frame().size
        hidden = slot.isVisible() is False
        left = target.origin.x + FOOTER_INSET
        y = target.origin.y + (target.size.height - size.height) / 2.0
        y = max(target.origin.y,
                min(y, target.origin.y + target.size.height - size.height))
        gap = FOOTER_SEPARATOR_GAP
        _PLUS_MINUS_ADD.setFrame_(((left, y), (size.width, size.height)))
        sep_x = left + size.width + gap
        sep_y = target.origin.y + (target.size.height - sep_size.height) / 2.0
        _PLUS_MINUS_SEP.setFrame_(((sep_x, sep_y),
                                   (sep_size.width, sep_size.height)))
        remove_x = sep_x + sep_size.width + gap
        _PLUS_MINUS_REMOVE.setFrame_(((remove_x, y),
                                      (size.width, size.height)))
        for view in (_PLUS_MINUS_ADD, _PLUS_MINUS_SEP, _PLUS_MINUS_REMOVE):
            view.setHidden_(hidden)
    except Exception as exc:
        _note('+/- placement failed: {}: {}'.format(type(exc).__name__, exc))


def set_native_plus_minus_enabled(remove_enabled):
    """Grey out the '-' button when nothing is selected."""
    try:
        if _PLUS_MINUS_REMOVE is not None:
            _PLUS_MINUS_REMOVE.setEnabled_(bool(remove_enabled))
    except Exception:
        pass


def native_plus_minus_height():
    """Height of a native footer glyph button in points (0 when absent),
    so the Qt row can be sized from it instead of guessing."""
    try:
        if _PLUS_MINUS_ADD is not None:
            return float(_PLUS_MINUS_ADD.frame().size.height)
    except Exception:
        pass
    return 0.0


def footer_tint_rgba(dark=None):
    """(r, g, b, a) overlay for the footer bar, or None when disabled.
    Used by the GUI, which paints it over the native material (a layer
    background on the material view itself is hidden behind it)."""
    if dark is None:
        dark = _FOOTER_DARK
    def env(name, default):
        try:
            return float(os.environ.get(name, default))
        except (TypeError, ValueError):
            return default
    if dark:
        alpha = env('FORMAT_TEX_FOOTER_TINT_DARK',
                    env('FORMAT_TEX_FOOTER_TINT', FOOTER_TINT_DARK))
        return (255, 255, 255, max(0.0, min(1.0, alpha)))
    alpha = env('FORMAT_TEX_FOOTER_TINT_LIGHT',
                env('FORMAT_TEX_FOOTER_TINT', FOOTER_TINT_LIGHT))
    return (0, 0, 0, max(0.0, min(1.0, alpha)))


def footer_metrics():
    """(row_padding, button_height, separator_height, inset, gap)
    so callers/tests can assert the native proportions."""
    return (FOOTER_ROW_PADDING, FOOTER_BUTTON_HEIGHT,
            FOOTER_SEPARATOR_HEIGHT, FOOTER_INSET,
            FOOTER_SEPARATOR_GAP)


def has_native_plus_minus():
    return _PLUS_MINUS_ADD is not None


def native_plus_minus_view():
    """The add button (kept for callers that only need the native view)."""
    return _PLUS_MINUS_ADD


def footer_control_views():
    """(add button, separator, remove button) or (None, None, None)."""
    return (_PLUS_MINUS_ADD, _PLUS_MINUS_SEP, _PLUS_MINUS_REMOVE)


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
        if _FOOTER_VIEW is not None and _FOOTER_SLOT is not None:
            place_footer_strip(window, _FOOTER_SLOT)
        if _PLUS_MINUS is not None and _PLUS_MINUS_SLOT is not None:
            place_native_plus_minus(window, _PLUS_MINUS_SLOT)
    except Exception as exc:
        _note('reposition failed: {}: {}'.format(type(exc).__name__, exc))


def _click_through_class():
    """pyobjc NSVisualEffectView subclass that never takes mouse events,
    so the strip material can sit above the Qt view without blocking it."""
    global _CLICK_THROUGH_CLASS
    if _CLICK_THROUGH_CLASS is None:
        import AppKit

        class _ClickThroughEffectView(AppKit.NSVisualEffectView):
            def hitTest_(self, point):
                return None

        _CLICK_THROUGH_CLASS = _ClickThroughEffectView
    return _CLICK_THROUGH_CLASS


def _make_material_view(AppKit, material, blending, click_through=False):
    if click_through:
        view = _click_through_class().alloc().init()
    else:
        view = AppKit.NSVisualEffectView.alloc().init()
    view.setMaterial_(material)
    view.setBlendingMode_(blending)
    view.setState_(AppKit.NSVisualEffectStateFollowsWindowActiveState)
    return view


def _macos(window, dark):
    import objc
    import AppKit

    global _BAND_VIEW, _SIDEBAR_VIEW, _TITLE_VIEW

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
    # AppKit draws the title of a non-main window unemphasized, and a Qt
    # window reports canBecomeMainWindow = NO, so its title stays grey
    # (#9a9b9c) no matter what textColor we set. Hide it and draw our own
    # label instead: full control over colour and weight, with the native
    # active/inactive dimming reproduced on activation changes.
    nswin.setTitleVisibility_(AppKit.NSWindowTitleHidden)
    try:
        nswin.setBackgroundColor_(AppKit.NSColor.windowBackgroundColor())
    except Exception as exc:
        _note('native window background failed: {}'.format(exc))
    try:
        nswin.setTitlebarSeparatorStyle_(AppKit.NSTitlebarSeparatorStyleNone)
    except Exception:
        pass

    if _BAND_VIEW is None:
        # toolbar-like strip for the title bar right of the sidebar: it
        # sits ABOVE the Qt view (the content panel is opaque, so a view
        # below it would never be seen) and blurs that content; it is
        # click-through so the Qt widgets keep receiving events
        _BAND_VIEW = _make_material_view(
            AppKit, _material_constant(AppKit, band_material_name()),
            AppKit.NSVisualEffectBlendingModeWithinWindow, click_through=True)
    if _SIDEBAR_VIEW is None:
        # sidebar-like: let the desktop blur through - below the Qt view,
        # which shows it wherever the sidebar widget is transparent
        _SIDEBAR_VIEW = _make_material_view(
            AppKit, AppKit.NSVisualEffectMaterialSidebar,
            AppKit.NSVisualEffectBlendingModeBehindWindow)
    if _TITLE_VIEW is None:
        _TITLE_VIEW = _make_title_field(AppKit, nswin.title())
    # the sidebar sits below Qt (its column is transparent) while the
    # band sits above Qt (the content panel under it is opaque)
    for view, position in ((_SIDEBAR_VIEW, AppKit.NSWindowBelow),
                           (_BAND_VIEW, AppKit.NSWindowAbove)):
        try:
            view.removeFromSuperview()
        except Exception:
            pass
        theme.addSubview_positioned_relativeTo_(view, position, qt_view)
    # our title must be above the band, otherwise the material frosts it
    try:
        _TITLE_VIEW.removeFromSuperview()
    except Exception:
        pass
    theme.addSubview_positioned_relativeTo_(_TITLE_VIEW, AppKit.NSWindowAbove,
                                            _BAND_VIEW)
    if not band_above_content(window):
        _note('band order unexpected (should be above the content view, '
              'below the titlebar chrome)')
    _place_band(nswin, theme)
    _place_sidebar(nswin, theme)
    _align_titlebar(nswin)
    _debug_outlines()
    return '52 pt band + sidebar blur'


def _make_title_field(AppKit, text):
    """A plain label for the window title, drawn above the band."""
    field = AppKit.NSTextField.alloc().init()
    field.setBezeled_(False)
    field.setDrawsBackground_(False)
    field.setEditable_(False)
    field.setSelectable_(False)
    field.setStringValue_(text or '')
    field.setFont_(AppKit.NSFont.systemFontOfSize_weight_(
        title_font_size(), AppKit.NSFontWeightSemibold))
    field.setAlignment_(AppKit.NSTextAlignmentLeft)
    field.sizeToFit()
    return field


def band_above_content(window):
    """True when the band material is ordered above the Qt view - so the
    opaque content panel cannot hide it - but below the native titlebar
    chrome, so it cannot hide the traffic lights and the title."""
    if sys.platform != 'darwin' or _BAND_VIEW is None:
        return False
    try:
        import objc
        qt_view = objc.objc_object(c_void_p=int(window.winId()))
        theme = qt_view.superview()
        subs = list(theme.subviews())
        if _BAND_VIEW not in subs or qt_view not in subs:
            return False
        band_i = subs.index(_BAND_VIEW)
        if band_i <= subs.index(qt_view):
            return False
        return not any('Titlebar' in type(v).__name__ for v in subs[:band_i])
    except Exception as exc:
        _note('band order check failed: {}: {}'.format(
            type(exc).__name__, exc))
        return False


def _place_band(nswin, theme):
    """Frame the toolbar-material strip. It covers only the title bar
    region to the RIGHT of the sidebar, so the sidebar's own blur runs
    uninterrupted from the top of the window down (no seam, no line)."""
    import AppKit
    if _BAND_VIEW is None:
        return
    if nswin.styleMask() & AppKit.NSWindowStyleMaskFullScreen:
        _BAND_VIEW.setHidden_(True)      # no title bar in full screen
        return
    bounds = theme.bounds()
    x = _SIDEBAR_WIDTH if _SIDEBAR_WIDTH > 0 else 0.0
    width = max(1.0, bounds.size.width - x)
    _BAND_VIEW.setHidden_(False)
    _BAND_VIEW.setFrame_(((x, bounds.size.height - BAND_HEIGHT),
                          (width, BAND_HEIGHT)))


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

    # title: our own label, left-aligned just right of the sidebar
    # boundary (falling back to just after the traffic lights when there
    # is no sidebar). AppKit draws the title of a non-main window in an
    # unemphasized grey and a Qt window can never be main, so we render
    # it ourselves (see _style_title for the colour/dimming).
    title = _TITLE_VIEW
    if title is None:
        return
    _style_title(AppKit, nswin, title)
    if _SIDEBAR_WIDTH > 0:
        target = left + _SIDEBAR_WIDTH + title_gap()
    else:
        zoom = nswin.standardWindowButton_(AppKit.NSWindowZoomButton)
        if zoom is None:
            return
        zoom_rect = screen_rect(zoom)
        target = zoom_rect.origin.x + zoom_rect.size.width + title_gap()
    title.sizeToFit()
    size = title.frame().size
    # vertical: centre the label on the band, like the traffic lights
    y = top - BAND_HEIGHT / 2.0 - size.height / 2.0
    title.setFrame_(((target - left, y - frame.origin.y),
                     (size.width, size.height)))
    title.setHidden_(False)


def _style_title(AppKit, nswin, title):
    """Colour our title label the way macOS renders a main window's
    title: the primary label colour (white in dark mode, black in light)
    while the window is active, dimmed to the secondary colour when it is
    inactive - the native auto-dim behaviour, applied by us because Qt's
    window can never become main (canBecomeMainWindow = NO), which is why
    AppKit's own title stayed grey (#9a9b9c vs Finder's #e8e8e9)."""
    try:
        active = bool(nswin.isKeyWindow())
        color = (AppKit.NSColor.labelColor() if active
                 else AppKit.NSColor.secondaryLabelColor())
        title.setTextColor_(color)
    except Exception as exc:
        _note('title colour failed: {}: {}'.format(type(exc).__name__, exc))
    if debug_enabled():
        try:
            rgb = title.textColor().colorUsingColorSpace_(
                AppKit.NSColorSpace.sRGBColorSpace())
            print('[debug] title key={} colour=#%02x%02x%02x pt=%.1f' % (
                bool(nswin.isKeyWindow()),
                round(rgb.redComponent() * 255),
                round(rgb.greenComponent() * 255),
                round(rgb.blueComponent() * 255),
                title.font().pointSize()), flush=True)
        except Exception:
            pass


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
