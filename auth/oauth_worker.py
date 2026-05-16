# SPDX-License-Identifier: GPL-2.0-or-later
# Copyright (C) 2024-2026 GeoEdge AI
#
# This file is part of the GeoEdge AI QGIS plugin.
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 2 of the License, or
# (at your option) any later version.
# See the COPYING file or <https://www.gnu.org/licenses/> for details.
"""``QThread`` wrapper around :meth:`AuthManager.login_via_browser`.

The browser-OAuth flow runs an embedded ``HTTPServer`` and waits for the
provider redirect. Doing that on the GUI thread freezes QGIS for up to
five minutes if the user abandons the browser tab. This worker pushes
the wait off-thread and surfaces completion via Qt signals.
"""

from __future__ import annotations

import logging
import threading
from typing import Any

try:
    from qgis.PyQt.QtCore import QObject, QThread, pyqtSignal

    HAS_QT = True
except ImportError:
    HAS_QT = False
    QThread = object  # type: ignore[misc,assignment]
    QObject = object  # type: ignore[misc,assignment]

logger = logging.getLogger(__name__)


if HAS_QT:

    class OAuthLoginWorker(QThread):
        """Run :meth:`AuthManager.login_via_browser` off the GUI thread.

        Signals
        -------
        completed(str)
            Provider name on successful sign-in.
        failed(str)
            Human-readable failure message (timeout, network, etc.).
        """

        completed = pyqtSignal(str)
        failed = pyqtSignal(str)

        def __init__(
            self,
            auth_manager: Any,
            provider: str,
            parent: QObject | None = None,
        ) -> None:
            super().__init__(parent)
            self._auth_manager = auth_manager
            self._provider = provider
            self._cancel_event = threading.Event()

        def cancel(self) -> None:
            """Abort the OAuth wait promptly.

            ``QThread.requestInterruption`` can't reach the inner
            ``done_event.wait`` — we plumb a real ``threading.Event``
            through ``login_via_browser`` instead so plugin unload
            during sign-in doesn't leak a thread holding a stale
            ``auth_manager`` reference.
            """
            self._cancel_event.set()

        def run(self) -> None:
            try:
                token = self._auth_manager.login_via_browser(
                    self._provider, cancel_event=self._cancel_event
                )
            except Exception as exc:  # broad — surfaces to the UI either way
                logger.warning("OAuth worker error: %s", exc)
                if not self._cancel_event.is_set():
                    self.failed.emit(str(exc))
                return
            if self._cancel_event.is_set():
                # Caller already moved on — don't fire stale signals.
                return
            if token:
                self.completed.emit(self._provider)
            else:
                self.failed.emit("Browser sign-in did not complete.")

    class EmailLoginWorker(QThread):
        """Run :meth:`AuthManager.login` off the GUI thread.

        Originally the email/password flow used a plain Python daemon
        thread + ``QTimer.singleShot(0, _on_result)`` to marshal the
        result back to the GUI. That pattern silently dropped the
        callback on some QGIS/PyQt5 builds — the login succeeded
        server-side, tokens were stored, but the dialog never closed
        because ``_on_result`` was never invoked. Switching to a
        :class:`QObject`-based worker with ``pyqtSignal`` makes the
        cross-thread delivery use Qt's queued connection machinery,
        which is reliable on every supported platform.

        Signals
        -------
        completed()
            Emitted on successful sign-in.
        failed(str)
            Human-readable failure message.
        """

        completed = pyqtSignal()
        failed = pyqtSignal(str)

        def __init__(
            self,
            auth_manager: Any,
            email: str,
            password: str,
            parent: QObject | None = None,
        ) -> None:
            super().__init__(parent)
            self._auth_manager = auth_manager
            self._email = email
            self._password = password
            self._cancel_event = threading.Event()

        def cancel(self) -> None:
            self._cancel_event.set()

        def run(self) -> None:
            try:
                self._auth_manager.login(self._email, self._password)
            except Exception as exc:  # broad — surfaces to the UI either way
                if self._cancel_event.is_set():
                    return
                logger.info("Email login worker error: %s", exc)
                self.failed.emit(str(exc) or "Login failed.")
                return
            if self._cancel_event.is_set():
                return
            self.completed.emit()

else:

    class OAuthLoginWorker:  # type: ignore[no-redef]
        """Stub — PyQt5 not available outside QGIS."""

        def __init__(self, *args, **kwargs):
            raise RuntimeError(
                "OAuthLoginWorker requires PyQt5 (run inside QGIS)."
            )

    class EmailLoginWorker:  # type: ignore[no-redef]
        """Stub — PyQt5 not available outside QGIS."""

        def __init__(self, *args, **kwargs):
            raise RuntimeError(
                "EmailLoginWorker requires PyQt5 (run inside QGIS)."
            )
