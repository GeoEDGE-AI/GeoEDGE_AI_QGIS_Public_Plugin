# SPDX-License-Identifier: GPL-2.0-or-later
# Copyright (C) 2024-2026 GeoEdge AI
#
# This file is part of the GeoEdge AI QGIS plugin.
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 2 of the License, or
# (at your option) any later version.
# See the COPYING file or <https://www.gnu.org/licenses/> for details.
"""Login dialog for GeoEdge AI cloud sign-in."""

from __future__ import annotations

import logging
import webbrowser

from .._qt_compat import QtC, QLineEditC

try:
    from qgis.PyQt.QtCore import Qt, pyqtSignal
    from qgis.PyQt.QtWidgets import (
        QCheckBox,
        QDialog,
        QHBoxLayout,
        QLabel,
        QLineEdit,
        QPushButton,
        QVBoxLayout,
        QWidget,
    )

    HAS_QT = True
except ImportError:
    HAS_QT = False

logger = logging.getLogger(__name__)

_COLORS = {
    "bg": "#1e1e2e",
    "surface": "#282840",
    "surface_alt": "#313148",
    "primary": "#7c6ff0",
    "primary_hover": "#6a5cd8",
    "text": "#e0e0e0",
    "text_dim": "#8888aa",
    "error": "#e05050",
    "border": "#3a3a5c",
    "link": "#8a9ff0",
}

_LOGIN_CSS = f"""
QDialog {{
    background-color: {_COLORS['bg']};
    color: {_COLORS['text']};
    font-family: "Segoe UI", "Inter", sans-serif;
    font-size: 13px;
}}
QLineEdit {{
    background-color: {_COLORS['surface_alt']};
    color: {_COLORS['text']};
    border: 1px solid {_COLORS['border']};
    border-radius: 6px;
    padding: 8px 10px;
    font-size: 13px;
    min-height: 28px;
}}
QLineEdit:focus {{
    border-color: {_COLORS['primary']};
}}
QPushButton {{
    background-color: {_COLORS['primary']};
    color: #ffffff;
    border: none;
    border-radius: 6px;
    padding: 8px 20px;
    font-weight: bold;
    font-size: 13px;
    min-height: 32px;
}}
QPushButton:hover {{
    background-color: {_COLORS['primary_hover']};
}}
QCheckBox {{
    color: {_COLORS['text_dim']};
    spacing: 6px;
}}
QLabel {{
    color: {_COLORS['text']};
    background: transparent;
}}
"""

_GEOEDGE_SIGNUP_URL = "https://public.geoedge.com.au/auth/register"
_GEOEDGE_FORGOT_URL = "https://public.geoedge.com.au/auth/forgot-password"
# Marketing site shown in the login-dialog footer. The dashboard URLs
# above (`app.geoedge.ai`) are auth-gated; this one is the public
# landing page where users can read about the full platform.
_GEOEDGE_MARKETING_URL = "https://www.geoedge.com.au/"
_GEOEDGE_MARKETING_DOMAIN = "geoedge.com.au"   # what we render in the UI


if HAS_QT:

    class _LinkLabel(QLabel):
        clicked = pyqtSignal()

        def __init__(self, text: str, parent: QWidget | None = None) -> None:
            super().__init__(text, parent)
            self.setStyleSheet(
                f"color: {_COLORS['link']}; font-size: 12px; "
                "text-decoration: underline;"
            )
            self.setCursor(QtC.PointingHandCursor)

        def mousePressEvent(self, event):  # type: ignore[override]
            if event.button() == QtC.LeftButton:
                self.clicked.emit()
            super().mousePressEvent(event)

    class GeoEdgeLoginDialog(QDialog):
        """Login dialog for GeoEdge AI authentication.

        Signals
        -------
        login_requested(str, str)
            Emitted with (email, password) when the user clicks Login.
        oauth_requested(str)
            Emitted with provider name (e.g. "google") when the user
            clicks an OAuth button.
        """

        login_requested = pyqtSignal(str, str)
        oauth_requested = pyqtSignal(str)

        def __init__(self, parent: QWidget | None = None) -> None:
            super().__init__(parent)
            self.setWindowTitle("GeoEdge AI — Sign in")
            self.setFixedSize(380, 452)
            self.setStyleSheet(_LOGIN_CSS)
            self._build_ui()

        def _build_ui(self) -> None:
            layout = QVBoxLayout(self)
            layout.setContentsMargins(30, 24, 30, 24)
            layout.setSpacing(10)

            # ---- free-to-use banner ----
            # First thing every signed-out user sees. Nudges toward account
            # creation before they hit the sign-in form below.
            banner = QLabel("Free to register and use")
            banner.setAlignment(QtC.AlignCenter)
            banner.setStyleSheet(
                f"background-color: {_COLORS['primary']}; color: #ffffff; "
                "font-size: 12px; font-weight: bold; border-radius: 6px; "
                "padding: 6px 10px;"
            )
            layout.addWidget(banner)

            title = QLabel("GeoEdge AI")
            title.setAlignment(QtC.AlignCenter)
            title.setStyleSheet(
                f"color: {_COLORS['primary']}; font-size: 22px; font-weight: bold;"
            )
            layout.addWidget(title)

            subtitle = QLabel("Sign in to your account")
            subtitle.setAlignment(QtC.AlignCenter)
            subtitle.setStyleSheet(f"color: {_COLORS['text_dim']}; font-size: 13px;")
            layout.addWidget(subtitle)

            layout.addSpacing(12)

            lbl_email = QLabel("Email")
            lbl_email.setStyleSheet("font-size: 12px; font-weight: bold;")
            layout.addWidget(lbl_email)
            self._txt_email = QLineEdit()
            self._txt_email.setPlaceholderText("you@example.com")
            layout.addWidget(self._txt_email)

            lbl_pass = QLabel("Password")
            lbl_pass.setStyleSheet("font-size: 12px; font-weight: bold;")
            layout.addWidget(lbl_pass)
            self._txt_password = QLineEdit()
            self._txt_password.setEchoMode(QLineEditC.Password)
            self._txt_password.setPlaceholderText("Enter your password")
            self._txt_password.returnPressed.connect(self._on_login)
            layout.addWidget(self._txt_password)

            row = QHBoxLayout()
            self._chk_remember = QCheckBox("Remember me")
            row.addWidget(self._chk_remember)
            row.addStretch()
            lnk_forgot = _LinkLabel("Forgot password?")
            lnk_forgot.clicked.connect(self._on_forgot)
            row.addWidget(lnk_forgot)
            layout.addLayout(row)

            self._lbl_error = QLabel("")
            self._lbl_error.setAlignment(QtC.AlignCenter)
            self._lbl_error.setWordWrap(True)
            self._lbl_error.setStyleSheet(
                f"color: {_COLORS['error']}; font-size: 12px; min-height: 20px;"
            )
            self._lbl_error.setVisible(False)
            layout.addWidget(self._lbl_error)

            self._btn_login = QPushButton("Sign in")
            self._btn_login.clicked.connect(self._on_login)
            layout.addWidget(self._btn_login)

            self._btn_google = QPushButton("Sign in with Google")
            self._btn_google.setStyleSheet(
                f"background-color: {_COLORS['surface_alt']}; color: {_COLORS['text']}; "
                f"border: 1px solid {_COLORS['border']};"
            )
            self._btn_google.clicked.connect(lambda: self._on_oauth("google"))
            layout.addWidget(self._btn_google)

            self._btn_create = QPushButton("Create Account")
            self._btn_create.setStyleSheet(
                f"background-color: {_COLORS['surface_alt']}; color: {_COLORS['text']}; "
                f"border: 1px solid {_COLORS['border']};"
            )
            self._btn_create.clicked.connect(self._on_create_account)
            layout.addWidget(self._btn_create)

            # ---- marketing footer ----
            # Visible to every signed-out user. Same _LinkLabel /
            # webbrowser.open pattern as "Forgot password?" so style
            # and click behaviour are consistent with the rest of the
            # dialog. Pinned to the bottom of the content area by the
            # final addStretch() below.
            layout.addSpacing(8)
            marketing_row = QHBoxLayout()
            marketing_row.addStretch()
            lbl_marketing_lead = QLabel("For full features and capabilities, visit ")
            lbl_marketing_lead.setStyleSheet(
                f"color: {_COLORS['text_dim']}; font-size: 12px;"
            )
            marketing_row.addWidget(lbl_marketing_lead)
            lnk_marketing = _LinkLabel(_GEOEDGE_MARKETING_DOMAIN)
            lnk_marketing.clicked.connect(self._on_marketing_link)
            marketing_row.addWidget(lnk_marketing)
            marketing_row.addStretch()
            layout.addLayout(marketing_row)

            layout.addStretch()

        def show_error(self, message: str) -> None:
            self._lbl_error.setText(message)
            self._lbl_error.setVisible(bool(message))

        def clear_error(self) -> None:
            self._lbl_error.setText("")
            self._lbl_error.setVisible(False)

        def remember_me(self) -> bool:
            return self._chk_remember.isChecked()

        def set_loading(self, loading: bool) -> None:
            self._btn_login.setEnabled(not loading)
            self._btn_google.setEnabled(not loading)

        def set_email(self, email: str) -> None:
            self._txt_email.setText(email)

        def _on_login(self) -> None:
            email = self._txt_email.text().strip()
            password = self._txt_password.text()
            if not email:
                self.show_error("Please enter your email address.")
                return
            if not password:
                self.show_error("Please enter your password.")
                return
            self.clear_error()
            # Disable BOTH buttons — otherwise the user can fire an OAuth
            # flow while an email login is mid-flight and end up with
            # two parallel auth attempts racing each other.
            self.set_loading(True)
            self.login_requested.emit(email, password)

        def _on_oauth(self, provider: str) -> None:
            if provider not in ("google", "microsoft"):
                logger.warning("Refusing unsupported OAuth provider: %s", provider)
                return
            self.clear_error()
            self.set_loading(True)
            self.oauth_requested.emit(provider)

        def _on_create_account(self) -> None:
            try:
                webbrowser.open(_GEOEDGE_SIGNUP_URL)
            except Exception:
                logger.warning("Could not open browser for account creation.")

        def _on_forgot(self) -> None:
            try:
                webbrowser.open(_GEOEDGE_FORGOT_URL)
            except Exception:
                logger.warning("Could not open browser for password reset.")

        def _on_marketing_link(self) -> None:
            try:
                webbrowser.open(_GEOEDGE_MARKETING_URL)
            except Exception:
                logger.warning("Could not open browser for marketing site.")

else:

    class GeoEdgeLoginDialog:  # type: ignore[no-redef]
        """Stub — PyQt5 not available outside QGIS."""

        def __init__(self, *args, **kwargs):
            raise RuntimeError(
                "GeoEdgeLoginDialog requires PyQt5 (run inside QGIS)."
            )
