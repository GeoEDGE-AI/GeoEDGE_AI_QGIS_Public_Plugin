# SPDX-License-Identifier: GPL-2.0-or-later
# Copyright (C) 2024-2026 GeoEdge AI
#
# This file is part of the GeoEdge AI QGIS plugin.
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 2 of the License, or
# (at your option) any later version.
# See the COPYING file or <https://www.gnu.org/licenses/> for details.
"""Single source of truth for QSettings keys used by the plugin.

Every module reads/writes settings through these constants so that a
key rename can't silently desynchronise reader from writer.
"""

from __future__ import annotations

SETTINGS_PREFIX = "GeoEdgeAI"

# Core
KEY_API_BASE = f"{SETTINGS_PREFIX}/api_base"
KEY_LOG_LEVEL = f"{SETTINGS_PREFIX}/log_level"

# Advanced — let the agent run server-supplied PyQGIS code locally in
# response to ``tool_call`` events. Default True; turning this off makes
# the plugin reply ``ok: false`` to every tool_call, which leaves the
# agent unable to complete most turns but is the kill switch if a bad
# code-gen run keeps emitting unsafe steps.
KEY_AGENT_TOOL_EXECUTION = f"{SETTINGS_PREFIX}/advanced/agent_tool_execution"

# Privacy toggles (defaults: all False — see PRIVACY.md).
KEY_SEND_LAYER_PATHS = f"{SETTINGS_PREFIX}/privacy/send_layer_paths"
KEY_TELEMETRY_OPT_IN = f"{SETTINGS_PREFIX}/privacy/telemetry_opt_in"
KEY_CRASH_REPORTS = f"{SETTINGS_PREFIX}/privacy/crash_reports"

# Internal: maps logical credential name -> QGIS auth-manager config id.
AUTH_CFG_IDS_PREFIX = f"{SETTINGS_PREFIX}/_authcfg_ids"
