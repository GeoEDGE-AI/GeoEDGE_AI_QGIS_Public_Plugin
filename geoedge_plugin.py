# SPDX-License-Identifier: GPL-2.0-or-later
# Copyright (C) 2024-2026 GeoEdge AI
#
# This file is part of the GeoEdge AI QGIS plugin.
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 2 of the License, or
# (at your option) any later version.
# See the COPYING file or <https://www.gnu.org/licenses/> for details.
"""GeoEdge AI — main plugin class (thin client).

Wires the dock-widget UI to the cloud agent's SSE stream. The agent
itself runs on ``api.geoedge.ai``; this class:

* registers the toolbar action and dock widget,
* mediates sign-in / sign-out via :class:`AuthManager`,
* opens a :class:`AgentStreamClient` per turn and dispatches SSE events
  (plan, message, need_approval, usage, …) to the chat UI,
* sends user approvals and cancel requests back to the server.
"""

from __future__ import annotations

import logging
import os
from typing import Any

from ._qt_compat import QtC, QMessageBoxC

logger = logging.getLogger(__name__)

try:
    from qgis.core import QgsApplication, QgsMapLayer, QgsProject, QgsWkbTypes
    from qgis.PyQt.QtCore import QSettings, Qt
    from qgis.PyQt.QtGui import QIcon
    from qgis.PyQt.QtWidgets import (
        QAction,
        QDockWidget,
        QMessageBox,
    )

    _HAS_QGIS = True
except ImportError:
    _HAS_QGIS = False
    QgsApplication = None  # type: ignore[misc,assignment]
    QSettings = None  # type: ignore[misc,assignment]
    Qt = None  # type: ignore[misc,assignment]

from . import __version__
from .auth.auth_manager import DEFAULT_API_BASE, AuthManager
from .auth.exceptions import GeoEdgeAuthError
from .cloud_client import PROTOCOL_VERSION
from .cloud_client.agent_stream import AgentStreamClient
from .cloud_client.session import AgentSession
from .qgis.credentials import GeoEdgeCredentials
from .safety.code_validator import validate_code
from .settings_keys import (
    KEY_AGENT_TOOL_EXECUTION,
    KEY_API_BASE,
    KEY_SEND_LAYER_PATHS,
)

# Cap conversation history sent on every POST. Older turns are dropped.
# Roughly the last ten user/assistant pairs.
_MAX_HISTORY_MESSAGES = 20

# Cap fields transmitted per layer in the QGIS context payload. The backend
# prompt only ever shows 8 fields per layer (context_manager.py), so sending
# more than this is pure wasted bandwidth for wide attribute tables.
_MAX_FIELDS_PER_LAYER = 15


class GeoEdgePlugin:
    """Main QGIS plugin class.

    Lifecycle: ``__init__`` → ``initGui`` → ``unload``.
    """

    def __init__(self, iface: Any) -> None:
        self.iface = iface
        self.plugin_dir = os.path.dirname(os.path.abspath(__file__))
        self.menu_name = "GeoEdge AI"
        self.actions: list[Any] = []
        self.toolbar: Any = None
        self.dock_widget: Any = None
        self._chat_panel: Any = None

        self._credentials: GeoEdgeCredentials | None = None
        self._auth_manager: AuthManager | None = None
        self._session = AgentSession()
        self._stream_worker: Any = None
        self._oauth_worker: Any = None
        self._capabilities_checked = False
        # Per-session chat history sent on every /v1/agent/stream POST so
        # the server's IntentParser can resolve replies to its own
        # clarification questions in one follow-up turn. Cleared on
        # logout / new session.
        self._chat_history: list[dict[str, str]] = []

    # ------------------------------------------------------------------
    # QGIS plugin interface
    # ------------------------------------------------------------------

    def initGui(self) -> None:
        if not _HAS_QGIS:
            logger.warning("QGIS runtime not available; initGui() noop.")
            return

        # Honour the user's saved log-level preference at startup so DEBUG
        # actually produces DEBUG logs without requiring the settings
        # dialog to be re-opened.
        from .ui.settings_dialog import apply_log_level
        apply_log_level()

        self.toolbar = self.iface.addToolBar("GeoEdge AI")
        self.toolbar.setObjectName("GeoEdgeAIToolbar")

        icon_path = os.path.join(self.plugin_dir, "icons", "geoedge.png")
        icon = QIcon(icon_path) if os.path.exists(icon_path) else QIcon()

        action_toggle = QAction(icon, "GeoEdge AI", self.iface.mainWindow())
        action_toggle.setCheckable(True)
        action_toggle.triggered.connect(self._toggle_dock_widget)
        self.toolbar.addAction(action_toggle)
        self.iface.addPluginToMenu(self.menu_name, action_toggle)
        self.actions.append(action_toggle)

        action_settings = QAction("Settings…", self.iface.mainWindow())
        action_settings.triggered.connect(self._open_settings)
        self.iface.addPluginToMenu(self.menu_name, action_settings)
        self.actions.append(action_settings)

        # Core services
        self._credentials = GeoEdgeCredentials()
        self._auth_manager = AuthManager(self._credentials, base_url=self._api_base())

        self._build_dock_widget()
        self._chat_panel.set_status("Restoring session…")

        try:
            self._auth_manager.try_restore_session()
        except Exception:
            logger.debug("Session restore failed.", exc_info=True)

        self._refresh_auth_state()

    def unload(self) -> None:
        """Remove menu items, toolbar, and dock widget."""
        if not _HAS_QGIS:
            return

        self._terminate_stream_worker(wait_ms=2000)

        if self._oauth_worker is not None:
            try:
                # cancel() sets the threading.Event that login_via_browser
                # polls — requestInterruption alone never breaks the wait.
                self._oauth_worker.cancel()
                self._oauth_worker.requestInterruption()
                self._oauth_worker.wait(2000)
            except Exception:
                pass  # nosec B110 - best-effort cleanup, failure is non-fatal
            self._oauth_worker = None

        for action in self.actions:
            try:
                self.iface.removePluginMenu(self.menu_name, action)
            except Exception:
                pass  # nosec B110 - best-effort cleanup, failure is non-fatal
        self.actions.clear()

        if self.toolbar is not None:
            try:
                self.toolbar.deleteLater()
            except Exception:
                pass  # nosec B110 - best-effort cleanup, failure is non-fatal
            self.toolbar = None

        if self.dock_widget is not None:
            try:
                self.iface.removeDockWidget(self.dock_widget)
                self.dock_widget.deleteLater()
            except Exception:
                pass  # nosec B110 - best-effort cleanup, failure is non-fatal
            self.dock_widget = None

        self._chat_panel = None
        self._auth_manager = None
        self._credentials = None

    # ------------------------------------------------------------------
    # Dock widget
    # ------------------------------------------------------------------

    def _build_dock_widget(self) -> None:
        from .ui.chat_panel import GeoEdgeChatPanel

        self._chat_panel = GeoEdgeChatPanel()
        self._chat_panel.send_requested.connect(self._on_send)
        self._chat_panel.cancel_requested.connect(self._on_cancel)
        self._chat_panel.sign_in_requested.connect(self._open_login)

        self.dock_widget = QDockWidget("GeoEdge AI", self.iface.mainWindow())
        self.dock_widget.setObjectName("GeoEdgeAIDock")
        self.dock_widget.setWidget(self._chat_panel)
        self.dock_widget.setAllowedAreas(QtC.LeftDockWidgetArea | QtC.RightDockWidgetArea)
        self.iface.addDockWidget(QtC.RightDockWidgetArea, self.dock_widget)
        # Auto-prompt for sign-in whenever the dock becomes visible
        # while the user isn't authenticated. Most users won't notice
        # the small "Sign in" button below the chat log; surfacing the
        # login dialog directly makes the entry path obvious.
        self.dock_widget.visibilityChanged.connect(
            self._on_dock_visibility_changed
        )
        self.dock_widget.hide()

    def _toggle_dock_widget(self) -> None:
        if self.dock_widget is None:
            return
        self.dock_widget.setVisible(not self.dock_widget.isVisible())

    def _on_dock_visibility_changed(self, visible: bool) -> None:
        """Auto-open the login dialog the first time the user makes
        the dock visible while signed out.

        Triggers on every show transition (not just the first one) so
        users who dismiss the dialog and re-open the plugin later get
        the same prompt. Once signed in, ``is_authenticated`` returns
        True and the auto-prompt no longer fires.

        The login dialog is opened via ``QTimer.singleShot`` so the
        dock paints first; the modal then appears on top of a
        rendered dock instead of an empty pane.
        """
        if not visible:
            return
        if self._auth_manager is None or self._chat_panel is None:
            return
        if self._auth_manager.is_authenticated:
            return
        from qgis.PyQt.QtCore import QTimer
        QTimer.singleShot(0, self._open_login)

    # ------------------------------------------------------------------
    # Auth flow
    # ------------------------------------------------------------------

    def _refresh_auth_state(self) -> None:
        if self._chat_panel is None or self._auth_manager is None:
            return
        signed_in = self._auth_manager.is_authenticated
        email = None
        if signed_in and self._auth_manager.user_profile:
            email = self._auth_manager.user_profile.get("email")
        self._chat_panel.set_authenticated(signed_in, email=email)

    def _open_login(self) -> None:
        if self._auth_manager is None:
            return

        from .ui.login_dialog import GeoEdgeLoginDialog

        dlg = GeoEdgeLoginDialog(parent=self.iface.mainWindow())

        # Shared across the email-password and OAuth flows so a late
        # callback (e.g. login completes after the user clicked X) never
        # tries to drive a destroyed dialog.
        dialog_alive = {"open": True}

        # In-flight email worker, parallel slot to ``self._oauth_worker``.
        # Stored on the closure rather than ``self`` because there's only
        # one login dialog at a time and the lifetime is the dialog's.
        email_worker_holder: dict[str, Any] = {"worker": None}

        def _on_dialog_finished(_result: int) -> None:
            dialog_alive["open"] = False
            # Tear down any in-flight OAuth worker.
            worker = self._oauth_worker
            if worker is not None:
                try:
                    worker.cancel()
                except Exception:
                    pass  # nosec B110 - best-effort cleanup, failure is non-fatal
                try:
                    worker.finished.connect(worker.deleteLater)
                except Exception:
                    pass  # nosec B110 - best-effort cleanup, failure is non-fatal
                self._oauth_worker = None
            # Tear down any in-flight email worker too.
            ew = email_worker_holder.get("worker")
            if ew is not None:
                try:
                    ew.cancel()
                except Exception:
                    pass  # nosec B110 - best-effort cleanup, failure is non-fatal
                try:
                    ew.finished.connect(ew.deleteLater)
                except Exception:
                    pass  # nosec B110 - best-effort cleanup, failure is non-fatal
                email_worker_holder["worker"] = None

        dlg.finished.connect(_on_dialog_finished)

        def _on_login_requested(email: str, password: str) -> None:
            # AuthManager.login does a synchronous urlopen with a 30 s
            # timeout. Running it on the GUI thread would freeze QGIS
            # for up to 30 s on a flaky network. Push to a QThread
            # worker — the previous daemon-thread + QTimer.singleShot
            # pattern silently dropped the result on some PyQt5 builds
            # (login succeeded server-side, tokens were stored, but
            # the dialog never closed). pyqtSignal across threads is
            # delivered via Qt's queued-connection machinery, which is
            # reliable on every supported QGIS/Qt version.
            from .auth.oauth_worker import EmailLoginWorker

            worker = EmailLoginWorker(
                self._auth_manager, email, password,
                parent=self.iface.mainWindow(),
            )
            email_worker_holder["worker"] = worker

            def _on_completed() -> None:
                email_worker_holder["worker"] = None
                if not dialog_alive["open"]:
                    return
                dlg.accept()
                self._refresh_auth_state()

            def _on_failed(message: str) -> None:
                email_worker_holder["worker"] = None
                if not dialog_alive["open"]:
                    return
                dlg.show_error(message)
                dlg.set_loading(False)

            worker.completed.connect(_on_completed)
            worker.failed.connect(_on_failed)
            try:
                worker.finished.connect(worker.deleteLater)
            except Exception:
                pass  # nosec B110 - best-effort cleanup, failure is non-fatal
            worker.start()

        def _on_oauth_requested(provider: str) -> None:
            from .auth.oauth_worker import OAuthLoginWorker

            worker = OAuthLoginWorker(
                self._auth_manager, provider, parent=self.iface.mainWindow()
            )
            self._oauth_worker = worker

            def _on_completed(_provider: str) -> None:
                self._oauth_worker = None
                if not dialog_alive["open"]:
                    return
                dlg.accept()
                self._refresh_auth_state()

            def _on_failed(message: str) -> None:
                self._oauth_worker = None
                if not dialog_alive["open"]:
                    return
                dlg.show_error(message)
                dlg.set_loading(False)

            worker.completed.connect(_on_completed)
            worker.failed.connect(_on_failed)
            try:
                worker.finished.connect(worker.deleteLater)
            except Exception:
                pass  # nosec B110 - best-effort cleanup, failure is non-fatal
            worker.start()

        dlg.login_requested.connect(_on_login_requested)
        dlg.oauth_requested.connect(_on_oauth_requested)
        dlg.exec()  # exec_() removed in PyQt6/Qt6; exec() works on both

    def _on_logout(self) -> None:
        if self._auth_manager is None:
            return
        # Stop any in-flight stream first so a now-revoked token doesn't
        # keep flowing over the wire.
        self._terminate_stream_worker(wait_ms=1000)
        self._auth_manager.clear_all()
        self._session.reset()
        self._chat_history.clear()
        self._capabilities_checked = False
        # Wipe the visible transcript so the next user on a shared
        # workstation doesn't inherit the previous session's chat.
        if self._chat_panel is not None:
            self._chat_panel.clear_log()
        self._refresh_auth_state()

    # ------------------------------------------------------------------
    # Settings
    # ------------------------------------------------------------------

    def _open_settings(self) -> None:
        if self._auth_manager is None:
            return
        from .ui.settings_dialog import GeoEdgeSettingsDialog

        email = None
        if self._auth_manager.user_profile:
            email = self._auth_manager.user_profile.get("email")

        dlg = GeoEdgeSettingsDialog(user_email=email, parent=self.iface.mainWindow())
        dlg.logout_requested.connect(self._on_logout)

        # Capture the "user clicked Sign in" intent — the dialog closes
        # itself when the user clicks the button, so we re-open the
        # login dialog after exec_() returns rather than stacking two
        # modal dialogs.
        open_login_after = {"yes": False}
        dlg.sign_in_requested.connect(
            lambda: open_login_after.__setitem__("yes", True)
        )

        # Snapshot the api_base before the dialog opens so we only
        # re-handshake capabilities when the user actually changed the
        # URL. Without this, every "open settings → close" trip wastes
        # a capabilities round-trip on the next send.
        previous_api_base = self._api_base()
        dlg.exec()  # exec_() removed in PyQt6/Qt6; exec() works on both

        new_api_base = self._api_base()
        self._auth_manager.api_base = new_api_base
        if new_api_base != previous_api_base:
            self._capabilities_checked = False

        if open_login_after["yes"]:
            self._open_login()

    # ------------------------------------------------------------------
    # Send / receive
    # ------------------------------------------------------------------

    def _on_send(self, user_message: str) -> None:
        if self._auth_manager is None or not self._auth_manager.is_authenticated:
            self._chat_panel.append_system(
                "Please sign in first.", error=True
            )
            return

        self._chat_panel.append_user(user_message)
        self._chat_panel.clear_input()

        # One-shot capability handshake. Failure is non-fatal: surface a
        # warning but let the turn proceed; the stream itself will reject
        # an incompatible client with a clear error.
        self._maybe_check_capabilities()

        # Snapshot history BEFORE appending the new user message — the
        # server reads conversation_history as "everything that came
        # before the current request".
        prior_history = list(self._chat_history)
        self._chat_history.append({"role": "user", "content": user_message})
        self._trim_history()

        self._spawn_stream_worker(
            user_message=user_message,
            approval=None,
            history=prior_history,
        )

    def _submit_approval(self, step_id: str, granted: bool) -> None:
        """Resume an awaiting turn by POSTing the approval decision.

        The server pauses on ``need_approval`` and resumes when the
        client opens a new ``/agent/stream`` request carrying the
        ``approval`` payload + same ``session_id``.
        """
        if self._auth_manager is None or not self._auth_manager.is_authenticated:
            self._chat_panel.append_system(
                "Cannot send approval — not signed in.", error=True
            )
            return

        # Backend's ApprovalDecision schema requires ``step_id`` (matches
        # the ``step_id`` carried in tool_call / need_approval events).
        # Sending ``approval_id`` here yielded HTTP 422.
        self._spawn_stream_worker(
            user_message=None,
            approval={"step_id": step_id, "granted": granted},
            history=list(self._chat_history),
        )

    # ------------------------------------------------------------------
    # tool_call handling — server emits run_pyqgis, client runs it
    # locally inside a QgsTask sandbox and POSTs the observation so the
    # awaiting orchestrator coroutine can resume.
    # ------------------------------------------------------------------

    def _tool_execution_enabled(self) -> bool:
        if QSettings is None:
            return True
        return bool(QSettings().value(KEY_AGENT_TOOL_EXECUTION, True, type=bool))

    def _reply_tool_call(
        self,
        *,
        worker_ref: Any,
        ok: bool,
        message: str,
        observation: dict[str, Any] | None = None,
        elapsed_ms: int = 0,
    ) -> None:
        """Synthesize an observation and hand it back to the worker.

        Worker is currently blocked on ``threading.Event.wait`` inside
        ``_dispatch_tool_call``; emitting ``tool_call_completed`` unblocks
        it so it can POST to ``/v1/agent/observation``.
        """
        if worker_ref is None or worker_ref is not self._stream_worker:
            # Late callback from a worker we've already replaced. Drop it.
            return
        payload = {
            "ok": ok,
            "observation": {**(observation or {}), "message": message},
            "elapsed_ms": int(elapsed_ms),
        }
        try:
            worker_ref.tool_call_completed.emit(payload)
        except Exception:
            logger.exception("Failed to emit tool_call_completed.")

    def _on_tool_call(self, data: dict[str, Any], *, _worker_ref: Any = None) -> None:
        """Validate + execute a server-supplied PyQGIS step on the GUI thread."""
        # Stale signal guard — same pattern as _on_stream_error.
        if _worker_ref is not None and _worker_ref is not self._stream_worker:
            return

        params = data.get("params") or {}
        code = params.get("code", "")
        step_id = data.get("step_id", "")
        step_name = params.get("step_name") or "step"
        output_layer_name = params.get("output_layer_name")

        if not self._tool_execution_enabled():
            self._reply_tool_call(
                worker_ref=_worker_ref,
                ok=False,
                message=(
                    "Tool execution is disabled in plugin settings. "
                    "Enable it under Settings → Advanced to let the agent "
                    "run code locally."
                ),
            )
            return

        if not code or not code.strip():
            self._reply_tool_call(
                worker_ref=_worker_ref,
                ok=False,
                message=f"Step '{step_name}': empty code from server.",
            )
            return

        # Re-validate locally — defence in depth, even though the server
        # validated before emitting. If the wire is MITM'd or the server
        # is compromised this is the last gate.
        ok, err = validate_code(code)
        if not ok:
            self._reply_tool_call(
                worker_ref=_worker_ref,
                ok=False,
                message=f"Step '{step_name}' rejected by client validator: {err}",
            )
            return

        if QgsApplication is None:
            self._reply_tool_call(
                worker_ref=_worker_ref,
                ok=False,
                message="QGIS not available — cannot execute step.",
            )
            return

        # Lazy import — avoids loading qgis-only modules outside QGIS.
        from .qgis.execution_signaller import ExecutionSignaller
        from .qgis.executor import GeoEdgeTask

        if self._chat_panel is not None:
            self._chat_panel.append_system(f"Running step: {step_name}")

        signaller = ExecutionSignaller()
        step_meta = {
            "step_id": step_id,
            "step_name": step_name,
            "output_layer_name": output_layer_name,
        }
        task = GeoEdgeTask(code, step_meta, signaller=signaller)

        # Keep references on the plugin so neither the task nor the
        # signaller is garbage-collected while QgsTaskManager owns the
        # task. We retire them when execution_complete fires.
        if not hasattr(self, "_active_tasks"):
            self._active_tasks: dict[str, Any] = {}
        self._active_tasks[step_id] = (task, signaller)

        def _on_complete(payload: dict[str, Any], _w=_worker_ref, _sid=step_id) -> None:
            self._active_tasks.pop(_sid, None)
            success = bool(payload.get("success"))
            message = payload.get("message") or (
                "Step completed." if success else "Step failed."
            )
            elapsed_ms = int(round(float(payload.get("execution_time_seconds", 0.0)) * 1000))
            observation = {
                "step_id": payload.get("step_id"),
                "step_name": payload.get("step_name"),
                "output_layer": payload.get("output_layer"),
                "execution_time_seconds": payload.get("execution_time_seconds"),
                "log": payload.get("log", ""),
            }
            if not success:
                observation["error_message"] = payload.get("error_message")
                observation["traceback"] = payload.get("traceback")
            if self._chat_panel is not None:
                self._chat_panel.append_system(
                    f"Step '{payload.get('step_name', step_name)}' "
                    f"{'completed' if success else 'failed'}."
                )
            self._reply_tool_call(
                worker_ref=_w,
                ok=success,
                message=message,
                observation=observation,
                elapsed_ms=elapsed_ms,
            )

        signaller.execution_complete.connect(_on_complete)

        try:
            QgsApplication.taskManager().addTask(task)
        except Exception as exc:
            logger.exception("Failed to submit GeoEdgeTask to QgsTaskManager.")
            self._active_tasks.pop(step_id, None)
            self._reply_tool_call(
                worker_ref=_worker_ref,
                ok=False,
                message=f"Step '{step_name}' could not be scheduled: {exc}",
            )

    def _spawn_stream_worker(
        self,
        *,
        user_message: str | None,
        approval: dict | None,
        history: list[dict[str, str]],
    ) -> None:
        """Open an ``/agent/stream`` connection in a fresh worker QThread.

        Properly terminates the previous worker (closes its socket and
        waits) so a stale stream cannot feed events into the UI after
        we've moved on (e.g. a resume-after-approval that races the
        previous turn's tail) and so we don't leak connections.
        """
        self._terminate_stream_worker(wait_ms=1000)

        self._chat_panel.show_streaming(True)

        api_base = self._api_base()
        token_provider = (
            lambda: (self._auth_manager.access_token or "" if self._auth_manager else "")
        )
        client = AgentStreamClient(
            api_base=api_base,
            token_provider=token_provider,
            refresh_callback=(self._auth_manager.refresh if self._auth_manager else None),
            protocol_version=PROTOCOL_VERSION,
        )

        from .cloud_client.worker import AgentStreamWorker

        qgis_context = self._build_qgis_context()
        self._stream_worker = AgentStreamWorker(
            client,
            session_id=self._session.session_id,
            user_message=user_message,
            qgis_context=qgis_context,
            api_base=api_base,
            token_provider=token_provider,
            approval=approval,
            conversation_history=history,
            parent=self.iface.mainWindow(),
        )
        _worker_ref = self._stream_worker
        self._stream_worker.sse_event.connect(self._on_stream_event)
        self._stream_worker.tool_call_requested.connect(
            lambda data, _w=_worker_ref: self._on_tool_call(data, _worker_ref=_w)
        )
        self._stream_worker.finished_with_error.connect(
            lambda msg, _w=_worker_ref: (
                self._on_stream_error(msg, _worker_ref=_w)
            )
        )
        self._stream_worker.finished.connect(
            lambda _w=_worker_ref: (
                self._on_stream_finished(_worker_ref=_w)
            )
        )
        self._stream_worker.start()

    def _terminate_stream_worker(self, *, wait_ms: int) -> None:
        """Close the in-flight stream and wait briefly for the thread to exit.

        If the worker doesn't exit within ``wait_ms`` (a hung TLS read,
        a stuck network), fall back to ``QThread.terminate()`` so the
        thread can't outlive the plugin and leak a stale token reference
        for the QGIS process lifetime.
        """
        old = self._stream_worker
        self._stream_worker = None
        if old is None:
            return
        try:
            old.sse_event.disconnect()
            old.tool_call_requested.disconnect()
            old.tool_call_completed.disconnect()
            old.finished_with_error.disconnect()
            old.finished.disconnect()
        except Exception:
            pass  # nosec B110 - best-effort cleanup, failure is non-fatal
        try:
            old.cancel()
        except Exception:
            pass  # nosec B110 - best-effort cleanup, failure is non-fatal
        try:
            exited = old.wait(wait_ms)
        except Exception:
            exited = False
        if not exited:
            logger.warning(
                "Stream worker did not exit within %dms; forcing terminate.",
                wait_ms,
            )
            try:
                old.terminate()
                old.wait(1000)
            except Exception:
                pass  # nosec B110 - best-effort cleanup, failure is non-fatal

    def _on_cancel(self) -> None:
        if self._stream_worker is None:
            return
        # Immediate UI feedback so the user knows the click registered;
        # _on_stream_finished will run set_authenticated state once the
        # worker exits.
        self._chat_panel.set_status("Cancelling…")

        # The orchestrator listens for /v1/agent/cancel. Fire-and-forget on
        # a daemon thread — synchronous urlopen here would freeze the GUI
        # for up to 15s on a flaky network, defeating the whole point of
        # having a Cancel button.
        if self._auth_manager and self._auth_manager.access_token:
            import threading

            from .cloud_client.cancel import post_cancel

            api_base = self._api_base()
            access_token = self._auth_manager.access_token
            session_id = self._session.session_id

            def _post() -> None:
                try:
                    post_cancel(api_base, access_token, session_id=session_id)
                except Exception:
                    logger.debug("agent/cancel failed.", exc_info=True)

            threading.Thread(target=_post, daemon=True).start()

        try:
            self._stream_worker.cancel()
        except Exception:
            pass  # nosec B110 - best-effort cleanup, failure is non-fatal

    def _on_stream_event(self, event_name: str, data: dict) -> None:
        if event_name == "capabilities":
            notices = data.get("deprecation_notices") or []
            for note in notices:
                msg = note.get("message") if isinstance(note, dict) else str(note)
                if msg:
                    self._chat_panel.append_system(f"Notice: {msg}")
            return
        if event_name == "plan":
            summary = data.get("summary", "")
            if summary:
                self._chat_panel.append_system(f"Plan: {summary}")
            return
        if event_name == "tool_call":
            # Real tool_calls are routed via tool_call_requested signal,
            # not sse_event. This branch only fires for the
            # tool_call_invalid sentinel the worker emits when a
            # tool_call event lacks step_id (server protocol bug).
            return
        if event_name == "tool_call_invalid":
            self._chat_panel.append_system(
                "Agent sent a tool request without a step id; "
                "skipping that step.",
                error=True,
            )
            return
        if event_name == "message":
            markdown = data.get("markdown", "")
            if not markdown:
                # Some servers emit a final empty message event to flush;
                # don't render an empty Agent: bubble or pollute history.
                return
            self._chat_panel.append_agent(markdown)
            # Record the assistant reply so the next user turn can be
            # classified as a follow-up.
            self._chat_history.append({"role": "assistant", "content": markdown})
            self._trim_history()
            return
        if event_name == "need_approval":
            action = data.get("action", "this action")
            reason = data.get("reason", "")
            # Prefer ``step_id`` (current backend contract — matches the
            # id carried in tool_call events). ``approval_id`` / ``id``
            # are tolerated for older servers.
            step_id = (
                data.get("step_id")
                or data.get("approval_id")
                or data.get("id")
                or ""
            )
            if not step_id:
                # Without an id the backend can't route our decision back
                # to the paused turn; bail loudly rather than POSTing an
                # empty string that fails Pydantic validation with 422.
                logger.warning(
                    "need_approval event missing step_id/approval_id/id; "
                    "skipping approval submission. data=%r",
                    data,
                )
                self._chat_panel.append_system(
                    "Approval request was malformed (missing step id); "
                    "the agent cannot proceed.",
                    error=True,
                )
                return
            ans = QMessageBox.question(
                self.iface.mainWindow(),
                "GeoEdge AI — Approval required",
                f"The agent wants to {action}.\n\n{reason}\n\nProceed?",
            )
            granted = ans == QMessageBoxC.Yes
            self._chat_panel.append_system(
                f"Approval {'granted' if granted else 'denied'} for: {action}"
            )
            self._submit_approval(step_id=step_id, granted=granted)
            return
        if event_name == "usage":
            cumulative = data.get("cumulative") or 0
            self._chat_panel.set_usage(f"Tokens used: {cumulative}")
            return
        if event_name == "deprecation_notice":
            msg = data.get("message", "Plugin version is becoming unsupported.")
            self._chat_panel.append_system(f"Notice: {msg}")
            return
        if event_name == "error":
            message = data.get("message", "Unknown error")
            self._chat_panel.append_system(f"Error: {message}", error=True)
            return
        if event_name == "cancelled":
            self._chat_panel.append_system("Cancelled.")
            return
        if event_name == "done":
            return  # finalisation handled in _on_stream_finished

    def _on_stream_error(self, message: str, _worker_ref: Any = None) -> None:
        # Closure-captured worker reference: drop stale signals from a
        # replaced worker. GeoEdgePlugin is not a QObject so self.sender()
        # is never available — we use a closure variable instead.
        if _worker_ref is not None and _worker_ref is not self._stream_worker:
            return
        if self._chat_panel is None:
            return
        if "agent/stream" in message and ("Not Found" in message or "404" in message):
            self._chat_panel.append_system(
                "GeoEdge Cloud agent endpoint is not yet live. The plugin will "
                "be fully functional once the backend is deployed. (Pre-release.)",
                error=True,
            )
        else:
            self._chat_panel.append_system(f"Stream error: {message}", error=True)
        self._chat_panel.show_streaming(False)
        self._refresh_auth_state()

    def _on_stream_finished(self, _worker_ref: Any = None) -> None:
        # Same closure-based stale-signal guard as _on_stream_error.
        if _worker_ref is not None and _worker_ref is not self._stream_worker:
            return
        if self._chat_panel is None:
            return
        self._chat_panel.show_streaming(False)
        self._stream_worker = None
        self._refresh_auth_state()

    # ------------------------------------------------------------------
    # Capability handshake
    # ------------------------------------------------------------------

    def _maybe_check_capabilities(self) -> None:
        """Kick off the capabilities handshake on a daemon thread.

        Synchronous urlopen here freezes QGIS on first send, so the
        result is marshalled back onto the GUI thread via
        ``QTimer.singleShot``. Mismatch is non-fatal at the client —
        the SSE stream itself echoes capabilities and the server will
        reject an incompatible request.
        """
        if self._capabilities_checked or self._auth_manager is None:
            return
        self._capabilities_checked = True

        import threading

        from qgis.PyQt.QtCore import QTimer

        from .cloud_client.capabilities import (
            CapabilitiesClient,
            ProtocolMismatchError,
        )

        api_base = self._api_base()
        access_token = self._auth_manager.access_token

        def _run() -> None:
            client = CapabilitiesClient(api_base=api_base, access_token=access_token)
            try:
                data = client.fetch(plugin_version=__version__)
            except ProtocolMismatchError as exc:
                msg = f"Protocol mismatch: {exc}. Update the plugin."
                QTimer.singleShot(
                    0, lambda: self._capabilities_notice(msg, error=True)
                )
                return
            except Exception as exc:
                logger.debug("Capabilities check failed: %s", exc)
                return

            for note in data.get("deprecation_notices") or []:
                m = note.get("message") if isinstance(note, dict) else str(note)
                if m:
                    QTimer.singleShot(
                        0, lambda msg=f"Notice: {m}": self._capabilities_notice(msg)
                    )

        threading.Thread(target=_run, daemon=True).start()

    def _capabilities_notice(self, msg: str, *, error: bool = False) -> None:
        """Append a capabilities notice to chat — main-thread safe."""
        if self._chat_panel is not None:
            self._chat_panel.append_system(msg, error=error)

    # ------------------------------------------------------------------
    # QGIS context capture
    # ------------------------------------------------------------------

    def _build_qgis_context(self) -> dict:
        """Build the ``qgis_context`` payload sent on every turn.

        Honours the privacy toggles in PRIVACY.md.
        """
        s = QSettings()
        # Default True — backend code-generation templates (e.g.
        # reproject.py.j2) require the layer source path. Without it the
        # template variable is Undefined and the turn fails with a JSON
        # serialisation error.
        send_layer_paths = s.value(KEY_SEND_LAYER_PATHS, True, type=bool)

        try:
            project = QgsProject.instance()
        except Exception:
            return {"layers": [], "active_layer_id": None}

        layers_payload = []
        for lyr_id, lyr in project.mapLayers().items():
            try:
                entry = self._summarise_layer(lyr_id, lyr, send_layer_paths)
            except Exception:
                logger.debug("Layer summary failed for %s", lyr_id, exc_info=True)
                entry = {"id": lyr_id, "name": getattr(lyr, "name", lambda: "")(), "error": "summary_failed"}
            layers_payload.append(entry)

        try:
            active_layer = self.iface.activeLayer() if hasattr(self.iface, "activeLayer") else None
            active_id = active_layer.id() if active_layer else None
        except Exception:
            active_id = None

        try:
            project_crs = project.crs().authid() if project.crs() else None
        except Exception:
            project_crs = None

        try:
            canvas = self.iface.mapCanvas() if hasattr(self.iface, "mapCanvas") else None
            viewport_bbox = None
            if canvas is not None:
                ext = canvas.extent()
                # Backend schema is list[float] in [xmin, ymin, xmax, ymax]
                # order. A dict here is rejected by Pydantic with HTTP 422.
                viewport_bbox = [
                    ext.xMinimum(),
                    ext.yMinimum(),
                    ext.xMaximum(),
                    ext.yMaximum(),
                ]
        except Exception:
            viewport_bbox = None

        return {
            "project_crs": project_crs,
            "layers": layers_payload,
            "active_layer_id": active_id,
            "viewport_bbox": viewport_bbox,
            "plugin_version": __version__,
        }

    @staticmethod
    def _summarise_layer(lyr_id: str, lyr: Any, send_layer_paths: bool) -> dict:
        def safe(call: Any, default: Any = None) -> Any:
            try:
                return call()
            except Exception:
                return default

        # Use explicit constant comparison rather than getattr(.., "name") on
        # the SIP enum value — QGIS SIP enums do not reliably expose a .name
        # attribute across all builds. Scoped access (QgsMapLayer.LayerType.*)
        # is required on Qt6/PyQt6 and already works on the plugin's minimum
        # supported QGIS version (3.40), so no flat-attribute fallback is needed.
        _TYPE_MAP = {
            QgsMapLayer.LayerType.VectorLayer: "vector",
            QgsMapLayer.LayerType.RasterLayer: "raster",
            QgsMapLayer.LayerType.MeshLayer: "mesh",
            QgsMapLayer.LayerType.VectorTileLayer: "vector_tile",
            QgsMapLayer.LayerType.AnnotationLayer: "annotation",
            QgsMapLayer.LayerType.PluginLayer: "plugin",
            QgsMapLayer.LayerType.GroupLayer: "group",
        }
        layer_type = None
        try:
            t = lyr.type() if hasattr(lyr, "type") else None
            layer_type = _TYPE_MAP.get(t) if t is not None else None
        except Exception:
            layer_type = None

        crs_authid = None
        try:
            crs = lyr.crs() if hasattr(lyr, "crs") else None
            crs_authid = crs.authid() if crs else None
        except Exception:
            pass  # nosec B110 - best-effort cleanup, failure is non-fatal

        fields_out: list[dict[str, str]] = []
        field_count = 0
        try:
            if hasattr(lyr, "fields"):
                all_fields = lyr.fields()
                field_count = len(all_fields)
                for f in list(all_fields)[:_MAX_FIELDS_PER_LAYER]:
                    fields_out.append({"name": f.name(), "type": f.typeName()})
        except Exception:
            fields_out = []
            field_count = 0

        geometry_type = None
        try:
            if hasattr(lyr, "geometryType"):
                gt = lyr.geometryType()
                geometry_type = QgsWkbTypes.geometryDisplayString(gt)
        except Exception:
            pass  # nosec B110 - best-effort cleanup, failure is non-fatal

        bbox = None
        try:
            ext = lyr.extent()
            if ext is not None and not ext.isEmpty():
                # Same [xmin, ymin, xmax, ymax] shape as viewport_bbox so
                # the backend can treat them uniformly.
                bbox = [
                    ext.xMinimum(),
                    ext.yMinimum(),
                    ext.xMaximum(),
                    ext.yMaximum(),
                ]
        except Exception:
            pass  # nosec B110 - best-effort cleanup, failure is non-fatal

        entry: dict[str, Any] = {
            "id": lyr_id,
            "name": safe(lambda: lyr.name(), ""),
            "type": layer_type,
            "geometry_type": geometry_type,
            "crs": crs_authid,
            "feature_count": safe(lambda: lyr.featureCount(), None) if hasattr(lyr, "featureCount") else None,
            "fields": fields_out,
            "field_count": field_count,
            "bbox": bbox,
        }
        if send_layer_paths and hasattr(lyr, "source"):
            entry["source"] = safe(lambda: lyr.source(), None)
        return entry

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _trim_history(self) -> None:
        if len(self._chat_history) > _MAX_HISTORY_MESSAGES:
            del self._chat_history[: len(self._chat_history) - _MAX_HISTORY_MESSAGES]

    def _api_base(self) -> str:
        if QSettings is None:
            return DEFAULT_API_BASE
        override = QSettings().value(KEY_API_BASE, "", type=str)
        return override.strip() or DEFAULT_API_BASE
