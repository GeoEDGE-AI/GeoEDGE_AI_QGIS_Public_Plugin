# SPDX-License-Identifier: GPL-2.0-or-later
# Copyright (C) 2024-2026 GeoEdge AI
#
# This file is part of the GeoEdge AI QGIS plugin.
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 2 of the License, or
# (at your option) any later version.
# See the COPYING file or <https://www.gnu.org/licenses/> for details.
"""Authentication for the GeoEdge AI cloud service."""

from .auth_manager import DEFAULT_API_BASE, AuthManager
from .exceptions import GeoEdgeAuthError

__all__ = [
    "DEFAULT_API_BASE",
    "AuthManager",
    "GeoEdgeAuthError",
]
