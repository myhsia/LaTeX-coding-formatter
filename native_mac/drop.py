#!/usr/bin/env python3
r"""A reusable AppKit view that accepts Finder file/folder drops.

The native app's empty-state hint is a plain view, not the file list, so
without its own drop target a drop onto the empty sidebar (the only
visible thing at that point) is silently ignored. This view registers for
``public.file-url`` and forwards the dropped paths to a callback.

    view = make_drop_view(on_paths)     # an NSView, or None off macOS
    view.registerForDraggedTypes_([...]) # done by the factory

The data source of the file list reuses :func:`pasteboard_paths` so both
drop paths parse the pasteboard identically.
"""

import sys

_DROP_VIEW_CLASS = None


def pasteboard_paths(pasteboard):
    """File paths from a dragging pasteboard (no deprecated APIs)."""
    import AppKit
    import Foundation

    paths = []
    try:
        items = pasteboard.pasteboardItems() or []
    except Exception:
        items = []
    for item in items:
        try:
            url_string = item.stringForType_(AppKit.NSPasteboardTypeFileURL)
        except Exception:
            url_string = None
        if not url_string:
            continue
        url = Foundation.NSURL.URLWithString_(url_string)
        if url is None:
            continue
        path = url.path()
        if path:
            paths.append(str(path))
    return paths


def _dragged_paths(info):
    """Paths from an NSDraggingInfo, tolerant of odd/empty pasteboards."""
    try:
        return pasteboard_paths(info.draggingPasteboard())
    except Exception:
        return []


def _drop_view_class():
    """The NSView subclass, defined lazily (the ObjC class registers once)."""
    global _DROP_VIEW_CLASS
    if _DROP_VIEW_CLASS is None:
        import AppKit

        class _DropView(AppKit.NSView):
            # helpers live at module level: an instance method with a lone
            # leading underscore confuses pyobjc's selector transform
            def draggingEntered_(self, info):
                return (AppKit.NSDragOperationCopy if _dragged_paths(info)
                        else AppKit.NSDragOperationNone)

            def draggingUpdated_(self, info):
                return (AppKit.NSDragOperationCopy if _dragged_paths(info)
                        else AppKit.NSDragOperationNone)

            def prepareForDragOperation_(self, info):
                return bool(_dragged_paths(info))

            def performDragOperation_(self, info):
                paths = _dragged_paths(info)
                callback = getattr(self, 'on_paths', None)
                if paths and callable(callback):
                    callback(paths)
                    return True
                return False

        _DROP_VIEW_CLASS = _DropView
    return _DROP_VIEW_CLASS


def make_drop_view(on_paths):
    """A view accepting file/folder drops, or None off macOS."""
    if sys.platform != 'darwin':
        return None
    try:
        import AppKit

        view = _drop_view_class().alloc().init()
        view.registerForDraggedTypes_([AppKit.NSPasteboardTypeFileURL])
        view.on_paths = on_paths
        return view
    except Exception:
        return None
