"""Bridge between proxy token data and SQLite store.

The proxy extracts tool_use_ids and exact token counts from API responses.
This module writes that data to the proxy_calls table, and at SessionEnd
reconciles it with tool_calls via tool_use_id matching.
"""

import json
import sqlite3
from pathlib import Path

from episodic_db.store.db import Database
from .pricing import calculate_cost


class TokenBridge:
    def __init__(self, db: Database):
        self.db = db

    def log_proxy_call(
        self,
        session_id: str,
        call_index: int,
        model: str,
        tokens: dict,
        tool_use_ids: list[str],
        latency_ms: float,
        timestamp: str,
    ):
        cost = calculate_cost(model, tokens)
        self.db.conn.execute(
            """INSERT OR REPLACE INTO proxy_calls
               (call_index, session_id, timestamp, model, tool_use_ids,
                input_tokens, output_tokens, cache_creation_tokens, cache_read_tokens,
                total_cost, latency_ms)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                call_index, session_id, timestamp, model,
                json.dumps(tool_use_ids),
                tokens.get("input_tokens", 0),
                tokens.get("output_tokens", 0),
                tokens.get("cache_creation_input_tokens", 0),
                tokens.get("cache_read_input_tokens", 0),
                cost["total_cost"],
                latency_ms,
            ),
        )
        self.db.conn.commit()

    def reconcile_session(self, session_id: str):
        """Match proxy_calls with tool_calls by tool_use_id and update token data."""
        cur = self.db.conn.execute(
            "SELECT * FROM proxy_calls WHERE session_id = ? ORDER BY call_index",
            (session_id,),
        )
        proxy_rows = cur.fetchall()

        total_session_tokens = 0
        total_session_cost = 0.0

        for row in proxy_rows:
            tool_use_ids = json.loads(row["tool_use_ids"]) if row["tool_use_ids"] else []
            n_tools = len(tool_use_ids) if tool_use_ids else 1

            input_tokens = row["input_tokens"]
            output_tokens = row["output_tokens"]
            cache_creation = row["cache_creation_tokens"]
            cache_read = row["cache_read_tokens"]

            carry_cost = (cache_read / 1_000_000) * 0.30
            own_cost = row["total_cost"] - carry_cost

            total_session_tokens += input_tokens + output_tokens + cache_creation + cache_read
            total_session_cost += row["total_cost"]

            for tid in tool_use_ids:
                per_tool_output = output_tokens // n_tools
                self.db.conn.execute(
                    """UPDATE tool_calls SET
                       input_tokens = ?, output_tokens = ?,
                       cache_creation_tokens = ?, cache_read_tokens = ?,
                       own_cost = ?, carry_cost = ?, total_cost = ?,
                       latency_ms = ?, model = COALESCE(model, ?)
                       WHERE tool_use_id = ?""",
                    (
                        input_tokens, per_tool_output,
                        cache_creation, cache_read,
                        own_cost / n_tools, carry_cost / n_tools,
                        row["total_cost"] / n_tools,
                        row["latency_ms"], row["model"],
                        tid,
                    ),
                )

        self.db.conn.execute(
            "UPDATE sessions SET total_tokens = ?, total_cost = ? WHERE session_id = ?",
            (total_session_tokens, total_session_cost, session_id),
        )
        self.db.conn.commit()
