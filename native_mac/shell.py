#!/usr/bin/env python3
r"""Native macOS window shell (no Qt).

Builds the Finder-like window the app is known for, entirely in AppKit:

    +-------------------------------------------+---------------+
    | sidebar material (full height)            | toolbar strip |
    |                                           | (52 pt)       |
    |   [ extension | recursive switch ]        +---------------+
    |   file list                               | content panel |
    |   [ + | - ]  footer                       | (opaque)      |
    +-------------------------------------------+---------------+

The window uses ``fullSizeContentView`` with a transparent titlebar, so
the sidebar material runs from the very top edge down (no seam) and only
the strip right of the sidebar gets the toolbar material - the same look
the Qt app produced, now without Qt. Callers host their own views inside
``sidebar_host`` / ``content_host`` and can use ``ViewTarget`` with the
``native_mac`` view modules.
"""

import sys

from platform_effects import (band_height, center_titlebar_in_band,
                              inset_traffic_lights, lights_inset,
                              make_title_field, style_title, title_gap)

BAND_HEIGHT = 52.0
FOOTER_HEIGHT = 24.0
SIDEBAR_WIDTH = 240.0
SIDEBAR_MIN = 180.0
WINDOW_MIN = (760.0, 560.0)
# macOS default window content margin (20 pt)
WINDOW_MARGIN = 20.0


class NativeShell:
    """The window, its split view and the material composition."""

    def __init__(self, title='LaTeX Coding Style Formatter'):
        self.title = title
        self.window = None
        self.split = None
        self.sidebar_material = None
        self.panel = None
        self.band = None
        self.sidebar_host = None
        self.content_host = None
        self.toolbar_host = None
        self.footer_separator = None
        self.footer_host = None
        self.sidebar_group = None
        self.title_label = None
        self._delegate = None
        self.on_layout = None          # called after each relayout

    @staticmethod
    def available():
        return sys.platform == 'darwin'

    # ---------- construction ----------
    def build(self, width=1000.0, height=700.0):
        if not self.available():
            return False
        try:
            import AppKit

            style = (AppKit.NSWindowStyleMaskTitled
                     | AppKit.NSWindowStyleMaskClosable
                     | AppKit.NSWindowStyleMaskMiniaturizable
                     | AppKit.NSWindowStyleMaskResizable
                     | AppKit.NSWindowStyleMaskFullSizeContentView)
            window = AppKit.NSWindow.alloc() \
                .initWithContentRect_styleMask_backing_defer_(
                    ((120.0, 120.0), (width, height)), style,
                    AppKit.NSBackingStoreBuffered, False)
            window.setTitle_(self.title)
            window.setTitlebarAppearsTransparent_(True)
            # AppKit draws the title of a non-main window unemphasized (this
            # native window never reports isMainWindow), so hide it and draw
            # our own label below - full control over colour and size
            window.setTitleVisibility_(AppKit.NSWindowTitleHidden)
            window.setMinSize_(WINDOW_MIN)
            window.setBackgroundColor_(AppKit.NSColor.windowBackgroundColor())
            try:
                window.setTitlebarSeparatorStyle_(
                    AppKit.NSTitlebarSeparatorStyleNone)
            except Exception:
                pass

            content = window.contentView()

            split = _split_class().alloc().initWithFrame_(
                content.bounds())
            split.setVertical_(True)
            split.setDividerStyle_(AppKit.NSSplitViewDividerStyleThin)
            split.setAutoresizingMask_(
                AppKit.NSViewWidthSizable | AppKit.NSViewHeightSizable)

            sidebar = AppKit.NSView.alloc().initWithFrame_(
                ((0.0, 0.0), (SIDEBAR_WIDTH, height)))
            panel = AppKit.NSView.alloc().initWithFrame_(
                ((SIDEBAR_WIDTH + 1, 0.0),
                 (width - SIDEBAR_WIDTH - 1, height)))
            panel.setWantsLayer_(True)
            panel.layer().setBackgroundColor_(
                AppKit.NSColor.windowBackgroundColor().CGColor())

            # the sidebar's own blur runs the full height, from the very
            # top edge (it continues behind the title bar)
            sidebar_material = AppKit.NSVisualEffectView.alloc().init()
            sidebar_material.setMaterial_(
                AppKit.NSVisualEffectMaterialSidebar)
            sidebar_material.setBlendingMode_(
                AppKit.NSVisualEffectBlendingModeBehindWindow)
            sidebar_material.setState_(
                AppKit.NSVisualEffectStateFollowsWindowActiveState)
            sidebar_material.setAutoresizingMask_(
                AppKit.NSViewWidthSizable | AppKit.NSViewHeightSizable)
            sidebar_material.setFrame_(sidebar.bounds())
            sidebar.addSubview_(sidebar_material)

            # the toolbar strip covers only the title bar right of the
            # sidebar (over the opaque panel)
            band = AppKit.NSVisualEffectView.alloc().init()
            band.setMaterial_(AppKit.NSVisualEffectMaterialHeaderView)
            band.setBlendingMode_(
                AppKit.NSVisualEffectBlendingModeWithinWindow)
            band.setState_(
                AppKit.NSVisualEffectStateFollowsWindowActiveState)
            panel.addSubview_(band)

            footer = AppKit.NSView.alloc().initWithFrame_(
                ((0.0, 0.0), (SIDEBAR_WIDTH, FOOTER_HEIGHT)))
            footer.setAutoresizingMask_(AppKit.NSViewWidthSizable
                                        | AppKit.NSViewMaxYMargin)
            # no separator line above the footer: the +/- bar already reads
            # as a raised bar by its own material (Finder-style)
            sidebar.addSubview_(footer)

            split.addSubview_(sidebar)
            split.addSubview_(panel)
            content.addSubview_(split)

            sidebar_host = AppKit.NSView.alloc().init()
            panel_host = AppKit.NSView.alloc().init()
            sidebar.addSubview_(sidebar_host)
            panel.addSubview_(panel_host)

            self.window, self.split = window, split
            self.sidebar_material, self.panel, self.band = (
                sidebar_material, panel, band)
            self.sidebar_host, self.content_host = sidebar_host, panel_host
            self.footer_host = footer
            self.footer_separator = None
            self.toolbar_host = band
            # our own title label, drawn above the band (the band material
            # would otherwise frost it) and centred in the band
            self.title_label = make_title_field(AppKit, window.title())
            content.addSubview_(self.title_label)
            # AppKit resets the titlebar chrome on resize/activation/full
            # screen, so re-centre it from the window delegate
            self._delegate = _window_delegate_class().alloc().init()
            self._delegate.owner = self
            window.setDelegate_(self._delegate)
            self.layout()
            return True
        except Exception as exc:
            self._note('native shell failed: {}: {}'.format(
                type(exc).__name__, exc))
            return False

    # ---------- layout ----------
    def sidebar_width(self):
        try:
            return float(self.sidebar_host.superview()
                         .frame().size.width)
        except Exception:
            return SIDEBAR_WIDTH

    def layout(self):
        """Recompute every frame from the window size (frame-based layout,
        so there is no auto-layout pass to fight with)."""
        if self.window is None:
            return
        import AppKit

        content = self.window.contentView()
        bounds = content.bounds()
        width, height = bounds.size.width, bounds.size.height
        sidebar = self.sidebar_host.superview()
        panel = self.panel
        sidebar_width = max(SIDEBAR_MIN, sidebar.frame().size.width)
        panel_width = max(1.0, width - sidebar_width - 1.0)

        sidebar.setFrame_(((0.0, 0.0), (sidebar_width, height)))
        panel.setFrame_(((sidebar_width + 1.0, 0.0),
                         (panel_width, height)))
        self.sidebar_material.setFrame_(sidebar.bounds())

        # toolbar strip: only the title bar right of the sidebar
        band_height = min(BAND_HEIGHT, height)
        self.band.setFrame_(((0.0, height - band_height),
                             (panel_width, band_height)))

        # sidebar contents: group at the top (below the band), then the
        # file list, then the footer row
        margin = WINDOW_MARGIN
        top = height - BAND_HEIGHT - margin
        group_height = 146.0       # 3 x 44 pt grid rows + 2 hairlines + insets
        if self.sidebar_group is not None:
            self.sidebar_group.setFrame_(
                ((margin, top - group_height),
                 (max(1.0, sidebar_width - 2 * margin), group_height)))
        self.sidebar_host.setFrame_(
            ((margin, FOOTER_HEIGHT),
             (max(1.0, sidebar_width - 2 * margin),
              max(1.0, top - group_height - margin - FOOTER_HEIGHT))))
        self.footer_host.setFrame_(((0.0, 0.0),
                                    (sidebar_width, FOOTER_HEIGHT)))

        # content: the strip occupies the top, the rest is the panel host
        self.content_host.setFrame_(
            ((margin, margin),
             (max(1.0, panel_width - 2 * margin),
              max(1.0, height - BAND_HEIGHT - 2 * margin))))

        self._align_chrome(sidebar_width)
        if callable(self.on_layout):
            self.on_layout()

    def _align_chrome(self, sidebar_width):
        """Centre the traffic lights in the top band, inset them like
        Finder, and place our own title label. In full screen macOS hides
        the titlebar, so the band and title are hidden."""
        try:
            import AppKit

            if self.window.styleMask() & AppKit.NSWindowStyleMaskFullScreen:
                if self.band is not None:
                    self.band.setHidden_(True)
                if self.title_label is not None:
                    self.title_label.setHidden_(True)
                return
            if self.band is not None:
                self.band.setHidden_(False)
            band = band_height() or BAND_HEIGHT
            center_titlebar_in_band(self.window, band)
            inset_traffic_lights(self.window, lights_inset())
            self._place_title(sidebar_width, band)
        except Exception:
            pass

    def _place_title(self, sidebar_width, band):
        """Style and place the title label: left-aligned just right of the
        sidebar, vertically centred in the band. Drawing it ourselves (rather
        than using AppKit's title) avoids the unemphasised grey AppKit uses
        for a non-main window and lets us set the size/weight."""
        if self.title_label is None:
            return
        import AppKit

        style_title(AppKit, self.window, self.title_label)
        self.title_label.sizeToFit()
        size = self.title_label.frame().size
        content = self.window.contentView()
        height = content.bounds().size.height
        x = sidebar_width + title_gap()
        y = height - band / 2.0 - size.height / 2.0
        self.title_label.setFrame_(((x, y), (size.width, size.height)))
        self.title_label.setHidden_(False)

    # ---------- helpers ----------
    def make_group_box(self):
        """The sidebar's grouped settings box (rounded translucent)."""
        import AppKit

        box = AppKit.NSView.alloc().init()
        box.setWantsLayer_(True)
        box.layer().setBackgroundColor_(
            AppKit.NSColor.colorWithSRGBRed_green_blue_alpha_(
                0.47, 0.47, 0.5, 0.12).CGColor())
        box.layer().setCornerRadius_(8.0)
        self.sidebar_group = box
        self.sidebar_host.superview().addSubview_(box)
        return box

    def show(self):
        if self.window is not None:
            self.window.makeKeyAndOrderFront_(None)

    def _note(self, message):
        try:
            import platform_effects as pe
            pe._note(message)
        except Exception:
            pass


_WINDOW_DELEGATE_CLASS = None


def _window_delegate_class():
    """``NSWindowDelegate`` that re-lays the window out when AppKit resets
    the titlebar chrome (resize / activation / full screen)."""
    global _WINDOW_DELEGATE_CLASS
    if _WINDOW_DELEGATE_CLASS is None:
        from AppKit import NSObject

        class _WindowDelegate(NSObject):
            def windowDidResize_(self, notification):
                _relayout_owner(self)

            def windowDidBecomeKey_(self, notification):
                _relayout_owner(self)

            def windowDidResignKey_(self, notification):
                _relayout_owner(self)

            def windowDidEnterFullScreen_(self, notification):
                _relayout_owner(self)

            def windowDidExitFullScreen_(self, notification):
                _relayout_owner(self)

        _WINDOW_DELEGATE_CLASS = _WindowDelegate
    return _WINDOW_DELEGATE_CLASS


def _relayout_owner(delegate):
    owner = getattr(delegate, 'owner', None)
    if owner is not None:
        owner.layout()


_SPLIT_CLASS = None


def _split_class():
    """``NSSplitView`` that draws no divider line.

    The sidebar and content differ by material only (Finder-style), so the
    1 pt divider is painted with the content background instead of the
    split view's separator hairline."""
    global _SPLIT_CLASS
    if _SPLIT_CLASS is None:
        import AppKit

        class _Split(AppKit.NSSplitView):
            def drawDividerInRect_(self, rect):
                try:
                    AppKit.NSColor.windowBackgroundColor().set()
                    AppKit.NSRectFill(rect)
                except Exception:
                    pass

        _SPLIT_CLASS = _Split
    return _SPLIT_CLASS
