# SPDX-License-Identifier: GPL-2.0-or-later
# Copyright (C) 2024-2026 GeoEdge AI
#
# This file is part of the GeoEdge AI QGIS plugin.
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 2 of the License, or
# (at your option) any later version.
# See the COPYING file or <https://www.gnu.org/licenses/> for details.
"""``QgsTask``-based executor for server-supplied PyQGIS code.

Stage 1 (``run``) executes the code on a background thread provided by
``QgsTaskManager``. Stage 2 (``finished``) is dispatched on the GUI
thread by the task framework; we use it to push results through
:class:`ExecutionSignaller.execution_complete` so the orchestrator's
``tool_call`` handler can wire them into an observation POST.

The code itself sees a curated namespace built by
:func:`GeoEDGE_AI.safety.safe_namespace.build_safe_namespace`. The AST
validator at :func:`GeoEDGE_AI.safety.code_validator.validate_code`
runs **before** the task is constructed; this module trusts that the
code has already been checked.
"""

from __future__ import annotations

import logging
import time
import traceback
from typing import Any, Optional

from ..safety.safe_namespace import build_safe_namespace
from .execution_signaller import ExecutionSignaller

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Conditional QGIS import — keep the class definition importable outside
# QGIS so the module can be loaded for unit tests / py_compile checks.
# ---------------------------------------------------------------------------
try:
    from qgis.core import QgsTask  # type: ignore[import-untyped]

    _HAS_QGS_TASK = True
except ImportError:
    _HAS_QGS_TASK = False

    class QgsTask:  # type: ignore[no-redef]
        """Minimal stub so the class definition works outside QGIS."""

        Complete = True
        Fail = False

        def __init__(self, description: str = "", flags: int = 0) -> None:
            self._description = description

        def isCanceled(self) -> bool:  # noqa: N802 — Qt API
            return False

        def setProgress(self, pct: float) -> None:  # noqa: N802 — Qt API
            pass


class _TaskProxy:
    """Read-only handle exposing only safe ``QgsTask`` methods to ``exec``.

    Generated code that wants to check for cancellation reads
    ``__task__.isCanceled()`` (the namespace injects this); we never
    hand it the real ``QgsTask`` so it can't ``finish()`` itself or
    monkeypatch internal state.
    """

    __slots__ = ("_is_canceled", "_set_progress")

    def __init__(self, task: QgsTask) -> None:
        self._is_canceled = task.isCanceled
        self._set_progress = task.setProgress

    def isCanceled(self) -> bool:  # noqa: N802 — mirror Qt API
        return self._is_canceled()

    def setProgress(self, pct: float) -> None:  # noqa: N802 — mirror Qt API
        self._set_progress(pct)


class GeoEdgeTask(QgsTask):  # type: ignore[misc]
    """Execute one server-supplied PyQGIS step inside ``QgsTaskManager``.

    Parameters
    ----------
    code : str
        The generated PyQGIS source. Already AST-validated.
    step : dict
        Step metadata. Required keys: ``step_id`` (str), ``step_name``
        (str). Optional: ``output_layer_name``.
    safe_globals : dict | None
        Pre-built namespace, or ``None`` to build one via
        :func:`build_safe_namespace`.
    signaller : ExecutionSignaller | None
        Result emitter. A fresh one is constructed when not supplied.
    """

    def __init__(
        self,
        code: str,
        step: dict[str, Any],
        safe_globals: Optional[dict[str, Any]] = None,
        signaller: Optional[ExecutionSignaller] = None,
    ) -> None:
        description = f"GeoEdge step {step.get('step_id', '?')}: {step.get('step_name', 'unnamed')}"
        super().__init__(description)

        self._code = code
        self._step = step
        self._safe_globals = safe_globals
        self._signaller = signaller or ExecutionSignaller()

        # Populated by run()
        self._result_payload: dict[str, Any] = {}
        self._success = False
        self._exec_time: float = 0.0
        # The actual layer object produced by ``processing.run`` — populated
        # from the exec namespace and consumed in ``finished()`` (GUI
        # thread) to register the layer with ``QgsProject``. Subsequent
        # steps reference outputs by name, so the layer MUST be in the
        # project before the next step's tool_call runs.
        self._output_layer_obj: Any = None

    # ------------------------------------------------------------------
    # Properties
    # ------------------------------------------------------------------

    @property
    def signaller(self) -> ExecutionSignaller:
        return self._signaller

    @property
    def result_payload(self) -> dict[str, Any]:
        return self._result_payload

    # ------------------------------------------------------------------
    # Stage 1 — background thread
    # ------------------------------------------------------------------

    def run(self) -> bool:  # type: ignore[override]
        """Execute the code in a sandboxed namespace.

        Returns ``True`` (``QgsTask.Complete``) on success, ``False``
        (``QgsTask.Fail``) on failure. The framework dispatches
        :meth:`finished` on the GUI thread regardless of the return.
        """
        ns = self._build_safe_namespace()

        # Capture print() output per-task without touching sys.stdout
        # (thread-safe — each task builds its own buffer and closure).
        _log_parts: list[str] = []

        def _capture_print(*args: Any, sep: str = " ", end: str = "\n", **_: Any) -> None:
            _log_parts.append(sep.join(str(a) for a in args) + end)

        ns["__builtins__"]["print"] = _capture_print

        start = time.monotonic()
        step_id = self._step.get("step_id")
        step_name = self._step.get("step_name")
        output_layer = self._step.get("output_layer_name")

        try:
            exec(self._code, ns)  # nosec B102 — intentional sandboxed exec (noqa: S102)
            # Templates conventionally bind ``output_layer = result['OUTPUT']``
            # at the end. Capture it for ``finished()`` to register with
            # QgsProject on the GUI thread — chained steps reference
            # previous outputs by NAME via mapLayersByName, so the layer
            # must be in the project before the next tool_call lands.
            self._output_layer_obj = ns.get("output_layer")
            captured_log = "".join(_log_parts)

            # ReAct contract (plan §C5): the model writes code freely and
            # may forget to bind ``output_layer``. Without this binding,
            # ``_register_output_layer`` is a no-op, the next step's
            # ``mapLayersByName`` lookup silently fails, and the model
            # gets ``ok=True`` with no clue why downstream broke. Turn
            # this prompt-violation into an explicit failure so the
            # model can self-correct on the next iteration.
            if self._output_layer_obj is None:
                self._success = False
                directive = (
                    "Script executed but did not bind the variable "
                    "'output_layer'. End your code with "
                    "`output_layer = result['OUTPUT']` (or otherwise "
                    "assign the produced QgsMapLayer)."
                )
                self._result_payload = {
                    "step_id": step_id,
                    "step_name": step_name,
                    "success": False,
                    "output_layer": None,
                    "message": directive,
                    "error_message": directive,
                    "log": captured_log,
                }
            else:
                self._success = True
                message = (
                    f"Step '{step_name}' completed; output layer '{output_layer}'."
                    if output_layer
                    else f"Step '{step_name}' completed."
                )
                self._result_payload = {
                    "step_id": step_id,
                    "step_name": step_name,
                    "success": True,
                    "output_layer": output_layer,
                    "message": message,
                    "log": captured_log,
                }
        except Exception as exc:
            self._success = False
            tb = traceback.format_exc()
            captured_log = "".join(_log_parts)
            logger.error(
                "Step %s (%s) failed: %s\n%s",
                step_id,
                step_name,
                exc,
                tb,
            )
            self._result_payload = {
                "step_id": step_id,
                "step_name": step_name,
                "success": False,
                "output_layer": None,
                "message": f"Step '{step_name}' failed: {exc}",
                "error_message": str(exc),
                "traceback": tb,
                "log": captured_log,
            }
        finally:
            self._exec_time = time.monotonic() - start
            self._result_payload["execution_time_seconds"] = round(self._exec_time, 3)

        return self._success

    # ------------------------------------------------------------------
    # Stage 2 — GUI thread (invoked by the task framework)
    # ------------------------------------------------------------------

    def finished(self, result: bool) -> None:  # type: ignore[override]
        """Register the output layer with ``QgsProject`` then emit results.

        Runs on the GUI thread per the ``QgsTask`` contract — the only
        safe place to touch ``QgsProject.instance().addMapLayer``.
        """
        self._result_payload["task_result"] = result
        if self._success and self._output_layer_obj is not None:
            self._register_output_layer()
        self._signaller.execution_complete.emit(self._result_payload)
        logger.info(
            "Step %s finished (success=%s, %.2fs).",
            self._step.get("step_id"),
            result,
            self._exec_time,
        )

    def _register_output_layer(self) -> None:
        """Add the exec'd step's output layer to ``QgsProject``.

        Subsequent steps reference outputs by name via
        ``QgsProject.instance().mapLayersByName(...)``; without this
        registration the next ``processing.run`` raises
        "Could not load source layer for INPUT".
        """
        try:
            from qgis.core import (  # type: ignore[import-untyped]
                QgsMapLayer,
                QgsProject,
                QgsRasterLayer,
                QgsVectorLayer,
            )
        except ImportError:
            logger.debug("qgis.core unavailable; skipping layer registration.")
            return

        layer = self._output_layer_obj
        desired_name = self._step.get("output_layer_name")
        step_id = self._step.get("step_id")

        # ``processing.run`` is inconsistent about what it returns for
        # OUTPUT: some algorithms hand back the loaded layer object,
        # others hand back a URI string (``"memory:<name>"`` or a file
        # path). When we see a string, construct the corresponding
        # layer ourselves — otherwise the next step's ``mapLayersByName``
        # would silently fail.
        if isinstance(layer, str):
            uri = layer
            name = desired_name or (uri.split(":", 1)[1] if ":" in uri else "output")
            logger.info(
                "Step %s: output is URI %r; constructing layer (%s).",
                step_id, uri, name,
            )
            if uri.startswith("memory:"):
                layer = QgsVectorLayer(uri, name, "memory")
            else:
                vec = QgsVectorLayer(uri, name, "ogr")
                if vec.isValid():
                    layer = vec
                else:
                    layer = QgsRasterLayer(uri, name)

        if not isinstance(layer, QgsMapLayer):
            logger.warning(
                "Step %s: output_layer is %s, not a QgsMapLayer; "
                "skipping addMapLayer. Subsequent steps that look up "
                "this layer by name will fail.",
                step_id,
                type(layer).__name__,
            )
            return

        if not layer.isValid():
            logger.warning(
                "Step %s: output layer %r is invalid (source=%r); "
                "not adding to project.",
                step_id,
                layer.name() if hasattr(layer, "name") else "?",
                layer.source() if hasattr(layer, "source") else "?",
            )
            return

        # Defence-in-depth: if the bound layer is already registered in
        # QgsProject (model bound `output_layer` to an INPUT/existing
        # layer to satisfy the C5 contract instead of producing a new
        # one), neither rename it nor re-add it. The unconditional
        # ``setName`` below would otherwise corrupt the user's project
        # by renaming a layer they care about.
        try:
            existing = QgsProject.instance().mapLayer(layer.id())
        except Exception:
            existing = None
        if existing is not None:
            logger.warning(
                "Step %s: output_layer was bound to a layer already in "
                "QgsProject (id=%s, name=%r). Skipping setName + "
                "addMapLayer to avoid renaming the user's existing "
                "layer. The model should have produced a NEW layer "
                "for this step.",
                step_id,
                layer.id(),
                layer.name() if hasattr(layer, "name") else "?",
            )
            return

        if desired_name:
            try:
                layer.setName(desired_name)
            except Exception:
                logger.debug("Failed to setName on output layer.", exc_info=True)

        try:
            added = QgsProject.instance().addMapLayer(layer)
            if added is None:
                logger.warning(
                    "Step %s: addMapLayer returned None for layer %r — "
                    "subsequent name lookup will fail.",
                    step_id,
                    layer.name(),
                )
            else:
                logger.info(
                    "Step %s: registered output layer '%s' (id=%s).",
                    step_id,
                    added.name(),
                    added.id(),
                )
        except Exception:
            logger.exception(
                "Failed to register output layer for step %s.", step_id,
            )

    # ------------------------------------------------------------------
    # Namespace builder
    # ------------------------------------------------------------------

    def _build_safe_namespace(self) -> dict[str, Any]:
        """Return the execution namespace, enriched with step context."""
        if self._safe_globals is not None:
            ns = dict(self._safe_globals)
        else:
            ns = build_safe_namespace()

        # Convenience refs the generated code may rely on.
        ns["__step__"] = self._step
        ns["__task__"] = _TaskProxy(self)
        ns["__log__"] = ""
        return ns
