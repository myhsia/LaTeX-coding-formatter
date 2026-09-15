#!/usr/bin/env python3
r"""Native macOS controls for the Qt GUI.

Small wrappers around ``NSButton`` (checkbox / push button),
``NSPopUpButton`` and ``NSTextField`` (label), each placed over a Qt slot
widget that provides the geometry - the same mechanism as the file list,
diff pane, footer, title and menus.

The Qt widget keeps participating in the layout (so the responsive grid
and the panel keep working) but is hidden/covered; the wrapper exposes the
small API the application already uses (``isChecked``/``setChecked``,
``currentText``/``setCurrentText``, ``setEnabled``, ``setText``), so the
rest of the GUI does not care which toolkit draws the control.
"""

import sys

_TARGET_CLASS = None


def _target_class():
    """NSObject target forwarding control actions to the Python
    wrappers (AppKit needs an Objective-C object as the target)."""
    global _TARGET_CLASS
    if _TARGET_CLASS is None:
        from AppKit import NSObject

        class _ControlTarget(NSObject):
            # the method names must match the selectors set as the actions
            # (toggled:/changed:/clicked:) - pyobjc exposes a trailing
            # underscore as the colon, so ``_toggled_native`` would NOT be
            # found and AppKit would auto-disable the popup menu items
            def toggled_(self, sender):
                owner = getattr(self, 'owner', None)
                if owner is not None:
                    owner.toggled_(sender)

            def changed_(self, sender):
                owner = getattr(self, 'owner', None)
                if owner is not None:
                    owner.changed_(sender)

            def clicked_(self, sender):
                owner = getattr(self, 'owner', None)
                if owner is not None:
                    owner.clicked_(sender)

        _TARGET_CLASS = _ControlTarget
    return _TARGET_CLASS


def _make_target(owner):
    target = _target_class().alloc().init()
    target.owner = owner
    return target


def _place_centred(window, view, target, align_left=True, natural=None):
    """Fit ``view`` into ``target``: its natural size, left aligned (or
    filling) and vertically centred - works for a Qt slot or a native view.
    ``natural`` is the size measured before any squeeze, so repeated
    placements cannot shrink the view to a stale slot."""
    import platform_effects as pe

    rect = target.rect()
    native = natural if natural is not None else view.frame().size
    height = min(native.height, rect.size.height)
    y = rect.origin.y + (rect.size.height - height) / 2.0
    if align_left:
        x = rect.origin.x
        width = min(native.width, rect.size.width)
    else:
        x = rect.origin.x
        width = rect.size.width
    view.setFrame_(((x, y), (width, height)))
    view.setHidden_(not target.visible())


class NativeCheckbox:
    """``NSButton`` checkbox over a Qt slot."""

    def __init__(self, window, slot, title, checked=False, on_toggle=None):
        self.window = window
        self.slot = slot
        self.on_toggle = on_toggle
        self.view = None
        self.active = False
        self._title = title
        self._checked = bool(checked)

    def build(self):
        if sys.platform != 'darwin':
            return False
        try:
            import AppKit

            import platform_effects as pe

            if self.view is None:
                button = AppKit.NSButton.alloc().init()
                button.setButtonType_(AppKit.NSSwitchButton)
                button.setTitle_(self._title)
                button.setFont_(AppKit.NSFont.systemFontOfSize_(13.0))
                button.setState_(AppKit.NSControlStateValueOn
                                 if self._checked
                                 else AppKit.NSControlStateValueOff)
                # keep a strong Python reference: NSControl's target is
                # weak, so without this the ObjC target is deallocated and
                # the action (and menu validation) silently dies
                self._control_target = _make_target(self)
                button.setTarget_(self._control_target)
                button.setAction_(b'toggled:')
                button.sizeToFit()
                self.view = button
            self._natural = self.view.frame().size
            self._target = pe.as_target(self.window, self.slot, inset=1.0)
            if not pe.place_in(self.window, self._target, self.view):
                return False
            self.active = True
            self.place()
            return True
        except Exception as exc:
            try:
                import platform_effects as pe
                pe._note('native checkbox failed: {}: {}'.format(
                    type(exc).__name__, exc))
            except Exception:
                pass
            self.active = False
            return False

    def toggled_(self, sender):
        self._checked = bool(sender.state() == 1)
        if callable(self.on_toggle):
            self.on_toggle(self._checked)

    # small API mirroring the Qt widget
    def isChecked(self):
        if self.active and self.view is not None:
            return bool(self.view.state() == 1)
        return self._checked

    def setChecked(self, value):
        self._checked = bool(value)
        if self.active and self.view is not None:
            import AppKit

            self.view.setState_(AppKit.NSControlStateValueOn if value
                                else AppKit.NSControlStateValueOff)

    def isVisible(self):
        return bool(self.active and self.view is not None
                    and not self.view.isHidden())

    def place(self):
        if self.active and self.view is not None:
            if isinstance(self, NativeLabel) and self.fill:
                _place_centred(self.window, self.view, self._target,
                               align_left=False,
                               natural=getattr(self, '_natural', None))
            else:
                _place_centred(self.window, self.view, self._target,
                               natural=getattr(self, '_natural', None))

    def size(self):
        if self.view is None:
            return (0.0, 0.0)
        size = self.view.frame().size
        return (float(size.width), float(size.height))


class NativePopUpButton:
    """``NSPopUpButton`` over a Qt slot (items + optional custom entry)."""

    def __init__(self, window, slot, items, current, on_change=None,
                 custom_label=None):
        self.window = window
        self.slot = slot
        self.on_change = on_change
        self.custom_label = custom_label
        self.view = None
        self.active = False
        self._items = list(items)
        self._current = str(current)

    def build(self):
        if sys.platform != 'darwin':
            return False
        try:
            import AppKit

            import platform_effects as pe

            if self.view is None:
                popup = AppKit.NSPopUpButton.alloc() \
                    .initWithFrame_pullsDown_(((0.0, 0.0), (120.0, 24.0)),
                                              False)
                titles = list(self._items)
                if self.custom_label and self.custom_label not in titles:
                    titles.append(self.custom_label)
                popup.addItemsWithTitles_(titles)
                # the items carry no per-item validation: keep them enabled
                # regardless of the target's validation (belt and braces)
                popup.menu().setAutoenablesItems_(False)
                self._control_target = _make_target(self)
                popup.setTarget_(self._control_target)
                popup.setAction_(b'changed:')
                if self._current in titles:
                    popup.selectItemWithTitle_(self._current)
                popup.sizeToFit()
                self.view = popup
            self._natural = self.view.frame().size
            self._target = pe.as_target(self.window, self.slot, inset=1.0)
            if not pe.place_in(self.window, self._target, self.view):
                return False
            self.active = True
            self.place()
            return True
        except Exception as exc:
            try:
                import platform_effects as pe
                pe._note('native popup failed: {}: {}'.format(
                    type(exc).__name__, exc))
            except Exception:
                pass
            self.active = False
            return False

    def changed_(self, sender):
        title = str(sender.titleOfSelectedItem() or '')
        if self.custom_label and title == self.custom_label:
            # the application asks for the custom value and calls back
            if callable(self.on_change):
                self.on_change(self.custom_label)
            return
        self._current = title
        if callable(self.on_change):
            self.on_change(title)

    def currentText(self):
        if self.active and self.view is not None:
            return str(self.view.titleOfSelectedItem() or self._current)
        return self._current

    def setCurrentText(self, value):
        self._current = str(value)
        if self.active and self.view is not None:
            titles = [self.view.itemTitleAtIndex_(i)
                      for i in range(self.view.numberOfItems())]
            if self._current in titles:
                self.view.selectItemWithTitle_(self._current)

    def isVisible(self):
        return bool(self.active and self.view is not None
                    and not self.view.isHidden())

    def place(self):
        if self.active and self.view is not None:
            if isinstance(self, NativeLabel) and self.fill:
                _place_centred(self.window, self.view, self._target,
                               align_left=False,
                               natural=getattr(self, '_natural', None))
            else:
                _place_centred(self.window, self.view, self._target,
                               natural=getattr(self, '_natural', None))

    def size(self):
        if self.view is None:
            return (0.0, 0.0)
        size = self.view.frame().size
        return (float(size.width), float(size.height))


class NativePushButton:
    """``NSButton`` (rounded) over a Qt slot."""

    def __init__(self, window, slot, title, on_click=None):
        self.window = window
        self.slot = slot
        self.on_click = on_click
        self.view = None
        self.active = False
        self._title = title

    def build(self):
        if sys.platform != 'darwin':
            return False
        try:
            import AppKit

            import platform_effects as pe

            if self.view is None:
                button = AppKit.NSButton.alloc().init()
                button.setTitle_(self._title)
                button.setBezelStyle_(AppKit.NSBezelStyleRounded)
                # keep a strong Python reference: NSControl's target is
                # weak, so without this the ObjC target is deallocated and
                # the action (and menu validation) silently dies
                self._control_target = _make_target(self)
                button.setTarget_(self._control_target)
                button.setAction_(b'clicked:')
                button.sizeToFit()
                self.view = button
            self._natural = self.view.frame().size
            self._target = pe.as_target(self.window, self.slot, inset=1.0)
            if not pe.place_in(self.window, self._target, self.view):
                return False
            self.active = True
            self.place()
            return True
        except Exception as exc:
            try:
                import platform_effects as pe
                pe._note('native button failed: {}: {}'.format(
                    type(exc).__name__, exc))
            except Exception:
                pass
            self.active = False
            return False

    def clicked_(self, sender):
        if callable(self.on_click):
            self.on_click()

    def setEnabled(self, value):
        if self.active and self.view is not None:
            self.view.setEnabled_(bool(value))

    def isEnabled(self):
        return bool(self.view is None or self.view.isEnabled())

    def isVisible(self):
        return bool(self.active and self.view is not None
                    and not self.view.isHidden())

    def place(self):
        if self.active and self.view is not None:
            if isinstance(self, NativeLabel) and self.fill:
                _place_centred(self.window, self.view, self._target,
                               align_left=False,
                               natural=getattr(self, '_natural', None))
            else:
                _place_centred(self.window, self.view, self._target,
                               natural=getattr(self, '_natural', None))

    def size(self):
        if self.view is None:
            return (0.0, 0.0)
        size = self.view.frame().size
        return (float(size.width), float(size.height))


class NativeLabel:
    """``NSTextField`` label over a Qt slot (``fill`` stretches it to the
    slot's width so long text is never clipped)."""

    def __init__(self, window, slot, text='', fill=False):
        self.fill = bool(fill)
        self.window = window
        self.slot = slot
        self.view = None
        self.active = False
        self._text = text
        self._colour = None

    def build(self):
        if sys.platform != 'darwin':
            return False
        try:
            import AppKit

            import platform_effects as pe

            if self.view is None:
                field = AppKit.NSTextField.alloc().init()
                field.setBezeled_(False)
                field.setDrawsBackground_(False)
                field.setEditable_(False)
                field.setSelectable_(False)
                field.setStringValue_(self._text)
                field.setFont_(AppKit.NSFont.systemFontOfSize_(13.0))
                field.sizeToFit()
                self.view = field
            self._natural = self.view.frame().size
            self._target = pe.as_target(self.window, self.slot, inset=1.0)
            if not pe.place_in(self.window, self._target, self.view):
                return False
            self.active = True
            self.place()
            return True
        except Exception as exc:
            try:
                import platform_effects as pe
                pe._note('native label failed: {}: {}'.format(
                    type(exc).__name__, exc))
            except Exception:
                pass
            self.active = False
            return False

    def setText(self, text):
        self._text = str(text)
        if self.active and self.view is not None:
            self.view.setStringValue_(self._text)
            self.view.sizeToFit()

    def text(self):
        return self._text

    def isVisible(self):
        return bool(self.active and self.view is not None
                    and not self.view.isHidden())

    def place(self):
        if self.active and self.view is not None:
            if isinstance(self, NativeLabel) and self.fill:
                _place_centred(self.window, self.view, self._target,
                               align_left=False,
                               natural=getattr(self, '_natural', None))
            else:
                _place_centred(self.window, self.view, self._target,
                               natural=getattr(self, '_natural', None))

    def size(self):
        if self.view is None:
            return (0.0, 0.0)
        size = self.view.frame().size
        return (float(size.width), float(size.height))
