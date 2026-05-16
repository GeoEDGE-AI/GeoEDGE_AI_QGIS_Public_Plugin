# SPDX-License-Identifier: GPL-2.0-or-later
# Copyright (C) 2024-2026 GeoEdge AI
#
# This file is part of the GeoEdge AI QGIS plugin.
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 2 of the License, or
# (at your option) any later version.
# See the COPYING file or <https://www.gnu.org/licenses/> for details.
"""QGIS-side helpers.

* :mod:`credentials` — auth-DB / QSettings backed token storage.
* :mod:`executor` — ``QgsTask`` runner for server-supplied PyQGIS code.
* :mod:`execution_signaller` — cross-thread result emitter for the
  executor.
"""
