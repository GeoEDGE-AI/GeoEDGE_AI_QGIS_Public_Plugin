# SPDX-License-Identifier: GPL-2.0-or-later
# Copyright (C) 2024-2026 GeoEdge AI
#
# This file is part of the GeoEdge AI QGIS plugin.
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 2 of the License, or
# (at your option) any later version.
# See the COPYING file or <https://www.gnu.org/licenses/> for details.
"""Credential storage abstraction for GeoEdge AI.

Tries the QGIS Authentication Database first (most secure), then falls
back to ``QSettings``. Credentials are never logged.
"""

from __future__ import annotations

import logging

from ..settings_keys import AUTH_CFG_IDS_PREFIX, SETTINGS_PREFIX

logger = logging.getLogger(__name__)

try:
    from qgis.core import QgsApplication  # noqa: F401
    _HAS_QGIS_AUTH = True
except ImportError:
    _HAS_QGIS_AUTH = False

try:
    from qgis.PyQt.QtCore import QSettings  # type: ignore[import-untyped]
except ImportError:
    try:
        from PyQt6.QtCore import QSettings  # type: ignore[import-untyped]  # QGIS 4.x standalone
    except ImportError:
        try:
            from PyQt5.QtCore import QSettings  # type: ignore[import-untyped]
        except ImportError:
            QSettings = None  # type: ignore[misc,assignment]

_AUTH_CFG_PREFIX = "geoedge_ai"


class GeoEdgeCredentials:
    """Abstraction over persistent credential storage.

    Storage priority:
      1. ``QgsAuthManager`` — encrypted database.
      2. ``QSettings`` — platform-native settings store.
    """

    def __init__(self) -> None:
        self._use_auth_db = _HAS_QGIS_AUTH and self._auth_db_available()

    def store_auth_token(self, token_type: str, token: str) -> bool:
        return self._store(f"token/{token_type}", token)

    def get_auth_token(self, token_type: str) -> str | None:
        return self._retrieve(f"token/{token_type}")

    def delete_auth_token(self, token_type: str) -> bool:
        return self._delete(f"token/{token_type}")

    def clear_all(self) -> None:
        for token_type in ("access", "refresh", "refresh_expires"):
            self.delete_auth_token(token_type)

    # ------------------------------------------------------------------
    # Internal storage layer
    # ------------------------------------------------------------------

    def _store(self, key: str, value: str) -> bool:
        if self._use_auth_db:
            ok = self._auth_db_store(key, value)
            if ok:
                return True
            logger.debug("Auth DB store failed for key=%s; falling back to QSettings.", key)
        return self._qsettings_store(key, value)

    def _retrieve(self, key: str) -> str | None:
        if self._use_auth_db:
            val = self._auth_db_retrieve(key)
            if val is not None:
                return val
        return self._qsettings_retrieve(key)

    def _delete(self, key: str) -> bool:
        ok_auth = True
        ok_qs = True
        if self._use_auth_db:
            ok_auth = self._auth_db_delete(key)
        ok_qs = self._qsettings_delete(key)
        return ok_auth and ok_qs

    @staticmethod
    def _auth_db_available() -> bool:
        try:
            from qgis.core import QgsApplication
            auth_mgr = QgsApplication.authManager()
            return auth_mgr is not None and auth_mgr.masterPasswordIsSet()
        except Exception:
            return False

    @staticmethod
    def _get_auth_cfg_id(key: str) -> str | None:
        if QSettings is None:
            return None
        settings = QSettings()
        return settings.value(f"{AUTH_CFG_IDS_PREFIX}/{key}") or None

    @staticmethod
    def _set_auth_cfg_id(key: str, cfg_id: str) -> None:
        if QSettings is None:
            return
        settings = QSettings()
        settings.setValue(f"{AUTH_CFG_IDS_PREFIX}/{key}", cfg_id)

    @staticmethod
    def _remove_auth_cfg_id(key: str) -> None:
        if QSettings is None:
            return
        settings = QSettings()
        settings.remove(f"{AUTH_CFG_IDS_PREFIX}/{key}")

    def _auth_db_store(self, key: str, value: str) -> bool:
        try:
            from qgis.core import QgsApplication, QgsAuthMethodConfig
            auth_mgr = QgsApplication.authManager()

            existing_id = self._get_auth_cfg_id(key)

            # Store the new config BEFORE removing the old one. The
            # previous order silently logged users out if the store
            # failed mid-operation (disk full, master password locked).
            config = QgsAuthMethodConfig("Basic")
            config.setName(f"GeoEdge AI: {key}")
            config.setConfigMap({"password": value})
            ok = auth_mgr.storeAuthenticationConfig(config)
            if not ok:
                return False

            new_id = config.id()
            self._set_auth_cfg_id(key, new_id)

            if existing_id and existing_id != new_id:
                try:
                    auth_mgr.removeAuthenticationConfig(existing_id)
                except Exception:
                    # Pointer already moved to the new id; an orphaned
                    # old config in the auth DB is recoverable on next
                    # restart and not worth failing the whole call over.
                    logger.debug(
                        "Failed to remove old auth config %s", existing_id
                    )
            return True
        except Exception:
            logger.debug("QgsAuthManager store failed.", exc_info=True)
            return False

    def _auth_db_retrieve(self, key: str) -> str | None:
        try:
            from qgis.core import QgsApplication, QgsAuthMethodConfig
            auth_mgr = QgsApplication.authManager()
            cfg_id = self._get_auth_cfg_id(key)
            if not cfg_id:
                return None
            config = QgsAuthMethodConfig()
            if auth_mgr.loadAuthenticationConfig(cfg_id, config, True):
                return config.configMap().get("password") or None
            return None
        except Exception:
            return None

    def _auth_db_delete(self, key: str) -> bool:
        try:
            from qgis.core import QgsApplication
            auth_mgr = QgsApplication.authManager()
            cfg_id = self._get_auth_cfg_id(key)
            if not cfg_id:
                return False
            ok = auth_mgr.removeAuthenticationConfig(cfg_id)
            if ok:
                self._remove_auth_cfg_id(key)
            return ok
        except Exception:
            return False

    @staticmethod
    def _qsettings_store(key: str, value: str) -> bool:
        if QSettings is None:
            logger.warning("QSettings not available — cannot persist credential.")
            return False
        try:
            settings = QSettings()
            settings.setValue(f"{SETTINGS_PREFIX}/{key}", value)
            return True
        except Exception:
            logger.debug("QSettings store failed.", exc_info=True)
            return False

    @staticmethod
    def _qsettings_retrieve(key: str) -> str | None:
        if QSettings is None:
            return None
        try:
            settings = QSettings()
            val = settings.value(f"{SETTINGS_PREFIX}/{key}")
            if val is not None and isinstance(val, str) and val:
                return val
            return None
        except Exception:
            return None

    @staticmethod
    def _qsettings_delete(key: str) -> bool:
        if QSettings is None:
            return False
        try:
            settings = QSettings()
            settings.remove(f"{SETTINGS_PREFIX}/{key}")
            return True
        except Exception:
            return False
