# SPDX-License-Identifier: GPL-2.0-or-later
# Copyright (C) 2024-2026 GeoEdge AI
#
# This file is part of the GeoEdge AI QGIS plugin.
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 2 of the License, or
# (at your option) any later version.
# See the COPYING file or <https://www.gnu.org/licenses/> for details.
"""Chat panel — message log + input row + send button."""

from __future__ import annotations

import logging
import re

try:
    from qgis.PyQt.QtCore import Qt, pyqtSignal
    from qgis.PyQt.QtGui import QFont
    from qgis.PyQt.QtWidgets import (
        QHBoxLayout,
        QLabel,
        QPlainTextEdit,
        QPushButton,
        QTextBrowser,
        QVBoxLayout,
        QWidget,
    )

    HAS_QT = True
except ImportError:
    HAS_QT = False

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Theme
# ---------------------------------------------------------------------------

_COLORS = {
    "bg": "#1e1e2e",
    "surface": "#242438",
    "surface_alt": "#313148",
    "surface_elev": "#2a2a42",
    "primary": "#7c6ff0",
    "primary_hover": "#6a5cd8",
    "primary_disabled": "#4a4670",
    "primary_accent": "#9d8cf2",
    "text": "#e8e8f0",
    "text_dim": "#8888aa",
    "text_muted": "#6c6c8a",
    "user_bubble": "#363656",
    "agent_bubble": "#2a2a42",
    "border": "#3a3a5c",
    "border_subtle": "#2e2e48",
    "error": "#e57373",
    "code_bg": "#1a1a28",
    "code_text": "#dcdcec",
    "link": "#9eb4ff",
    # Luminous light green for the marketing CTA in the status bar.
    # High contrast against #242438 surface; reads as a hyperlink
    # without colliding with the lavender of the regular link colour.
    "marketing_green": "#7cf078",
}

# Cross-platform sans-serif fallback chain. Segoe UI lands on every
# Windows version; SF / Helvetica land on macOS; Cantarell / Noto on
# Linux. "Inter" first so power users with it installed get the
# crispest rendering.
_FONT_STACK = (
    "Inter, 'Segoe UI', -apple-system, BlinkMacSystemFont, "
    "'Helvetica Neue', 'Cantarell', 'Noto Sans', sans-serif"
)
_MONO_STACK = (
    "'JetBrains Mono', 'Cascadia Code', 'Consolas', 'Menlo', "
    "'Monaco', 'Liberation Mono', monospace"
)

# Body text size in the chat log. 14px reads comfortably at a normal
# viewing distance and stays readable on hi-DPI displays where 11-12px
# Qt defaults look cramped.
_BODY_PX = 14
_CHROME_PX = 12   # status / usage labels


# ---------------------------------------------------------------------------
# Minimal markdown renderer
# ---------------------------------------------------------------------------


def _render_markdown(text: str) -> str:
    """Render a deliberately-small markdown subset to HTML.

    Goal: make the agent's reply look like a chat reply instead of raw
    markdown source. Supports:

    - Headers: ``# H1`` … ``### H3`` (line-start)
    - Bold: ``**text**``
    - Italic: ``*text*`` (asterisks only; underscores collide with
      common QGIS identifiers like ``output_layer``)
    - Inline code: `` `code` ``
    - Fenced code blocks: ``` ... ```
    - Bullet lists: ``- item`` / ``* item``
    - Numbered lists: ``1. item``
    - Links: ``[text](https://…)``
    - Paragraphs (blank-line separated)

    Anything else is HTML-escaped and rendered as plain text. Order of
    operations is fragile — code blocks are extracted to placeholders
    first so their contents don't get further processed.
    """
    # 1) Extract fenced code blocks to placeholders.
    code_blocks: list[str] = []

    def _stash_block(m: re.Match) -> str:
        body = m.group(2)
        code_blocks.append(body)
        return f"\x00BLOCK{len(code_blocks) - 1}\x00"

    fenced = re.sub(
        r"```(\w*)\n(.*?)```",
        _stash_block,
        text,
        flags=re.DOTALL,
    )

    # 2) Extract inline code (`code`) to placeholders too.
    inline_codes: list[str] = []

    def _stash_inline(m: re.Match) -> str:
        inline_codes.append(m.group(1))
        return f"\x00INLINE{len(inline_codes) - 1}\x00"

    after_inline = re.sub(r"`([^`\n]+)`", _stash_inline, fenced)

    # 3) HTML-escape everything that remains.
    escaped = (
        after_inline
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )

    # 4) Headers (line-start).
    escaped = re.sub(
        r"^### (.+)$",
        rf"<h3 style='margin:8px 0 4px 0; font-size:{_BODY_PX + 1}px; "
        rf"color:{_COLORS['text']}; font-weight:600;'>\1</h3>",
        escaped,
        flags=re.MULTILINE,
    )
    escaped = re.sub(
        r"^## (.+)$",
        rf"<h2 style='margin:10px 0 4px 0; font-size:{_BODY_PX + 2}px; "
        rf"color:{_COLORS['text']}; font-weight:600;'>\1</h2>",
        escaped,
        flags=re.MULTILINE,
    )
    escaped = re.sub(
        r"^# (.+)$",
        rf"<h1 style='margin:12px 0 6px 0; font-size:{_BODY_PX + 4}px; "
        rf"color:{_COLORS['text']}; font-weight:700;'>\1</h1>",
        escaped,
        flags=re.MULTILINE,
    )

    # 5) Bold / italic — bold first (since ** would steal asterisks).
    escaped = re.sub(r"\*\*([^*\n]+)\*\*", r"<b>\1</b>", escaped)
    escaped = re.sub(r"(?<!\*)\*([^*\n]+)\*(?!\*)", r"<i>\1</i>", escaped)

    # 6) Links [text](url). Restrict to http(s) URLs.
    escaped = re.sub(
        r"\[([^\]]+)\]\((https?://[^\s)]+)\)",
        rf"<a href='\2' style='color:{_COLORS['link']}; "
        rf"text-decoration:none;'>\1</a>",
        escaped,
    )

    # 7) Lists — group consecutive list lines into <ul>/<ol>.
    lines = escaped.split("\n")
    out_lines: list[str] = []
    i = 0
    while i < len(lines):
        line = lines[i]
        # Bullet list?
        if re.match(r"^[\-\*] ", line):
            items = []
            while i < len(lines) and re.match(r"^[\-\*] ", lines[i]):
                items.append(re.sub(r"^[\-\*] ", "", lines[i]))
                i += 1
            out_lines.append(
                "<ul style='margin:4px 0 4px 18px; padding:0;'>"
                + "".join(f"<li style='margin:2px 0;'>{it}</li>" for it in items)
                + "</ul>"
            )
            continue
        # Numbered list?
        if re.match(r"^\d+\. ", line):
            items = []
            while i < len(lines) and re.match(r"^\d+\. ", lines[i]):
                items.append(re.sub(r"^\d+\. ", "", lines[i]))
                i += 1
            out_lines.append(
                "<ol style='margin:4px 0 4px 22px; padding:0;'>"
                + "".join(f"<li style='margin:2px 0;'>{it}</li>" for it in items)
                + "</ol>"
            )
            continue
        out_lines.append(line)
        i += 1
    escaped = "\n".join(out_lines)

    # 8) Paragraphs: blank-line-separated chunks become <p>…</p>;
    # in-chunk newlines stay as <br/>. Skip wrapping when a chunk is
    # already block-level (header / list / placeholder).
    paragraphs = []
    for chunk in re.split(r"\n\s*\n", escaped):
        chunk = chunk.strip("\n")
        if not chunk:
            continue
        first = chunk.lstrip()
        if (
            first.startswith("<h1")
            or first.startswith("<h2")
            or first.startswith("<h3")
            or first.startswith("<ul")
            or first.startswith("<ol")
            or first.startswith("\x00BLOCK")
        ):
            paragraphs.append(chunk.replace("\n", "<br/>"))
        else:
            paragraphs.append(
                f"<p style='margin:6px 0; line-height:1.55;'>"
                f"{chunk.replace(chr(10), '<br/>')}</p>"
            )
    rendered = "\n".join(paragraphs)

    # 9) Restore inline-code placeholders with their own style.
    def _restore_inline(m: re.Match) -> str:
        idx = int(m.group(1))
        body = (
            inline_codes[idx]
            .replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;")
        )
        return (
            f"<code style='background:{_COLORS['code_bg']}; "
            f"color:{_COLORS['code_text']}; "
            f"padding:1px 5px; border-radius:3px; "
            f"font-family:{_MONO_STACK}; "
            f"font-size:{_BODY_PX - 1}px;'>{body}</code>"
        )

    rendered = re.sub(r"\x00INLINE(\d+)\x00", _restore_inline, rendered)

    # 10) Restore fenced-code placeholders.
    def _restore_block(m: re.Match) -> str:
        idx = int(m.group(1))
        body = (
            code_blocks[idx]
            .replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;")
        )
        return (
            f"<pre style='background:{_COLORS['code_bg']}; "
            f"color:{_COLORS['code_text']}; "
            f"padding:8px 10px; border-radius:6px; "
            f"border:1px solid {_COLORS['border_subtle']}; "
            f"margin:6px 0; overflow-x:auto; "
            f"font-family:{_MONO_STACK}; "
            f"font-size:{_BODY_PX - 1}px; line-height:1.45; "
            f"white-space:pre-wrap;'>{body}</pre>"
        )

    rendered = re.sub(r"\x00BLOCK(\d+)\x00", _restore_block, rendered)
    return rendered


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _html_escape(text: str) -> str:
    return (
        text
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace("\n", "<br/>")
    )


# ---------------------------------------------------------------------------
# Widgets
# ---------------------------------------------------------------------------


if HAS_QT:

    class _ChatInput(QPlainTextEdit):
        """QPlainTextEdit that submits on Enter, newline on Shift+Enter."""

        def __init__(self, on_submit, parent: QWidget | None = None) -> None:
            super().__init__(parent)
            self._on_submit = on_submit

        def keyPressEvent(self, event):  # type: ignore[override]
            if event.key() in (Qt.Key_Return, Qt.Key_Enter) and not (
                event.modifiers() & Qt.ShiftModifier
            ):
                self._on_submit()
                event.accept()
                return
            super().keyPressEvent(event)


    class GeoEdgeChatPanel(QWidget):
        """Chat panel widget.

        Signals
        -------
        send_requested(str)
            Emitted when the user submits a message.
        cancel_requested()
            Emitted when the user clicks Cancel during an in-flight turn.
        sign_in_requested()
            Emitted when the user clicks the (visible-when-signed-out)
            "Sign in" button.
        """

        send_requested = pyqtSignal(str)
        cancel_requested = pyqtSignal()
        sign_in_requested = pyqtSignal()

        def __init__(self, parent: QWidget | None = None) -> None:
            super().__init__(parent)
            self.setStyleSheet(
                f"QWidget {{ background-color: {_COLORS['bg']}; "
                f"color: {_COLORS['text']}; "
                f"font-family: {_FONT_STACK}; }}"
            )
            self._build_ui()
            self.set_authenticated(False)

        def _build_ui(self) -> None:
            v = QVBoxLayout(self)
            v.setContentsMargins(10, 10, 10, 10)
            v.setSpacing(8)

            # ---- Status / usage row -----------------------------------
            status_row = QHBoxLayout()
            status_row.setSpacing(8)
            self._status = QLabel("")
            # Rich text + external-link support so the signed-in status
            # can carry an inline marketing CTA. Without RichText the
            # <a> tag would render as literal characters.
            self._status.setTextFormat(Qt.RichText)
            self._status.setOpenExternalLinks(True)
            self._status.setTextInteractionFlags(
                Qt.TextBrowserInteraction
            )
            self._status.setStyleSheet(
                f"color: {_COLORS['text_dim']}; "
                f"font-size: {_CHROME_PX}px; "
                f"padding: 4px 6px;"
            )
            status_row.addWidget(self._status, 1)
            self._usage = QLabel("")
            self._usage.setStyleSheet(
                f"color: {_COLORS['text_muted']}; "
                f"font-size: {_CHROME_PX}px; "
                f"padding: 4px 6px;"
            )
            status_row.addWidget(self._usage)
            v.addLayout(status_row)

            # ---- Message log ------------------------------------------
            self._log = QTextBrowser()
            self._log.setOpenExternalLinks(True)
            # Default font on the document — used as the baseline
            # everywhere the per-element CSS doesn't override.
            self._log.document().setDefaultFont(
                QFont("Segoe UI", _BODY_PX, QFont.Normal),
            )
            self._log.setStyleSheet(
                f"QTextBrowser {{ "
                f"background-color: {_COLORS['surface']}; "
                f"color: {_COLORS['text']}; "
                f"border: 1px solid {_COLORS['border']}; "
                f"border-radius: 8px; padding: 10px 12px; "
                f"font-family: {_FONT_STACK}; "
                f"font-size: {_BODY_PX}px; "
                f"selection-background-color: {_COLORS['primary']}; "
                f"selection-color: white; "
                f"}}"
                f"QScrollBar:vertical {{ "
                f"background: {_COLORS['surface']}; "
                f"width: 10px; border: none; }}"
                f"QScrollBar::handle:vertical {{ "
                f"background: {_COLORS['border']}; "
                f"border-radius: 4px; min-height: 24px; }}"
                f"QScrollBar::handle:vertical:hover {{ "
                f"background: {_COLORS['primary_disabled']}; }}"
                f"QScrollBar::add-line:vertical, "
                f"QScrollBar::sub-line:vertical {{ height: 0; }}"
            )
            v.addWidget(self._log, 1)

            # ---- Input row --------------------------------------------
            input_row = QHBoxLayout()
            input_row.setSpacing(8)
            self._input = _ChatInput(self._on_send)
            self._input.setPlaceholderText(
                "Describe what you want to do — e.g. 'buffer roads by 100 m'"
                "  (Enter to send, Shift+Enter for newline)"
            )
            self._input.setFixedHeight(82)
            self._input.setStyleSheet(
                f"QPlainTextEdit {{ "
                f"background-color: {_COLORS['surface_alt']}; "
                f"color: {_COLORS['text']}; "
                f"border: 1px solid {_COLORS['border']}; "
                f"border-radius: 8px; padding: 8px 10px; "
                f"font-family: {_FONT_STACK}; "
                f"font-size: {_BODY_PX}px; "
                f"selection-background-color: {_COLORS['primary']}; "
                f"selection-color: white; "
                f"}}"
                f"QPlainTextEdit:focus {{ "
                f"border: 1px solid {_COLORS['primary_accent']}; }}"
            )
            input_row.addWidget(self._input, 1)

            # ---- Button column ----------------------------------------
            btn_col = QVBoxLayout()
            btn_col.setSpacing(6)
            btn_style_primary = (
                f"QPushButton {{ "
                f"background-color: {_COLORS['primary']}; "
                f"color: white; border: none; "
                f"border-radius: 8px; padding: 8px 18px; "
                f"font-family: {_FONT_STACK}; "
                f"font-size: {_BODY_PX - 1}px; font-weight: 600; "
                f"min-width: 80px; "
                f"}}"
                f"QPushButton:hover {{ "
                f"background-color: {_COLORS['primary_hover']}; }}"
                f"QPushButton:disabled {{ "
                f"background-color: {_COLORS['primary_disabled']}; }}"
            )
            self._btn_send = QPushButton("Send")
            self._btn_send.setStyleSheet(btn_style_primary)
            self._btn_send.clicked.connect(self._on_send)
            btn_col.addWidget(self._btn_send)

            self._btn_cancel = QPushButton("Cancel")
            self._btn_cancel.setStyleSheet(
                f"QPushButton {{ "
                f"background-color: {_COLORS['surface_alt']}; "
                f"color: {_COLORS['text']}; "
                f"border: 1px solid {_COLORS['border']}; "
                f"border-radius: 8px; padding: 8px 18px; "
                f"font-family: {_FONT_STACK}; "
                f"font-size: {_BODY_PX - 1}px; font-weight: 600; "
                f"min-width: 80px; "
                f"}}"
                f"QPushButton:hover {{ "
                f"background-color: {_COLORS['surface_elev']}; "
                f"border-color: {_COLORS['primary_accent']}; }}"
            )
            self._btn_cancel.setVisible(False)
            self._btn_cancel.clicked.connect(self.cancel_requested.emit)
            btn_col.addWidget(self._btn_cancel)

            self._btn_signin = QPushButton("Sign in")
            self._btn_signin.setStyleSheet(btn_style_primary)
            self._btn_signin.clicked.connect(self.sign_in_requested.emit)
            btn_col.addWidget(self._btn_signin)

            btn_col.addStretch()
            input_row.addLayout(btn_col)
            v.addLayout(input_row)

        # ------------------------------------------------------------------
        # Public API
        # ------------------------------------------------------------------

        def set_authenticated(self, signed_in: bool, *, email: str | None = None) -> None:
            """Toggle between signed-in (Send visible) and signed-out (Sign in visible)."""
            self._btn_send.setVisible(signed_in)
            self._btn_cancel.setVisible(False)
            self._btn_signin.setVisible(not signed_in)
            self._input.setEnabled(signed_in)
            if signed_in:
                # Embed a luminous-green marketing CTA inline with the
                # signed-in status so it stays visible during normal
                # use without claiming a separate row. The QLabel was
                # configured to render RichText + open external links.
                identity = (
                    f"Signed in as {_html_escape(email)}." if email
                    else "Signed in."
                )
                msg = (
                    f"<span style='color:{_COLORS['text_dim']};'>"
                    f"{identity}</span>"
                    f"&nbsp;&nbsp;"
                    f"<a href='https://www.geoedge.com.au/' "
                    f"style='color:{_COLORS['marketing_green']}; "
                    f"text-decoration:none; font-weight:600;'>"
                    f"For full features, visit geoedge.com.au</a>"
                )
            else:
                msg = "Sign in to start. A free starter plan is available."
            self.set_status(msg)

        def set_status(self, text: str) -> None:
            self._status.setText(text)

        def set_usage(self, text: str) -> None:
            self._usage.setText(text)

        def show_streaming(self, in_flight: bool) -> None:
            self._btn_send.setVisible(not in_flight)
            self._btn_cancel.setVisible(in_flight)
            self._input.setEnabled(not in_flight)

        def append_user(self, text: str) -> None:
            # User messages stay plain-text (no markdown rendering) —
            # users don't typically write markdown into a chat prompt,
            # and rendering it would create stray formatting from
            # accidental punctuation.
            self._log.append(
                f"<div style='margin: 8px 0; padding: 10px 14px; "
                f"background:{_COLORS['user_bubble']}; "
                f"border-radius: 10px; "
                f"border-left: 3px solid {_COLORS['primary_accent']}; "
                f"line-height: 1.55;'>"
                f"<div style='font-size: {_CHROME_PX}px; "
                f"color:{_COLORS['text_dim']}; "
                f"letter-spacing: 0.3px; "
                f"text-transform: uppercase; margin-bottom: 4px;'>You</div>"
                f"<div style='color:{_COLORS['text']}; "
                f"font-size:{_BODY_PX}px;'>{_html_escape(text)}</div>"
                f"</div>"
            )
            self._scroll_bottom()

        def append_agent(self, markdown: str) -> None:
            # Agent replies are real markdown — render the supported
            # subset so headers, lists, **bold**, and `code` look like
            # a polished chat reply rather than raw source.
            body = _render_markdown(markdown)
            self._log.append(
                f"<div style='margin: 8px 0; padding: 10px 14px; "
                f"background:{_COLORS['agent_bubble']}; "
                f"border-radius: 10px; "
                f"border-left: 3px solid {_COLORS['primary']}; "
                f"line-height: 1.55;'>"
                f"<div style='font-size: {_CHROME_PX}px; "
                f"color:{_COLORS['primary_accent']}; "
                f"letter-spacing: 0.3px; "
                f"text-transform: uppercase; margin-bottom: 4px;'>"
                f"GeoEdge AI</div>"
                f"<div style='color:{_COLORS['text']}; "
                f"font-size:{_BODY_PX}px;'>{body}</div>"
                f"</div>"
            )
            self._scroll_bottom()

        def append_system(self, text: str, *, error: bool = False) -> None:
            colour = _COLORS["error"] if error else _COLORS["text_muted"]
            self._log.append(
                f"<div style='margin: 4px 0 4px 6px; padding: 2px 0; "
                f"color:{colour}; font-style:italic; "
                f"font-size:{_BODY_PX - 1}px;'>"
                f"· {_html_escape(text)}</div>"
            )
            self._scroll_bottom()

        def clear_input(self) -> None:
            self._input.clear()

        def clear_log(self) -> None:
            """Wipe the message log + input draft + usage counter. Used on
            logout so the next user on a shared workstation doesn't see
            the previous transcript or inherit a half-typed message."""
            self._log.clear()
            self._usage.setText("")
            self._input.clear()

        def _scroll_bottom(self) -> None:
            scrollbar = self._log.verticalScrollBar()
            scrollbar.setValue(scrollbar.maximum())

        def _on_send(self) -> None:
            # Guard against Enter being pressed while signed-out or while
            # a turn is already in flight — both states hide the Send
            # button, but the keyboard shortcut would otherwise still fire.
            if not self._btn_send.isVisible() or not self._btn_send.isEnabled():
                return
            text = self._input.toPlainText().strip()
            if not text:
                return
            self.send_requested.emit(text)


else:

    class GeoEdgeChatPanel:  # type: ignore[no-redef]
        """Stub — PyQt5 not available outside QGIS."""

        def __init__(self, *args, **kwargs):
            raise RuntimeError(
                "GeoEdgeChatPanel requires PyQt5 (run inside QGIS)."
            )
