# SPDX-License-Identifier: GPL-2.0-or-later
# Copyright (C) 2024-2026 GeoEdge AI
#
# This file is part of the GeoEdge AI QGIS plugin.
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 2 of the License, or
# (at your option) any later version.
# See the COPYING file or <https://www.gnu.org/licenses/> for details.
"""``POST /v1/agent/observation`` — return a tool-execution result to the server.

The backend orchestrator emits a ``tool_call`` SSE event and then
``await``s on ``router.wait(session_id, step_id, timeout=600s)``. This
function delivers the matching observation so the awaiting coroutine
resumes and the next step (or ``done``) can be emitted. Without it the
orchestrator stalls and eventually times out — the user sees no reply.

Single function so the caller can fire it on a daemon thread without
having to manage a client object's lifetime.
"""

from __future__ import annotations

import json
import logging
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from .agent_stream import _get_plugin_hash, _get_plugin_version

logger = logging.getLogger(__name__)


def post_observation(
    api_base: str,
    access_token: str,
    *,
    session_id: str,
    step_id: str,
    tool: str,
    ok: bool,
    observation: dict[str, Any] | None = None,
    elapsed_ms: int = 0,
    timeout: float = 30.0,
) -> bool:
    """``POST /v1/agent/observation`` and return ``True`` on a 2xx response.

    The backend always replies ``204 No Content`` — even when no
    coroutine is waiting (e.g. the stream already closed). We surface
    that as ``True`` since there's nothing the caller can do about it.
    On network failure or non-2xx response, returns ``False`` and logs.
    """
    url = f"{api_base.rstrip('/')}/agent/observation"
    body = json.dumps(
        {
            "session_id": session_id,
            "step_id": step_id,
            "tool": tool,
            "ok": ok,
            "observation": observation if observation is not None else {},
            "elapsed_ms": int(elapsed_ms),
        }
    ).encode()
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {access_token}",
        "X-GeoEdge-Plugin-Version": _get_plugin_version(),
        "X-GeoEdge-Integrity-Hash": _get_plugin_hash(),
    }
    req = Request(url, data=body, headers=headers, method="POST")
    try:
        with urlopen(req, timeout=timeout):
            return True
    except HTTPError as exc:
        logger.warning(
            "agent/observation failed: HTTP %s (session=%s step=%s)",
            exc.code,
            session_id,
            step_id,
        )
        return False
    except URLError as exc:
        logger.warning(
            "agent/observation network error: %s (session=%s step=%s)",
            exc.reason,
            session_id,
            step_id,
        )
        return False
