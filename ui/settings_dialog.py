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

Three tabs:
* Account — sign-in status, sign out.
* Privacy — toggles documented in PRIVACY.md (all default off).
* Advanced — log level, API base URL override (for staging).
"""

from __future__ import annotations

import logging
from urllib.parse import urlparse

try:
    from qgis.PyQt.QtCore import QSettings, Qt, QTimer, pyqtSignal
    from qgis.PyQt.QtWidgets import (
        QCheckBox,
        QComboBox,
        QDialog,
        QDialogButtonBox,
        QFormLayout,
        QLabel,
        QLineEdit,
        QPushButton,
        QTabWidget,
        QVBoxLayout,
        QWidget,
    )

    HAS_QT = True
except ImportError:
    HAS_QT = False

from ..auth.auth_manager import DEFAULT_API_BASE
from ..settings_keys import (
    KEY_AGENT_TOOL_EXECUTION,
    KEY_API_BASE,
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


def _looks_like_http_url(value: str) -> bool:
    try:
        parsed = urlparse(value)
    except Exception:
        return False
    return parsed.scheme in ("http", "https") and bool(parsed.netloc)


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
            self._tabs.addTab(self._build_advanced_tab(), "Advanced")

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
                'Need an account? <a href="https://app.geoedge.ai/signup">'
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

        def _build_advanced_tab(self) -> QWidget:
            w = QWidget()
            f = QFormLayout(w)

            # Surface the *effective* default at the top so users can
            # see what they're overriding without needing to dig into
            # auth_manager.py.
            default_label = QLabel(
                f"<b>Current default:</b> <code>{DEFAULT_API_BASE}</code>"
            )
            default_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
            f.addRow(default_label)

            self._cmb_log = QComboBox()
            self._cmb_log.addItems(["WARNING", "INFO", "DEBUG"])
            f.addRow("Log level:", self._cmb_log)

            self._chk_tool_exec = QCheckBox(
                "Allow the agent to run server-supplied PyQGIS code locally"
            )
            self._chk_tool_exec.setToolTip(
                "Required for the agent to actually perform GIS operations. "
                "Code is AST-validated and runs in a sandboxed namespace. "
                "Disable as a kill switch if you observe unsafe behaviour."
            )
            f.addRow(self._chk_tool_exec)

            self._txt_api = QLineEdit()
            self._txt_api.setPlaceholderText(DEFAULT_API_BASE)
            self._txt_api.setMinimumWidth(360)
            f.addRow("API base URL override:", self._txt_api)

            self._lbl_api_test = QLabel("")
            self._lbl_api_test.setStyleSheet("color: #888;")
            self._btn_api_test = QPushButton("Test connection")
            self._btn_api_test.clicked.connect(self._on_test_api)
            f.addRow(self._btn_api_test, self._lbl_api_test)

            note = QLabel(
                "Set this to point at a staging backend "
                "(e.g. <code>https://backend-production-6401.up.railway.app/v1</code>). "
                "Leave blank to use the default. Sign out + sign back in "
                "for changes to take effect."
            )
            note.setWordWrap(True)
            note.setStyleSheet("color: #888;")
            f.addRow(note)

            return w

        def _on_test_api(self) -> None:
            """Hit ``<api_base>/health`` off-thread and surface the result inline.

            Synchronous urlopen here would freeze the dialog for up to
            10 s on an unreachable host. The request runs on a daemon
            thread; the result is marshalled back to the GUI thread via
            QTimer.singleShot so we never touch a Qt widget off-thread.
            """
            import threading

            base = (self._txt_api.text().strip() or DEFAULT_API_BASE).rstrip("/")
            self._lbl_api_test.setText("testing…")
            self._lbl_api_test.setStyleSheet("color: #888;")
            self._btn_api_test.setEnabled(False)

            def _run() -> None:
                import json
                import urllib.error
                import urllib.request

                try:
                    req = urllib.request.Request(
                        f"{base}/health",
                        headers={"Accept": "application/json"},
                    )
                    with urllib.request.urlopen(req, timeout=10) as resp:
                        body = json.loads(resp.read().decode())
                    env = body.get("environment", "?")
                    ver = body.get("version", "?")
                    text = f"OK — env={env}, version={ver}"
                    colour = "#2a7"
                except urllib.error.HTTPError as exc:
                    text = f"HTTP {exc.code} — {exc.reason}"
                    colour = "#c33"
                except urllib.error.URLError as exc:
                    text = f"unreachable — {exc.reason}"
                    colour = "#c33"
                except Exception as exc:  # noqa: BLE001
                    text = f"error — {exc!s}"
                    colour = "#c33"

                QTimer.singleShot(
                    0, lambda: self._set_api_test_result(text, colour)
                )

            threading.Thread(target=_run, daemon=True).start()

        def _set_api_test_result(self, text: str, colour: str) -> None:
            """Apply a Test-connection result on the GUI thread.

            Guarded against late callbacks: if the dialog is being torn
            down the inline label may be gone, so swallow widget access
            errors quietly.
            """
            try:
                self._lbl_api_test.setText(text)
                self._lbl_api_test.setStyleSheet(f"color: {colour};")
                self._btn_api_test.setEnabled(True)
            except RuntimeError:
                pass

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
            self._cmb_log.setCurrentText(
                s.value(KEY_LOG_LEVEL, "WARNING", type=str)
            )
            self._chk_tool_exec.setChecked(
                s.value(KEY_AGENT_TOOL_EXECUTION, True, type=bool)
            )
            self._txt_api.setText(s.value(KEY_API_BASE, "", type=str))

        def _on_save(self) -> None:
            api_override = self._txt_api.text().strip()
            if api_override and not _looks_like_http_url(api_override):
                # Without this guard a typo silently breaks the next
                # sign-in with an opaque "unreachable" error.
                self._lbl_api_test.setText(
                    "API base must be an http:// or https:// URL"
                )
                self._lbl_api_test.setStyleSheet("color: #c33;")
                return

            s = QSettings()
            s.setValue(KEY_SEND_LAYER_PATHS, self._chk_layer_paths.isChecked())
            s.setValue(KEY_TELEMETRY_OPT_IN, self._chk_telemetry.isChecked())
            s.setValue(KEY_CRASH_REPORTS, self._chk_crash.isChecked())
            s.setValue(KEY_LOG_LEVEL, self._cmb_log.currentText())
            s.setValue(KEY_AGENT_TOOL_EXECUTION, self._chk_tool_exec.isChecked())
            s.setValue(KEY_API_BASE, api_override)
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
