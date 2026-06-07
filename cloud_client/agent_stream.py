# SPDX-License-Identifier: GPL-2.0-or-later
# Copyright (C) 2024-2026 GeoEdge AI
#
# This file is part of the GeoEdge AI QGIS plugin.
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 2 of the License, or
# (at your option) any later version.
# See the COPYING file or <https://www.gnu.org/licenses/> for details.
"""SSE consumer for ``POST /v1/agent/stream``.

Each ``open_turn(...)`` call POSTs the user message + QGIS context, then
yields parsed SSE events until the server emits ``done``. The plugin
dispatches each event to the UI (see ``geoedge_plugin.GeoEdgePlugin``).

Event types:

* ``capabilities`` — first event; client validates compatibility.
* ``plan`` — one-line plan summary.
* ``message`` — markdown chat text.
* ``need_approval`` — destructive-action gate.
* ``usage`` — token accounting for the usage panel.
* ``deprecation_notice`` — surface to the UI.
* ``error`` — fatal mid-stream error.
* ``cancelled`` — cancel confirmation.
* ``done`` — end of turn; close the stream.
"""

from __future__ import annotations

import json
import logging
import threading
from collections.abc import Callable, Iterator
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request

from ._http import safe_urlopen

logger = logging.getLogger(__name__)

_PLUGIN_VERSION: str | None = None
_PLUGIN_HASH: str | None = None


def _get_plugin_version() -> str:
    """Read (and cache) the plugin version from metadata.txt."""
    global _PLUGIN_VERSION
    if _PLUGIN_VERSION is not None:
        return _PLUGIN_VERSION
    import re
    from pathlib import Path

    txt = (Path(__file__).resolve().parent.parent / "metadata.txt").read_text(
        encoding="utf-8"
    )
    m = re.search(r"^version=(.+)$", txt, re.MULTILINE)
    _PLUGIN_VERSION = m.group(1).strip() if m else "0.0.0"
    return _PLUGIN_VERSION


def _get_plugin_hash() -> str:
    """Compute (and cache) a content hash of this plugin's installed .py files.

    Uses the same algorithm as build_zip.compute_content_hash so the value
    matches what the backend loaded from integrity_hashes.json.
    """
    global _PLUGIN_HASH
    if _PLUGIN_HASH is not None:
        return _PLUGIN_HASH
    import hashlib
    from pathlib import Path

    pkg_root = Path(__file__).resolve().parent.parent  # GeoEDGE_AI/
    repo_root = pkg_root.parent
    entries = []
    for p in sorted(pkg_root.rglob("*.py"), key=lambda f: f.relative_to(repo_root).as_posix()):
        if "__pycache__" in p.parts:
            continue
        arc = p.relative_to(repo_root).as_posix()
        entries.append(f"{arc}:{hashlib.sha256(p.read_bytes()).hexdigest()}")
    combined = "\n".join(entries)
    _PLUGIN_HASH = "sha256:" + hashlib.sha256(combined.encode()).hexdigest()
    return _PLUGIN_HASH


class AgentStreamError(RuntimeError):
    """Raised when the SSE stream cannot be opened."""


class AgentStreamClient:
    """Thin wrapper around the streaming endpoint.

    ``token_provider`` is called immediately before each request so the
    most recently refreshed access token is sent. ``refresh_callback``,
    if supplied, is invoked once on a 401 to refresh the token and the
    request is retried a single time.
    """

    def __init__(
        self,
        api_base: str,
        token_provider: Callable[[], str],
        *,
        refresh_callback: Callable[[], bool] | None = None,
        protocol_version: int = 1,
        timeout: float = 600.0,
    ) -> None:
        self._api_base = api_base.rstrip("/")
        self._token_provider = token_provider
        self._refresh_callback = refresh_callback
        self._protocol_version = protocol_version
        self._timeout = timeout
        self._lock = threading.Lock()
        self._response: Any = None
        self._closed = False

    def close(self) -> None:
        """Close the underlying response if any. Safe from any thread.

        Closing the urllib response unblocks the iterator so ``open_turn``
        returns promptly — this is how Cancel actually cuts the stream.
        """
        with self._lock:
            self._closed = True
            resp = self._response
            self._response = None
        if resp is not None:
            try:
                resp.close()
            except Exception:
                pass

    def open_turn(
        self,
        session_id: str,
        user_message: str | None,
        qgis_context: dict[str, Any],
        *,
        approval: dict[str, Any] | None = None,
        conversation_history: list[dict[str, Any]] | None = None,
    ) -> Iterator[dict[str, Any]]:
        """Open the SSE stream and yield ``{"event": str, "data": dict}``.

        ``conversation_history`` is the list of prior turns for this
        session ([{"role": "user"|"assistant", "content": "..."}], oldest
        first). Sending it lets the server's IntentParser resolve replies
        to its own clarification questions in a single follow-up turn.
        """
        body = json.dumps(
            {
                "session_id": session_id,
                "user_message": user_message,
                "qgis_context": qgis_context,
                "approval": approval,
                "conversation_history": conversation_history or [],
            }
        ).encode()

        resp = self._open(body)
        try:
            yield from _iter_sse(resp)
        finally:
            with self._lock:
                if self._response is resp:
                    self._response = None
            try:
                resp.close()
            except Exception:
                pass

    def _open(self, body: bytes) -> Any:
        """POST and return the streaming response, refreshing once on 401."""
        try:
            return self._post(body)
        except HTTPError as exc:
            if exc.code == 401 and self._refresh_callback is not None:
                logger.info("agent/stream returned 401 — attempting token refresh.")
                if self._refresh_callback():
                    # close() may have been called during the refresh
                    # (user clicked Cancel, plugin unloaded). Bail rather
                    # than issuing a second POST that we'd immediately
                    # have to cancel.
                    with self._lock:
                        already_closed = self._closed
                    if already_closed:
                        raise AgentStreamError(
                            "agent/stream closed during token refresh."
                        ) from exc
                    try:
                        return self._post(body)
                    except HTTPError as exc2:
                        raise AgentStreamError(
                            f"agent/stream failed: HTTP {exc2.code}"
                        ) from exc2
                    except URLError as exc2:
                        raise AgentStreamError(
                            f"agent/stream failed: {exc2.reason}"
                        ) from exc2
            raise AgentStreamError(
                f"agent/stream failed: HTTP {exc.code}"
            ) from exc
        except URLError as exc:
            raise AgentStreamError(
                f"agent/stream failed: {exc.reason}"
            ) from exc

    def _post(self, body: bytes) -> Any:
        url = f"{self._api_base}/agent/stream"
        token = self._token_provider() or ""
        headers = {
            "Content-Type": "application/json",
            "Accept": "text/event-stream",
            "Authorization": f"Bearer {token}",
            "X-GeoEdge-Protocol-Version": str(self._protocol_version),
            "X-GeoEdge-Plugin-Version": _get_plugin_version(),
            "X-GeoEdge-Integrity-Hash": _get_plugin_hash(),
        }
        req = Request(url, data=body, headers=headers, method="POST")
        resp = safe_urlopen(req, timeout=self._timeout)
        with self._lock:
            if self._closed:
                # close() raced us; honour it.
                try:
                    resp.close()
                except Exception:
                    pass
                raise AgentStreamError("agent/stream closed before open completed.")
            self._response = resp
        return resp


def _iter_sse(resp: Any) -> Iterator[dict[str, Any]]:
    """Yield parsed SSE events from a ``urllib`` response.

    Handles the SSE ``event: <name>\\ndata: <json>\\n\\n`` shape with
    multi-line data buffering. Per the SSE spec, exactly one leading
    space is stripped from each ``data:`` value.
    """
    event_name = "message"
    data_lines: list[str] = []

    for raw in resp:
        line = raw.decode("utf-8", errors="replace").rstrip("\r\n")
        if line == "":
            if data_lines:
                payload = "\n".join(data_lines)
                try:
                    parsed = json.loads(payload) if payload else {}
                except json.JSONDecodeError:
                    logger.warning("SSE non-JSON payload (event=%s): %r", event_name, payload)
                    parsed = {"_raw": payload}
                # Downstream signal is pyqtSignal(str, dict); a top-level
                # list / scalar from a misbehaving server would otherwise
                # take out the worker thread via PyQt's signature check.
                if not isinstance(parsed, dict):
                    parsed = {"_raw": parsed}
                yield {"event": event_name, "data": parsed}
                if event_name == "done":
                    return
            event_name = "message"
            data_lines = []
            continue
        if line.startswith(":"):
            continue  # SSE comment / keep-alive
        if line.startswith("event:"):
            event_name = line[len("event:"):].strip()
        elif line.startswith("data:"):
            value = line[len("data:"):]
            # SSE spec: strip exactly one leading space, no more.
            if value.startswith(" "):
                value = value[1:]
            data_lines.append(value)
        # other field names (id:, retry:) ignored
