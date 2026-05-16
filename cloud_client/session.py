# SPDX-License-Identifier: GPL-2.0-or-later
# Copyright (C) 2024-2026 GeoEdge AI
#
# This file is part of the GeoEdge AI QGIS plugin.
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 2 of the License, or
# (at your option) any later version.
# See the COPYING file or <https://www.gnu.org/licenses/> for details.
"""Session id + access-token helper for cloud agent calls."""

from __future__ import annotations

import logging
import uuid

logger = logging.getLogger(__name__)


class AgentSession:
    """Represents one user-visible chat session.

    A session has a stable ``session_id`` (UUID) that is passed on every
    ``/v1/agent/stream`` and ``/v1/agent/observation`` call so the server
    can correlate context. The id is regenerated on logout.
    """

    def __init__(self, session_id: str | None = None) -> None:
        self.session_id: str = session_id or str(uuid.uuid4())

    def reset(self) -> str:
        """Start a new session. Returns the new session id."""
        self.session_id = str(uuid.uuid4())
        logger.info("New agent session: %s", self.session_id)
        return self.session_id
