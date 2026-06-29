# SPDX-License-Identifier: GPL-2.0-or-later
# Copyright (C) 2024-2026 GeoEdge AI
#
# This file is part of the GeoEdge AI QGIS plugin.
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 2 of the License, or
# (at your option) any later version.
# See the COPYING file or <https://www.gnu.org/licenses/> for details.
"""GeoEdge AI — QGIS plugin entry point.

Thin client for plugins.qgis.org. The agent itself lives on
``api.geoedge.ai``; this package exposes the UI, auth, local tool
execution, and the SSE consumer that connects to the cloud agent.
"""

from __future__ import annotations

__version__ = "1.0.18"
__author__ = "GeoEdge AI"


def classFactory(iface):
    """QGIS plugin loader.

    Called by QGIS during plugin loading. Returns the main plugin
    instance which implements ``initGui()`` and ``unload()``.
    """
    from .geoedge_plugin import GeoEdgePlugin

    return GeoEdgePlugin(iface)
