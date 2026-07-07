"""Compute per-call waste metrics: new_information_rate, repeated_read_rate, duplicates."""

import sqlite3

from episodic_db.store.nodes import get_session_tool_calls, get_tool_call_results
from episodic_db.store.edges import insert_duplicate_of


def compute_waste_signals(conn: sqlite3.Connection, session_id: str):
    """Identify duplicate calls and mark wasteful ones."""
    tool_calls = get_session_tool_calls(conn, session_id)
    if not tool_calls:
        return

    seen_hashes: dict[str, str] = {}
    duplicate_count = 0

    for tc in tool_calls:
        input_hash = tc["input_hash"]
        tid = tc["tool_use_id"]

        if input_hash and input_hash in seen_hashes:
            insert_duplicate_of(conn, tid, seen_hashes[input_hash])
            duplicate_count += 1
        elif input_hash:
            seen_hashes[input_hash] = tid

    seen_result_hashes: dict[str, str] = {}
    no_new_info_ids = set()

    for tc in tool_calls:
        tid = tc["tool_use_id"]
        results = get_tool_call_results(conn, tid)
        for r in results:
            rh = r["result_hash"]
            if rh and rh in seen_result_hashes:
                no_new_info_ids.add(tid)
            elif rh:
                seen_result_hashes[rh] = tid

    for tid in no_new_info_ids:
        conn.execute(
            "UPDATE tool_calls SET is_wasteful = 1 WHERE tool_use_id = ? AND contributed_to = 'DID_NOT'",
            (tid,),
        )
    conn.commit()
