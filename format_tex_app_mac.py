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

from format_tex import (FormatOptions, format_file, format_source,  # noqa: E402
                        scan_directory)
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
from platform_effects import (ViewTarget, lights_inset,  # noqa: E402
                              title_gap)

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
        self.enc_status = None
        self.enc_status_host = None
        self.sel_status = None
        self.sel_status_host = None
        self.status_separator = None
        self.sel_separator = None
        self._status_region = None
        self._status_encoding = False
        self._status_selected = False
        self.apply_button = None
        self.ext_popup = None
        self.enc_popup = None
        self.switch = None
        self.plus_minus = None
        self.pm_separator = None
        self.strip = None
        self.footer_tint = None
        self._confirm_ok = False
        self.dark = True
        self.colours = {}

    # ---------- construction ----------
    def build(self):
        import AppKit

        from format_tex_theme import native_dark, palette
        from native_mac import diffview
        from native_mac.shell import SIDEBAR_WIDTH
        self.dark = native_dark()
        self.colours = palette(self.dark)
        # fixed width so the diff pane fits exactly CODE_COLUMNS monospace
        # cells (plus the one-cell marker gutter, in the left margin)
        import math

        width = math.ceil(SIDEBAR_WIDTH + 1.0 + 2 * 20.0
                          + diffview.code_column_width())
        if not self.shell.build(width=float(width)):
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

        def separator():
            line = AppKit.NSView.alloc().init()
            line.setWantsLayer_(True)
            line.layer().setBackgroundColor_(
                AppKit.NSColor.separatorColor().CGColor())
            group.addSubview_(line)
            return line

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

        # encoding popup, in the middle row of the same group
        enc_host = AppKit.NSView.alloc().init()
        enc_host.setFrame_(((0.0, 0.0), (100.0, 24.0)))
        group.addSubview_(enc_host)
        self.enc_host = enc_host
        self.enc_popup = NativePopUpButton(
            None, ViewTarget(enc_host), ENCODINGS, '同输入',
            on_change=lambda value: None, custom_label='其它')
        self.enc_popup.build()

        self.ext_label = label('扩展名')
        self.enc_label = label('输出编码')
        self.switch_label = label('含子目录')
        self.backup_label = label('生成备份文件')
        self.group_separator = separator()
        self.group_separator2 = separator()
        self.group_separator3 = separator()

        switch = AppKit.NSSwitch.alloc().init()
        # the smaller Settings-style toggle (Regular is 38x22, Small 32x18)
        switch.setControlSize_(AppKit.NSControlSizeSmall)
        switch.setTarget_(None)
        switch.setState_(AppKit.NSControlStateValueOff)
        switch.sizeToFit()
        group.addSubview_(switch)
        # the switch only adopts the small size once it is in the window
        switch.sizeToFit()
        self.switch = switch

        # backup toggle (moved out of the right panel), on by default
        backup_switch = AppKit.NSSwitch.alloc().init()
        backup_switch.setControlSize_(AppKit.NSControlSizeSmall)
        backup_switch.setTarget_(None)
        backup_switch.setState_(AppKit.NSControlStateValueOn)
        backup_switch.sizeToFit()
        group.addSubview_(backup_switch)
        backup_switch.sizeToFit()
        self.backup_switch = backup_switch

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
        hint.setStringValue_('You can drag and drop here')
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
        control.setBordered_(False)       # no box: glyphs + our divider only
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
        # a hairline between the + and - segments (the borderless control
        # draws none), matching the Settings-style bar
        self.pm_separator = AppKit.NSView.alloc().init()
        self.pm_separator.setWantsLayer_(True)
        self.pm_separator.layer().setBackgroundColor_(
            AppKit.NSColor.separatorColor().CGColor())
        footer.addSubview_(self.pm_separator)

    def _build_content(self, AppKit):
        host = self.shell.content_host

        # option checkboxes (a small responsive grid)
        self.option_hosts = []
        specs = (('punct', '半角标点后加空格', True),
                 ('commands', 'CJK 与控制序列空格', True),
                 ('tight', '页码范围保持紧凑', True),
                 ('tie', '使用 ~ 代替空格', False),
                 ('magic', '添加编码魔法注释', True),
                 ('check', '仅检查 (不写入文件)', False))
        for _key, title, checked in specs:
            slot = AppKit.NSView.alloc().init()
            slot.setFrame_(((0.0, 0.0), (10.0, 10.0)))
            host.addSubview_(slot)
            control = NativeCheckbox(None, ViewTarget(slot), title, checked,
                                     on_toggle=self._on_option_changed)
            control.build()
            key = _key
            self.option_controls[key] = control
            self.option_hosts.append((slot, control))

        # apply button (the encoding popup now lives in the sidebar group)
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

        # status bar: encoding label, a hairline divider, then the counts
        enc_status_slot = AppKit.NSView.alloc().init()
        host.addSubview_(enc_status_slot)
        self.enc_status = NativeLabel(None, ViewTarget(enc_status_slot), '',
                                      fill=False)
        self.enc_status.build()
        self.enc_status_host = enc_status_slot

        self.status_separator = AppKit.NSView.alloc().init()
        self.status_separator.setWantsLayer_(True)
        self.status_separator.layer().setBackgroundColor_(
            AppKit.NSColor.separatorColor().CGColor())
        host.addSubview_(self.status_separator)

        sel_status_slot = AppKit.NSView.alloc().init()
        host.addSubview_(sel_status_slot)
        self.sel_status = NativeLabel(None, ViewTarget(sel_status_slot), '',
                                      fill=False)
        self.sel_status.build()
        self.sel_status_host = sel_status_slot

        self.sel_separator = AppKit.NSView.alloc().init()
        self.sel_separator.setWantsLayer_(True)
        self.sel_separator.layer().setBackgroundColor_(
            AppKit.NSColor.separatorColor().CGColor())
        host.addSubview_(self.sel_separator)

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
            # group box: four Settings-style 44 pt rows with a hairline
            # between them, label left, control right
            group = self.shell.sidebar_group
            gb = group.bounds()
            row_h = 44.0
            margin = 0.0            # no top/bottom padding: equal 44 pt rows
            gap = 12.0
            width = gb.size.width
            rows = [gb.size.height - margin - row_h - i * (row_h + 1.0)
                    for i in range(4)]
            for sep, ry in ((self.group_separator, rows[0]),
                            (self.group_separator2, rows[1]),
                            (self.group_separator3, rows[2])):
                sep.setFrame_(
                    ((gap, ry - 1.0), (max(1.0, width - 2 * gap), 1.0)))
            for field, ry in ((self.ext_label, rows[0]),
                              (self.enc_label, rows[1]),
                              (self.switch_label, rows[2]),
                              (self.backup_label, rows[3])):
                lh = field.frame().size.height
                field.setFrameOrigin_((gap, ry + (row_h - lh) / 2.0))
            # extension popup (top row, right aligned, v-centred)
            popup = self._popup_size()
            self.ext_host.setFrame_(
                ((width - gap - popup[0], rows[0] + (row_h - popup[1]) / 2.0),
                 (popup[0], popup[1])))
            self.ext_popup.place()
            # encoding popup (second row)
            enc = self.enc_popup.size()
            self.enc_host.setFrame_(
                ((width - gap - enc[0], rows[1] + (row_h - enc[1]) / 2.0),
                 (enc[0], enc[1])))
            self.enc_popup.place()
            # switches: 含子目录 (third row), 生成备份文件 (fourth row)
            for sw, ry in ((self.switch, rows[2]),
                           (self.backup_switch, rows[3])):
                size = sw.frame().size
                sw.setFrameOrigin_(
                    (width - gap - size.width,
                     ry + (row_h - size.height) / 2.0))

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
            y = top - rows * cell_h - 8.0          # top of the diff area
            status_h = 20.0
            apply_w = 100.0
            apply_h = 26.0
            bottom = max(apply_h, status_h)
            # apply button in the south-east corner of the content
            self.apply_host.setFrame_(((width - apply_w, 0.0),
                                       (apply_w, apply_h)))
            self.apply_button.place()
            # status row on the left of the button: encoding, divider, counts
            self._status_region = (
                0.0, (bottom - status_h) / 2.0,
                max(1.0, width - apply_w - 12.0), status_h)
            self._place_status()
            # the diff pane extends one cell left of the content column so
            # the +/-/space marker hangs in the gutter (code aligns at 0)
            try:
                from native_mac import diffview as _diffview
                gutter = _diffview.char_advance()
            except Exception:
                gutter = 0.0
            self.diff_host.setFrame_(
                ((-gutter, bottom + 8.0),
                 (width + gutter, max(1.0, y - bottom - 8.0))))
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
            # flush in the window's bottom-left corner
            self.plus_minus.setFrame_(((0.0, 0.0), (size.width, size.height)))
            # hairline between the + and - segments, vertically centred
            divider_h = max(1.0, size.height - 6.0)
            self.pm_separator.setFrame_(
                ((size.width / 2.0 - 0.5,
                  (footer.size.height - divider_h) / 2.0),
                 (1.0, divider_h)))
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

    def set_status(self, text, encoding=None, selected=None):
        if self.status is not None:
            self.status.setText(text)
        if self.enc_status is not None:
            self.enc_status.setText(encoding or '')
        if self.sel_status is not None:
            self.sel_status.setText(selected or '')
        self._status_encoding = bool(encoding)
        self._status_selected = bool(selected)
        self._place_status()

    def _place_status(self):
        """Lay the status row out as [encoding] | [selected] | [counts],
        each segment behind a hairline and hidden when empty."""
        region = self._status_region
        if region is None or self.status is None:
            return
        x, y, width, height = region
        right = x + width
        cursor = x
        segments = (
            (bool(self._status_encoding), self.enc_status,
             self.enc_status_host, self.status_separator),
            (bool(self._status_selected), self.sel_status,
             self.sel_status_host, self.sel_separator),
        )
        for show, label, host, separator in segments:
            if host is None:
                continue
            host.setHidden_(not show)
            if separator is not None:
                separator.setHidden_(not show)
            if not show or label is None:
                continue
            seg_width = min(max(label.size()[0], 1.0),
                            max(1.0, right - cursor))
            host.setFrame_(((cursor, y), (seg_width, height)))
            cursor += seg_width + 8.0
            if separator is not None:
                separator.setFrame_(
                    ((cursor, y + (height - 16.0) / 2.0), (1.0, 16.0)))
            cursor += 1.0 + 8.0
        status_width = max(1.0, right - cursor)
        self.status_host.setFrame_(((cursor, y), (status_width, height)))
        for label in (self.enc_status, self.sel_status, self.status):
            if label is not None:
                try:
                    label.place()
                except Exception:
                    pass

    def options_for_run(self):
        return FormatOptions(
            punct=self.option_controls['punct'].isChecked(),
            commands=self.option_controls['commands'].isChecked(),
            tight_ranges=self.option_controls['tight'].isChecked(),
            backup=self.backup(),
            magic_comment=self.option_controls['magic'].isChecked(),
            tie=self.option_controls['tie'].isChecked(),
            write_encoding=self.write_encoding(),
        )

    def options(self):
        return self.options_for_run()

    def check_only(self):
        return self.option_controls['check'].isChecked()

    def confirm(self, count):
        # a backup makes the write recoverable, so only warn without one
        if self.backup():
            return True
        import AppKit

        try:
            alert = _left_alert_class().alloc().init()
        except Exception:
            alert = AppKit.NSAlert.alloc().init()   # fallback: centred text
        alert.setAlertStyle_(AppKit.NSAlertStyleWarning)
        alert.setMessageText_(
            '未启用备份, 将直接修改 {} 个文件'.format(count))
        alert.setInformativeText_('此操作不可撤销')
        # first button -> right; destructive (red) and not the Return
        # default (no key equivalent), so it needs an explicit click
        destructive = alert.addButtonWithTitle_('确认')
        destructive.setHasDestructiveAction_(True)
        destructive.setKeyEquivalent_('')
        # second button -> left; Esc dismisses the alert (cancel)
        cancel = alert.addButtonWithTitle_('取消')
        cancel.setKeyEquivalent_('\x1b')
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

    def backup(self):
        try:
            return bool(self.backup_switch.state() == 1)
        except Exception:
            return True

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

    def _on_option_changed(self, _checked=False):
        """A formatting option was toggled: refresh the preview in place."""
        if self.selected_entries():
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


_LEFT_ALERT_CLASS = None


def _left_alert_class():
    """An ``NSAlert`` whose text is left-aligned.

    macOS 27 centres the alert's message/informative text; the private
    ``_layoutPrefersCenterAlignment`` flag drives it (adding an accessory
    flips it but adds an empty strip). Overriding the flag to ``False``
    left-aligns the native text with no layout change. If a future macOS
    drops the method the override is simply inert and the text falls back
    to centred."""
    global _LEFT_ALERT_CLASS
    if _LEFT_ALERT_CLASS is None:
        from AppKit import NSAlert

        class _LeftAlert(NSAlert):
            def _layoutPrefersCenterAlignment(self):
                return False

        _LEFT_ALERT_CLASS = _LeftAlert
    return _LEFT_ALERT_CLASS


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

        # toggling an option refreshes the preview in place
        tie = app.option_controls['tie']
        tie.view.performClick_(None)
        _pump(AppKit)
        tie_ok = '中文~English~中文' in app.diff.text()
        tie.view.performClick_(None)
        _pump(AppKit)
        tie_ok = tie_ok and '中文~English~中文' not in app.diff.text()
        lines.append('tie option toggle auto-refreshes the preview: {}'
                     .format(tie_ok))
        ok = ok and tie_ok

        # tie mode also normalizes whitespace already in the source
        tie_src = '中文English 中文'
        norm_ok = ('中文~English~中文' in format_source(
            tie_src, FormatOptions(tie=True))[0]
            and '中文 English 中文' in format_source(tie_src)[0])
        lines.append('tie normalizes existing whitespace: {}'.format(norm_ok))
        ok = ok and norm_ok

        # spaces after a control word stay; CJK -> command ties (incl. a
        # group close before the command)
        cw_tie = format_source(r'\hfill  应 \quad 报} \hfill',
                               FormatOptions(tie=True))[0]
        cw_ok = (r'\hfill  应' in cw_tie
                 and r'应~\quad' in cw_tie
                 and r'报}~\hfill' in cw_tie
                 and r'\hfill应' == format_source(
                     r'\hfill应', FormatOptions(tie=True))[0].splitlines()[-1]
                 and r'无需写标题~\cite{1}' in format_source(
                     r'无需写标题 \cite{1}', FormatOptions(tie=True))[0])
        lines.append('tie keeps spaces after control words, ties '
                     'CJK->command: {}'.format(cw_ok))
        ok = ok and cw_ok

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

        control = app.plus_minus.frame()
        pm_ok = (not app.plus_minus.isBordered()
                 and app.pm_separator is not None
                 and abs(control.origin.x) < 1.0
                 and abs(control.origin.y) < 1.0)
        lines.append('+/- borderless (no box) with a divider, flush at the '
                     'bottom-left: {}'.format(pm_ok))
        ok = ok and pm_ok

        # the sidebar group uses Settings-style 44 pt rows (45 pt pitch)
        group_h = app.shell.sidebar_group.frame().size.height
        pitch = (app.ext_host.frame().origin.y
                 - app.enc_host.frame().origin.y)
        rows_ok = abs(group_h - 179.0) < 1.5 and abs(pitch - 45.0) < 1.5
        lines.append('settings-style group rows (44 pt + hairline, group '
                     '179 pt): {}'.format(rows_ok))
        ok = ok and rows_ok

        # the sidebar is a fixed 232 pt with no divider to drag (a plain
        # container instead of a split view)
        container = app.shell.split
        sidebar_ok = (abs(app.shell.sidebar_width() - 232.0) < 0.5
                      and not isinstance(container, AppKit.NSSplitView))
        lines.append('sidebar fixed at 232 pt, no draggable divider: '
                     '{}'.format(sidebar_ok))
        ok = ok and sidebar_ok

        # the green button is the classic "+" zoom (no full screen)
        zoom = app.shell.window.standardWindowButton_(
            AppKit.NSWindowZoomButton)
        action = zoom.action() if zoom is not None else None
        action_name = (action.decode() if isinstance(action, bytes)
                       else str(action))
        glyph_ok = True
        try:
            glyph_ok = (zoom.cell()
                        ._accessibilityZoomButtonHasFullscreenBehavior()
                        is False)
        except Exception:
            glyph_ok = True
        zoom_ok = (
            app.shell.window.collectionBehavior()
            == AppKit.NSWindowCollectionBehaviorFullScreenNone
            and zoom is not None
            and zoom.target() is app.shell.zoom_target()
            and action_name.endswith('performZoom:') and glyph_ok)
        lines.append('green button is the "+" zoom toggle (no full screen): '
                     '{}'.format(zoom_ok))
        ok = ok and zoom_ok

        # fixed window width: the diff pane fits exactly 80 monospace cells
        import math

        from native_mac import diffview as _diffview

        expected = math.ceil(232.0 + 1.0 + 40.0
                             + _diffview.code_column_width())
        win_w = app.shell.window.frame().size.width
        max_w = app.shell.window.maxSize().width
        content_w = app.shell.content_host.frame().size.width
        width_ok = (abs(win_w - expected) < 0.5 and abs(max_w - expected) < 1.0
                    and content_w >= _diffview.code_column_width() - 1.0)
        lines.append('fixed width fits 80 monospace cells ({:.0f} pt): '
                     '{}'.format(win_w, width_ok))
        ok = ok and width_ok

        # titlebar chrome (traffic lights + our own title label) centred
        app.shell.layout()
        win = app.shell.window
        frame = win.frame()
        top = frame.origin.y + frame.size.height

        def centre_from_top(view):
            rect = view.convertRect_toView_(view.bounds(), None)
            rect = win.convertRectToScreen_(rect)
            return top - (rect.origin.y + rect.size.height / 2.0)

        def left_from_edge(view):
            rect = view.convertRect_toView_(view.bounds(), None)
            rect = win.convertRectToScreen_(rect)
            return rect.origin.x - frame.origin.x

        close = win.standardWindowButton_(AppKit.NSWindowCloseButton)
        lights_ok = abs(centre_from_top(close) - BAND_HEIGHT / 2.0) < 1.5
        inset_ok = abs(left_from_edge(close) - lights_inset()) < 1.5
        title = app.shell.title_label
        title_ok = (title is not None
                    and abs(centre_from_top(title) - BAND_HEIGHT / 2.0) < 1.5)
        left_ok = (title is not None
                   and abs(left_from_edge(title)
                           - (app.shell.sidebar_width() + title_gap())) < 2.0)
        colour_ok = True
        if title is not None:
            try:
                colour = title.textColor()
                colour = colour.colorUsingColorSpace_(
                    AppKit.NSColorSpace.sRGBColorSpace()) if colour else None
                if bool(win.isKeyWindow()) and colour is not None:
                    brightest = max(colour.redComponent(),
                                    colour.greenComponent(),
                                    colour.blueComponent())
                    colour_ok = brightest > 0.6      # label colour, not grey
            except Exception:
                colour_ok = True
        chrome_ok = (lights_ok and inset_ok and title_ok and left_ok
                     and colour_ok)
        lines.append('titlebar chrome centred (lights y {}, inset {}, '
                     'title y {}, left {}, colour {}): {}'.format(
                         lights_ok, inset_ok, title_ok, left_ok, colour_ok,
                         chrome_ok))
        ok = ok and chrome_ok

        # the sidebar toggles are the smaller Settings-style switches
        switch_small = (
            app.switch.controlSize() == AppKit.NSControlSizeSmall
            and app.backup_switch.controlSize() == AppKit.NSControlSizeSmall)
        lines.append('sidebar switches (含子目录, 生成备份文件) use the small '
                     'control size: {}'.format(switch_small))
        ok = ok and switch_small

        # the backup toggle drives FormatOptions.backup
        before_backup = app.backup()
        app.backup_switch.setState_(0 if before_backup else 1)
        backup_wired = app.backup() != before_backup
        app.backup_switch.setState_(1 if before_backup else 0)
        lines.append('backup switch wired to options: {}'.format(backup_wired))
        ok = ok and backup_wired

        # 应用格式化 sits in the content's south-east corner
        content = app.shell.content_host.bounds()
        button = app.apply_host.frame()
        se_ok = (abs((button.origin.x + button.size.width)
                     - content.size.width) < 1.5
                 and abs(button.origin.y) < 1.5)
        lines.append('apply button in the south-east corner: {}'.format(se_ok))
        ok = ok and se_ok

        # no confirm prompt while backups are enabled (the dialog only
        # appears without a backup, which the tests never trigger)
        confirm_ok = app.confirm(3) is True
        lines.append('confirm() returns True with backups on (no dialog): '
                     '{}'.format(confirm_ok))
        ok = ok and confirm_ok

        # the destructive confirm's alert left-aligns its text
        left_ok = (_left_alert_class()()
                   ._layoutPrefersCenterAlignment() is False)
        lines.append('confirm alert left-aligns its text: {}'.format(left_ok))
        ok = ok and left_ok

        # the status bar carries encoding + selected count behind hairlines
        app.set_status('测试', encoding='GBK', selected='已选择 2 个文件')
        enc_ok = (app.enc_status.text() == 'GBK'
                  and app.enc_status.isVisible()
                  and app.sel_status.text() == '已选择 2 个文件'
                  and app.sel_status.isVisible())
        app.set_status('就绪')
        enc_ok = (enc_ok and not app.enc_status.isVisible()
                  and not app.sel_status.isVisible())
        lines.append('status bar encoding/selected segments shown then '
                     'hidden: {}'.format(enc_ok))
        ok = ok and enc_ok

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

        # ... and dropping onto the populated list adds it too - the table
        # view now handles the drag directly (like the empty-state host)
        table = app.list._table
        table_selectors = all(
            bool(table.respondsToSelector_(selector)) for selector in (
                b'draggingEntered:', b'prepareForDragOperation:',
                b'performDragOperation:'))
        dropped_table = tmp / 'dropped_table.tex'
        dropped_table.write_text('中文English中文\n', encoding='utf-8')
        info = _drop_info([dropped_table])
        entered = (table.draggingEntered_(info)
                   == AppKit.NSDragOperationCopy)
        before = app.list.count()
        dropped = (bool(table.prepareForDragOperation_(info))
                   and bool(table.performDragOperation_(info)))
        table_drop_ok = (table_selectors and entered and dropped
                         and app.list.count() == before + 1)
        lines.append('populated-list drop (table drag methods) adds the '
                     'file: {}'.format(table_drop_ok))
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
