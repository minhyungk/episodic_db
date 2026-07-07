"""Episode assembler — segment tool call chains and build episodes."""

import json
import uuid
from datetime import datetime, timezone

from episodic_db.store.db import Database
from episodic_db.store.nodes import get_session_tool_calls, insert_episode
from episodic_db.config import Config
from .signature import extract_signature
from .waste_classifier import classify_waste_type
from .metrics import compute_cost_rollup, compute_episode_metrics


def _generate_episode_id() -> str:
    return f"ep_{uuid.uuid4().hex[:8]}"


def _segment_tool_calls(tool_calls: list[dict]) -> list[list[dict]]:
    """Segment tool calls into episodes at WROTE+CONTRIBUTED boundaries."""
    if not tool_calls:
        return []

    segments = []
    current_segment = []

    for tc in tool_calls:
        current_segment.append(tc)
        is_write = tc["tool_name"] in ("Edit", "Write")
        is_contributed = tc.get("contributed_to") == "CONTRIBUTED"

        if is_write and is_contributed and len(current_segment) > 1:
            segments.append(current_segment)
            current_segment = []

    if current_segment:
        segments.append(current_segment)

    return segments


def _has_waste(segment: list[dict]) -> bool:
    """Check if a segment contains any wasteful calls."""
    did_not_count = sum(1 for tc in segment if tc.get("contributed_to") == "DID_NOT")
    return did_not_count > 0 or any(tc.get("is_wasteful") for tc in segment)


def assemble_episodes(db: Database, session_id: str, config: Config):
    """Assemble episodes for a completed session."""
    conn = db.conn
    tool_calls = get_session_tool_calls(conn, session_id)
    if not tool_calls:
        return

    segments = _segment_tool_calls(tool_calls)

    for segment in segments:
        if not _has_waste(segment):
            continue

        member_ids = [tc["tool_use_id"] for tc in segment]
        episode_id = _generate_episode_id()

        sig = extract_signature(conn, member_ids, tool_calls, session_id)

        metrics = compute_episode_metrics(conn, member_ids, tool_calls)

        waste_type = classify_waste_type(
            metrics=metrics,
            num_calls=len(segment),
            tool_calls=segment,
            thresholds=config.thresholds,
        )

        if waste_type is None:
            waste_type = "futile-exploration"

        cost = compute_cost_rollup(conn, member_ids)

        wrote_calls = [tc for tc in segment if tc["tool_name"] in ("Edit", "Write")]
        converged_by = wrote_calls[-1]["tool_use_id"] if wrote_calls else None

        has_wrote = bool(wrote_calls)
        from collections import Counter
        hashes = Counter(tc["input_hash"] for tc in segment if tc["input_hash"])
        max_rep = max(hashes.values()) if hashes else 0

        if has_wrote:
            outcome = "converged"
        elif max_rep >= 3:
            outcome = "looped"
        else:
            outcome = "abandoned"

        wasted_ids = [tc["tool_use_id"] for tc in segment if tc.get("contributed_to") == "DID_NOT" or tc.get("is_wasteful")]
        wasted_cost = sum(tc.get("own_cost", 0) for tc in segment if tc["tool_use_id"] in wasted_ids)
        wasted_tokens = sum(
            tc.get("input_tokens", 0) + tc.get("output_tokens", 0)
            for tc in segment if tc["tool_use_id"] in wasted_ids
        )

        episode_data = {
            "episode_id": episode_id,
            "session_id": session_id,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "converged_by": converged_by,
            "waste_type": waste_type,
            "outcome": outcome,
            "converged_resource": sig.get("converged_resource"),
            "touched_paths": sig.get("touched_paths", []),
            "path_prefix": sig.get("path_prefix", ""),
            "changed_symbols": sig.get("changed_symbols", []),
            "test_names": sig.get("test_names", []),
            "grep_terms": sig.get("grep_terms", []),
            "error_signature": sig.get("error_signature"),
            "lang": sig.get("lang"),
            "tool_mix": sig.get("tool_mix", {}),
            **cost,
            **metrics,
            "is_wasteful": 1,
            "wasted_member_ids": wasted_ids,
            "wasted_own_cost": round(wasted_cost, 6),
            "wasted_carry_cost": 0.0,
            "wasted_tokens": wasted_tokens,
        }

        insert_episode(conn, episode_data)

        for tid in member_ids:
            conn.execute(
                "UPDATE tool_calls SET episode_id = ? WHERE tool_use_id = ?",
                (episode_id, tid),
            )
        conn.commit()
