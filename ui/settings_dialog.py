# SPDX-License-Identifier: GPL-2.0-or-later
# Copyright (C) 2024-2026 GeoEdge AI
#
# This file is part of the GeoEdge AI QGIS plugin.
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 2 of the License, or
# (at your option) any later version.
# See the COPYING file or <https://www.gnu.org/licenses/> for details.
"""Settings dialog for the public thin client.

Two tabs:
* Account — sign-in status, sign out.
* Privacy — toggles documented in PRIVACY.md (all default off).
"""

from __future__ import annotations

import logging

try:
    from qgis.PyQt.QtCore import QSettings, Qt, pyqtSignal
    from qgis.PyQt.QtWidgets import (
        QCheckBox,
        QDialog,
        QDialogButtonBox,
        QLabel,
        QPushButton,
        QTabWidget,
        QVBoxLayout,
        QWidget,
    )

    HAS_QT = True
except ImportError:
    HAS_QT = False

from ..settings_keys import (
    KEY_CRASH_REPORTS,
    KEY_LOG_LEVEL,
    KEY_SEND_LAYER_PATHS,
    KEY_TELEMETRY_OPT_IN,
)

logger = logging.getLogger(__name__)


def apply_log_level() -> None:
    """Read the configured log level and apply it to the geoedge_ai logger.

    Called at plugin startup and whenever the user saves the settings
    dialog. Without this, the log-level setting is collected but never
    has any effect.
    """
    if not HAS_QT:
        return
    s = QSettings()
    name = s.value(KEY_LOG_LEVEL, "WARNING", type=str)
    level = getattr(logging, name.upper(), logging.WARNING)
    logging.getLogger("geoedge_ai").setLevel(level)


if HAS_QT:

    class GeoEdgeSettingsDialog(QDialog):
        """Settings dialog. Persists to ``QSettings``.

        Emits ``logout_requested`` when the user clicks "Sign out", and
        ``sign_in_requested`` when the user clicks "Sign in" from the
        signed-out Account tab.
        """

        logout_requested = pyqtSignal()
        sign_in_requested = pyqtSignal()

        def __init__(
            self,
            *,
            user_email: str | None = None,
            parent: QWidget | None = None,
        ) -> None:
            super().__init__(parent)
            self.setWindowTitle("GeoEdge AI — Settings")
            self.setMinimumSize(480, 420)
            self._user_email = user_email
            self._build_ui()
            self._load_state()

        def _build_ui(self) -> None:
            outer = QVBoxLayout(self)
            self._tabs = QTabWidget()
            outer.addWidget(self._tabs)

            self._tabs.addTab(self._build_account_tab(), "Account")
            self._tabs.addTab(self._build_privacy_tab(), "Privacy")

            buttons = QDialogButtonBox(
                QDialogButtonBox.Save | QDialogButtonBox.Cancel
            )
            buttons.accepted.connect(self._on_save)
            buttons.rejected.connect(self.reject)
            outer.addWidget(buttons)

        def _build_account_tab(self) -> QWidget:
            w = QWidget()
            v = QVBoxLayout(w)

            if self._user_email:
                v.addWidget(QLabel(f"Signed in as: <b>{self._user_email}</b>"))
                btn_signout = QPushButton("Sign out")
                btn_signout.clicked.connect(self._on_signout)
                v.addWidget(btn_signout, alignment=Qt.AlignLeft)
            else:
                v.addWidget(QLabel("Not signed in."))
                btn_signin = QPushButton("Sign in")
                btn_signin.clicked.connect(self._on_signin)
                v.addWidget(btn_signin, alignment=Qt.AlignLeft)

            link = QLabel(
                'Need an account? <a href="https://public.geoedge.com.au/auth/register">'
                "Sign up for the free starter plan</a>."
            )
            link.setOpenExternalLinks(True)
            v.addWidget(link)

            v.addStretch()
            return w

        def _build_privacy_tab(self) -> QWidget:
            w = QWidget()
            v = QVBoxLayout(w)

            blurb = QLabel(
                "All toggles are off by default. See <a href=\"https://github.com/"
                "GeoEDGE-AI/geoedge-qgis-plugin/blob/main/PRIVACY.md\">"
                "PRIVACY.md</a> for the full data inventory."
            )
            blurb.setWordWrap(True)
            blurb.setOpenExternalLinks(True)
            v.addWidget(blurb)

            self._chk_layer_paths = QCheckBox(
                "Include layer source file paths in metadata (recommended — required for buffer, reproject, and other geoprocessing operations)"
            )
            v.addWidget(self._chk_layer_paths)

            self._chk_telemetry = QCheckBox(
                "Anonymous usage telemetry (event names + timing only)"
            )
            v.addWidget(self._chk_telemetry)

            self._chk_crash = QCheckBox(
                "Crash reports (Python tracebacks, PII redacted)"
            )
            v.addWidget(self._chk_crash)

            v.addStretch()
            return w

        def _load_state(self) -> None:
            s = QSettings()
            # Default True — the backend requires layer source paths for
            # operations that involve code generation (e.g. buffer/reproject).
            # Disabling this may prevent such operations from completing.
            self._chk_layer_paths.setChecked(
                s.value(KEY_SEND_LAYER_PATHS, True, type=bool)
            )
            self._chk_telemetry.setChecked(
                s.value(KEY_TELEMETRY_OPT_IN, False, type=bool)
            )
            self._chk_crash.setChecked(
                s.value(KEY_CRASH_REPORTS, False, type=bool)
            )

        def _on_save(self) -> None:
            s = QSettings()
            s.setValue(KEY_SEND_LAYER_PATHS, self._chk_layer_paths.isChecked())
            s.setValue(KEY_TELEMETRY_OPT_IN, self._chk_telemetry.isChecked())
            s.setValue(KEY_CRASH_REPORTS, self._chk_crash.isChecked())
            apply_log_level()
            self.accept()

        def _on_signout(self) -> None:
            self.logout_requested.emit()
            self.accept()

        def _on_signin(self) -> None:
            # Closing the dialog before the login flow runs avoids a
            # stacked-modal mess; the plugin re-opens the login dialog
            # after this one returns from exec_().
            self.sign_in_requested.emit()
            self.accept()

else:

    class GeoEdgeSettingsDialog:  # type: ignore[no-redef]
        """Stub — PyQt5 not available outside QGIS."""

        def __init__(self, *args, **kwargs):
            raise RuntimeError(
                "GeoEdgeSettingsDialog requires PyQt5 (run inside QGIS)."
            )
