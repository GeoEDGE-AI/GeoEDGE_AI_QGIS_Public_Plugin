# SPDX-License-Identifier: GPL-2.0-or-later
# Copyright (C) 2024-2026 GeoEdge AI
#
# This file is part of the GeoEdge AI QGIS plugin.
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 2 of the License, or
# (at your option) any later version.
# See the COPYING file or <https://www.gnu.org/licenses/> for details.
"""Code safety validation + restricted execution namespace.

Defence-in-depth wrapping ``exec()`` of server-supplied PyQGIS code:

* :mod:`code_validator` — AST walk that rejects unsafe imports / calls /
  attribute chains before code is executed.
* :mod:`safe_namespace` — locked-down ``__builtins__`` + curated QGIS
  classes the ``exec`` sees; anything outside that namespace is
  effectively missing.

The server runs an equivalent validator before emitting ``tool_call``
events. This package re-validates client-side: if the server is
compromised or the wire is MITM'd, the local AST checks still hold.
"""

from .code_validator import CodeSafetyError, validate_code
from .safe_namespace import build_safe_namespace

__all__ = [
    "CodeSafetyError",
    "build_safe_namespace",
    "validate_code",
]
