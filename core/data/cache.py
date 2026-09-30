"""Простой HTTP-кэш в SQLite: URL+параметры -> JSON/текст с TTL."""
from __future__ import annotations

import hashlib
import json
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any, Optional

from core.config import CACHE_DB

_lock = threading.Lock()


class HttpCache:
    def __init__(self, path: Path = CACHE_DB):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        with self._conn() as c:
            c.execute("CREATE TABLE IF NOT EXISTS http (k TEXT PRIMARY KEY, ts REAL, body TEXT)")

    def _conn(self):
        return sqlite3.connect(self.path, timeout=30)

    @staticmethod
    def key(url: str, params: Optional[dict]) -> str:
        raw = url + "?" + json.dumps(params or {}, sort_keys=True, ensure_ascii=False)
        return hashlib.sha1(raw.encode()).hexdigest()

    def get(self, k: str, ttl: Optional[float]) -> Optional[str]:
        with _lock, self._conn() as c:
            row = c.execute("SELECT ts, body FROM http WHERE k=?", (k,)).fetchone()
        if not row:
            return None
        ts, body = row
        if ttl is not None and time.time() - ts > ttl:
            return None
        return body

    def put(self, k: str, body: str) -> None:
        with _lock, self._conn() as c:
            c.execute("INSERT OR REPLACE INTO http VALUES (?,?,?)", (k, time.time(), body))

    def clear(self) -> None:
        with _lock, self._conn() as c:
            c.execute("DELETE FROM http")


def dumps(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False)
