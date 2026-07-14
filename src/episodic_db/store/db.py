"""Database connection management with WAL mode."""

import sqlite3
from pathlib import Path

from .schema import get_full_schema, SCHEMA_VERSION


class Database:
    def __init__(self, db_path: Path):
        self.db_path = db_path
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn: sqlite3.Connection | None = None

    def connect(self) -> sqlite3.Connection:
        if self._conn is not None:
            return self._conn
        self._conn = sqlite3.connect(str(self.db_path), timeout=10)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode = WAL")
        self._conn.execute("PRAGMA foreign_keys = ON")
        self._conn.execute("PRAGMA busy_timeout = 5000")
        self._ensure_schema()
        return self._conn

    def _ensure_schema(self):
        conn = self._conn
        cur = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='schema_version'"
        )
        if cur.fetchone() is None:
            conn.executescript(get_full_schema())
            conn.execute(
                "INSERT INTO schema_version (version) VALUES (?)", (SCHEMA_VERSION,)
            )
            conn.commit()
        else:
            self._migrate(conn)

    def _migrate(self, conn):
        cur = conn.execute("SELECT MAX(version) FROM schema_version")
        current = cur.fetchone()[0] or 1
        if current < 2:
            conn.execute("ALTER TABLE proxy_calls ADD COLUMN assistant_text TEXT")
            conn.execute(
                "INSERT OR REPLACE INTO schema_version (version) VALUES (?)", (2,)
            )
            conn.commit()
        if current < 3:
            conn.execute("ALTER TABLE proxy_calls ADD COLUMN user_message TEXT")
            conn.execute(
                "INSERT OR REPLACE INTO schema_version (version) VALUES (?)", (3,)
            )
            conn.commit()
        if current < 4:
            conn.execute("ALTER TABLE tool_calls ADD COLUMN tool_input_json TEXT")
            conn.execute(
                "INSERT OR REPLACE INTO schema_version (version) VALUES (?)", (4,)
            )
            conn.commit()

    def close(self):
        if self._conn:
            self._conn.close()
            self._conn = None

    @property
    def conn(self) -> sqlite3.Connection:
        if self._conn is None:
            return self.connect()
        return self._conn
