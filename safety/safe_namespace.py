# SPDX-License-Identifier: GPL-2.0-or-later
# Copyright (C) 2024-2026 GeoEdge AI
#
# This file is part of the GeoEdge AI QGIS plugin.
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 2 of the License, or
# (at your option) any later version.
# See the COPYING file or <https://www.gnu.org/licenses/> for details.
"""Build a restricted globals namespace for safe ``exec`` of generated code.

The namespace exposes a curated allowlist of QGIS classes plus a
locked-down ``__builtins__``. Anything not explicitly listed is missing
from the ``exec`` frame's view — the AST validator (see
:mod:`code_validator`) is the first gate, this namespace is the second.
"""

from __future__ import annotations

import builtins
import logging
import math
from typing import Any

logger = logging.getLogger(__name__)

# ``__import__`` has to be present in ``__builtins__`` for plain ``import X``
# statements to work inside ``exec``. The :mod:`code_validator` runs first
# and already gates which modules can be imported (via ``ALLOWED_IMPORTS``)
# at the AST level, so exposing ``__import__`` here doesn't widen the
# attack surface — the validator rejects ``import socket`` long before
# this builtin is reached. Direct runtime calls to ``__import__(...)``
# from generated code remain blocked by the validator's ``BLOCKED_CALLS``.
_ALLOWED_BUILTINS: dict[str, Any] = {
    "__import__": builtins.__import__,
    "bool": bool, "int": int, "float": float, "str": str,
    "list": list, "dict": dict, "tuple": tuple, "set": set,
    "frozenset": frozenset, "bytes": bytes, "bytearray": bytearray, "complex": complex,
    "range": range, "enumerate": enumerate, "zip": zip, "map": map,
    "filter": filter, "reversed": reversed, "sorted": sorted,
    "len": len, "sum": sum, "min": min, "max": max, "abs": abs,
    "round": round, "pow": pow, "divmod": divmod,
    "isinstance": isinstance, "issubclass": issubclass, "callable": callable,
    "hasattr": hasattr,
    "hex": hex, "oct": oct, "bin": bin, "ord": ord, "chr": chr,
    "repr": repr, "hash": hash, "id": id,
    "print": print, "format": format, "any": any, "all": all,
    "iter": iter, "next": next, "slice": slice,
    "Exception": Exception, "ValueError": ValueError, "TypeError": TypeError,
    "KeyError": KeyError, "IndexError": IndexError, "RuntimeError": RuntimeError,
    "StopIteration": StopIteration, "AttributeError": AttributeError,
    "None": None, "True": True, "False": False,
}

_QGIS_CORE_CLASSES = (
    "QgsVectorLayer", "QgsRasterLayer", "QgsFeature", "QgsGeometry",
    "QgsField", "QgsFields", "QgsPointXY", "QgsRectangle",
    "QgsCoordinateReferenceSystem", "QgsCoordinateTransform",
    "QgsCoordinateTransformContext", "QgsExpression",
    "QgsExpressionContext", "QgsExpressionContextScope",
    "QgsExpressionContextUtils", "QgsProcessingFeedback",
    "QgsVectorFileWriter", "QgsRasterFileWriter",
    "QgsMapLayerType", "QgsWkbTypes", "QgsUnitTypes",
    "QgsMapSettings", "QgsLayerTreeGroup", "QgsLayerTreeLayer",
    "QgsFillSymbol", "QgsLineSymbol", "QgsMarkerSymbol",
    "QgsSingleSymbolRenderer", "QgsGraduatedSymbolRenderer",
    "QgsCategorizedSymbolRenderer", "QgsRuleBasedRenderer",
    "QgsRendererRange", "QgsSymbol",
    "QgsSimpleFillSymbolLayer", "QgsSimpleLineSymbolLayer",
    "QgsSimpleMarkerSymbolLayer",
    "QgsPalLayerSettings", "QgsVectorLayerSimpleLabeling", "QgsTextFormat",
    "QgsRasterBandStats", "QgsColorRampShader", "QgsRasterShader",
    "QgsSingleBandPseudoColorRenderer", "QgsStyle", "QgsGradientColorRamp",
    "QgsProject",
)


def _try_import_qgis_core() -> dict[str, Any]:
    ns: dict[str, Any] = {}
    try:
        import qgis.core as qc
        for name in _QGIS_CORE_CLASSES:
            obj = getattr(qc, name, None)
            if obj is not None:
                ns[name] = obj
    except ImportError:
        logger.debug("qgis.core not available.")
    return ns


def _try_import_processing() -> dict[str, Any]:
    ns: dict[str, Any] = {}
    try:
        import processing
        ns["processing"] = processing
    except ImportError:
        logger.debug("processing module not available.")
    return ns


def _try_import_pyqt() -> dict[str, Any]:
    ns: dict[str, Any] = {}
    try:
        from qgis.PyQt.QtCore import QVariant
        ns["QVariant"] = QVariant
    except ImportError:
        pass
    try:
        from qgis.PyQt.QtGui import QColor, QFont
        ns["QColor"] = QColor
        ns["QFont"] = QFont
    except ImportError:
        pass
    return ns


def build_safe_namespace() -> dict[str, Any]:
    """Construct a restricted globals dict for use with ``exec``.

    Includes only the curated builtins, ``math``, and the whitelisted
    QGIS / PyQt classes. Generated code that needs anything else fails
    fast with ``NameError`` inside ``exec`` rather than silently reaching
    out to the host environment.
    """
    ns: dict[str, Any] = {
        "__builtins__": dict(_ALLOWED_BUILTINS),
        "math": math,
    }
    ns.update(_try_import_qgis_core())
    ns.update(_try_import_processing())
    ns.update(_try_import_pyqt())
    return ns
