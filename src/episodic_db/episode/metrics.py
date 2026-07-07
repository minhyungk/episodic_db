"""Compute episode-level cost rollup and metrics."""

import sqlite3

from episodic_db.store.nodes import get_tool_call_results


def compute_cost_rollup(conn: sqlite3.Connection, member_ids: list[str]) -> dict:
    """Sum token and cost data for episode members."""
    if not member_ids:
        return {
            "total_input_tokens": 0,
            "total_output_tokens": 0,
            "total_cache_creation": 0,
            "total_cache_read": 0,
            "own_cost": 0.0,
            "carry_cost": 0.0,
            "total_cost": 0.0,
            "carry_ratio": 0.0,
        }

    placeholders = ",".join(["?"] * len(member_ids))
    cur = conn.execute(
        f"""SELECT
            COALESCE(SUM(input_tokens), 0) as total_input,
            COALESCE(SUM(output_tokens), 0) as total_output,
            COALESCE(SUM(cache_creation_tokens), 0) as total_cache_creation,
            COALESCE(SUM(cache_read_tokens), 0) as total_cache_read,
            COALESCE(SUM(own_cost), 0) as own_cost,
            COALESCE(SUM(carry_cost), 0) as carry_cost,
            COALESCE(SUM(total_cost), 0) as total_cost
        FROM tool_calls WHERE tool_use_id IN ({placeholders})""",
        member_ids,
    )
    row = cur.fetchone()
    total = row["total_cost"] if row["total_cost"] else 0.0001
    carry = row["carry_cost"] if row["carry_cost"] else 0.0

    return {
        "total_input_tokens": row["total_input"],
        "total_output_tokens": row["total_output"],
        "total_cache_creation": row["total_cache_creation"],
        "total_cache_read": row["total_cache_read"],
        "own_cost": row["own_cost"],
        "carry_cost": carry,
        "total_cost": row["total_cost"],
        "carry_ratio": round(carry / total, 3) if total > 0 else 0.0,
    }


def compute_episode_metrics(conn: sqlite3.Connection, member_ids: list[str], tool_calls: list[dict]) -> dict:
    """Compute analytical metrics for the episode."""
    if not tool_calls:
        return {
            "read_output_token_ratio": 0.0,
            "new_information_rate": 0.0,
            "repeated_read_rate": 0.0,
            "futility_score": 0.0,
        }

    member_set = set(member_ids)
    members = [tc for tc in tool_calls if tc["tool_use_id"] in member_set]

    total_output_tokens = sum(tc["output_tokens"] for tc in members)
    read_tools = ("Read", "Grep", "Search", "Bash")
    read_output_tokens = sum(
        tc["output_tokens"] for tc in members if tc["tool_name"] in read_tools
    )
    read_output_ratio = (
        read_output_tokens / total_output_tokens if total_output_tokens > 0 else 0.0
    )

    seen_result_hashes = set()
    new_info_count = 0
    for tc in members:
        results = get_tool_call_results(conn, tc["tool_use_id"])
        for r in results:
            rh = r["result_hash"]
            if rh and rh not in seen_result_hashes:
                seen_result_hashes.add(rh)
                new_info_count += 1
                break
        else:
            if not results:
                new_info_count += 1

    new_info_rate = new_info_count / len(members) if members else 0.0

    read_targets = {}
    repeated_reads = 0
    for tc in members:
        if tc["tool_name"] in ("Read", "Grep"):
            key = tc["input_hash"]
            if key in read_targets:
                repeated_reads += 1
            else:
                read_targets[key] = True

    total_reads = sum(1 for tc in members if tc["tool_name"] in ("Read", "Grep"))
    repeated_read_rate = repeated_reads / total_reads if total_reads > 0 else 0.0

    futility_score = (
        0.3 * (1 - new_info_rate)
        + 0.3 * repeated_read_rate
        + 0.2 * read_output_ratio
        + 0.2 * (len(members) / 50.0 if len(members) < 50 else 1.0)
    )

    return {
        "read_output_token_ratio": round(read_output_ratio, 3),
        "new_information_rate": round(new_info_rate, 3),
        "repeated_read_rate": round(repeated_read_rate, 3),
        "futility_score": round(min(futility_score, 1.0), 3),
    }
