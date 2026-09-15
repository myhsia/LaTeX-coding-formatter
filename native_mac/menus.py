#!/usr/bin/env python3
r"""Native macOS main menu.

A real ``NSMenu`` replaces Qt's menu bar on macOS. The window/file items
use nil targets, so AppKit sends them along the responder chain (the key
window/application implement them); the standard Edit items go through a
small bridge object that first asks the AppKit responder chain and then
falls back to the focused *Qt* widget - so shortcuts keep working both in
the native views (the ``NSTableView`` handles ``selectAll:``) and in the
Qt widgets that are still around (text fields, the diff pane).

Installed from ``apply_window_effects`` and re-asserted whenever the Qt
menu bar would otherwise take over (activation, layout).
"""

import sys

OUR_ITEM_TAG = 20260915      # marks the items we created

_app_menu = None
_edit_menu = None
_bridge = None
_bridge_class = None


def available():
    return sys.platform == 'darwin'


# --------------------------------------------------------------------------
# responder helpers
# --------------------------------------------------------------------------
def _target_for(selector):
    """The responder that implements ``selector``, or None."""
    import AppKit

    try:
        return AppKit.NSApp().targetForAction_to_from_(selector, None, None)
    except Exception:
        return None


def _send(selector):
    """Perform an action through the responder chain; True when handled."""
    import AppKit

    target = _target_for(selector)
    if target is None:
        return False
    try:
        AppKit.NSApp().sendAction_to_from_(selector, target, None)
        return True
    except Exception:
        return False


def _qt_widget():
    from PySide6.QtWidgets import QApplication

    return QApplication.focusWidget()


def _qt_can(name, read_only_ok=True):
    widget = _qt_widget()
    if widget is None or not callable(getattr(widget, name, None)):
        return False
    if not read_only_ok and bool(
            getattr(widget, 'isReadOnly', lambda: False)()):
        return False
    return True


def _qt_call(name):
    widget = _qt_widget()
    fn = getattr(widget, name, None) if widget is not None else None
    if callable(fn):
        fn()
        return True
    return False


# --------------------------------------------------------------------------
# the Edit bridge
# --------------------------------------------------------------------------
def _edit_action(selector, qt_name, read_only_ok=True):
    """AppKit responder first, then the focused Qt widget."""
    if _send(selector):
        return True
    if not read_only_ok and not _qt_can(qt_name, read_only_ok):
        return False
    return _qt_call(qt_name)


def _bridge_class_for():
    global _bridge_class
    if _bridge_class is None:
        from AppKit import NSObject

        class _EditActions(NSObject):
            """Menu target for the standard Edit commands: AppKit first
            responder when one handles the selector, else the focused Qt
            widget. The dispatching lives in module helpers so pyobjc does
            not mistake them for Objective-C methods."""

            def undo_(self, sender):
                _edit_action(b'undo:', 'undo')

            def redo_(self, sender):
                _edit_action(b'redo:', 'redo')

            def cut_(self, sender):
                _edit_action(b'cut:', 'cut', read_only_ok=False)

            def copy_(self, sender):
                _edit_action(b'copy:', 'copy')

            def paste_(self, sender):
                _edit_action(b'paste:', 'paste', read_only_ok=False)

            def delete_(self, sender):
                # in the file list, Delete removes the selected rows
                gui = getattr(self, 'gui', None)
                if gui is not None and getattr(gui, 'files_view', None) \
                        and gui.files_view.has_selection():
                    gui._remove_selected()
                    return
                _edit_action(b'delete:', 'clear', read_only_ok=False)

            def selectAll_(self, sender):
                _edit_action(b'selectAll:', 'selectAll')

            def validateMenuItem_(self, item):
                selector = item.action()
                name = selector.decode() if isinstance(selector, bytes) \
                    else str(selector)
                table = {
                    'undo:': ('undo', True),
                    'redo:': ('redo', True),
                    'cut:': ('cut', False),
                    'copy:': ('copy', True),
                    'paste:': ('paste', False),
                    'delete:': ('clear', False),
                    'selectAll:': ('selectAll', True),
                }
                qt_name, read_only_ok = table.get(name, (None, True))
                if _target_for(selector) is not None:
                    return True
                if qt_name and _qt_can(qt_name, read_only_ok):
                    return True
                if name == 'delete:':
                    gui = getattr(self, 'gui', None)
                    return bool(gui is not None
                                and getattr(gui, 'files_view', None)
                                and gui.files_view.has_selection())
                return False

        _bridge_class = _EditActions
    return _bridge_class


def _item(menu, title, action, key='', mask=None, target=None):
    import AppKit

    item = AppKit.NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(
        title, action, key)
    item.setTag_(OUR_ITEM_TAG)
    if mask is not None:
        item.setKeyEquivalentModifierMask_(mask)
    if target is not None:
        item.setTarget_(target)
    menu.addItem_(item)
    return item


def _submenu(main, title):
    import AppKit

    main_item = AppKit.NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(
        title, b'', '')
    main_item.setTag_(OUR_ITEM_TAG)
    submenu = AppKit.NSMenu.alloc().initWithTitle_(title)
    main_item.setSubmenu_(submenu)
    main.addItem_(main_item)
    return submenu


def install(gui):
    """Build and install the native main menu. Returns True on success."""
    global _bridge
    if not available():
        return False
    try:
        import AppKit

        command = AppKit.NSEventModifierFlagCommand
        shift_command = command | AppKit.NSEventModifierFlagShift

        main = AppKit.NSMenu.alloc().init()
        # --- application menu -------------------------------------------
        app_menu = _submenu(main, 'LaTeX Coding Style Formatter')
        _item(app_menu, 'About LaTeX Coding Style Formatter',
              b'orderFrontStandardAboutPanel:', target=AppKit.NSApp())
        app_menu.addItem_(AppKit.NSMenuItem.separatorItem())
        _item(app_menu, 'Hide LaTeX Coding Style Formatter', b'hide:',
              'h', command)
        _item(app_menu, 'Hide Others', b'hideOtherApplications:',
              'h', command | AppKit.NSEventModifierFlagOption)
        _item(app_menu, 'Show All', b'unhideAllApplications:')
        app_menu.addItem_(AppKit.NSMenuItem.separatorItem())
        _item(app_menu, 'Quit LaTeX Coding Style Formatter', b'terminate:',
              'q', command)

        # --- File --------------------------------------------------------
        file_menu = _submenu(main, 'File')
        _item(file_menu, 'Close Window', b'performClose:', 'w', command)

        # --- Edit (bridge: AppKit responder, then the focused Qt widget) --
        bridge = _bridge_class_for().alloc().init()
        bridge.gui = gui
        _bridge = bridge
        edit_menu = _submenu(main, 'Edit')
        _item(edit_menu, 'Undo', b'undo:', 'z', command, target=bridge)
        _item(edit_menu, 'Redo', b'redo:', 'z', shift_command, target=bridge)
        edit_menu.addItem_(AppKit.NSMenuItem.separatorItem())
        _item(edit_menu, 'Cut', b'cut:', 'x', command, target=bridge)
        _item(edit_menu, 'Copy', b'copy:', 'c', command, target=bridge)
        _item(edit_menu, 'Paste', b'paste:', 'v', command, target=bridge)
        _item(edit_menu, 'Delete', b'delete:', target=bridge)
        edit_menu.addItem_(AppKit.NSMenuItem.separatorItem())
        _item(edit_menu, 'Select All', b'selectAll:', 'a', command,
              target=bridge)

        # --- Window (nil targets: window/application handle them) --------
        window_menu = _submenu(main, 'Window')
        _item(window_menu, 'Minimize', b'performMiniaturize:', 'm', command)
        _item(window_menu, 'Zoom', b'performZoom:')
        _item(window_menu, 'Bring All to Front', b'arrangeInFront:')

        AppKit.NSApp().setMainMenu_(main)
        _strip_foreign(main)
        return True
    except Exception as exc:
        try:
            import platform_effects as pe
            pe._note('native menu failed: {}: {}'.format(
                type(exc).__name__, exc))
        except Exception:
            pass
        return False


def _strip_foreign(main):
    """Remove items Qt merged into our menus (e.g. File ▸ Close All).

    Qt's cocoa plugin appends its standard items to the current menu bar,
    including after we install ours, so this runs on every assert."""
    try:
        import AppKit

        for i in range(main.numberOfItems()):
            top = main.itemAtIndex_(i)
            sub = top.submenu()
            if sub is None:
                continue
            for j in range(sub.numberOfItems() - 1, -1, -1):
                item = sub.itemAtIndex_(j)
                if item.tag() != OUR_ITEM_TAG \
                        and not item.isSeparatorItem():
                    sub.removeItemAtIndex_(j)
    except Exception:
        pass


def installed():
    return _bridge is not None


def is_current():
    """True when our menu is the application's current main menu."""
    if not available():
        return False
    try:
        import AppKit

        main = AppKit.NSApp().mainMenu()
        if main is None or main.numberOfItems() != 4:
            return False
        for i in range(main.numberOfItems()):
            top = main.itemAtIndex_(i)
            if top.title() != 'File' or top.submenu() is None:
                continue
            sub = top.submenu()
            if sub.numberOfItems() != 1:
                return False
            return sub.itemAtIndex_(0).title() == 'Close Window'
        return False
    except Exception:
        return False


def edit_bridge():
    """The Edit menu target (for tests: invoke actions directly)."""
    return _bridge


def menu_titles():
    """Top-level menu titles (for tests)."""
    if not available():
        return []
    try:
        import AppKit

        main = AppKit.NSApp().mainMenu()
        if main is None:
            return []
        return [main.itemAtIndex_(i).title()
                for i in range(main.numberOfItems())]
    except Exception:
        return []


def items_of(title):
    """[(title, key equivalent, modifiers, has target)] for a submenu."""
    if not available():
        return []
    try:
        import AppKit

        main = AppKit.NSApp().mainMenu()
        if main is None:
            return []
        for i in range(main.numberOfItems()):
            top = main.itemAtIndex_(i)
            if top.title() != title or top.submenu() is None:
                continue
            sub = top.submenu()
            out = []
            for j in range(sub.numberOfItems()):
                item = sub.itemAtIndex_(j)
                out.append((item.title(), item.keyEquivalent(),
                            int(item.keyEquivalentModifierMask()),
                            item.target() is not None))
            return out
    except Exception:
        pass
    return []
