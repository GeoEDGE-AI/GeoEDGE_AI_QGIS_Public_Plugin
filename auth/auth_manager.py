# SPDX-License-Identifier: GPL-2.0-or-later
# Copyright (C) 2024-2026 GeoEdge AI
#
# This file is part of the GeoEdge AI QGIS plugin.
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 2 of the License, or
# (at your option) any later version.
# See the COPYING file or <https://www.gnu.org/licenses/> for details.
"""Authentication manager for the GeoEdge AI cloud service.

Tokens live only in memory; persistence is delegated to
:class:`GeoEdgeCredentials` (QGIS Auth DB → QSettings fallback).
"""

from __future__ import annotations

import base64
import json
import logging
import secrets
import threading
import time
from typing import TYPE_CHECKING, Any

from .exceptions import GeoEdgeAuthError

if TYPE_CHECKING:
    from geoedge_ai.qgis.credentials import GeoEdgeCredentials

try:
    from urllib.error import HTTPError, URLError
    from urllib.parse import urlencode
    from urllib.request import Request, urlopen
except ImportError:  # pragma: no cover — should never happen on CPython
    raise

logger = logging.getLogger(__name__)

# Points at the Railway-hosted backend during the experimental phase.
# Flip to https://api.geoedge.ai/v1 once DNS is cut over.
DEFAULT_API_BASE = "https://backend-production-6401.up.railway.app/v1"

# Offline grace: tolerate brief network blips by treating a recently-expired
# token as still valid for this many seconds.
_OFFLINE_GRACE_SECONDS = 60


class AuthManager:
    """Manages authentication lifecycle against the GeoEdge AI API.

    Tokens are held only in memory; persistent storage is delegated to
    ``GeoEdgeCredentials`` (QGIS Auth DB / QSettings).
    """

    def __init__(
        self,
        credentials: GeoEdgeCredentials,
        base_url: str = DEFAULT_API_BASE,
    ) -> None:
        self._credentials = credentials
        self._api_base = base_url.rstrip("/")
        self._access_token: str | None = None
        self._refresh_token: str | None = None
        self._user_profile: dict[str, Any] | None = None
        self._token_fetched_at: float | None = None

    # ------------------------------------------------------------------
    # Public properties
    # ------------------------------------------------------------------

    @property
    def api_base(self) -> str:
        return self._api_base

    @api_base.setter
    def api_base(self, value: str) -> None:
        self._api_base = (value or DEFAULT_API_BASE).rstrip("/")

    @property
    def access_token(self) -> str | None:
        return self._access_token

    @property
    def is_authenticated(self) -> bool:
        if self._access_token is None:
            return False
        claims = self._decode_jwt_local(self._access_token)
        if claims is None:
            return False
        exp = claims.get("exp")
        # Tokens without an exp claim are not trusted — defensive against
        # opaque tokens or malformed JWTs.
        if exp is None:
            return False
        now = time.time()
        if now < exp:
            return True
        return (now - exp) < _OFFLINE_GRACE_SECONDS

    @property
    def user_profile(self) -> dict[str, Any] | None:
        return self._user_profile

    # ------------------------------------------------------------------
    # Core auth flow
    # ------------------------------------------------------------------

    def login(self, email: str, password: str) -> dict[str, Any]:
        """Authenticate with email + password. Returns the user profile."""
        payload = json.dumps({"email": email, "password": password}).encode()
        try:
            data = self._api_post("/auth/login", payload)
        except GeoEdgeAuthError as exc:
            msg = str(exc)
            # 401 "Invalid credentials" → friendly message
            if not msg or "invalid" in msg.lower() or "credential" in msg.lower():
                raise GeoEdgeAuthError(
                    "Login failed. Username or password did not match."
                ) from exc
            raise GeoEdgeAuthError(msg) from exc
        except Exception as exc:
            raise GeoEdgeAuthError(f"Login failed: {exc}") from exc

        # Validate first, mutate second — otherwise a malformed response
        # leaves a refresh token / profile / fetched_at set without a
        # matching access token, and is_authenticated returns False
        # while logout() still tries to revoke a half-set session.
        access_token = data.get("access_token")
        if not access_token:
            raise GeoEdgeAuthError("Login response did not contain an access_token")

        self._access_token = access_token
        self._refresh_token = data.get("refresh_token")
        self._user_profile = data.get("profile", {})
        self._token_fetched_at = time.time()

        self._persist_tokens(refresh_days=30)
        logger.info("Login successful for %s", email)
        return self._user_profile  # type: ignore[return-value]

    def logout(self) -> None:
        """Clear all in-memory and persisted tokens.

        Server-side revocation is fire-and-forget on a daemon thread so
        the GUI never hangs on an unreachable backend (a 3 s urlopen
        timeout is still 3 s of frozen UI on a flaky network — the
        cancel path uses the same pattern for the same reason).
        """
        rt = self._refresh_token or self._credentials.get_auth_token("refresh")
        if self._access_token and rt:
            api_base = self._api_base
            access_token = self._access_token
            payload = json.dumps({"refresh_token": rt}).encode()

            def _revoke() -> None:
                try:
                    url = f"{api_base}/auth/logout"
                    headers = {
                        "Content-Type": "application/json",
                        "Authorization": f"Bearer {access_token}",
                    }
                    req = Request(url, data=payload, headers=headers, method="POST")
                    with urlopen(req, timeout=5):
                        pass
                except Exception:
                    logger.debug("Server-side logout failed; local cleanup already done.")

            threading.Thread(target=_revoke, daemon=True).start()

        self._access_token = None
        self._refresh_token = None
        self._user_profile = None
        self._token_fetched_at = None
        # delete_auth_token("access") is kept to clean up tokens written
        # by older plugin versions that persisted access tokens — current
        # versions only persist the refresh token.
        self._credentials.delete_auth_token("access")
        self._credentials.delete_auth_token("refresh")
        logger.info("Logged out — all tokens cleared.")

    def refresh(self) -> bool:
        """Refresh the access token using the stored refresh token.

        Returns ``True`` on success. On network failure returns ``False``
        without touching stored tokens (transient — retry next time). On
        an explicit auth failure (HTTP 401/403) the stored refresh token
        is cleared so we don't keep retrying a revoked token on every
        plugin start.
        """
        rt = self._refresh_token or self._credentials.get_auth_token("refresh")
        if not rt:
            logger.warning("No refresh token available.")
            return False

        payload = json.dumps({"refresh_token": rt}).encode()
        try:
            data = self._api_post("/auth/refresh", payload)
        except GeoEdgeAuthError as exc:
            # _api_post wraps HTTPError as GeoEdgeAuthError("HTTP <code> ...").
            # If the server explicitly rejected the token, drop it locally.
            msg = str(exc)
            if "HTTP 401" in msg or "HTTP 403" in msg:
                logger.info("Refresh token rejected by server; clearing.")
                self._refresh_token = None
                self._credentials.delete_auth_token("refresh")
                self._credentials.delete_auth_token("refresh_expires")
            else:
                logger.warning("Token refresh request failed: %s", msg)
            return False
        except Exception:
            logger.warning("Token refresh request failed.", exc_info=True)
            return False

        new_access = data.get("access_token")
        if not new_access:
            return False

        self._access_token = new_access
        self._token_fetched_at = time.time()

        new_refresh = data.get("refresh_token")
        if new_refresh:
            self._refresh_token = new_refresh
            self._credentials.store_auth_token("refresh", new_refresh)

        # The server just accepted our refresh token, so push the local
        # "give up and re-login" deadline forward. Without this, the
        # 30-day clock set at login forces a sign-in even on a heavily-
        # used session that refreshes daily.
        self._store_refresh_expiry(days=30)

        logger.info("Token refreshed successfully.")
        return True

    def login_via_browser(
        self,
        provider: str = "google",
        *,
        cancel_event: threading.Event | None = None,
    ) -> str | None:
        """Open system browser for OAuth login; receive code via localhost callback.

        Returns the access token on success, ``None`` on failure/timeout.
        Uses a CSRF ``state`` nonce, and only accepts the redirect when
        the path is ``/callback`` and the state matches.

        ``cancel_event``, if provided, lets the caller abort the wait —
        for example when the QThread wrapper is interrupted on plugin
        unload.
        """
        from http.server import BaseHTTPRequestHandler, HTTPServer
        from urllib.parse import parse_qs, urlparse

        state_nonce = secrets.token_urlsafe(32)
        result_holder: dict[str, str | None] = {"code": None, "error": None}
        done_event = threading.Event()
        lock = threading.Lock()

        class CallbackHandler(BaseHTTPRequestHandler):
            def _send_html(self, status: int, body: bytes) -> None:
                self.send_response(status)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_GET(self):  # noqa: N802 — http.server API
                parsed = urlparse(self.path)
                if parsed.path != "/callback":
                    # Ignore favicon.ico and any other stray request — do
                    # not dismiss the wait, do not claim success.
                    self._send_html(404, b"Not found")
                    return

                params = parse_qs(parsed.query)
                code = (params.get("code") or [None])[0]
                state = (params.get("state") or [None])[0]
                err = (params.get("error") or [None])[0]

                with lock:
                    if err:
                        result_holder["error"] = err
                    elif not state or state != state_nonce:
                        result_holder["error"] = "state_mismatch"
                    elif code:
                        result_holder["code"] = code
                    else:
                        result_holder["error"] = "missing_code"

                if result_holder["error"]:
                    self._send_html(
                        400,
                        b"<html><body><h2>Sign-in failed.</h2>"
                        b"<p>You can close this tab and try again in QGIS.</p>"
                        b"</body></html>",
                    )
                else:
                    self._send_html(
                        200,
                        b"<html><body><h2>Login successful!</h2>"
                        b"<p>You can close this tab and return to QGIS.</p>"
                        b"</body></html>",
                    )
                done_event.set()

            def log_message(self, *args):  # silence stderr noise
                pass

        server = HTTPServer(("127.0.0.1", 0), CallbackHandler)
        port = server.server_address[1]
        redirect_uri = f"http://127.0.0.1:{port}/callback"

        def _serve_until_done() -> None:
            # serve_forever doesn't honour shutdown across threads cleanly
            # without a join, so we drive one request at a time and stop
            # once the done_event is set (either by the callback or by an
            # external cancel).
            server.timeout = 1.0
            while not done_event.is_set():
                server.handle_request()

        server_thread = threading.Thread(target=_serve_until_done, daemon=True)
        server_thread.start()

        authorize_url = (
            f"{self._api_base}/auth/oauth/{provider}/authorize"
            f"?{urlencode({'redirect_uri': redirect_uri, 'client': 'plugin', 'state': state_nonce})}"
        )
        try:
            from qgis.PyQt.QtCore import QUrl
            from qgis.PyQt.QtGui import QDesktopServices
            QDesktopServices.openUrl(QUrl(authorize_url))
        except ImportError:
            import webbrowser
            webbrowser.open(authorize_url)

        logger.info("Waiting for OAuth callback on port %d…", port)
        # Poll instead of one big wait so an external cancel_event can
        # break us out promptly on plugin unload.
        deadline = time.monotonic() + 300
        while not done_event.is_set():
            if cancel_event is not None and cancel_event.is_set():
                done_event.set()  # unblock the server thread
                break
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            done_event.wait(timeout=min(0.5, remaining))
        try:
            server.server_close()
        except Exception:
            pass

        if cancel_event is not None and cancel_event.is_set():
            logger.info("OAuth login cancelled.")
            return None
        if result_holder["error"]:
            logger.warning("OAuth login failed: %s", result_holder["error"])
            return None

        code = result_holder["code"]
        if not code:
            logger.warning("OAuth login timed out.")
            return None

        try:
            payload = json.dumps({"code": code}).encode()
            data = self._api_post("/auth/oauth/exchange", payload)
        except Exception as exc:
            logger.error("OAuth code exchange failed: %s", exc)
            return None

        access_token = data.get("access_token")
        if not access_token:
            return None

        self._access_token = access_token
        self._refresh_token = data.get("refresh_token")
        # Capture the user profile too — without this the chat panel
        # can't show "Signed in as <email>" after an OAuth sign-in,
        # only after email/password.
        self._user_profile = data.get("profile", {})
        self._token_fetched_at = time.time()

        self._persist_tokens(refresh_days=7)
        logger.info("Browser OAuth login successful for provider=%s", provider)
        return self._access_token

    def try_restore_session(self) -> bool:
        """Attempt to restore a session from a stored refresh token."""
        import datetime as _dt
        expiry_str = self._credentials.get_auth_token("refresh_expires")
        if expiry_str:
            try:
                expiry = _dt.datetime.fromisoformat(expiry_str)
                if _dt.datetime.now(_dt.timezone.utc) > expiry:
                    logger.info("Stored refresh token has expired.")
                    self._credentials.delete_auth_token("refresh")
                    self._credentials.delete_auth_token("refresh_expires")
                    return False
            except (ValueError, TypeError):
                pass

        return self.refresh()

    def clear_all(self) -> None:
        """Clear all stored credentials (for logout)."""
        self.logout()
        self._credentials.delete_auth_token("refresh_expires")

    # ------------------------------------------------------------------
    # JWT helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _decode_jwt_local(token: str) -> dict[str, Any] | None:
        """Decode a JWT payload without cryptographic verification.

        Used only for displaying user info / expiry in the UI. Never trust
        the result for authorization decisions.
        """
        try:
            parts = token.split(".")
            if len(parts) < 2:
                return None
            payload_b64 = parts[1]
            padding = 4 - len(payload_b64) % 4
            if padding != 4:
                payload_b64 += "=" * padding
            payload_bytes = base64.urlsafe_b64decode(payload_b64)
            return json.loads(payload_bytes)
        except Exception:
            logger.debug("Failed to decode JWT locally.", exc_info=True)
            return None

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _persist_tokens(self, *, refresh_days: int) -> None:
        # We only persist the refresh token + expiry. Access tokens are
        # short-lived and re-fetched on session restore via /auth/refresh,
        # so writing them to the auth DB is just extra disk + an extra
        # secret on disk for no benefit.
        if self._refresh_token:
            self._credentials.store_auth_token("refresh", self._refresh_token)
            self._store_refresh_expiry(days=refresh_days)

    def _store_refresh_expiry(self, *, days: int) -> None:
        import datetime as _dt
        expiry = (
            _dt.datetime.now(_dt.timezone.utc) + _dt.timedelta(days=days)
        ).isoformat()
        self._credentials.store_auth_token("refresh_expires", expiry)

    def _api_post(
        self,
        path: str,
        body: bytes,
        *,
        auth: bool = False,
        timeout: float = 30.0,
    ) -> dict[str, Any]:
        url = f"{self._api_base}{path}"
        headers = {"Content-Type": "application/json"}
        if auth and self._access_token:
            headers["Authorization"] = f"Bearer {self._access_token}"

        req = Request(url, data=body, headers=headers, method="POST")
        try:
            with urlopen(req, timeout=timeout) as resp:
                return json.loads(resp.read().decode())
        except HTTPError as exc:
            detail = ""
            try:
                body = json.loads(exc.read().decode())
                raw = body.get("detail") or ""
                # detail may be a dict (FastAPI validation errors) or a string
                if isinstance(raw, dict):
                    detail = raw.get("message") or raw.get("msg") or str(raw)
                elif isinstance(raw, str):
                    detail = raw
            except Exception:
                pass
            raise GeoEdgeAuthError(detail or f"Request failed (HTTP {exc.code})") from exc
        except URLError as exc:
            raise GeoEdgeAuthError(
                f"Network error reaching {path}: {exc.reason}"
            ) from exc
