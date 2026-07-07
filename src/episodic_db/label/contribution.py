"""Patch reachability — determine which tool calls contributed to final output.

Algorithm: from WROTE resources, walk backward through the call chain.
A call CONTRIBUTED if it's on a path that reaches a WROTE call.
"""

import sqlite3

from episodic_db.store.edges import get_wrote_resources, get_read_tool_calls_for_resource, get_wrote_tool_calls
from episodic_db.store.nodes import get_session_tool_calls


def mark_contributions(conn: sqlite3.Connection, session_id: str):
    """Mark each tool_call as CONTRIBUTED or DID_NOT based on patch reachability.

    A call is CONTRIBUTED if:
    1. It directly WROTE a final patch resource, OR
    2. It READ a resource that was eventually WROTE by a later call in the same session
       (i.e., it read the same file that was later edited — information gathering for the patch)

    Simple NEXT-chain adjacency does NOT propagate contribution.
    """
    wrote_resources = get_wrote_resources(conn, session_id)
    if not wrote_resources:
        conn.execute(
            "UPDATE tool_calls SET contributed_to = 'DID_NOT' WHERE session_id = ? AND contributed_to IS NULL",
            (session_id,),
        )
        conn.commit()
        return

    tool_calls = get_session_tool_calls(conn, session_id)
    if not tool_calls:
        return

    wrote_call_ids = set(get_wrote_tool_calls(conn, session_id))
    reachable = set(wrote_call_ids)

    for resource_id in wrote_resources:
        reader_ids = get_read_tool_calls_for_resource(conn, session_id, resource_id)
        for rid in reader_ids:
            reachable.add(rid)

    for tc in tool_calls:
        tid = tc["tool_use_id"]
        label = "CONTRIBUTED" if tid in reachable else "DID_NOT"
        conn.execute(
            "UPDATE tool_calls SET contributed_to = ? WHERE tool_use_id = ?",
            (label, tid),
        )
    conn.commit()
