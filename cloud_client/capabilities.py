# SPDX-License-Identifier: GPL-2.0-or-later
# Copyright (C) 2024-2026 GeoEdge AI
#
# This file is part of the GeoEdge AI QGIS plugin.
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 2 of the License, or
# (at your option) any later version.
# See the COPYING file or <https://www.gnu.org/licenses/> for details.
"""Protocol negotiation against ``GET /v1/agent/capabilities``.

The plugin calls this once on startup to:

* confirm the server supports its protocol version,
* receive the server's tool registry hash for cross-checking,
* surface deprecation notices to the UI.
"""

from __future__ import annotations

import json
import logging
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request

from ._http import safe_urlopen

logger = logging.getLogger(__name__)


class ProtocolMismatchError(RuntimeError):
    """Raised when the server's protocol version is incompatible."""


class CapabilitiesClient:
    """One-shot client for the capabilities handshake."""

    def __init__(
        self,
        api_base: str,
        access_token: str | None = None,
    ) -> None:
        self._api_base = api_base.rstrip("/")
        self._access_token = access_token

    def fetch(self, plugin_version: str) -> dict[str, Any]:
        """Call ``GET /v1/agent/capabilities?plugin_version=…``.

        Returns the JSON body. Raises ``ProtocolMismatchError`` if the
        protocol version is unsupported, or ``ConnectionError`` on any
        network failure.
        """
        from . import PROTOCOL_VERSION

        url = f"{self._api_base}/agent/capabilities?{urlencode({'plugin_version': plugin_version})}"
        headers = {"Accept": "application/json"}
        if self._access_token:
            headers["Authorization"] = f"Bearer {self._access_token}"

        req = Request(url, headers=headers, method="GET")
        try:
            with safe_urlopen(req, timeout=15) as resp:
                data = json.loads(resp.read().decode())
        except HTTPError as exc:
            raise ConnectionError(
                f"Capabilities request failed: HTTP {exc.code}"
            ) from exc
        except URLError as exc:
            raise ConnectionError(
                f"Capabilities request failed: {exc.reason}"
            ) from exc

        supported = data.get("supported_protocol_versions") or [data.get("protocol_version")]
        if PROTOCOL_VERSION not in supported:
            raise ProtocolMismatchError(
                f"Server does not support protocol version {PROTOCOL_VERSION}; "
                f"server reports {supported}."
            )

        return data
