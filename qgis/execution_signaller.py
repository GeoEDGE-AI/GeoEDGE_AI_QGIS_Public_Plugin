# SPDX-License-Identifier: GPL-2.0-or-later
# Copyright (C) 2024-2026 GeoEdge AI
#
# This file is part of the GeoEdge AI QGIS plugin.
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 2 of the License, or
# (at your option) any later version.
# See the COPYING file or <https://www.gnu.org/licenses/> for details.
"""Signal relay between a ``GeoEdgeTask`` worker thread and a Qt slot.

``QgsTask`` cannot reliably carry custom ``pyqtSignal`` attributes
across every PyQGIS build (it's not a pure ``QObject`` subclass on all
platforms). This tiny ``QObject`` lives on the GUI thread and is used
by :class:`GeoEDGE_AI.qgis.executor.GeoEdgeTask` to emit results back to
whoever connected to ``execution_complete`` once the background ``run``
finishes.
"""

from __future__ import annotations

from qgis.PyQt.QtCore import QObject, pyqtSignal  # type: ignore[import-untyped]


class ExecutionSignaller(QObject):  # type: ignore[misc]
    """Cross-thread signal emitter for ``GeoEdgeTask`` results.

    Signals
    -------
    execution_complete : dict
        Payload containing ``success``, ``output_layer``, ``message`` /
        ``error_message``, ``traceback`` (on failure), and
        ``execution_time_seconds``. Consumers translate this into the
        wire observation dict POSTed to ``/v1/agent/observation``.
    step_progress : (str, float)
        ``(step_id, progress)`` where ``progress`` is in ``[0, 1]``.
        Currently unused but kept for forward compatibility with progress
        reporting from inside the executed code (``__task__.setProgress``).
    """

    execution_complete = pyqtSignal(dict)
    step_progress = pyqtSignal(str, float)
