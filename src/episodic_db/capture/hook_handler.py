"""Hook handler: receives Claude Code hook events on stdin, writes to SQLite.

Invoked as a subprocess by Claude Code for each hook event.
Must complete within 5s (30s for SessionEnd).
"""

import hashlib
import json
import os
import sys
from pathlib import Path

from episodic_db.store.db import Database
from episodic_db.store.nodes import (
    insert_session,
    insert_tool_call,
    insert_resource,
    insert_result,
    update_session_end,
    update_tool_call_result,
)
from episodic_db.store.edges import insert_touches
from episodic_db.store.blob_store import BlobStore
from episodic_db.config import Config


def _hash_input(tool_input: dict | str) -> str:
    raw = json.dumps(tool_input, sort_keys=True) if isinstance(tool_input, dict) else str(tool_input)
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


def _hash_content(content: str) -> str:
    return hashlib.sha256(content.encode()).hexdigest()[:16]


def _normalize_input(tool_name: str, tool_input: dict) -> str:
    if tool_name in ("Read", "Write", "Edit"):
        path = tool_input.get("file_path", "")
        return f"{tool_name} {path}"
    if tool_name == "Bash":
        cmd = tool_input.get("command", "")
        return f"Bash: {cmd[:100]}"
    if tool_name in ("Grep", "Search"):
        query = tool_input.get("query", tool_input.get("pattern", ""))
        return f"{tool_name}: {query}"
    return f"{tool_name}: {json.dumps(tool_input)[:100]}"


def _extract_resource(tool_name: str, tool_input: dict, mode: str) -> tuple[str, str] | None:
    """Extract resource_id and kind from tool input."""
    if tool_name in ("Read", "Write", "Edit"):
        path = tool_input.get("file_path", "")
        if path:
            return f"path:{path}", "path"
    if tool_name == "Bash":
        cmd = tool_input.get("command", "")
        if cmd.startswith("git "):
            return f"git_ref:{cmd[:50]}", "git_ref"
    return None


def _get_seq(conn, session_id: str) -> int:
    cur = conn.execute(
        "SELECT COALESCE(MAX(seq), -1) + 1 FROM tool_calls WHERE session_id = ?",
        (session_id,),
    )
    return cur.fetchone()[0]


def _resolve_session_id(payload: dict) -> str:
    """Use env-overridden session_id if set (bench mode), else Claude's native one."""
    return os.environ.get("EPISODIC_DB_SESSION_ID", "") or payload.get("session_id", "")


def handle_session_start(payload: dict, db: Database, config: Config):
    session_id = _resolve_session_id(payload)
    cwd = payload.get("cwd", os.getcwd())

    from episodic_db.capture.env_capture import full_snapshot
    env = full_snapshot(cwd)

    insert_session(db.conn, session_id, env)


def handle_pre_tool_use(payload: dict, db: Database, config: Config):
    session_id = _resolve_session_id(payload)
    tool_use_id = payload.get("tool_use_id", "")
    tool_name = payload.get("tool_name", "")
    tool_input = payload.get("tool_input", {})

    if not tool_use_id or not session_id:
        return

    seq = _get_seq(db.conn, session_id)
    input_hash = _hash_input(tool_input)
    normalized = _normalize_input(tool_name, tool_input)
    tool_input_json = json.dumps(tool_input) if tool_input else None

    insert_tool_call(
        db.conn,
        tool_use_id=tool_use_id,
        session_id=session_id,
        seq=seq,
        tool_name=tool_name,
        input_hash=input_hash,
        normalized_input=normalized,
        tool_input_json=tool_input_json,
    )

    mode = "WROTE" if tool_name in ("Write", "Edit") else "READ"
    resource_info = _extract_resource(tool_name, tool_input, mode)
    if resource_info:
        resource_id, kind = resource_info
        insert_resource(db.conn, resource_id, kind)
        insert_touches(db.conn, tool_use_id, resource_id, mode)


def handle_post_tool_use(payload: dict, db: Database, config: Config):
    debug_log = os.environ.get("EPISODIC_DB_DEBUG_LOG")
    if debug_log:
        with open(debug_log, "a") as f:
            f.write(f"PostToolUse keys: {list(payload.keys())}\n")
            tool_resp = payload.get("tool_response", "")
            f.write(f"  tool_response type={type(tool_resp).__name__} len={len(str(tool_resp)[:200])}\n")
            if isinstance(tool_resp, dict):
                f.write(f"  tool_response keys: {list(tool_resp.keys())}\n")

    session_id = _resolve_session_id(payload)
    tool_use_id = payload.get("tool_use_id", "")
    tool_output = payload.get("tool_response", "") or payload.get("tool_result", "") or payload.get("tool_output", "")

    if not tool_use_id:
        return

    output_str = tool_output if isinstance(tool_output, str) else json.dumps(tool_output)
    output_chars = len(output_str)
    output_lines = output_str.count("\n") + 1
    is_error = payload.get("is_error", False)

    if output_chars > config.blob_inline_max_chars:
        blob_store = BlobStore(config.blob_dir)
        digest_handle = blob_store.store(output_str)
        inline_content = None
    else:
        digest_handle = None
        inline_content = output_str

    result_hash = _hash_content(output_str)

    insert_result(
        db.conn,
        tool_use_id=tool_use_id,
        result_hash=result_hash,
        digest_handle=digest_handle,
        inline_content=inline_content,
        is_error=is_error,
        output_chars=output_chars,
        output_lines=output_lines,
    )

    update_tool_call_result(db.conn, tool_use_id)


def handle_session_end(payload: dict, db: Database, config: Config):
    session_id = _resolve_session_id(payload)
    if not session_id:
        return

    update_session_end(db.conn, session_id)

    from episodic_db.label.pipeline import run_labeling
    from episodic_db.episode.assembler import assemble_episodes

    run_labeling(db, session_id)
    assemble_episodes(db, session_id, config)


def handle_stop(payload: dict, db: Database, config: Config):
    session_id = _resolve_session_id(payload)
    if session_id:
        update_session_end(db.conn, session_id, success=True)


def main():
    debug_log = os.environ.get("EPISODIC_DB_DEBUG_LOG")
    if debug_log:
        with open(debug_log, "a") as f:
            f.write(f"hook_handler invoked: ACTIVE={os.environ.get('EPISODIC_DB_ACTIVE')} PATH={os.environ.get('EPISODIC_DB_PATH')}\n")

    if not os.environ.get("EPISODIC_DB_ACTIVE"):
        sys.exit(0)

    db_path = os.environ.get("EPISODIC_DB_PATH")
    if not db_path:
        sys.exit(0)

    try:
        raw = sys.stdin.read()
        payload = json.loads(raw) if raw.strip() else {}
    except (json.JSONDecodeError, IOError):
        sys.exit(0)

    hook_event = payload.get("hook_event_name", "")
    if not hook_event:
        sys.exit(0)

    config = Config(db_path=Path(db_path))
    db = Database(config.db_path)
    db.connect()

    try:
        handlers = {
            "SessionStart": handle_session_start,
            "PreToolUse": handle_pre_tool_use,
            "PostToolUse": handle_post_tool_use,
            "SessionEnd": handle_session_end,
            "Stop": handle_stop,
        }
        handler = handlers.get(hook_event)
        if handler:
            handler(payload, db, config)
    finally:
        db.close()

    sys.exit(0)


if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        raise
    except Exception:
        sys.exit(0)
