"""Postgres connectivity via Cloud SQL Python Connector.

Authentication order:
  1. GCLOUD_ADC_B64 — base64-encoded service account JSON key (recommended for VMs and local).
  2. Application Default Credentials (ADC) — fallback if GCLOUD_ADC_B64 is not set.

Required env vars:
    GCLOUD_ADC_B64                         base64-encoded service account JSON
    CALLISTRA_DB_INSTANCE_CONNECTION_NAME  e.g. project:region:instance
    CALLISTRA_DB_NAME                      database name
    CALLISTRA_DB_USER                      database user
    CALLISTRA_DB_PASSWORD                  database password

Optional:
    CALLISTRA_DB_IP_TYPE                   'public' (default) or 'private'

Driver: pg8000 (required by Cloud SQL Python Connector v1.0+).
No direct TCP connections. No IP allowlisting needed.
"""
from __future__ import annotations

import base64
import json
import logging
import os
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Optional, Sequence

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parents[1]
load_dotenv(BASE_DIR / ".env.local")
load_dotenv(BASE_DIR / ".env")


def _first_env(*names: str) -> Optional[str]:
    for name in names:
        value = os.getenv(name)
        if value:
            return value
    return None


def _rows_to_dicts(cursor) -> list[dict]:
    if cursor.description is None:
        return []
    columns = [desc[0] for desc in cursor.description]
    return [dict(zip(columns, row)) for row in cursor.fetchall()]


class AnalyticsDatabase:
    """Cloud SQL connector-backed Postgres wrapper (pg8000 driver)."""

    def __init__(self) -> None:
        self._instance_connection_name = _first_env(
            "CALLISTRA_DB_INSTANCE_CONNECTION_NAME",
            "CLOUD_SQL_CONNECTION_NAME",
            "INSTANCE_CONNECTION_NAME",
        )
        self._dbname   = _first_env("CALLISTRA_DB_NAME",     "PGDATABASE", "DB_NAME")
        self._user     = _first_env("CALLISTRA_DB_USER",     "PGUSER",     "DB_USER")
        self._password = _first_env("CALLISTRA_DB_PASSWORD", "PGPASSWORD", "DB_PASSWORD")
        self._ip_type  = (_first_env("CALLISTRA_DB_IP_TYPE", "DB_IP_TYPE") or "public").lower()
        self._adc_b64  = _first_env("GCLOUD_ADC_B64")
        self._connector = None

    def is_configured(self) -> bool:
        return bool(self._instance_connection_name and self._dbname and self._user)

    def configuration_summary(self) -> str:
        if self._instance_connection_name:
            return f"cloud-sql:{self._instance_connection_name}"
        return "unconfigured"

    def _get_credentials(self):
        """Decode GCLOUD_ADC_B64 into a Credentials object.

        Handles both authorized_user (gcloud auth application-default login)
        and service_account JSON key formats.
        """
        if not self._adc_b64:
            return None  # fall back to ADC from environment
        try:
            import google.auth
            info = json.loads(base64.b64decode(self._adc_b64))
            creds, _ = google.auth.load_credentials_from_dict(
                info,
                scopes=["https://www.googleapis.com/auth/cloud-platform"],
            )
            return creds
        except Exception as exc:
            raise RuntimeError(f"Failed to decode GCLOUD_ADC_B64: {exc}") from exc

    def _get_connector(self):
        if self._connector is None:
            from google.cloud.sql.connector import Connector
            credentials = self._get_credentials()
            self._connector = Connector(
                credentials=credentials,
                refresh_strategy="lazy",
            )
            logging.debug(
                "Cloud SQL connector initialised (%s credentials).",
                "service-account" if credentials else "ADC",
            )
        return self._connector

    def _new_connection(self):
        if not self.is_configured():
            raise RuntimeError(
                "Database not configured. Set CALLISTRA_DB_INSTANCE_CONNECTION_NAME, "
                "CALLISTRA_DB_NAME, CALLISTRA_DB_USER, CALLISTRA_DB_PASSWORD, "
                "and GCLOUD_ADC_B64."
            )
        from google.cloud.sql.connector import IPTypes
        ip_type = IPTypes.PRIVATE if self._ip_type == "private" else IPTypes.PUBLIC
        return self._get_connector().connect(
            self._instance_connection_name,
            "pg8000",
            user=self._user,
            password=self._password,
            db=self._dbname,
            ip_type=ip_type,
        )

    @contextmanager
    def connection(self):
        conn = self._new_connection()
        conn.autocommit = True
        try:
            yield conn
        finally:
            conn.close()

    def query_all(self, query: str, params: Sequence[Any] | None = None) -> list[dict[str, Any]]:
        with self.connection() as conn:
            cur = conn.cursor()
            try:
                cur.execute(query, params or ())
                return _rows_to_dicts(cur)
            finally:
                cur.close()

    def query_one(self, query: str, params: Sequence[Any] | None = None) -> Optional[dict[str, Any]]:
        with self.connection() as conn:
            cur = conn.cursor()
            try:
                cur.execute(query, params or ())
                rows = _rows_to_dicts(cur)
                return rows[0] if rows else None
            finally:
                cur.close()

    def execute(self, query: str, params: Sequence[Any] | None = None) -> None:
        with self.connection() as conn:
            cur = conn.cursor()
            try:
                cur.execute(query, params or ())
            finally:
                cur.close()


_analytics_db: Optional[AnalyticsDatabase] = None


def get_analytics_db() -> AnalyticsDatabase:
    global _analytics_db
    if _analytics_db is None:
        _analytics_db = AnalyticsDatabase()
    return _analytics_db
