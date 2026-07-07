"""SQL-based facet intersection queries for episode retrieval."""

import json
from typing import Any

from episodic_db.store.db import Database


def search_episodes(
    db: Database,
    path_prefix: str | None = None,
    waste_type: str | None = None,
    outcome: str | None = None,
    lang: str | None = None,
    converged_resource: str | None = None,
    grep_terms: list[str] | None = None,
    is_wasteful: bool | None = None,
    limit: int = 20,
) -> list[dict]:
    """Search episodes by facet intersection."""
    where_clauses = []
    params: list[Any] = []

    if path_prefix:
        where_clauses.append("path_prefix = ?")
        params.append(path_prefix)

    if waste_type:
        where_clauses.append("waste_type = ?")
        params.append(waste_type)

    if outcome:
        where_clauses.append("outcome = ?")
        params.append(outcome)

    if lang:
        where_clauses.append("lang = ?")
        params.append(lang)

    if converged_resource:
        where_clauses.append("converged_resource = ?")
        params.append(converged_resource)

    if is_wasteful is not None:
        where_clauses.append("is_wasteful = ?")
        params.append(int(is_wasteful))

    if grep_terms:
        for term in grep_terms:
            where_clauses.append("grep_terms LIKE ?")
            params.append(f"%{term}%")

    where_sql = " AND ".join(where_clauses) if where_clauses else "1=1"

    sql = f"""
        SELECT episode_id, session_id, waste_type, outcome, path_prefix,
               converged_resource, lang, total_cost, is_wasteful,
               read_output_token_ratio, futility_score, created_at
        FROM episodes
        WHERE {where_sql}
        ORDER BY created_at DESC
        LIMIT ?
    """
    params.append(limit)

    cur = db.conn.execute(sql, params)
    return [dict(row) for row in cur.fetchall()]


def get_episode_detail(db: Database, episode_id: str) -> dict | None:
    """Get full episode record with member tool calls."""
    cur = db.conn.execute("SELECT * FROM episodes WHERE episode_id = ?", (episode_id,))
    row = cur.fetchone()
    if not row:
        return None

    episode = dict(row)

    cur = db.conn.execute(
        "SELECT * FROM tool_calls WHERE episode_id = ? ORDER BY seq", (episode_id,)
    )
    episode["members"] = [dict(r) for r in cur.fetchall()]

    for key in ("touched_paths", "changed_symbols", "test_names", "grep_terms", "tool_mix", "wasted_member_ids"):
        val = episode.get(key)
        if isinstance(val, str):
            try:
                episode[key] = json.loads(val)
            except (json.JSONDecodeError, TypeError):
                pass

    return episode
