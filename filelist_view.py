#!/usr/bin/env python3
r"""File-list adapters for the GUI.

Two implementations of one small interface, so the application (and the
self-test) never care which one is active:

* :class:`NativeFileListAdapter` - a real ``NSTableView`` (macOS), with
  the Qt list kept as the empty-state host (its placeholder shows the
  hint and the 选择文件/选择目录 links);
* :class:`QtFileListAdapter` - the existing ``QListWidget`` (used on other
  platforms and as the fallback when the native list cannot be created).

Interface: ``add``, ``clear``, ``count``, ``entries``, ``selected_entries``,
``has_selection``, ``remove_selected``, ``select_index``, ``select_first``,
``place`` and ``native``.
"""

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QFileIconProvider, QListWidgetItem

# item data roles used by the Qt list
ROLE_PATH = Qt.ItemDataRole.UserRole
ROLE_ROOT = Qt.ItemDataRole.UserRole + 1


def _entries(rows):
    """Normalise ``[(path, root)]`` into ``[(str, str | None)]``."""
    out = []
    for path, root in rows:
        out.append((str(path), str(root) if root else None))
    return out


class QtFileListAdapter:
    """Wraps the existing ``DropListWidget`` (model lives in its items)."""

    native = False

    def __init__(self, window):
        self.window = window
        self.list = window.file_list
        self._icons = QFileIconProvider()
        self._icon_cache = {}

    # ---------- model ----------
    def add(self, rows):
        entries = _entries(rows)
        existing = {self.list.item(i).data(ROLE_PATH)
                    for i in range(self.list.count())}
        added = 0
        first = None
        for path, root in entries:
            if path in existing:
                continue
            item = QListWidgetItem(self._icon(path), Path(path).name)
            item.setData(ROLE_PATH, path)
            item.setData(ROLE_ROOT, root or str(Path(path).parent))
            item.setToolTip(path)
            self.list.addItem(item)
            existing.add(path)
            added += 1
            if first is None:
                first = item
        if first is not None and not self.list.selectedItems():
            self.list.setCurrentItem(first)          # triggers preview
        return added

    def clear(self):
        self.list.clear()

    def count(self):
        return self.list.count()

    def _count(self):
        return self.list.count()

    def _entries_list(self):
        return self.entries()

    def entries(self):
        return [(self.list.item(i).data(ROLE_PATH) or self.list.item(i).text(),
                 self.list.item(i).data(ROLE_ROOT))
                for i in range(self.list.count())]

    def selected_entries(self):
        out = []
        for item in self.list.selectedItems():
            path = item.data(ROLE_PATH) or item.text()
            root = item.data(ROLE_ROOT)
            out.append((Path(path), Path(root) if root else None))
        return out

    def has_selection(self):
        return bool(self.list.selectedItems())

    def remove_selected(self):
        rows = sorted({self.list.row(item)
                       for item in self.list.selectedItems()}, reverse=True)
        for row in rows:
            self.list.takeItem(row)
        return len(rows)

    def select_index(self, row):
        self.select_rows([row])

    def select_rows(self, rows):
        self.list.clearSelection()
        for row in rows:
            if 0 <= row < self.list.count():
                self.list.item(row).setSelected(True)

    def select_first(self):
        if self.list.count():
            self.list.item(0).setSelected(True)

    def selection_rows(self):
        return sorted({self.list.row(item)
                       for item in self.list.selectedItems()})

    def place(self):
        pass

    def build(self):
        """Nothing to build: the Qt list is always ready (this is what
        the native adapter's build() means for the other platforms)."""
        return False

    # ---------- helpers ----------
    def _icon(self, path):
        entry = Path(path)
        key = ('dir',) if entry.is_dir() else ('file', entry.suffix.lower())
        icon = self._icon_cache.get(key)
        if icon is None:
            if entry.is_dir():
                icon = self._icons.icon(QFileIconProvider.IconType.Folder)
            else:
                from PySide6.QtCore import QFileInfo
                icon = self._icons.icon(QFileInfo(str(entry)))
            self._icon_cache[key] = icon
        return icon


class NativeFileListAdapter:
    """A real ``NSTableView``; the Qt list stays as the empty-state host.

    The native view is built later (from ``apply_window_effects``), once
    the window is on screen - inserting native subviews during
    ``__init__`` crashes. If the build fails, every call falls back to the
    Qt adapter so the application keeps working."""

    native = True

    def __init__(self, window):
        from native_mac import NativeFileList

        self.window = window
        self.list = window.file_list           # hint host / Qt fallback
        self.view = NativeFileList(window, self.list,
                                   on_selection=window._on_list_selection,
                                   on_drop=window.drop_paths)
        self.active = False
        self._fallback = None

    def build(self):
        """Create the native list; on failure fall back to Qt."""
        if self.active:
            return True
        try:
            self.active = bool(self.view.build())
        except Exception:
            self.active = False
        if self.active:
            # any items added before the native view existed move over
            if self._fallback is not None:
                self.view.add(self._fallback.entries())
                self._fallback = None
            self._sync_hint()
            self.place()
            return True
        return False

    def _impl(self):
        """The active implementation: this adapter when the native view
        was built, otherwise the Qt fallback (created on demand)."""
        if self.active:
            return self
        if self._fallback is None:
            self._fallback = QtFileListAdapter(self.window)
        return self._fallback

    # ---------- interface (delegates when the native view is inactive) --
    def add(self, rows):
        impl = self._impl()
        if impl is not self:
            return impl.add(rows)
        added = self.view.add(_entries(rows))
        if added and not self.view.has_selection():
            self.view.select_index(0)
        self._sync_hint()
        return added

    def clear(self):
        impl = self._impl()
        if impl is not self:
            return impl.clear()
        self.view.clear()
        self._sync_hint()

    def count(self):
        return self._impl()._count()

    def _count(self):
        return self.view.count()

    def entries(self):
        impl = self._impl()
        return list(self.view.items) if impl is self else impl.entries()

    def _entries_list(self):
        return list(self.view.items)

    def selected_entries(self):
        impl = self._impl()
        if impl is not self:
            return impl.selected_entries()
        return self.view.selected_entries()

    def has_selection(self):
        impl = self._impl()
        if impl is not self:
            return impl.has_selection()
        return self.view.has_selection()

    def remove_selected(self):
        impl = self._impl()
        if impl is not self:
            return impl.remove_selected()
        removed = self.view.remove_selected()
        self._sync_hint()
        return removed

    def select_index(self, row):
        return self.select_rows([row])

    def select_rows(self, rows):
        impl = self._impl()
        if impl is not self:
            return impl.select_rows(rows)
        return self.view.select_rows(rows)

    def select_first(self):
        impl = self._impl()
        if impl is not self:
            return impl.select_first()
        return self.view.select_index(0)

    def selection_rows(self):
        impl = self._impl()
        if impl is not self:
            return impl.selection_rows()
        return self.view.selected_rows()

    def place(self):
        if self.active:
            self.view.place()

    # ---------- internals ----------
    def _sync_hint(self):
        """Show the Qt hint + links only when the model is empty (the Qt
        list itself stays empty, so its own placeholder is driven here)."""
        self.list.placeholder_widget().setVisible(self.view.count() == 0)


class Win32FileListAdapter:
    """A real ``SysListView32`` hosted in the Qt list widget (Windows).

    Built later (from ``apply_window_effects``); if it cannot be created
    every call falls back to the Qt adapter, so Windows cannot regress.
    The Qt list stays as the empty-state host."""

    native = True

    def __init__(self, window):
        from win32_list import Win32FileList

        self.window = window
        self.list = window.file_list
        self.view = Win32FileList(
            window, self.list,
            on_selection=window._on_list_selection,
            on_drop=window.drop_paths,
            colours=self._colours())
        self.active = False
        self._failed = False
        self._fallback = None

    def _colours(self):
        from PySide6.QtGui import QPalette

        palette = self.list.palette()
        return {
            'window': palette.color(QPalette.ColorRole.Window).name(),
            'text': palette.color(QPalette.ColorRole.WindowText).name(),
        }

    def build(self):
        if self.active:
            return True
        if self._failed:
            return False
        try:
            self.active = bool(self.view.build())
        except Exception:
            self.active = False
        if self.active:
            self._sync_hint()
            self.place()
            return True
        self._failed = True
        return False

    def _impl(self):
        if self.active:
            return self
        if self._fallback is None:
            self._fallback = QtFileListAdapter(self.window)
        return self._fallback

    # ---------- interface ----------
    def add(self, rows):
        impl = self._impl()
        if impl is not self:
            return impl.add(rows)
        added = self.view.add(_entries(rows))
        if added and not self.view.has_selection():
            self.view.select_index(0)
        self._sync_hint()
        return added

    def clear(self):
        impl = self._impl()
        if impl is not self:
            return impl.clear()
        self.view.clear()
        self._sync_hint()

    def count(self):
        return self._impl()._count()

    def _count(self):
        return self.view.count()

    def entries(self):
        impl = self._impl()
        return list(self.view.items) if impl is self else impl.entries()

    def selected_entries(self):
        impl = self._impl()
        if impl is not self:
            return impl.selected_entries()
        return self.view.selected_entries()

    def has_selection(self):
        impl = self._impl()
        if impl is not self:
            return impl.has_selection()
        return self.view.has_selection()

    def remove_selected(self):
        impl = self._impl()
        if impl is not self:
            return impl.remove_selected()
        removed = self.view.remove_selected()
        self._sync_hint()
        return removed

    def select_index(self, row):
        impl = self._impl()
        if impl is not self:
            return impl.select_index(row)
        return self.view.select_index(row)

    def select_rows(self, rows):
        impl = self._impl()
        if impl is not self:
            return impl.select_rows(rows)
        return self.view.select_rows(rows)

    def select_first(self):
        impl = self._impl()
        if impl is not self:
            return impl.select_first()
        return self.view.select_first()

    def selection_rows(self):
        impl = self._impl()
        if impl is not self:
            return impl.selection_rows()
        return self.view.selection_rows()

    def place(self):
        if self.active:
            self.view.place()

    def _sync_hint(self):
        self.list.placeholder_widget().setVisible(self.view.count() == 0)


def create_file_list_view(window):
    """Native on macOS (falling back to Qt if it cannot be created)."""
    import sys as _sys

    if _sys.platform == 'darwin':
        try:
            return NativeFileListAdapter(window)
        except Exception:
            pass
    elif _sys.platform == 'win32':
        try:
            return Win32FileListAdapter(window)
        except Exception:
            pass
    return QtFileListAdapter(window)
