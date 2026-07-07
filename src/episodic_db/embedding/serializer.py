"""Serialize episode signature facets into embedding source text."""

import json


def serialize_signature(episode: dict) -> str:
    """Serialize semantic facets into a fixed-order text string for embedding.

    Only semantic facets are included (no numeric metrics/costs).
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

    return " | ".join(parts)
