# SPDX-License-Identifier: GPL-2.0-or-later
# Copyright (C) 2024-2026 GeoEdge AI
#
# This file is part of the GeoEdge AI QGIS plugin.
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 2 of the License, or
# (at your option) any later version.
# See the COPYING file or <https://www.gnu.org/licenses/> for details.
"""Cloud client — SSE consumer for the GeoEdge agent at api.geoedge.ai.

The agent itself runs server-side. This package contains:

* ``agent_stream`` — POST + SSE consumer for ``/v1/agent/stream``.
* ``cancel`` — POST ``/v1/agent/cancel`` to abort an in-flight turn.
* ``capabilities`` — protocol negotiation against ``/v1/agent/capabilities``.
* ``session`` — session id lifecycle and token refresh.
* ``worker`` — QThread wrapper so the SSE loop never blocks the UI.
"""

from .capabilities import CapabilitiesClient, ProtocolMismatchError
from .session import AgentSession

__all__ = [
    "AgentSession",
    "CapabilitiesClient",
    "ProtocolMismatchError",
]

# Wire-protocol version. The first event of every stream must echo this.
PROTOCOL_VERSION = 1
