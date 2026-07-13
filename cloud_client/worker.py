# SPDX-License-Identifier: GPL-2.0-or-later
# Copyright (C) 2024-2026 GeoEdge AI
#
# This file is part of the GeoEdge AI QGIS plugin.
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 2 of the License, or
# (at your option) any later version.
# See the COPYING file or <https://www.gnu.org/licenses/> for details.
"""``QThread`` wrapper around ``AgentStreamClient.open_turn``.

The SSE loop runs off the GUI thread; events are emitted as Qt signals
so the UI can update without thread-affinity headaches.

``tool_call`` events are intercepted: the worker hands them to the GUI
thread (via :pyattr:`tool_call_requested`) and waits for a paired
:pyattr:`tool_call_completed` signal carrying the execution result. The
result is then POSTed to ``/v1/agent/observation`` so the orchestrator's
``router.wait(...)`` resolves and emits the next event. Without this
roundtrip the server stalls for the full 600s observation timeout and
the user sees no reply.
"""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable
from typing import Any

from .observation import post_observation

try:
    from qgis.PyQt.QtCore import QObject, QThread, pyqtSignal

    HAS_QT = True
except ImportError:
    HAS_QT = False
    QThread = object  # type: ignore[misc,assignment]
    QObject = object  # type: ignore[misc,assignment]

logger = logging.getLogger(__name__)


if HAS_QT:

    class AgentStreamWorker(QThread):
        """Run an agent turn off the GUI thread.

        Signals
        -------
        sse_event(str, dict)
            For each non-``tool_call`` SSE event: ``(event_name, data)``.
            Named ``sse_event`` (not ``event``) so it does not shadow
            ``QObject.event``.
        tool_call_requested(dict)
            Emitted when a ``tool_call`` event arrives. The GUI thread
            validates + runs the code and must reply by emitting
            :pyattr:`tool_call_completed`.
        tool_call_completed(dict)
            GUI thread → worker thread. Carries the observation payload
            ``{"ok": bool, "observation": {...}, "elapsed_ms": int}``.
            The worker POSTs it to ``/v1/agent/observation`` and resumes
            the SSE loop. Connect with ``Qt.DirectConnection`` so the
            slot runs on whatever thread emits — we want the wait Event
            to unblock immediately.
        finished_with_error(str)
            On stream failure (network / protocol).
        """

        sse_event = pyqtSignal(str, dict)
        tool_call_requested = pyqtSignal(dict)
        tool_call_completed = pyqtSignal(dict)
        finished_with_error = pyqtSignal(str)

        def __init__(
            self,
            client: Any,  # AgentStreamClient
            *,
            session_id: str,
            user_message: str | None,
            qgis_context: dict[str, Any],
            api_base: str,
            token_provider: Callable[[], str],
            approval: dict[str, Any] | None = None,
            conversation_history: list[dict[str, Any]] | None = None,
            tool_call_timeout: float = 540.0,
            parent: QObject | None = None,
        ) -> None:
            super().__init__(parent)
            self._client = client
            self._session_id = session_id
            self._user_message = user_message
            self._qgis_context = qgis_context
            self._api_base = api_base
            self._token_provider = token_provider
            self._approval = approval
            self._conversation_history = conversation_history
            # Cap < the server's 600s ``_DEFAULT_OBSERVATION_TIMEOUT`` so
            # we always observe the result locally before the server
            # gives up; tighter caps trade flexibility for snappier
            # failures.
            self._tool_call_timeout = tool_call_timeout
            self._cancelled = False

            # Tool-call wait state. _tool_call_event unblocks the worker
            # thread when either the GUI thread provides a result or
            # cancel() is invoked.
            self._tool_call_event = threading.Event()
            self._tool_call_result: dict[str, Any] | None = None
            # Connect on the worker so the slot runs synchronously and
            # the threading.Event flips immediately rather than queueing
            # behind GUI events.
            self.tool_call_completed.connect(self._on_tool_call_completed)

        # ------------------------------------------------------------------
        # Cancel
        # ------------------------------------------------------------------

        def cancel(self) -> None:
            """Cut the SSE socket and unblock any tool-call wait.

            Closing the underlying response unblocks the SSE iterator so
            the worker returns promptly even though ``urlopen`` doesn't
            otherwise honour interruption requests. Setting the
            tool-call event unblocks the wait inside ``_dispatch_tool_call``
            if a step is currently executing.
            """
            self._cancelled = True
            try:
                self._client.close()
            except Exception:
                pass  # nosec B110 - best-effort cleanup, failure is non-fatal
            # Unblock any in-flight tool-call wait so the worker can exit.
            self._tool_call_event.set()
            try:
                self.requestInterruption()
            except Exception:
                pass  # nosec B110 - best-effort cleanup, failure is non-fatal

        # ------------------------------------------------------------------
        # GUI-thread reply slot
        # ------------------------------------------------------------------

        def _on_tool_call_completed(self, payload: dict[str, Any]) -> None:
            """Receive the executor's result from the GUI thread."""
            self._tool_call_result = payload
            self._tool_call_event.set()

        # ------------------------------------------------------------------
        # Main loop
        # ------------------------------------------------------------------

        def run(self) -> None:
            try:
                for evt in self._client.open_turn(
                    self._session_id,
                    self._user_message,
                    self._qgis_context,
                    approval=self._approval,
                    conversation_history=self._conversation_history,
                ):
                    if self.isInterruptionRequested() or self._cancelled:
                        break
                    name = evt.get("event", "message")
                    data = evt.get("data", {})
                    if name == "tool_call":
                        self._dispatch_tool_call(data)
                        # Worker may have been cancelled while we were
                        # waiting for the executor — bail before reading
                        # the next SSE event.
                        if self.isInterruptionRequested() or self._cancelled:
                            break
                        continue
                    self.sse_event.emit(name, data)
            except Exception as exc:  # broad — surfaces to the UI either way
                if self._cancelled:
                    logger.debug("Stream worker exited after cancel: %s", exc)
                    return
                logger.warning("Agent stream worker error: %s", exc)
                self.finished_with_error.emit(str(exc))

        # ------------------------------------------------------------------
        # tool_call → executor → /v1/agent/observation
        # ------------------------------------------------------------------

        def _dispatch_tool_call(self, data: dict[str, Any]) -> None:
            """Round-trip a tool_call through the GUI-thread executor.

            * Emits :pyattr:`tool_call_requested` so the plugin can
              validate + run the code.
            * Blocks until :meth:`_on_tool_call_completed` (or cancel)
              sets the wait event.
            * POSTs the observation back to the server so the awaiting
              orchestrator coroutine resumes.
            """
            step_id = data.get("step_id") or ""
            tool = data.get("tool", "run_pyqgis")

            if not step_id:
                # No id ⇒ server has no waiter we can match against;
                # forward as a regular event so the UI can surface the
                # protocol bug and skip the observation POST.
                logger.warning("tool_call event missing step_id: %r", data)
                self.sse_event.emit("tool_call_invalid", data)
                return

            self._tool_call_event.clear()
            self._tool_call_result = None
            start = time.monotonic()
            self.tool_call_requested.emit(data)

            if not self._tool_call_event.wait(timeout=self._tool_call_timeout):
                # GUI thread never responded — synthesise a failure
                # observation so the server doesn't sit on its 600s
                # timeout. Don't trust ``self._tool_call_result`` here.
                self._tool_call_result = {
                    "ok": False,
                    "observation": {
                        "message": (
                            f"Client-side execution timed out after "
                            f"{int(self._tool_call_timeout)}s."
                        ),
                    },
                    "elapsed_ms": int((time.monotonic() - start) * 1000),
                }

            if self._cancelled:
                # User cancelled while we waited — server is about to be
                # told to abort via /v1/agent/cancel, so don't bother
                # POSTing an observation it isn't waiting for.
                return

            result = self._tool_call_result or {
                "ok": False,
                "observation": {"message": "No execution result available."},
                "elapsed_ms": int((time.monotonic() - start) * 1000),
            }

            # POST on a daemon thread so the SSE loop can resume reading
            # the next event without waiting on a network round-trip.
            access_token = ""  # nosec B105 — empty default, populated below
            try:
                access_token = self._token_provider() or ""
            except Exception:
                logger.debug("token_provider raised — POSTing observation without auth.")

            def _post() -> None:
                try:
                    post_observation(
                        self._api_base,
                        access_token,
                        session_id=self._session_id,
                        step_id=step_id,
                        tool=tool,
                        ok=bool(result.get("ok", False)),
                        observation=result.get("observation") or {},
                        elapsed_ms=int(result.get("elapsed_ms", 0)),
                    )
                except Exception:
                    logger.exception("post_observation crashed.")

            threading.Thread(target=_post, daemon=True).start()

else:

    class AgentStreamWorker:  # type: ignore[no-redef]
        """Stub — PyQt5 not available outside QGIS."""

        def __init__(self, *args, **kwargs):
            raise RuntimeError(
                "AgentStreamWorker requires PyQt5 (run inside QGIS)."
            )
