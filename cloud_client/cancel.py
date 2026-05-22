# SPDX-License-Identifier: GPL-2.0-or-later
# Copyright (C) 2024-2026 GeoEdge AI
#
# This file is part of the GeoEdge AI QGIS plugin.
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 2 of the License, or
# (at your option) any later version.
# See the COPYING file or <https://www.gnu.org/licenses/> for details.
"""``POST /v1/agent/cancel`` — abort an in-flight turn server-side.

Best-effort: the cancel may race the next SSE event, but the server
treats a cancelled session as terminal regardless.
"""

from __future__ import annotations

import json
import logging
from urllib.error import HTTPError, URLError
from urllib.request import Request

from ._http import safe_urlopen

logger = logging.getLogger(__name__)


def post_cancel(
    api_base: str,
    access_token: str,
    *,
    session_id: str,
    timeout: float = 15.0,
) -> None:
    """``POST /v1/agent/cancel`` — abort an in-flight turn."""
    url = f"{api_base.rstrip('/')}/agent/cancel"
    body = json.dumps({"session_id": session_id}).encode()
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {access_token}",
    }
    req = Request(url, data=body, headers=headers, method="POST")
    try:
        with safe_urlopen(req, timeout=timeout):
            return None
    except (HTTPError, URLError) as exc:
        logger.warning("agent/cancel failed: %s", exc)
