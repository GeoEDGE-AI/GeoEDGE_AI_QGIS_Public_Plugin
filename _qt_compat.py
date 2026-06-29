# SPDX-License-Identifier: GPL-2.0-or-later
# Copyright (C) 2024-2026 GeoEdge AI
#
# This file is part of the GeoEdge AI QGIS plugin.
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 2 of the License, or
# (at your option) any later version.
# See the COPYING file or <https://www.gnu.org/licenses/> for details.
"""Qt5 / Qt6 enum compatibility shim.

In Qt5 (QGIS 3.x), enum members are flat attributes on the Qt namespace:
    Qt.AlignCenter, Qt.RichText, Qt.LeftDockWidgetArea, ...

In Qt6 (QGIS 4.x / PyQt6), they live under scoped enum classes:
    Qt.AlignmentFlag.AlignCenter, Qt.TextFormat.RichText, ...

The ``qgis.PyQt`` shim does not bridge this scoping change automatically,
so this module resolves every constant we use once at import time and
exposes them as plain attributes.  Import and use as::

    from ._qt_compat import QtC          # from the plugin root
    from .._qt_compat import QtC         # from a sub-package (ui/, qgis/, …)

    widget.setAlignment(QtC.AlignCenter)
"""

from __future__ import annotations

try:
    from qgis.PyQt.QtCore import Qt as _Qt
except ImportError:
    try:
        from PyQt6.QtCore import Qt as _Qt  # type: ignore[no-redef]
    except ImportError:
        from PyQt5.QtCore import Qt as _Qt  # type: ignore[no-redef]


def _r(qt6_getter, qt5_getter):
    """Resolve a Qt enum constant: try Qt6 scoped form, fall back to Qt5 flat."""
    try:
        return qt6_getter()
    except AttributeError:
        return qt5_getter()


class _QtCompat:
    """Namespace of Qt enum constants compatible with both Qt5 and Qt6."""

    # --- Alignment -------------------------------------------------------
    AlignCenter = _r(lambda: _Qt.AlignmentFlag.AlignCenter, lambda: _Qt.AlignCenter)
    AlignLeft   = _r(lambda: _Qt.AlignmentFlag.AlignLeft,   lambda: _Qt.AlignLeft)
    AlignRight  = _r(lambda: _Qt.AlignmentFlag.AlignRight,  lambda: _Qt.AlignRight)

    # --- Cursor ----------------------------------------------------------
    PointingHandCursor = _r(lambda: _Qt.CursorShape.PointingHandCursor,
                            lambda: _Qt.PointingHandCursor)

    # --- Mouse buttons ---------------------------------------------------
    LeftButton  = _r(lambda: _Qt.MouseButton.LeftButton,  lambda: _Qt.LeftButton)
    RightButton = _r(lambda: _Qt.MouseButton.RightButton, lambda: _Qt.RightButton)

    # --- Dock areas ------------------------------------------------------
    LeftDockWidgetArea  = _r(lambda: _Qt.DockWidgetArea.LeftDockWidgetArea,
                             lambda: _Qt.LeftDockWidgetArea)
    RightDockWidgetArea = _r(lambda: _Qt.DockWidgetArea.RightDockWidgetArea,
                             lambda: _Qt.RightDockWidgetArea)

    # --- Keys ------------------------------------------------------------
    Key_Return = _r(lambda: _Qt.Key.Key_Return, lambda: _Qt.Key_Return)
    Key_Enter  = _r(lambda: _Qt.Key.Key_Enter,  lambda: _Qt.Key_Enter)

    # --- Keyboard modifiers ----------------------------------------------
    ShiftModifier = _r(lambda: _Qt.KeyboardModifier.ShiftModifier,
                       lambda: _Qt.ShiftModifier)

    # --- Text format / interaction ---------------------------------------
    RichText               = _r(lambda: _Qt.TextFormat.RichText,
                                lambda: _Qt.RichText)
    TextBrowserInteraction = _r(lambda: _Qt.TextInteractionFlag.TextBrowserInteraction,
                                lambda: _Qt.TextBrowserInteraction)


QtC = _QtCompat()
