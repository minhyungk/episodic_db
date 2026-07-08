"""Serialize episode signature facets into embedding source text."""

import json


def serialize_signature(episode: dict, tool_calls: list[dict] | None = None) -> str:
    """Serialize semantic facets into a fixed-order text string for embedding.

    Includes: waste_type, outcome, lang, paths, grep terms, changed symbols,
    error signature, and a sample of normalized tool inputs for context.
    """
    parts = []

    if episode.get("waste_type"):
        parts.append(episode["waste_type"])

    if episode.get("outcome"):
        parts.append(episode["outcome"])

    if episode.get("lang"):
        parts.append(episode["lang"])

    if episode.get("path_prefix"):
        parts.append(episode["path_prefix"])

    if episode.get("converged_resource"):
        parts.append(f"conv={episode['converged_resource']}")

    grep_terms = episode.get("grep_terms")
    if grep_terms:
        if isinstance(grep_terms, str):
            grep_terms = json.loads(grep_terms)
        if grep_terms:
            parts.append(f"grep({','.join(grep_terms)})")

    changed_symbols = episode.get("changed_symbols")
    if changed_symbols:
        if isinstance(changed_symbols, str):
            changed_symbols = json.loads(changed_symbols)
        if changed_symbols:
            parts.append(f"changed({','.join(changed_symbols)})")

    test_names = episode.get("test_names")
    if test_names:
        if isinstance(test_names, str):
            test_names = json.loads(test_names)
        if test_names:
            parts.append(f"test({','.join(test_names)})")

    if episode.get("error_signature"):
        parts.append(f"err({episode['error_signature']})")

    # Add normalized_input context from tool calls
    if tool_calls:
        wasted_member_ids = episode.get("wasted_member_ids", [])
        if isinstance(wasted_member_ids, str):
            wasted_member_ids = json.loads(wasted_member_ids)

        # Sample up to 10 normalized inputs from wasteful calls
        sample_inputs = []
        for tc in tool_calls:
            if tc.get("tool_use_id") in wasted_member_ids and tc.get("normalized_input"):
                norm = tc["normalized_input"]
                # Strip long paths for brevity
                if norm.startswith(("Read ", "Write ", "Edit ")):
                    norm = norm.split("/")[-1] if "/" in norm else norm
                sample_inputs.append(norm)
                if len(sample_inputs) >= 10:
                    break

        if sample_inputs:
            parts.append(f"actions({'; '.join(sample_inputs[:10])})")

    return " | ".join(parts)
