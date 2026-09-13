#!/usr/bin/env python3
r"""Native macOS dropdown menus for the Qt GUI.

Qt draws its own combo-box popup (a plain rectangle with no system
material). On macOS we can instead show an NSMenu, which renders with
the system material (liquid glass on macOS 26+) and puts a checkmark on
the selected item - matching Finder/Notes-style popup buttons.

Usage
-----
    from native_menu import menu_entries, popup_native_menu
    entries = menu_entries(items, current, custom_label='自定义…')
    shown = popup_native_menu(widget, items, current, on_select, '自定义…')
"""

import sys

CUSTOM_SENTINEL = '__custom__'

_TARGET_CLASS = None


def menu_entries(items, current, custom_label=None):
    """Pure menu model: a list of ``(title, checked, payload)`` with the
    current value checked and the optional custom entry last. The custom
    entry's payload is :data:`CUSTOM_SENTINEL`."""
    entries = [(str(title), str(title) == str(current), str(title))
               for title in items]
    if custom_label:
        entries.append((custom_label, False, CUSTOM_SENTINEL))
    return entries


def _target_class():
    """A single pyobjc NSObject subclass used as the menu action target
    (defined lazily so the ObjC class is only registered once)."""
    global _TARGET_CLASS
    if _TARGET_CLASS is None:
        from AppKit import NSObject

        class _MenuTarget(NSObject):
            def select_(self, sender):
                self.callback(str(sender.representedObject()))

        _TARGET_CLASS = _MenuTarget
    return _TARGET_CLASS


def popup_native_menu(widget, items, current, on_select, custom_label=None):
    """Show a native NSMenu anchored at ``widget``. Returns True when the
    menu was shown (macOS only), False to let the caller fall back to
    Qt's own popup. ``on_select`` receives the chosen title (or
    :data:`CUSTOM_SENTINEL`)."""
    if sys.platform != 'darwin':
        return False
    try:
        import objc
        from AppKit import (NSControlStateValueOff, NSControlStateValueOn,
                            NSMenu, NSMenuItem)
        from PySide6.QtCore import QPoint

        entries = menu_entries(items, current, custom_label)

        target = _target_class().alloc().init()
        target.callback = on_select

        menu = NSMenu.alloc().init()
        menu.setAutoenablesItems_(False)
        for title, checked, payload in entries:
            item = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(
                title, b'select:', '')
            item.setTarget_(target)
            item.setRepresentedObject_(payload)
            item.setEnabled_(True)
            item.setState_(NSControlStateValueOn if checked
                           else NSControlStateValueOff)
            menu.addItem_(item)

        view = objc.objc_object(c_void_p=int(widget.window().winId()))
        pos = widget.mapTo(widget.window(), QPoint(0, 0))
        height = view.bounds().size.height
        y = float(pos.y()) if view.isFlipped() else height - float(pos.y())
        menu.popUpMenuPositioningItem_atLocation_inView_(
            None, (float(pos.x()), y), view)
        del target          # keep alive while the menu tracks, then drop
        return True
    except Exception:
        return False
