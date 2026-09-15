#!/usr/bin/env python3
"""Native macOS views for the Qt GUI (AppKit via pyobjc).

Each module owns one piece of the interface and follows the same shape:
``build()`` creates and inserts the view above the Qt view, ``place()``
re-fits it to the Qt "slot" widget that provides the geometry (so the Qt
layout keeps driving everything), and the module exposes small accessors
for the application and the self-test.
"""

from .filelist import NativeFileList

__all__ = ['NativeFileList']
