"""Classify session outcome: converged, abandoned, or looped."""

import sqlite3
from collections import Counter

from episodic_db.store.edges import get_wrote_resources
from episodic_db.store.nodes import get_session_tool_calls, get_session


def classify_outcome(conn: sqlite3.Connection, session_id: str) -> str:
    """Determine session outcome. Returns 'converged', 'abandoned', or 'looped'."""
    session = get_session(conn, session_id)
    wrote_resources = get_wrote_resources(conn, session_id)
    tool_calls = get_session_tool_calls(conn, session_id)

    if not tool_calls:
        return "abandoned"

    if wrote_resources and session and session.get("success"):
        return "converged"

    input_hashes = [tc["input_hash"] for tc in tool_calls if tc["input_hash"]]
    hash_counts = Counter(input_hashes)
    max_repeat = max(hash_counts.values()) if hash_counts else 0
    if max_repeat >= 3:
        return "looped"

    if not wrote_resources:
        return "abandoned"

    return "converged"
