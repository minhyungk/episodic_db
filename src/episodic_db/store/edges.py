"""CRUD operations for graph edges: TOUCHES, DUPLICATE_OF."""

import sqlite3
from datetime import datetime, timezone


def insert_touches(
    conn: sqlite3.Connection,
    tool_use_id: str,
    resource_id: str,
    mode: str,
):
    now = datetime.now(timezone.utc).isoformat()
    conn.execute(
        "INSERT OR IGNORE INTO edges_touches (tool_use_id, resource_id, mode, valid_from) VALUES (?, ?, ?, ?)",
        (tool_use_id, resource_id, mode, now),
    )
    if mode == "WROTE":
        conn.execute(
            """UPDATE edges_touches SET valid_to = ?
               WHERE resource_id = ? AND mode = 'READ' AND valid_to IS NULL
               AND tool_use_id != ?""",
            (now, resource_id, tool_use_id),
        )
    conn.commit()


def insert_duplicate_of(conn: sqlite3.Connection, tool_use_id: str, duplicate_of: str):
    conn.execute(
        "INSERT OR IGNORE INTO edges_duplicate_of (tool_use_id, duplicate_of) VALUES (?, ?)",
        (tool_use_id, duplicate_of),
    )
    conn.commit()


def get_touches_by_session(conn: sqlite3.Connection, session_id: str) -> list[dict]:
    cur = conn.execute(
        """SELECT et.* FROM edges_touches et
           JOIN tool_calls tc ON et.tool_use_id = tc.tool_use_id
           WHERE tc.session_id = ?""",
        (session_id,),
    )
    return [dict(row) for row in cur.fetchall()]


def get_wrote_resources(conn: sqlite3.Connection, session_id: str) -> list[str]:
    cur = conn.execute(
        """SELECT DISTINCT et.resource_id FROM edges_touches et
           JOIN tool_calls tc ON et.tool_use_id = tc.tool_use_id
           WHERE tc.session_id = ? AND et.mode = 'WROTE'""",
        (session_id,),
    )
    return [row["resource_id"] for row in cur.fetchall()]


def get_read_tool_calls_for_resource(conn: sqlite3.Connection, session_id: str, resource_id: str) -> list[str]:
    cur = conn.execute(
        """SELECT et.tool_use_id FROM edges_touches et
           JOIN tool_calls tc ON et.tool_use_id = tc.tool_use_id
           WHERE tc.session_id = ? AND et.resource_id = ? AND et.mode = 'READ'""",
        (session_id, resource_id),
    )
    return [row["tool_use_id"] for row in cur.fetchall()]


def get_wrote_tool_calls(conn: sqlite3.Connection, session_id: str) -> list[str]:
    cur = conn.execute(
        """SELECT DISTINCT et.tool_use_id FROM edges_touches et
           JOIN tool_calls tc ON et.tool_use_id = tc.tool_use_id
           WHERE tc.session_id = ? AND et.mode = 'WROTE'""",
        (session_id,),
    )
    return [row["tool_use_id"] for row in cur.fetchall()]
