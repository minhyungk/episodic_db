"""Deterministic compression of session trajectories for sLLM context injection.

Operates on the conversation data loaded from episodic DB (proxy_calls table).
Applied at query time, just before feeding into the sLLM context window.

Scoring strategy:
  When DB episode/tool_call labels are available, scoring uses pre-computed
  waste signals (is_wasteful, waste_type, futility_score) for accuracy.
  Falls back to keyword heuristics when DB labels are unavailable.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, field


@dataclass
class CompressedResult:
    original_chars: int
    compressed_chars: int
    compressed_messages: list[dict]
    dropped_middle: int = 0

    @property
    def ratio(self) -> float:
        if self.original_chars == 0:
            return 1.0
        return self.compressed_chars / self.original_chars


# ---------------------------------------------------------------------------
# Strategy 1: tool_result truncation only
# ---------------------------------------------------------------------------


def truncate_tool_results(
    messages: list[dict],
    max_result_chars: int = 100,
    preserve_edit_results: bool = True,
) -> CompressedResult:
    """Truncate tool_result content to first N chars + metadata stub.

    Rules:
      - tool_result blocks get truncated to max_result_chars
      - A [TRUNCATED original_size=X] marker is appended
      - If preserve_edit_results=True, results following an Edit tool_use
        are kept intact (they're usually short success/error messages)
      - Error results (is_error=True) are always preserved in full
    """
    original_chars = len(json.dumps(messages, ensure_ascii=False))

    edit_tool_ids: set[str] = set()
    for msg in messages:
        if msg.get("role") != "assistant":
            continue
        content = msg.get("content", [])
        if not isinstance(content, list):
            continue
        for block in content:
            if block.get("type") == "tool_use" and block.get("name") == "Edit":
                edit_tool_ids.add(block.get("id", ""))

    compressed = []
    for msg in messages:
        content = msg.get("content", "")

        if not isinstance(content, list):
            compressed.append(msg)
            continue

        new_content = []
        for block in content:
            if block.get("type") != "tool_result":
                new_content.append(block)
                continue

            if block.get("is_error"):
                new_content.append(block)
                continue

            if preserve_edit_results and block.get("tool_use_id") in edit_tool_ids:
                new_content.append(block)
                continue

            result_content = block.get("content", "")
            if isinstance(result_content, str):
                original_len = len(result_content)
                if original_len <= max_result_chars:
                    new_content.append(block)
                else:
                    new_content.append({
                        **block,
                        "content": (
                            result_content[:max_result_chars]
                            + f"\n[TRUNCATED original_chars={original_len}]"
                        ),
                    })
            elif isinstance(result_content, list):
                flat_text = "".join(
                    b.get("text", "") for b in result_content if isinstance(b, dict)
                )
                original_len = len(flat_text)
                if original_len <= max_result_chars:
                    new_content.append(block)
                else:
                    new_content.append({
                        **block,
                        "content": (
                            flat_text[:max_result_chars]
                            + f"\n[TRUNCATED original_chars={original_len}]"
                        ),
                    })
            else:
                new_content.append(block)

        compressed.append({**msg, "content": new_content})

    compressed_chars = len(json.dumps(compressed, ensure_ascii=False))
    return CompressedResult(
        original_chars=original_chars,
        compressed_chars=compressed_chars,
        compressed_messages=compressed,
    )


# ---------------------------------------------------------------------------
# Strategy 2: dynamic budget-fit compression
# ---------------------------------------------------------------------------


def _chars_to_tokens(chars: int) -> int:
    return chars // 4


def _tokens_to_chars(tokens: int) -> int:
    return tokens * 4


def _find_edit_ids(messages: list[dict]) -> set[str]:
    ids: set[str] = set()
    for msg in messages:
        if msg.get("role") != "assistant":
            continue
        content = msg.get("content", [])
        if not isinstance(content, list):
            continue
        for block in content:
            if block.get("type") == "tool_use" and block.get("name") == "Edit":
                ids.add(block.get("id", ""))
    return ids


def _msg_has_edit(msg: dict, edit_ids: set[str]) -> bool:
    content = msg.get("content", [])
    if not isinstance(content, list):
        return False
    for block in content:
        if block.get("type") == "tool_use" and block.get("name") == "Edit":
            return True
        if block.get("type") == "tool_result" and block.get("tool_use_id") in edit_ids:
            return True
    return False


_FAILURE_KEYWORDS = frozenset([
    "error", "traceback", "failed", "exception", "not found",
    "no such file", "cannot", "permission denied", "exit code 1",
])

_REASONING_FAILURE_KEYWORDS = frozenset([
    "wrong", "doesn't work", "failed", "incorrect", "mistake",
    "bug", "the issue is", "the problem is", "not working",
])


def _is_failure_result(block: dict) -> bool:
    """Check if a tool_result block represents a failure."""
    if block.get("is_error"):
        return True
    rc = block.get("content", "")
    if isinstance(rc, str):
        lower = rc.lower()
        return any(kw in lower for kw in _FAILURE_KEYWORDS)
    return False


def _is_failure_reasoning(text: str) -> bool:
    """Check if assistant text contains reasoning about a failure."""
    lower = text.lower()
    return any(kw in lower for kw in _REASONING_FAILURE_KEYWORDS)


def _skeleton_message(msg: dict, preserve_failures: bool = True) -> dict:
    """Collapse a message to a skeleton, optionally preserving failure info.

    When preserve_failures=True:
      - Error tool_results are kept (truncated to 500 chars)
      - Assistant reasoning about failures is kept (truncated to 300 chars)
      - Everything else is skeletonized as before
    """
    content = msg.get("content", [])
    if not isinstance(content, list):
        return msg

    skeleton_parts = []
    for block in content:
        btype = block.get("type", "")
        if btype == "text" and msg.get("role") == "assistant":
            text = block.get("text", "")
            if not text.strip():
                continue
            if preserve_failures and _is_failure_reasoning(text):
                skeleton_parts.append({
                    "type": "text",
                    "text": text[:300] + ("[...]" if len(text) > 300 else ""),
                })
            else:
                skeleton_parts.append({"type": "text", "text": text[:80]})
        elif btype == "tool_use":
            name = block.get("name", "")
            inp = block.get("input", {})
            if name == "Bash":
                cmd = inp.get("command", "")[:120]
                skeleton_parts.append({
                    "type": "text",
                    "text": f"[{name}] {cmd}",
                })
            elif name == "Read":
                fp = inp.get("file_path", "").split("/")[-1]
                skeleton_parts.append({
                    "type": "text",
                    "text": f"[{name}] {fp} @{inp.get('offset', 0)}",
                })
            else:
                skeleton_parts.append({
                    "type": "text",
                    "text": f"[{name}] {json.dumps(inp)[:80]}",
                })
        elif btype == "tool_result":
            if preserve_failures and _is_failure_result(block):
                rc = block.get("content", "")
                if isinstance(rc, str):
                    truncated = rc[:500] + (
                        f"\n[TRUNCATED original_chars={len(rc)}]"
                        if len(rc) > 500 else ""
                    )
                else:
                    truncated = json.dumps(rc)[:500]
                skeleton_parts.append({
                    "type": "text",
                    "text": f"[FAILURE] {truncated}",
                })
            else:
                rc = block.get("content", "")
                size = len(rc) if isinstance(rc, str) else len(json.dumps(rc))
                skeleton_parts.append({
                    "type": "text",
                    "text": f"[result: {size} chars]",
                })

    if not skeleton_parts:
        return msg

    return {"role": msg["role"], "content": skeleton_parts}


_NOISE_COMMANDS = frozenset([
    "git log", "git show", "git blame", "git diff",
    "find ", "ls ", "wc ", "cat ",
])


def _score_message(msg: dict) -> int:
    """Score a message's learning value (higher = more worth keeping).

    Scoring:
      4 — assistant reasoning about failure/wrong approach
      3 — tool_result with error (the actual failure output)
      2 — tool_use that led somewhere (verification, test run)
      1 — exploration (grep/find/read) with meaningful reasoning
      0 — noise (git log, ls, redundant read with no reasoning)
    """
    content = msg.get("content", [])
    if not isinstance(content, list):
        return 1

    score = 0
    for block in content:
        btype = block.get("type", "")
        if btype == "text" and msg.get("role") == "assistant":
            text = block.get("text", "")
            if _is_failure_reasoning(text):
                score = max(score, 4)
            elif text.strip():
                score = max(score, 1)
        elif btype == "tool_result":
            if _is_failure_result(block):
                score = max(score, 3)
        elif btype == "tool_use":
            name = block.get("name", "")
            inp = block.get("input", {})
            cmd = inp.get("command", "") if name == "Bash" else ""
            if name in ("Edit", "Write"):
                score = max(score, 4)
            elif any(cmd.lower().startswith(n) for n in _NOISE_COMMANDS):
                score = max(score, 0)
            elif any(x in cmd.lower() for x in ["python", "pytest", "test"]):
                score = max(score, 2)
            else:
                score = max(score, 1)
    return score


# ---------------------------------------------------------------------------
# DB-aware scoring: uses pre-computed episode labels for accurate scoring
# ---------------------------------------------------------------------------


@dataclass
class WasteAnnotation:
    """Per-message waste annotation loaded from DB."""
    tool_use_id: str
    is_wasteful: bool = False
    episode_id: str | None = None
    waste_type: str | None = None
    futility_score: float = 0.0
    wasted_cost: float = 0.0


def load_waste_annotations(
    conn: sqlite3.Connection,
    session_id: str,
) -> dict[str, WasteAnnotation]:
    """Load waste labels for all tool_calls in a session.

    Returns a dict mapping tool_use_id → WasteAnnotation.
    """
    cur = conn.execute(
        """SELECT tc.tool_use_id, tc.is_wasteful, tc.episode_id,
                  ep.waste_type, ep.futility_score, ep.wasted_own_cost
           FROM tool_calls tc
           LEFT JOIN episodes ep ON tc.episode_id = ep.episode_id
           WHERE tc.session_id = ?
           ORDER BY tc.seq""",
        (session_id,),
    )
    annotations = {}
    for row in cur.fetchall():
        annotations[row["tool_use_id"]] = WasteAnnotation(
            tool_use_id=row["tool_use_id"],
            is_wasteful=bool(row["is_wasteful"]),
            episode_id=row["episode_id"],
            waste_type=row["waste_type"],
            futility_score=row["futility_score"] or 0.0,
            wasted_cost=row["wasted_own_cost"] or 0.0,
        )
    return annotations


def _score_message_with_labels(msg: dict, annotations: dict[str, WasteAnnotation]) -> int:
    """Score using DB-computed waste labels. More accurate than keyword heuristics.

    Scoring (higher = more learning value, keep longer):
      5 — is_wasteful call in expensive-failure or repeated-loop episode
      4 — is_wasteful call in any waste episode / assistant reasoning about failure
      3 — non-wasteful call in a waste episode (context for understanding the waste)
      2 — productive call with verification (test/python)
      1 — productive exploration
      0 — noise (git log, ls) in productive episode
    """
    content = msg.get("content", [])
    if not isinstance(content, list):
        return 1

    tool_use_ids_in_msg = []
    for block in content:
        if block.get("type") == "tool_use" and block.get("id"):
            tool_use_ids_in_msg.append(block["id"])
        if block.get("type") == "tool_result" and block.get("tool_use_id"):
            tool_use_ids_in_msg.append(block["tool_use_id"])

    matched_annotations = [annotations[tid] for tid in tool_use_ids_in_msg if tid in annotations]

    if matched_annotations:
        for ann in matched_annotations:
            if ann.is_wasteful and ann.waste_type in ("expensive-failure", "repeated-loop"):
                return 5
            if ann.is_wasteful:
                return 4
            if ann.waste_type and ann.waste_type != "productive":
                return 3

    # No DB labels matched — fall back to keyword heuristics
    return _score_message(msg)


def score_messages(
    messages: list[dict],
    conn: sqlite3.Connection | None = None,
    session_id: str | None = None,
) -> list[int]:
    """Score all messages. Uses DB labels when available, else keyword fallback."""
    annotations = {}
    if conn and session_id:
        try:
            annotations = load_waste_annotations(conn, session_id)
        except Exception:
            pass

    if annotations:
        return [_score_message_with_labels(m, annotations) for m in messages]
    return [_score_message(m) for m in messages]


def _compress_message_by_tier(msg: dict, tier: int) -> dict:
    """Compress a message based on its tier assignment.

    Tier 0 (noise):   skeleton — tool name only, no result
    Tier 1 (explore): skeleton — tool name + short command, result size
    Tier 2 (verify):  keep command, truncate result to 200 chars
    Tier 3 (error):   keep command + error result (500 chars)
    Tier 4 (reason):  keep full assistant text + command + error result
    """
    content = msg.get("content", [])
    if not isinstance(content, list):
        return msg

    parts = []
    for block in content:
        btype = block.get("type", "")

        if btype == "text" and msg.get("role") == "assistant":
            text = block.get("text", "")
            if not text.strip():
                continue
            if tier >= 4:
                parts.append({"type": "text", "text": text[:500]})
            elif tier >= 1 and _is_failure_reasoning(text):
                parts.append({"type": "text", "text": text[:300]})
            elif tier >= 1:
                parts.append({"type": "text", "text": text[:80]})
            # tier 0: drop assistant text

        elif btype == "tool_use":
            name = block.get("name", "")
            inp = block.get("input", {})
            if tier >= 2:
                if name == "Bash":
                    cmd = inp.get("command", "")[:200]
                    parts.append({"type": "text", "text": f"[{name}] {cmd}"})
                else:
                    parts.append({"type": "text", "text": f"[{name}] {json.dumps(inp)[:150]}"})
            else:
                if name == "Bash":
                    cmd = inp.get("command", "")[:80]
                    parts.append({"type": "text", "text": f"[{name}] {cmd}"})
                else:
                    parts.append({"type": "text", "text": f"[{name}]"})

        elif btype == "tool_result":
            rc = block.get("content", "")
            rc_str = rc if isinstance(rc, str) else json.dumps(rc)
            is_fail = _is_failure_result(block)

            if tier >= 3 and is_fail:
                truncated = rc_str[:500]
                if len(rc_str) > 500:
                    truncated += f"\n[TRUNCATED original_chars={len(rc_str)}]"
                parts.append({"type": "text", "text": f"[FAILURE] {truncated}"})
            elif tier >= 2:
                truncated = rc_str[:200]
                if len(rc_str) > 200:
                    truncated += f"\n[TRUNCATED]"
                parts.append({"type": "text", "text": f"[result] {truncated}"})
            elif tier >= 1:
                parts.append({"type": "text", "text": f"[result: {len(rc_str)} chars]"})
            # tier 0: drop result entirely

    if not parts:
        return {"role": msg["role"], "content": [{"type": "text", "text": "[...]"}]}
    return {"role": msg["role"], "content": parts}


def fit_to_budget(
    messages: list[dict],
    max_tokens: int = 32000,
    head_messages: int = 3,
    preserve_failures: bool = True,
    conn: sqlite3.Connection | None = None,
    session_id: str | None = None,
) -> CompressedResult:
    """Dynamically compress a conversation to fit within a token budget.

    Process-preserving strategy: prioritizes keeping the "how it failed"
    narrative over the solution details. Wasteful episodes are kept longest
    because they are the most valuable learning material.

    Compression passes (applied until it fits):
      1. Truncate all tool_results to 100 chars (keep everything else)
      2. Score each message by learning value (DB labels or keyword fallback),
         compress low-value messages more aggressively
      3. Progressively lower the tier threshold until budget is met

    Priority (what to keep longest — higher score = more learning value):
      With DB labels:
        5 — is_wasteful in expensive-failure/repeated-loop episode
        4 — is_wasteful in any waste episode
        3 — non-wasteful call in a waste episode (context)
        2 — productive verification
        1 — productive exploration
        0 — noise
      Without DB labels (keyword fallback):
        4 — assistant reasoning about failure
        3 — error tool_results
        2 — test/python commands
        1 — exploration with reasoning
        0 — git log, ls, find noise

    Args:
        messages: Full conversation messages from DB.
        max_tokens: Target context window size.
        head_messages: Number of messages to always keep at the start.
        preserve_failures: Keep error results and failure reasoning.
        conn: Optional DB connection for waste label lookup.
        session_id: Session ID for waste label lookup.
    """
    budget_chars = _tokens_to_chars(max_tokens)
    original_chars = len(json.dumps(messages, ensure_ascii=False))

    # --- Pass 1: tool_result truncation (gentle, keeps all messages) ---
    pass1 = truncate_tool_results(messages, max_result_chars=100)
    if pass1.compressed_chars <= budget_chars:
        return pass1

    # --- Pass 2: value-based selective compression ---
    truncated_msgs = pass1.compressed_messages
    edit_ids = _find_edit_ids(messages)

    # Score messages using DB labels if available, else keyword heuristics
    all_scores = score_messages(messages, conn=conn, session_id=session_id)

    # Head is always kept intact
    head = truncated_msgs[:head_messages]
    rest = truncated_msgs[head_messages:]

    # Use pre-computed scores (skip head)
    scores = all_scores[head_messages:]

    # Try progressively compressing lower-tier messages
    # Start by compressing only tier-0, then tier-0+1, etc.
    max_tier = max(scores) if scores else 4
    for cut_below in range(1, max_tier + 1):
        compressed_rest = []
        for m, score in zip(rest, scores):
            if _msg_has_edit(m, edit_ids):
                compressed_rest.append(m)
            elif score >= cut_below:
                compressed_rest.append(m)
            else:
                compressed_rest.append(_compress_message_by_tier(m, tier=score))

        candidate = head + compressed_rest
        candidate_chars = len(json.dumps(candidate, ensure_ascii=False))
        if candidate_chars <= budget_chars:
            dropped = sum(1 for s in scores if s < cut_below)
            return CompressedResult(
                original_chars=original_chars,
                compressed_chars=candidate_chars,
                compressed_messages=candidate,
                dropped_middle=dropped,
            )

    # --- Pass 3: everything skeletonized at its own tier level ---
    compressed_rest = []
    for m, score in zip(rest, scores):
        if _msg_has_edit(m, edit_ids):
            compressed_rest.append(m)
        else:
            compressed_rest.append(_compress_message_by_tier(m, tier=min(score, 1)))

    candidate = head + compressed_rest
    candidate_chars = len(json.dumps(candidate, ensure_ascii=False))
    if candidate_chars <= budget_chars:
        return CompressedResult(
            original_chars=original_chars,
            compressed_chars=candidate_chars,
            compressed_messages=candidate,
            dropped_middle=sum(1 for s in scores if s > 1),
        )

    # --- Pass 4: drop tier 0-1 entirely, keep only tier 2+ as skeletons ---
    compressed_rest = []
    for m, score in zip(rest, scores):
        if _msg_has_edit(m, edit_ids):
            compressed_rest.append(m)
        elif score >= 2:
            compressed_rest.append(_compress_message_by_tier(m, tier=min(score, 2)))

    separator = {
        "role": "user",
        "content": [{"type": "text", "text": f"[... {sum(1 for s in scores if s < 2)} low-value messages dropped ...]"}],
    }
    candidate = head + [separator] + compressed_rest
    candidate_chars = len(json.dumps(candidate, ensure_ascii=False))
    if candidate_chars <= budget_chars:
        return CompressedResult(
            original_chars=original_chars,
            compressed_chars=candidate_chars,
            compressed_messages=candidate,
            dropped_middle=sum(1 for s in scores if s < 2),
        )

    # --- Pass 5: keep only Edit messages + failure reasoning ---
    essential = []
    for m, score in zip(rest, scores):
        if _msg_has_edit(m, edit_ids) or score >= 4:
            essential.append(_compress_message_by_tier(m, tier=2))

    separator = {
        "role": "user",
        "content": [{"type": "text", "text": f"[... {len(rest) - len(essential)} messages dropped, {len(essential)} essential kept ...]"}],
    }
    candidate = head + [separator] + essential
    candidate_chars = len(json.dumps(candidate, ensure_ascii=False))

    return CompressedResult(
        original_chars=original_chars,
        compressed_chars=candidate_chars,
        compressed_messages=candidate,
        dropped_middle=len(rest) - len(essential),
    )
