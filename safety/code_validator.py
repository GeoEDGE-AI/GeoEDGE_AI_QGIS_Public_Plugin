# SPDX-License-Identifier: GPL-2.0-or-later
# Copyright (C) 2024-2026 GeoEdge AI
#
# This file is part of the GeoEdge AI QGIS plugin.
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 2 of the License, or
# (at your option) any later version.
# See the COPYING file or <https://www.gnu.org/licenses/> for details.
"""AST-based code safety validator for generated PyQGIS scripts.

Walks the AST of a code blob before ``exec`` and rejects:

* imports outside :data:`ALLOWED_IMPORTS`,
* calls to blocked builtins (``exec``, ``eval``, ``compile``, ``open``…),
* blocked attribute chains (``os.system``, ``subprocess.run``,
  ``urllib.request.urlopen``, ``socket.socket``, …),
* dunder access used in classic sandbox escapes
  (``__subclasses__``, ``__globals__``, ``__reduce__``, …),
* ``while True:`` without a reachable ``break``,
* code exceeding :data:`MAX_CODE_LENGTH`.

The server runs an equivalent validator before emitting ``tool_call``
events; this is the second line of defence in case the server is
compromised or the wire is MITM'd.
"""

from __future__ import annotations

import ast
import logging
from typing import Optional

logger = logging.getLogger(__name__)


class CodeSafetyError(Exception):
    """Raised when generated code fails safety validation."""


ALLOWED_IMPORTS: set[str] = {
    "qgis", "qgis.core", "qgis.gui", "qgis.utils", "qgis.analysis",
    "qgis.PyQt", "qgis.PyQt.QtCore", "qgis.PyQt.QtGui", "qgis.PyQt.QtWidgets",
    "processing",
    "math", "statistics", "json", "re", "os.path", "pathlib",
    "datetime", "collections", "itertools", "functools",
    "decimal", "fractions", "copy", "enum", "typing", "dataclasses",
    "textwrap", "string", "uuid", "hashlib", "csv", "io",
    "PyQt5", "PyQt5.QtCore", "PyQt5.QtGui", "PyQt5.QtWidgets",
}

BLOCKED_CALLS: set[str] = {
    "exec", "eval", "compile", "execfile", "__import__",
    "globals", "locals", "breakpoint", "exit", "quit", "input",
    "open", "help", "getattr", "setattr", "delattr",
}

BLOCKED_ATTRIBUTE_CHAINS: set[str] = {
    "os.system", "os.popen", "os.exec", "os.execl", "os.execle",
    "os.execlp", "os.execv", "os.execve", "os.execvp", "os.execvpe",
    "os.spawn", "os.spawnl", "os.spawnle", "os.spawnlp", "os.spawnv",
    "os.spawnve", "os.spawnvp", "os.fork", "os.kill",
    "os.remove", "os.unlink", "os.rmdir", "os.removedirs",
    "os.rename", "os.renames", "os.replace",
    "os.makedirs", "os.mkdir", "os.environ",
    "subprocess.run", "subprocess.call", "subprocess.check_call",
    "subprocess.check_output", "subprocess.Popen",
    "shutil.rmtree", "shutil.move", "shutil.copy", "shutil.copy2", "shutil.copytree",
    "socket.socket",
    "http.client.HTTPConnection", "http.client.HTTPSConnection",
    "urllib.request.urlopen",
    "requests.get", "requests.post", "requests.put", "requests.delete", "requests.patch",
    "ctypes.cdll", "ctypes.windll",
}

BLOCKED_DUNDER_ATTRS: set[str] = {
    # Class-tree traversal — used in classic ``().__class__.__bases__[0]``
    # style escapes to reach ``object`` and walk subclasses.
    "__subclasses__", "__bases__", "__base__", "__mro__", "__class__",
    "__class_getitem__", "__subclasshook__", "__init_subclass__",
    # Reach into the function/module that owns a callable.
    "__globals__", "__code__", "__func__", "__self__", "__closure__",
    "__wrapped__",
    # Generic introspection that exposes builtins/imports.
    "__dict__", "__module__", "__builtins__", "__loader__", "__spec__",
    "__import__",
    # Dunders that let you re-enter the type system.
    "__init__", "__new__", "__getattribute__", "__getattr__",
    "__setattr__", "__delattr__",
    # Pickle-based escapes (``obj.__reduce__()`` returns a callable + args).
    "__reduce__", "__reduce_ex__",
}

MAX_CODE_LENGTH = 5000


class CodeSafetyVisitor(ast.NodeVisitor):
    """Walk an AST tree and collect policy violations as strings."""

    def __init__(self) -> None:
        self.errors: list[str] = []

    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            self._check_import(alias.name, node.lineno)
        self.generic_visit(node)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        module = node.module or ""
        self._check_import(module, node.lineno)
        self.generic_visit(node)

    def _check_import(self, module: str, lineno: int) -> None:
        parts = module.split(".")
        for i in range(len(parts), 0, -1):
            prefix = ".".join(parts[:i])
            if prefix in ALLOWED_IMPORTS:
                return
        self.errors.append(
            f"Line {lineno}: import of '{module}' is not allowed."
        )

    def visit_Call(self, node: ast.Call) -> None:
        if isinstance(node.func, ast.Name) and node.func.id in BLOCKED_CALLS:
            self.errors.append(
                f"Line {node.lineno}: call to builtin '{node.func.id}()' is blocked."
            )
        if isinstance(node.func, ast.Attribute):
            chain = _resolve_attribute_chain(node.func)
            if chain:
                for blocked in BLOCKED_ATTRIBUTE_CHAINS:
                    if _chain_matches(chain, blocked):
                        self.errors.append(
                            f"Line {node.lineno}: call to '{chain}' is blocked."
                        )
                        break
        self.generic_visit(node)

    def visit_Attribute(self, node: ast.Attribute) -> None:
        if node.attr in BLOCKED_DUNDER_ATTRS:
            self.errors.append(
                f"Line {node.lineno}: access to dunder attribute '{node.attr}' is blocked."
            )
        chain = _resolve_attribute_chain(node)
        if chain:
            for blocked in BLOCKED_ATTRIBUTE_CHAINS:
                if _chain_matches(chain, blocked):
                    self.errors.append(
                        f"Line {node.lineno}: attribute access '{chain}' is blocked."
                    )
                    break
        self.generic_visit(node)

    def visit_While(self, node: ast.While) -> None:
        """Reject ``while True:`` loops with no reachable ``break``."""
        if isinstance(node.test, ast.Constant) and node.test.value is True:
            has_break = any(
                isinstance(child, ast.Break)
                for child in ast.walk(node)
            )
            if not has_break:
                self.errors.append(
                    f"Line {node.lineno}: 'while True' without break is not allowed."
                )
        self.generic_visit(node)

    def visit_Lambda(self, node: ast.Lambda) -> None:
        self.generic_visit(node)


def _resolve_attribute_chain(node: ast.AST) -> Optional[str]:
    """Reduce ``a.b.c`` Attribute chain to the literal string ``"a.b.c"``.

    Returns ``None`` if the chain root isn't a plain Name (e.g. a call
    return value — those are not chain-matchable against the literal
    allowlist).
    """
    parts: list[str] = []
    current = node
    while True:
        if isinstance(current, ast.Attribute):
            parts.append(current.attr)
            current = current.value
        elif isinstance(current, ast.Name):
            parts.append(current.id)
            break
        else:
            return None
    parts.reverse()
    return ".".join(parts)


def _chain_matches(chain: str, blocked: str) -> bool:
    return chain == blocked or chain.startswith(blocked + ".")


def validate_code(code: str) -> tuple[bool, Optional[str]]:
    """Validate generated PyQGIS code for safety.

    Returns ``(True, None)`` if the code passes every check;
    ``(False, error_description)`` on the first failure path with all
    collected violations joined by ``"; "``.
    """
    if not code or not code.strip():
        return False, "Empty code string."

    if len(code) > MAX_CODE_LENGTH:
        return False, f"Code exceeds {MAX_CODE_LENGTH} characters."

    try:
        tree = ast.parse(code)
    except SyntaxError as exc:
        return False, f"Syntax error: {exc}"

    visitor = CodeSafetyVisitor()
    visitor.visit(tree)

    if visitor.errors:
        combined = "; ".join(visitor.errors)
        logger.warning("Code safety validation failed: %s", combined)
        return False, combined

    return True, None
