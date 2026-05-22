# SPDX-License-Identifier: GPL-2.0-or-later
# Copyright (C) 2024-2026 GeoEdge AI
#
# This file is part of the GeoEdge AI QGIS plugin.
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 2 of the License, or
# (at your option) any later version.
# See the COPYING file or <https://www.gnu.org/licenses/> for details.
"""Scheme-validated ``urlopen`` wrapper.

The API base URL is user-configurable via QSettings, which means a
misconfigured (or maliciously-set) value could point at ``file://`` or a
custom scheme and exfiltrate bearer tokens / read local files. Every
network call in this plugin routes through :func:`safe_urlopen`, which
refuses anything other than http(s) before the request leaves the
process.
"""

from __future__ import annotations

from typing import Any
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

# http retained for localhost dev against a self-hosted backend.
_ALLOWED_SCHEMES = ("https", "http")


def safe_urlopen(req: Request, *, timeout: float) -> Any:
    """``urlopen`` with an http(s)-only scheme check on the request URL."""
    scheme = urlsplit(req.full_url).scheme.lower()
    if scheme not in _ALLOWED_SCHEMES:
        raise ValueError(
            f"Refusing to open URL with scheme {scheme!r}; "
            f"allowed: {_ALLOWED_SCHEMES}"
        )
    return urlopen(req, timeout=timeout)  # nosec B310 — scheme validated above
