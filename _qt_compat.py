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

In Qt5 (QGIS 3.x), enum members are flat attributes on their class:
    Qt.AlignCenter, QFont.Normal, QLineEdit.Password, ...

In Qt6 (QGIS 4.x / PyQt6), they live under scoped enum sub-classes:
    Qt.AlignmentFlag.AlignCenter, QFont.Weight.Normal, QLineEdit.EchoMode.Password, ...

The ``qgis.PyQt`` shim does not bridge this scoping change automatically,
so this module resolves every constant we use once at import time and
exposes them as plain attributes.  Import and use as::

    from ._qt_compat import QtC, QFontC, QLineEditC, QDialogButtonBoxC
    from .._qt_compat import QtC, QFontC, QLineEditC, QDialogButtonBoxC

    widget.setAlignment(QtC.AlignCenter)
    QFont("Segoe UI", 12, QFontC.Normal)
"""

from __future__ import annotations

try:
    from qgis.PyQt.QtCore import Qt as _Qt
except ImportError:
    try:
        from PyQt6.QtCore import Qt as _Qt  # type: ignore[no-redef]
    except ImportError:
        from PyQt5.QtCore import Qt as _Qt  # type: ignore[no-redef]

try:
    from qgis.PyQt.QtGui import QFont as _QFont
except ImportError:
    try:
        from PyQt6.QtGui import QFont as _QFont  # type: ignore[no-redef]
    except ImportError:
        from PyQt5.QtGui import QFont as _QFont  # type: ignore[no-redef]

try:
    from qgis.PyQt.QtWidgets import QLineEdit as _QLineEdit, QDialogButtonBox as _QDialogButtonBox
except ImportError:
    try:
        from PyQt6.QtWidgets import QLineEdit as _QLineEdit, QDialogButtonBox as _QDialogButtonBox  # type: ignore[no-redef]
    except ImportError:
        from PyQt5.QtWidgets import QLineEdit as _QLineEdit, QDialogButtonBox as _QDialogButtonBox  # type: ignore[no-redef]


def _r(qt6_getter, qt5_getter):
    """Resolve a Qt enum constant: try Qt6 scoped form, fall back to Qt5 flat."""
    try:
        return qt6_getter()
    except AttributeError:
        return qt5_getter()


class _QtCompat:
    """Namespace of Qt.* enum constants compatible with both Qt5 and Qt6."""

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


class _QFontCompat:
    """Namespace of QFont.* enum constants compatible with both Qt5 and Qt6."""

    # --- Font weight (Qt6: QFont.Weight.*) --------------------------------
    Thin       = _r(lambda: _QFont.Weight.Thin,       lambda: _QFont.Thin)
    ExtraLight = _r(lambda: _QFont.Weight.ExtraLight,  lambda: _QFont.ExtraLight)
    Light      = _r(lambda: _QFont.Weight.Light,       lambda: _QFont.Light)
    Normal     = _r(lambda: _QFont.Weight.Normal,      lambda: _QFont.Normal)
    Medium     = _r(lambda: _QFont.Weight.Medium,      lambda: _QFont.Medium)
    DemiBold   = _r(lambda: _QFont.Weight.DemiBold,    lambda: _QFont.DemiBold)
    Bold       = _r(lambda: _QFont.Weight.Bold,        lambda: _QFont.Bold)
    ExtraBold  = _r(lambda: _QFont.Weight.ExtraBold,   lambda: _QFont.ExtraBold)
    Black      = _r(lambda: _QFont.Weight.Black,       lambda: _QFont.Black)


class _QLineEditCompat:
    """Namespace of QLineEdit.* enum constants compatible with both Qt5 and Qt6."""

    # --- Echo mode (Qt6: QLineEdit.EchoMode.*) ----------------------------
    Normal          = _r(lambda: _QLineEdit.EchoMode.Normal,         lambda: _QLineEdit.Normal)
    Password        = _r(lambda: _QLineEdit.EchoMode.Password,       lambda: _QLineEdit.Password)
    NoEcho          = _r(lambda: _QLineEdit.EchoMode.NoEcho,         lambda: _QLineEdit.NoEcho)
    PasswordEchoOnEdit = _r(lambda: _QLineEdit.EchoMode.PasswordEchoOnEdit,
                            lambda: _QLineEdit.PasswordEchoOnEdit)


class _QDialogButtonBoxCompat:
    """Namespace of QDialogButtonBox.* enum constants compatible with both Qt5 and Qt6."""

    # --- Standard buttons (Qt6: QDialogButtonBox.StandardButton.*) --------
    Ok     = _r(lambda: _QDialogButtonBox.StandardButton.Ok,     lambda: _QDialogButtonBox.Ok)
    Cancel = _r(lambda: _QDialogButtonBox.StandardButton.Cancel, lambda: _QDialogButtonBox.Cancel)
    Save   = _r(lambda: _QDialogButtonBox.StandardButton.Save,   lambda: _QDialogButtonBox.Save)
    Close  = _r(lambda: _QDialogButtonBox.StandardButton.Close,  lambda: _QDialogButtonBox.Close)
    Yes    = _r(lambda: _QDialogButtonBox.StandardButton.Yes,    lambda: _QDialogButtonBox.Yes)
    No     = _r(lambda: _QDialogButtonBox.StandardButton.No,     lambda: _QDialogButtonBox.No)


QtC               = _QtCompat()
QFontC            = _QFontCompat()
QLineEditC        = _QLineEditCompat()
QDialogButtonBoxC = _QDialogButtonBoxCompat()
