#!/usr/bin/env python3
r"""Native macOS file list for the Qt GUI.

A real ``NSTableView`` (view based) inside a transparent ``NSScrollView``,
placed over a Qt "slot" widget that provides the geometry - the same
mechanism the native switch, title, band and footer already use. AppKit
supplies the row selection (system accent colour, dimmed while the window
is inactive), the hairline grid, overlay scrollbars, keyboard navigation,
multi-selection (cmd/shift/arrows) and Finder drag & drop; this module
supplies the data model, the row cells (native file icon + file name with
the full path as tooltip) and the callbacks into the application.

Usage
-----
    view = NativeFileList(window, slot, on_selection, on_drop)
    view.add([(path, root), ...])      # model changes
    view.selected_entries()            # [(Path, Path | None), ...]
    view.remove_selected()
"""

import sys
from pathlib import Path

from native_mac.drop import pasteboard_paths


class NativeFileList:
    """``NSTableView``-backed file list (macOS only; ``available()`` False
    elsewhere so callers fall back to the Qt list)."""

    def __init__(self, window, slot, on_selection=None, on_drop=None):
        self.window = window
        self.slot = slot
        self.on_selection = on_selection
        self.on_drop = on_drop
        self.items = []                 # [(path str, root str | None)]
        self._icons = {}                # (is_dir, suffix) -> NSImage
        self._table = None
        self._scroll = None
        self._datasource = None

    # ---------- availability ----------
    @staticmethod
    def available():
        return sys.platform == 'darwin'

    # ---------- creation ----------
    def build(self):
        """Create the scroll view + table and insert them above the Qt
        view. Returns True on success."""
        if not self.available():
            return False
        try:
            import AppKit

            import platform_effects as pe

            if self._scroll is None:
                table = AppKit.NSTableView.alloc().init()
                column = AppKit.NSTableColumn.alloc().initWithIdentifier_(
                    'file')
                column.setResizingMask_(
                    AppKit.NSTableColumnAutoresizingMask)
                table.addTableColumn_(column)
                table.setHeaderView_(None)
                table.setStyle_(AppKit.NSTableViewStylePlain)
                table.setGridStyleMask_(AppKit.NSTableViewGridNone)
                table.setSelectionHighlightStyle_(
                    AppKit.NSTableViewSelectionHighlightStyleRegular)
                table.setAllowsMultipleSelection_(True)
                table.setAllowsEmptySelection_(True)
                table.setUsesAlternatingRowBackgroundColors_(False)
                table.setBackgroundColor_(AppKit.NSColor.clearColor())
                table.setColumnAutoresizingStyle_(
                    AppKit.NSTableViewLastColumnOnlyAutoresizingStyle)

                source = _table_source_class().alloc().init()
                source.owner = self
                table.setDataSource_(source)
                table.setDelegate_(source)
                table.registerForDraggedTypes_(
                    [AppKit.NSPasteboardTypeFileURL])

                scroll = AppKit.NSScrollView.alloc().init()
                scroll.setDocumentView_(table)
                scroll.setDrawsBackground_(False)
                scroll.setBorderType_(AppKit.NSNoBorder)
                scroll.setHasVerticalScroller_(True)
                scroll.setAutohidesScrollers_(True)
                self._table, self._scroll, self._datasource = (
                    table, scroll, source)

            self._target = pe.as_target(self.window, self.slot, inset=1.0)
            if not pe.place_in(self.window, self._target, self._scroll):
                return False
            self.place()
            return True
        except Exception as exc:
            try:
                import platform_effects as pe
                pe._note('native file list failed: {}: {}'.format(
                    type(exc).__name__, exc))
            except Exception:
                pass
            return False

    def place(self):
        """Re-fit the list to its Qt slot (called on layout changes)."""
        if self._scroll is None:
            return
        try:
            rect = self._target.rect()
            self._scroll.setFrame_((rect.origin, rect.size))
            self._refresh_visibility()
        except Exception:
            pass

    # ---------- model ----------
    def add(self, entries):
        """Append ``(path, root)`` entries; duplicates are ignored (first
        root wins, like the Qt list). Returns the number added."""
        seen = {str(p) for p, _r in self.items}
        added = 0
        for path, root in entries:
            key = str(path)
            if key in seen:
                continue
            seen.add(key)
            self.items.append((key, str(root) if root else None))
            added += 1
        if added:
            self._reload()
        return added

    def clear(self):
        if self.items:
            self.items = []
            self._reload()

    def count(self):
        return len(self.items)

    def selected_rows(self):
        if self._table is None:
            return []
        return list(self._table.selectedRowIndexes())

    def selected_entries(self):
        rows = self.selected_rows()
        out = []
        for row in rows:
            if 0 <= row < len(self.items):
                path, root = self.items[row]
                out.append((Path(path), Path(root) if root else None))
        return out

    def has_selection(self):
        return bool(self.selected_rows())

    def remove_selected(self):
        rows = sorted(set(self.selected_rows()), reverse=True)
        for row in rows:
            if 0 <= row < len(self.items):
                del self.items[row]
        if rows:
            self._reload()
        return len(rows)

    def select_rows(self, rows):
        if self._table is None:
            return
        indexes = _index_set(rows)
        self._table.selectRowIndexes_byExtendingSelection_(indexes, False)

    def select_index(self, row):
        self.select_rows([row])

    # ---------- internals ----------
    def _reload(self):
        if self._table is not None:
            self._table.reloadData()
        self._refresh_visibility()

    def _refresh_visibility(self):
        if self._scroll is not None:
            visible = bool(getattr(self, '_target', None) is None
                           or self._target.visible())
            self._scroll.setHidden_(not (self.items and visible))

    def cell_view(self, row):
        """The view for a row (also used by the self-test)."""
        if self._table is None:
            return None
        return self._cell_view(self._table, row)

    def _cell_view(self, table, row):
        """Build/recycle the view for a row: native icon + file name (the
        full path doubles as its tooltip)."""
        import AppKit

        identifier = 'filecell'
        # NSTableView takes the two-argument form; a wrong selector here
        # would raise inside an AppKit callback (pyobjc traps that)
        view = table.makeViewWithIdentifier_owner_(identifier, None)
        if view is None:
            view = AppKit.NSTableCellView.alloc().init()
            view.setIdentifier_(identifier)
            icon = AppKit.NSImageView.alloc().init()
            icon.setImageScaling_(AppKit.NSImageScaleProportionallyDown)
            view.addSubview_(icon)
            view.setImageView_(icon)
            label = AppKit.NSTextField.alloc().init()
            label.setBezeled_(False)
            label.setDrawsBackground_(False)
            label.setEditable_(False)
            label.setSelectable_(False)
            label.setLineBreakMode_(AppKit.NSLineBreakByTruncatingMiddle)
            label.setFont_(AppKit.NSFont.systemFontOfSize_(13.0))
            view.addSubview_(label)
            view.setTextField_(label)
        if row >= len(self.items):
            return view
        path = self.items[row][0]
        icon_view = view.imageView()
        label = view.textField()
        icon_view.setImage_(self.cell_image(path))
        icon_view.setFrame_(((2.0, 3.0), (16.0, 16.0)))
        label.setStringValue_(Path(path).name)
        label.setToolTip_(path)
        width = float(table.frame().size.width) - 30.0
        label.setFrame_(((24.0, 2.0), (max(40.0, width), 18.0)))
        return view

    def _row_view(self, table):
        import AppKit

        return _row_view_class(AppKit).alloc().init()

    def cell_image(self, path):
        """Native icon for a row, cached per kind (folder / suffix)."""
        import AppKit

        entry = Path(path)
        key = ('dir',) if entry.is_dir() else ('file', entry.suffix.lower())
        image = self._icons.get(key)
        if image is None:
            try:
                image = AppKit.NSWorkspace.sharedWorkspace() \
                    .iconForFile_(str(entry))
            except Exception:
                image = None
            self._icons[key] = image
        return image

    def _selection_changed(self):
        if callable(self.on_selection):
            self.on_selection()

    def _dropped(self, paths):
        if callable(self.on_drop) and paths:
            self.on_drop(paths)


def _index_set(rows):
    import Foundation

    indexes = Foundation.NSMutableIndexSet.alloc().init()
    for row in rows:
        indexes.addIndex_(int(row))
    return indexes


def _make_table_source():
    """The NSObject subclass that acts as data source and delegate.

    Defined lazily so the Objective-C class is registered once."""
    import AppKit

    class _TableSourceImpl(AppKit.NSObject):
        def numberOfRowsInTableView_(self, table):
            return len(self.owner.items)

        def tableView_viewForTableColumn_row_(self, table, column, row):
            return self.owner._cell_view(table, row)

        def tableViewSelectionDidChange_(self, notification):
            self.owner._selection_changed()

        def tableView_rowViewForRow_(self, table, row):
            return self.owner._row_view(table)

        # --- Finder drag & drop (current API: public.file-url) ---
        def tableView_validateDrop_proposedRow_proposedDropOperation_(
                self, table, info, row, operation):
            if pasteboard_paths(info.draggingPasteboard()):
                info.setDropOperation_(AppKit.NSTableViewDropOn)
                return AppKit.NSDragOperationCopy
            return AppKit.NSDragOperationNone

        def tableView_acceptDrop_row_dropOperation_(
                self, table, info, row, operation):
            paths = pasteboard_paths(info.draggingPasteboard())
            self.owner._dropped(paths)
            return AppKit.NSDragOperationCopy

    return _TableSourceImpl


_ROW_VIEW_CLASS = None
_ROW_SEPARATOR_ERROR = None


def row_separator_error():
    """Last error from the row separator drawing (for tests)."""
    return _ROW_SEPARATOR_ERROR


def _row_view_class(AppKit):
    """NSTableRowView subclass drawing the hairline separator under each
    row, so the empty area below the rows stays clean (the table view's
    solid horizontal grid mask also paints that area)."""
    global _ROW_VIEW_CLASS
    if _ROW_VIEW_CLASS is None:
        class _RowView(AppKit.NSTableRowView):
            def drawSeparatorInRect_(self, rect):
                # never let an exception escape into AppKit's drawing:
                # pyobjc turns that into a hard trap
                global _ROW_SEPARATOR_ERROR
                try:
                    bounds = self.bounds()
                    AppKit.NSColor.separatorColor().setStroke()
                    path = AppKit.NSBezierPath.bezierPath()
                    path.setLineWidth_(1.0)
                    y = bounds.size.height - 0.5
                    path.moveToPoint_((0.0, y))
                    path.lineToPoint_((bounds.size.width, y))
                    path.stroke()
                except Exception as exc:
                    _ROW_SEPARATOR_ERROR = '{}: {}'.format(
                        type(exc).__name__, exc)

        _ROW_VIEW_CLASS = _RowView
    return _ROW_VIEW_CLASS


def _table_source_class():
    """The NSObject subclass used as data source and delegate (created
    once, so the Objective-C class is registered only once)."""
    global _TABLE_SOURCE_CLASS
    if _TABLE_SOURCE_CLASS is None:
        _TABLE_SOURCE_CLASS = _make_table_source()
    return _TABLE_SOURCE_CLASS


_TABLE_SOURCE_CLASS = None
