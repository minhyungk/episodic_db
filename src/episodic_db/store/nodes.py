"""CRUD operations for graph nodes: Session, ToolCall, Resource, Result, Episode."""

import json
import sqlite3
from datetime import datetime, timezone


def insert_session(conn: sqlite3.Connection, session_id: str, exec_env: dict | None = None):
    conn.execute(
        "INSERT OR IGNORE INTO sessions (session_id, started_at, exec_env) VALUES (?, ?, ?)",
        (session_id, datetime.now(timezone.utc).isoformat(), json.dumps(exec_env) if exec_env else None),
    )
    conn.commit()


def update_session_end(conn: sqlite3.Connection, session_id: str, success: bool | None = None):
    conn.execute(
        "UPDATE sessions SET ended_at = ?, success = ? WHERE session_id = ?",
        (datetime.now(timezone.utc).isoformat(), int(success) if success is not None else None, session_id),
    )
    conn.commit()


def update_session_totals(conn: sqlite3.Connection, session_id: str, total_tokens: int, total_cost: float):
    conn.execute(
        "UPDATE sessions SET total_tokens = ?, total_cost = ? WHERE session_id = ?",
        (total_tokens, total_cost, session_id),
    )
    conn.commit()


def insert_tool_call(
    conn: sqlite3.Connection,
    tool_use_id: str,
    session_id: str,
    seq: int,
    tool_name: str,
    input_hash: str | None = None,
    normalized_input: str | None = None,
    model: str | None = None,
):
    conn.execute(
        """INSERT OR IGNORE INTO tool_calls
           (tool_use_id, session_id, seq, timestamp, model, tool_name, input_hash, normalized_input)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (tool_use_id, session_id, seq, datetime.now(timezone.utc).isoformat(), model, tool_name, input_hash, normalized_input),
    )
    conn.commit()


def update_tool_call_result(conn: sqlite3.Connection, tool_use_id: str, latency_ms: float | None = None):
    if latency_ms is not None:
        conn.execute(
            "UPDATE tool_calls SET latency_ms = ? WHERE tool_use_id = ?",
            (latency_ms, tool_use_id),
        )
        conn.commit()


def update_tool_call_tokens(
    conn: sqlite3.Connection,
    tool_use_id: str,
    input_tokens: int,
    output_tokens: int,
    cache_creation_tokens: int,
    cache_read_tokens: int,
    own_cost: float,
    carry_cost: float,
    total_cost: float,
):
    conn.execute(
        """UPDATE tool_calls SET
           input_tokens = ?, output_tokens = ?, cache_creation_tokens = ?,
           cache_read_tokens = ?, own_cost = ?, carry_cost = ?, total_cost = ?
           WHERE tool_use_id = ?""",
        (input_tokens, output_tokens, cache_creation_tokens, cache_read_tokens, own_cost, carry_cost, total_cost, tool_use_id),
    )
    conn.commit()


def update_tool_call_labels(
    conn: sqlite3.Connection,
    tool_use_id: str,
    contributed_to: str | None = None,
    is_wasteful: bool = False,
    episode_id: str | None = None,
):
    conn.execute(
        "UPDATE tool_calls SET contributed_to = ?, is_wasteful = ?, episode_id = ? WHERE tool_use_id = ?",
        (contributed_to, int(is_wasteful), episode_id, tool_use_id),
    )
    conn.commit()


def insert_resource(conn: sqlite3.Connection, resource_id: str, kind: str):
    conn.execute(
        "INSERT OR IGNORE INTO resources (resource_id, kind, first_seen) VALUES (?, ?, ?)",
        (resource_id, kind, datetime.now(timezone.utc).isoformat()),
    )
    conn.commit()


def insert_result(
    conn: sqlite3.Connection,
    tool_use_id: str,
    result_hash: str | None = None,
    digest_handle: str | None = None,
    inline_content: str | None = None,
    model_visible_tokens: int = 0,
    is_error: bool = False,
    output_chars: int = 0,
    output_lines: int = 0,
):
    conn.execute(
        """INSERT INTO results
           (tool_use_id, result_hash, digest_handle, inline_content, model_visible_tokens, is_error, output_chars, output_lines)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (tool_use_id, result_hash, digest_handle, inline_content, model_visible_tokens, int(is_error), output_chars, output_lines),
    )
    conn.commit()


def insert_episode(conn: sqlite3.Connection, episode_data: dict):
    cols = [
        "episode_id", "session_id", "created_at", "converged_by",
        "waste_type", "outcome", "converged_resource", "touched_paths",
        "path_prefix", "changed_symbols", "test_names", "grep_terms",
        "error_signature", "lang", "tool_mix",
        "total_input_tokens", "total_output_tokens", "total_cache_creation", "total_cache_read",
        "own_cost", "carry_cost", "total_cost", "carry_ratio",
        "read_output_token_ratio", "new_information_rate", "repeated_read_rate", "futility_score",
        "is_wasteful", "wasted_member_ids", "wasted_own_cost", "wasted_carry_cost", "wasted_tokens",
    ]
    placeholders = ", ".join(["?"] * len(cols))
    col_str = ", ".join(cols)

    values = []
    for col in cols:
        val = episode_data.get(col)
        if isinstance(val, (list, dict)):
            val = json.dumps(val)
        values.append(val)

    conn.execute(f"INSERT OR REPLACE INTO episodes ({col_str}) VALUES ({placeholders})", values)
    conn.commit()


def get_session_tool_calls(conn: sqlite3.Connection, session_id: str) -> list[dict]:
    cur = conn.execute(
        "SELECT * FROM tool_calls WHERE session_id = ? ORDER BY seq", (session_id,)
    )
    return [dict(row) for row in cur.fetchall()]


def get_tool_call_results(conn: sqlite3.Connection, tool_use_id: str) -> list[dict]:
    cur = conn.execute(
        "SELECT * FROM results WHERE tool_use_id = ?", (tool_use_id,)
    )
    return [dict(row) for row in cur.fetchall()]


def get_session(conn: sqlite3.Connection, session_id: str) -> dict | None:
    cur = conn.execute("SELECT * FROM sessions WHERE session_id = ?", (session_id,))
    row = cur.fetchone()
    return dict(row) if row else None


def get_episodes_by_session(conn: sqlite3.Connection, session_id: str) -> list[dict]:
    cur = conn.execute(
        "SELECT * FROM episodes WHERE session_id = ? ORDER BY created_at", (session_id,)
    )
    return [dict(row) for row in cur.fetchall()]
