#!/usr/bin/env python3
r"""Native macOS application (AppKit only - no Qt).

The macOS build uses this entry point: it builds the native shell
(`native_mac.shell`), hosts the native views (file list, diff pane,
controls), drives them with the Qt-free `FormatController`, and supplies
the dialogs. Windows/Linux keep the Qt application (`format_tex_gui.py`).

    python3 format_tex_app_mac.py [--self-test]
"""

import sys
import traceback
import warnings
from pathlib import Path

# setting a layer colour passes a CGColorRef, which pyobjc cannot type:
# harmless, but it would spam the logs
warnings.filterwarnings('ignore', message='.*PyObjCPointer.*')

sys.path.insert(0, str(Path(__file__).resolve().parent))

from format_tex import FormatOptions, format_file, scan_directory  # noqa: E402
from format_tex_controller import FormatController  # noqa: E402
from native_menu import CUSTOM_SENTINEL  # noqa: E402
from native_mac import menus  # noqa: E402
from native_mac.controls import (NativeCheckbox, NativeLabel,  # noqa: E402
                                 NativePopUpButton, NativePushButton)
from native_mac.diffview import NativeDiffView  # noqa: E402
from native_mac.drop import make_drop_view  # noqa: E402
from native_mac.filelist import NativeFileList  # noqa: E402
from native_mac.shell import (BAND_HEIGHT, FOOTER_HEIGHT,  # noqa: E402
                              NativeShell)
from platform_effects import ViewTarget  # noqa: E402

EXTENSIONS = ['*.tex', '*.ctx', '*.sty', '*.cls', '*.dtx', '*.txt']
ENCODINGS = ['同输入', 'utf-8', 'utf-8-sig', 'gb2312', 'gbk', 'big5',
             'latin-1', 'cp1252']


class MacApp:
    """The application object: shell + views + controller + dialogs."""

    def __init__(self):
        self.shell = NativeShell()
        self.controller = FormatController(self)
        self.list = None
        self.diff = None
        self.option_controls = {}
        self.option_hosts = {}
        self.status = None
        self.apply_button = None
        self.ext_popup = None
        self.enc_popup = None
        self.switch = None
        self.plus_minus = None
        self.strip = None
        self.footer_tint = None
        self._confirm_ok = False
        self.dark = True
        self.colours = {}

    # ---------- construction ----------
    def build(self):
        import AppKit

        from format_tex_theme import native_dark, palette
        self.dark = native_dark()
        self.colours = palette(self.dark)
        if not self.shell.build():
            import platform_effects as pe
            print('shell build failed: {}'.format(pe.notes()[-3:]),
                  file=sys.stderr)
            return False
        self._build_sidebar(AppKit)
        self._build_content(AppKit)
        self.shell.on_layout = self._layout_children
        self.update_hint()
        menus.install(self)
        self.shell.show()
        self.shell.layout()
        return True

    def _build_sidebar(self, AppKit):
        shell = self.shell
        group = shell.make_group_box()

        # extension popup + recursive switch, inside the group
        host = AppKit.NSView.alloc().init()
        host.setFrame_(((0.0, 0.0), (100.0, 24.0)))
        group.addSubview_(host)
        self.ext_host = host
        self.ext_popup = NativePopUpButton(
            None, ViewTarget(host), EXTENSIONS, '.tex',
            on_change=self._extension_changed,
            custom_label='其它')
        self.ext_popup.build()

        def label(text):
            field = AppKit.NSTextField.alloc().init()
            field.setBezeled_(False)
            field.setDrawsBackground_(False)
            field.setEditable_(False)
            field.setSelectable_(False)
            field.setStringValue_(text)
            field.setFont_(AppKit.NSFont.systemFontOfSize_(13.0))
            field.sizeToFit()
            group.addSubview_(field)
            return field

        self.ext_label = label('扩展名')
        self.switch_label = label('含子目录')
        self.group_separator = AppKit.NSBox.alloc().init()
        self.group_separator.setBoxType_(AppKit.NSBoxSeparator)
        group.addSubview_(self.group_separator)

        switch = AppKit.NSSwitch.alloc().init()
        switch.setControlSize_(AppKit.NSControlSizeRegular)
        switch.setTarget_(None)
        switch.setState_(AppKit.NSControlStateValueOff)
        switch.sizeToFit()
        group.addSubview_(switch)
        self.switch = switch
        self.switch_host = host

        # empty-state hint (the Qt app's placeholder: text + two links);
        # the host doubles as a drop target while the list is empty (the
        # NSTableView is hidden then, so it cannot receive the drop)
        hint_host = make_drop_view(self.add_paths)
        if hint_host is None:
            hint_host = AppKit.NSView.alloc().init()
        shell.sidebar_host.addSubview_(hint_host)
        hint = AppKit.NSTextField.alloc().init()
        hint.setBezeled_(False)
        hint.setDrawsBackground_(False)
        hint.setEditable_(False)
        hint.setSelectable_(False)
        hint.setAlignment_(AppKit.NSTextAlignmentCenter)
        hint.setStringValue_('Click or drag and drop files/folders\n'
                             'into the box')
        hint.setFont_(AppKit.NSFont.systemFontOfSize_(13.0))
        hint.sizeToFit()
        hint_host.addSubview_(hint)
        links = []
        for title, action in (('选择文件', self.choose_files),
                              ('选择目录', self.choose_folder)):
            button = AppKit.NSButton.alloc().init()
            button.setTitle_(title)
            button.setBordered_(False)
            button.setFont_(AppKit.NSFont.systemFontOfSize_(13.0))
            button.setTarget_(self._link_target())
            button.setAction_(b'clicked:')
            button.setTag_(len(links))
            button.sizeToFit()
            hint_host.addSubview_(button)
            links.append(button)
        self.hint_host, self.hint_label, self.hint_links = (
            hint_host, hint, links)

        # file list over the sidebar host
        self.list = NativeFileList(None, ViewTarget(shell.sidebar_host),
                                   on_selection=self._selection_changed,
                                   on_drop=self.add_paths)
        self.list.build()

        # footer: material strip + the +/- segmented control
        footer = shell.footer_host
        strip = AppKit.NSVisualEffectView.alloc().init()
        strip.setMaterial_(AppKit.NSVisualEffectMaterialHeaderView)
        strip.setBlendingMode_(
            AppKit.NSVisualEffectBlendingModeWithinWindow)
        strip.setState_(AppKit.NSVisualEffectStateFollowsWindowActiveState)
        strip.setWantsLayer_(True)
        tint = AppKit.NSView.alloc().init()
        tint.setWantsLayer_(True)
        tint.layer().setBackgroundColor_(
            AppKit.NSColor.colorWithSRGBRed_green_blue_alpha_(
                1.0, 1.0, 1.0, 0.12).CGColor())
        footer.addSubview_(strip)
        footer.addSubview_(tint)
        self.strip, self.footer_tint = strip, tint

        control = AppKit.NSSegmentedControl.alloc().init()
        control.setSegmentCount_(2)
        control.setSegmentStyle_(AppKit.NSSegmentStyleSmallSquare)
        control.setTrackingMode_(AppKit.NSSegmentSwitchTrackingMomentary)
        for index, name in enumerate(('NSAddTemplate', 'NSRemoveTemplate')):
            control.setImage_forSegment_(AppKit.NSImage.imageNamed_(name),
                                         index)
        control.sizeToFit()
        global _PlusMinusTarget
        if _PlusMinusTarget is None:
            _PlusMinusTarget = _make_plus_minus_target()
        self._pm_target = _PlusMinusTarget.alloc().init()
        self._pm_target.owner = self
        control.setTarget_(self._pm_target)
        control.setAction_(b'clicked:')
        footer.addSubview_(control)
        self.plus_minus = control

    def _build_content(self, AppKit):
        host = self.shell.content_host

        # option checkboxes (a small responsive grid)
        self.option_hosts = []
        specs = (('punct', '半角标点后加空格', True),
                 ('commands', 'CJK 与控制序列空格', True),
                 ('tight', '页码范围保持紧凑', True),
                 ('backup', '生成备份文件 (backup/*.bak)', True),
                 ('magic', '添加编码魔法注释', True),
                 ('check', '仅检查 (不写入文件)', False))
        for _key, title, checked in specs:
            slot = AppKit.NSView.alloc().init()
            slot.setFrame_(((0.0, 0.0), (10.0, 10.0)))
            host.addSubview_(slot)
            control = NativeCheckbox(None, ViewTarget(slot), title, checked)
            control.build()
            key = _key
            self.option_controls[key] = control
            self.option_hosts.append((slot, control))

        # encoding popup + apply button
        enc_slot = AppKit.NSView.alloc().init()
        host.addSubview_(enc_slot)
        self.enc_popup = NativePopUpButton(
            None, ViewTarget(enc_slot), ENCODINGS, '同输入',
            on_change=lambda value: None, custom_label='其它')
        self.enc_popup.build()
        self.enc_host = enc_slot

        apply_slot = AppKit.NSView.alloc().init()
        host.addSubview_(apply_slot)
        self.apply_button = NativePushButton(
            None, ViewTarget(apply_slot), '应用格式化',
            on_click=lambda: self.controller.run(True))
        self.apply_button.build()
        self.apply_host = apply_slot

        # diff pane + status label
        diff_slot = AppKit.NSView.alloc().init()
        host.addSubview_(diff_slot)
        self.diff = NativeDiffView(
            None, ViewTarget(diff_slot),
            colours={'add': self.colours.get('add'),
                     'del': self.colours.get('del'),
                     'meta': self.colours.get('meta')})
        self.diff.build()
        self.diff_host = diff_slot

        status_slot = AppKit.NSView.alloc().init()
        host.addSubview_(status_slot)
        self.status = NativeLabel(None, ViewTarget(status_slot), '就绪',
                                  fill=True)
        self.status.build()
        self.status_host = status_slot

    def _link_target(self):
        if getattr(self, '_link_target_obj', None) is None:
            from AppKit import NSObject

            class _Links(NSObject):
                def clicked_(self, sender):
                    owner = getattr(self, 'owner', None)
                    if owner is None:
                        return
                    if int(sender.tag()) == 0:
                        owner.choose_files()
                    else:
                        owner.choose_folder()

            target = _Links.alloc().init()
            target.owner = self
            self._link_target_obj = target
        return self._link_target_obj

    def update_hint(self):
        try:
            empty = bool(self.list is None or not self.list.count())
            self.hint_host.setHidden_(not empty)
            if self.list is not None:
                self.list._refresh_visibility()
        except Exception:
            pass

    def _popup_size(self):
        try:
            size = self.ext_popup.view.frame().size
            return (float(size.width), float(size.height))
        except Exception:
            return (120.0, 24.0)

    def _layout_children(self):
        """Lay out the children of the two hosts (called by the shell)."""
        try:
            import AppKit

            shell = self.shell
            sidebar = shell.sidebar_host.frame()
            # group box contents: label-less rows, popup right, switch right
            group = self.shell.sidebar_group
            gb = group.bounds()
            row_h = 30.0
            top_row_y = gb.size.height - row_h - 2.0
            bottom_row_y = 2.0
            self.group_separator.setFrame_(
                ((10.0, bottom_row_y + row_h - 1.0),
                 (gb.size.width - 20.0, 1.0)))
            self.ext_label.setFrameOrigin_((12.0, top_row_y + 6.0))
            self.switch_label.setFrameOrigin_((12.0, bottom_row_y + 7.0))
            # extension popup (upper row, right aligned)
            popup = self._popup_size()
            self.ext_host.setFrame_(
                ((gb.size.width - 12.0 - popup[0], top_row_y + 3.0),
                 (popup[0], popup[1])))
            self.ext_popup.place()
            # recursive switch (lower row, right aligned)
            switch = self.switch.frame()
            self.switch.setFrameOrigin_(
                (gb.size.width - 12.0 - switch.size.width,
                 bottom_row_y + (row_h - switch.size.height) / 2.0))

            options = [c for _s, c in self.option_hosts]
            host = shell.content_host.bounds()
            width = host.size.width
            columns = 3 if width >= 620 else 2
            rows = (len(options) + columns - 1) // columns
            cell_w = width / columns
            cell_h = 24.0
            top = host.size.height - cell_h
            for index, (_slot, control) in enumerate(self.option_hosts):
                row, column = divmod(index, columns)
                _slot.setFrame_(((column * cell_w, top - row * cell_h),
                                 (cell_w, cell_h)))
                control.place()
            y = top - rows * cell_h - 8.0
            self.enc_host.setFrame_(((0.0, y), (90.0, 26.0)))
            self.enc_popup.place()
            self.apply_host.setFrame_(((width - 100.0, y), (100.0, 26.0)))
            self.apply_button.place()
            y -= 34.0
            status_h = 20.0
            self.status_host.setFrame_(((0.0, 0.0), (width, status_h)))
            self.status.place()
            self.diff_host.setFrame_(
                ((0.0, status_h + 6.0), (width, max(1.0, y - status_h - 6.0))))
            self.diff.place()

            if self.list is not None:
                self.list.place()
            host = shell.sidebar_host.bounds()
            self.hint_host.setFrame_(host)
            hint_size = self.hint_label.frame().size
            total = sum(b.frame().size.width for b in self.hint_links) + 16.0
            y = host.size.height / 2.0
            self.hint_label.setFrameOrigin_(
                ((host.size.width - hint_size.width) / 2.0,
                 y + 6.0))
            x = (host.size.width - total) / 2.0
            for button in self.hint_links:
                size = button.frame().size
                button.setFrameOrigin_((x, y - size.height - 6.0))
                x += size.width + 16.0
            if self.diff is not None:
                self.diff.place()

            # footer pieces
            footer = shell.footer_host.bounds()
            self.strip.setFrame_(footer)
            self.footer_tint.setFrame_(footer)
            control = self.plus_minus
            size = control.frame().size
            self.plus_minus.setFrame_(
                ((10.0, (footer.size.height - size.height) / 2.0),
                 (size.width, size.height)))
            self.update_remove_enabled()
        except Exception:
            self.show_error(traceback.format_exc())

    # ---------- view interface (used by the controller) ----------
    def selected_entries(self):
        return self.list.selected_entries() if self.list is not None else []

    def append(self, text, tag=None):
        if self.diff is not None:
            self.diff.append(text, tag)

    def clear_output(self):
        if self.diff is not None:
            self.diff.clear()

    def set_status(self, text):
        if self.status is not None:
            self.status.setText(text)

    def options_for_run(self):
        return FormatOptions(
            punct=self.option_controls['punct'].isChecked(),
            commands=self.option_controls['commands'].isChecked(),
            tight_ranges=self.option_controls['tight'].isChecked(),
            backup=self.option_controls['backup'].isChecked(),
            magic_comment=self.option_controls['magic'].isChecked(),
            write_encoding=self.write_encoding(),
        )

    def options(self):
        return self.options_for_run()

    def check_only(self):
        return self.option_controls['check'].isChecked()

    def confirm(self, count):
        import AppKit

        alert = AppKit.NSAlert.alloc().init()
        alert.setMessageText_('确认')
        alert.setInformativeText_(
            '将修改 {} 个文件, 是否继续?'.format(count))
        alert.addButtonWithTitle_('继续')
        alert.addButtonWithTitle_('取消')
        return alert.runModal() == 1000

    # ---------- actions ----------
    def write_encoding(self):
        value = self.enc_popup.currentText().strip() if self.enc_popup else ''
        return None if (not value or value == '同输入') else value

    def extension(self):
        value = self.ext_popup.currentText().strip() if self.ext_popup else ''
        return value or '.tex'

    def _extension_changed(self, title):
        if title == '其它':
            custom = self._ask_text('自定义扩展名', '.tex')
            if custom:
                self.ext_popup.setCurrentText(custom)

    def _ask_text(self, title, initial=''):
        import AppKit

        alert = AppKit.NSAlert.alloc().init()
        alert.setMessageText_(title)
        field = AppKit.NSTextField.alloc() \
            .initWithFrame_(((0.0, 0.0), (220.0, 24.0)))
        field.setStringValue_(initial)
        alert.setAccessoryView_(field)
        alert.addButtonWithTitle_('确定')
        alert.addButtonWithTitle_('取消')
        if alert.runModal() != 1000:
            return ''
        return str(field.stringValue())

    def recursive(self):
        try:
            return bool(self.switch.state() == 1)
        except Exception:
            return False

    def add_paths(self, paths):
        entries = []
        for raw in paths:
            path = Path(raw)
            if path.is_dir():
                try:
                    matches = scan_directory(path, self.extension(),
                                             self.recursive())
                except Exception:
                    matches = []
                for match in matches:
                    entries.append((str(match), str(path)))
            elif path.is_file():
                entries.append((str(path), str(path.parent)))
        added = self.list.add(entries)
        if added and not self.list.has_selection():
            self.list.select_index(0)
        self.update_hint()
        self.update_remove_enabled()
        self.set_status('已选择 {} 个文件 (新增 {} 个)'.format(
            self.list.count(), added))
        return added

    def choose_files(self):
        import AppKit

        panel = AppKit.NSOpenPanel.openPanel()
        panel.setAllowsMultipleSelection_(True)
        panel.setCanChooseDirectories_(False)
        # NSOpenPanel returns NSModalResponseOK (1), NOT the NSAlert value
        # 1000 - comparing to 1000 silently discarded every selection
        if panel.runModal() != AppKit.NSModalResponseOK:
            return
        self.add_paths([str(url.path()) for url in panel.URLs()])

    def choose_folder(self):
        import AppKit

        panel = AppKit.NSOpenPanel.openPanel()
        panel.setCanChooseDirectories_(True)
        panel.setCanChooseFiles_(False)
        if panel.runModal() != AppKit.NSModalResponseOK:
            return
        self.add_paths([str(url.path()) for url in panel.URLs()])

    def remove_selected(self):
        if self.list is not None:
            self.list.remove_selected()
        self.update_hint()
        self.update_remove_enabled()
        if self.list is not None and not self.list.count():
            self.clear_output()
            self.set_status('列表已清空')

    def plan_apply(self):
        """The menu's Delete command removes the selected rows."""
        self.remove_selected()

    def update_remove_enabled(self):
        try:
            enabled = bool(self.list is not None and self.list.has_selection())
            self.plus_minus.setEnabled_forSegment_(enabled, 1)
        except Exception:
            pass

    def _selection_changed(self):
        self.update_remove_enabled()
        self.controller.preview_selection()

    # ---------- error reporting ----------
    def show_error(self, text):
        try:
            import AppKit

            alert = AppKit.NSAlert.alloc().init()
            alert.setMessageText_('错误')
            alert.setInformativeText_(str(text)[:2000])
            alert.runModal()
        except Exception:
            print(text, file=sys.stderr)


def _make_app_delegate():
    from AppKit import NSObject

    class _AppDelegate(NSObject):
        def applicationShouldTerminateAfterLastWindowClosed_(self, app):
            return True

    return _AppDelegate


def _make_plus_minus_target():
    from AppKit import NSObject

    class _PlusMinus(NSObject):
        def clicked_(self, sender):
            owner = getattr(self, 'owner', None)
            if owner is None:
                return
            if int(sender.selectedSegment()) == 0:
                owner.choose_files()
            else:
                owner.remove_selected()

    return _PlusMinus


_PlusMinusTarget = None
_keep_alive = []


_DropInfoClass = None


def _drop_info_class():
    """A minimal ``NSDraggingInfo`` stand-in for the self-test (an ObjC
    object so pyobjc can pass it to the drag callbacks)."""
    global _DropInfoClass
    if _DropInfoClass is None:
        from AppKit import NSObject

        class _DropInfo(NSObject):
            def draggingPasteboard(self):
                return self._pasteboard

            def setDropOperation_(self, operation):
                self._operation = operation

        _DropInfoClass = _DropInfo
    return _DropInfoClass


def _drop_info(paths):
    """An NSDraggingInfo-like object carrying a synthetic file pasteboard."""
    import AppKit
    import Foundation

    pasteboard = AppKit.NSPasteboard.pasteboardWithName_(
        'com.latexformatter.selftest')
    pasteboard.clearContents()
    pasteboard.declareTypes_owner_([AppKit.NSPasteboardTypeFileURL], None)
    for path in paths[:1]:      # one item per synthetic pasteboard
        url = Foundation.NSURL.fileURLWithPath_(str(path))
        pasteboard.setString_forType_(url.absoluteString(),
                                      AppKit.NSPasteboardTypeFileURL)
    info = _drop_info_class().alloc().init()
    info._pasteboard = pasteboard
    return info


def _pump(AppKit, seconds=0.1):
    """Run the run loop briefly (NSApplication.run() would block)."""
    try:
        AppKit.NSRunLoop.currentRunLoop().runUntilDate_(
            AppKit.NSDate.dateWithTimeIntervalSinceNow_(seconds))
    except Exception:
        pass


def run_self_test(app):
    """A small native self-test (CI runs it from the native build)."""
    lines = ['PASS']
    ok = True
    try:
        import AppKit
        import tempfile

        shell = app.shell
        ok = ok and shell.window is not None and shell.split is not None
        lines.append('shell window + split view: {}'.format(ok))

        tmp = Path(tempfile.mkdtemp(prefix='mac_selftest_'))
        root = tmp / 'proj'
        (root / 'sub').mkdir(parents=True)
        sample = root / 'sub' / 'sample.tex'
        sample.write_text('中文English中文\n', encoding='utf-8')
        # recursion is off by default (the sidebar switch), so turn it on
        # to pick up the nested sample
        app.switch.setState_(1)
        app.add_paths([str(root)])
        app.list.select_index(0)
        _pump(AppKit)
        _pump(AppKit)
        text = app.diff.text()
        preview_ok = (str(sample) in text and '+' in text)
        lines.append('folder scan -> list -> preview (tagged diff): {}'
                     .format(preview_ok))
        ok = ok and preview_ok

        app.controller.run(True, confirm=False)
        backup = root / 'backup' / 'sub' / 'sample.tex.bak'
        applied_ok = ('中文 English 中文' in sample.read_text(encoding='utf-8')
                      and backup.is_file())
        lines.append('apply writes + mirrors the backup folder: {}'.format(
            applied_ok))
        ok = ok and applied_ok

        lines.append('native menu installed and current: {}'.format(
            menus.installed() and menus.is_current()))
        ok = ok and menus.installed() and menus.is_current()

        lines.append('+/- segmented control (Small Square, 2 segments): '
                     '{}'.format(app.plus_minus.segmentCount() == 2
                                 and app.plus_minus.segmentStyle()
                                 == AppKit.NSSegmentStyleSmallSquare))
        ok = ok and (app.plus_minus.segmentCount() == 2
                     and app.plus_minus.segmentStyle()
                     == AppKit.NSSegmentStyleSmallSquare)

        # --- interactive regressions -------------------------------------
        # every native control's action must be implemented by its target,
        # or AppKit auto-disables it (gray popup menus / dead buttons)
        controls = [c.view for _slot, c in app.option_hosts]
        controls += [app.ext_popup.view, app.enc_popup.view,
                     app.apply_button.view]
        actions_ok = all(
            view.target() is not None and bool(view.action())
            and bool(view.target().respondsToSelector_(view.action()))
            for view in controls)
        lines.append('native control actions implemented by their target: '
                     '{}'.format(actions_ok))
        ok = ok and actions_ok

        # the extension popup's items must be enabled (regression: gray)
        popup = app.ext_popup.view
        popup.menu().update()
        items = popup.menu().itemArray()
        enabled_ok = all(bool(item.isEnabled()) for item in items)
        lines.append('extension popup items enabled ({}): {}'.format(
            len(items), enabled_ok))
        ok = ok and enabled_ok

        # ... and selecting one must fire on_change
        seen = []
        original_change = app.ext_popup.on_change
        app.ext_popup.on_change = seen.append
        popup.selectItemAtIndex_(1)
        AppKit.NSApp().sendAction_to_from_(
            popup.action(), popup.target(), popup)
        app.ext_popup.on_change = original_change
        fired_ok = seen == [popup.itemTitleAtIndex_(1)]
        popup.selectItemWithTitle_('.tex')
        lines.append('extension popup selection fires on_change: {}'.format(
            fired_ok))
        ok = ok and fired_ok

        # a native checkbox click toggles the control
        probe = app.option_controls['check']
        before = bool(probe.isChecked())
        probe.view.performClick_(None)
        toggle_ok = bool(probe.isChecked()) != before
        probe.setChecked(before)
        lines.append('native checkbox click toggles: {}'.format(toggle_ok))
        ok = ok and toggle_ok

        # the empty-state links must carry an action the target implements
        links_ok = all(
            button.target() is not None and bool(button.action())
            and bool(button.target().respondsToSelector_(button.action()))
            for button in app.hint_links)
        lines.append('empty-state links wired: {}'.format(links_ok))
        ok = ok and links_ok

        # dropping onto the empty list (the hint host) adds the file
        drop_host = app.hint_host
        registered = bool(AppKit.NSPasteboardTypeFileURL
                          in (drop_host.registeredDraggedTypes() or []))
        dropped_empty = tmp / 'dropped_empty.tex'
        dropped_empty.write_text('中文English中文\n', encoding='utf-8')
        before = app.list.count()
        accepted = bool(drop_host.performDragOperation_(
            _drop_info([dropped_empty])))
        empty_drop_ok = accepted and app.list.count() == before + 1
        lines.append('empty-state drop target registered {} + adds the '
                     'file: {}'.format(registered, empty_drop_ok))
        ok = ok and registered and empty_drop_ok

        # ... and dropping onto the populated list (the table) adds it too
        dropped_table = tmp / 'dropped_table.tex'
        dropped_table.write_text('中文English中文\n', encoding='utf-8')
        source = app.list._datasource
        info = _drop_info([dropped_table])
        operation = source \
            .tableView_validateDrop_proposedRow_proposedDropOperation_(
                app.list._table, info, -1, 0)
        before = app.list.count()
        source.tableView_acceptDrop_row_dropOperation_(
            app.list._table, info, -1, operation)
        table_drop_ok = (operation == AppKit.NSDragOperationCopy
                         and app.list.count() == before + 1)
        lines.append('populated-list drop adds the file: {}'.format(
            table_drop_ok))
        ok = ok and table_drop_ok
    except Exception:
        lines.append('self-test exception: {}'.format(
            traceback.format_exc()))
        ok = False
    result = 'PASS' if ok else 'FAIL'
    lines[0] = result
    try:
        Path('format_tex_mac_selftest.txt').write_text(
            '\n'.join(lines) + '\n', encoding='utf-8')
    except Exception:
        pass
    return ok


def main(argv=None):
    argv = list(sys.argv if argv is None else argv)
    if sys.platform != 'darwin':
        print('format_tex_app_mac requires macOS', file=sys.stderr)
        return 1
    import AppKit

    global _PlusMinusTarget
    _PlusMinusTarget = _make_plus_minus_target()

    app = AppKit.NSApplication.sharedApplication()
    app.setActivationPolicy_(AppKit.NSApplicationActivationPolicyRegular)

    application = MacApp()
    _keep_alive.append(application)
    delegate = _make_app_delegate().alloc().init()
    _keep_alive.append(delegate)
    app.setDelegate_(delegate)
    if not application.build():
        print('failed to build the native window', file=sys.stderr)
        return 1
    app.activateIgnoringOtherApps_(True)

    if '--self-test' in argv:
        ok = run_self_test(application)
        return 0 if ok else 1
    app.run()
    return 0


if __name__ == '__main__':
    sys.exit(main())
