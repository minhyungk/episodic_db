"""Extract signature facets from episode members."""

import json
import os
import re
import sqlite3
from collections import Counter


def _detect_lang(paths: list[str]) -> str | None:
    ext_map = {
        ".py": "python", ".js": "javascript", ".ts": "typescript",
        ".tsx": "typescript", ".jsx": "javascript", ".rs": "rust",
        ".go": "go", ".rb": "ruby", ".java": "java", ".c": "c",
        ".cpp": "cpp", ".h": "c", ".cs": "csharp", ".swift": "swift",
        ".kt": "kotlin", ".scala": "scala", ".php": "php",
    }
    ext_counts: Counter = Counter()
    for p in paths:
        _, ext = os.path.splitext(p)
        if ext in ext_map:
            ext_counts[ext_map[ext]] += 1
    if ext_counts:
        return ext_counts.most_common(1)[0][0]
    return None


def _extract_grep_terms(tool_calls: list[dict]) -> list[str]:
    """Extract search terms from Grep, Search, and Bash grep commands."""
    terms = set()
    for tc in tool_calls:
        norm = tc.get("normalized_input", "")

        # Grep/Search tools
        if tc["tool_name"] in ("Grep", "Search"):
            match = re.search(r"(?:Grep|Search): (.+)", norm)
            if match:
                raw = match.group(1).strip()
                for token in re.split(r"[\s|&,]+", raw):
                    clean = token.strip("'\"()[]")
                    if clean and len(clean) > 2:
                        terms.add(clean.lower())

        # Bash grep commands
        elif tc["tool_name"] == "Bash" and "grep" in norm.lower():
            # Extract quoted strings from grep commands
            quoted = re.findall(r'["\']([^"\']+)["\']', norm)
            for q in quoted:
                if len(q) > 2 and not q.startswith('-'):
                    terms.add(q.lower())

            # Extract patterns after grep without quotes
            grep_match = re.search(r'grep\s+(?:-\w+\s+)*"?([^"\s|]+)"?', norm)
            if grep_match:
                pattern = grep_match.group(1).strip()
                if len(pattern) > 2 and not pattern.startswith('-'):
                    terms.add(pattern.lower())

    return sorted(terms)[:20]


def _extract_error_signature(conn: sqlite3.Connection, member_ids: list[str]) -> str | None:
    """Extract and normalize error messages from failed tool calls."""
    placeholders = ",".join(["?"] * len(member_ids))

    # Try is_error=1 results first
    cur = conn.execute(
        f"SELECT inline_content FROM results WHERE tool_use_id IN ({placeholders}) AND is_error = 1 LIMIT 1",
        member_ids,
    )
    row = cur.fetchone()

    # Fallback: look for error patterns in any result
    if not row or not row["inline_content"]:
        cur = conn.execute(
            f"SELECT inline_content FROM results WHERE tool_use_id IN ({placeholders}) AND inline_content IS NOT NULL",
            member_ids,
        )
        for candidate_row in cur.fetchall():
            content = candidate_row["inline_content"] or ""
            if any(pattern in content.lower() for pattern in ("error", "exception", "traceback", "failed")):
                row = candidate_row
                break

    if not row or not row["inline_content"]:
        return None

    content = row["inline_content"]
    lines = content.strip().split("\n")

    # Look for standard error patterns
    for line in reversed(lines[-20:]):  # last 20 lines
        line = line.strip()
        # Python exceptions
        if re.match(r"^([\w.]+Error|[\w.]+Exception):", line):
            normalized = re.sub(r"line \d+", "line N", line)
            normalized = re.sub(r'"/[^"]+"', '"PATH"', normalized)
            normalized = re.sub(r"'[^']*'", "'X'", normalized)
            return normalized[:200]
        # Generic error messages
        if re.search(r"\b(error|failed|exception)\b", line, re.IGNORECASE) and len(line) > 10:
            normalized = re.sub(r"\d+", "N", line)
            normalized = re.sub(r'"/[^"]+"', '"PATH"', normalized)
            return normalized[:200]

    return lines[-1][:200] if lines else None


def extract_signature(conn: sqlite3.Connection, member_ids: list[str], tool_calls: list[dict], session_id: str) -> dict:
    """Extract all facets for an episode's signature."""
    member_set = set(member_ids)
    members = [tc for tc in tool_calls if tc["tool_use_id"] in member_set]

    placeholders = ",".join(["?"] * len(member_ids))
    cur = conn.execute(
        f"SELECT DISTINCT resource_id FROM edges_touches WHERE tool_use_id IN ({placeholders})",
        member_ids,
    )
    touched_resources = [row["resource_id"] for row in cur.fetchall()]

    touched_paths = [r.replace("path:", "") for r in touched_resources if r.startswith("path:")]

    path_prefix = ""
    if touched_paths:
        path_prefix = os.path.commonprefix(touched_paths)
        if path_prefix and not path_prefix.endswith("/"):
            path_prefix = os.path.dirname(path_prefix)
            if path_prefix:
                path_prefix += "/"

    cur = conn.execute(
        f"""SELECT DISTINCT et.resource_id FROM edges_touches et
            WHERE et.tool_use_id IN ({placeholders}) AND et.mode = 'WROTE'""",
        member_ids,
    )
    wrote_resources = [row["resource_id"] for row in cur.fetchall()]
    converged_resource = None
    if wrote_resources:
        converged_resource = wrote_resources[-1].replace("path:", "")

    changed_symbols = _extract_changed_symbols(conn, member_ids)

    test_names = [
        r.replace("test:", "") for r in touched_resources if r.startswith("test:")
    ]

    grep_terms = _extract_grep_terms(members)

    error_sig = _extract_error_signature(conn, member_ids)

    lang = _detect_lang(touched_paths)

    tool_mix = dict(Counter(tc["tool_name"] for tc in members))

    return {
        "converged_resource": converged_resource,
        "touched_paths": touched_paths,
        "path_prefix": path_prefix,
        "changed_symbols": changed_symbols,
        "test_names": test_names,
        "grep_terms": grep_terms,
        "error_signature": error_sig,
        "lang": lang,
        "tool_mix": tool_mix,
    }


def _extract_changed_symbols(conn: sqlite3.Connection, member_ids: list[str]) -> list[str]:
    """Extract function/class names from edit operations."""
    symbols = set()
    placeholders = ",".join(["?"] * len(member_ids))
    cur = conn.execute(
        f"SELECT normalized_input FROM tool_calls WHERE tool_use_id IN ({placeholders}) AND tool_name IN ('Edit', 'Write')",
        member_ids,
    )
    for row in cur.fetchall():
        norm = row["normalized_input"] or ""
        parts = norm.split(" ")
        if len(parts) >= 2:
            path = parts[1] if len(parts) > 1 else ""
            basename = os.path.basename(path).replace(".py", "").replace(".ts", "").replace(".js", "")
            if basename:
                symbols.add(basename)
    return sorted(symbols)[:20]
