"""Test hook handler with simulated Claude Code payloads."""

import json
import os
import tempfile
from pathlib import Path
from unittest.mock import patch
import io
import sys

from episodic_db.config import Config
from episodic_db.store.db import Database
from episodic_db.store.nodes import get_session_tool_calls, get_session
from episodic_db.capture.hook_handler import (
    handle_session_start,
    handle_pre_tool_use,
    handle_post_tool_use,
    handle_stop,
)


def test_session_start(db, config):
    payload = {
        "session_id": "sess-abc123",
        "cwd": "/workspace/myproject",
        "hook_event_name": "SessionStart",
        "matcher": "startup",
    }
    handle_session_start(payload, db, config)

    session = get_session(db.conn, "sess-abc123")
    assert session is not None
    assert session["session_id"] == "sess-abc123"


def test_pre_tool_use(db, config):
    # First create session
    from episodic_db.store.nodes import insert_session
    insert_session(db.conn, "sess-001")

    payload = {
        "session_id": "sess-001",
        "hook_event_name": "PreToolUse",
        "tool_use_id": "toolu_abc",
        "tool_name": "Read",
        "tool_input": {"file_path": "/workspace/app/main.py"},
    }
    handle_pre_tool_use(payload, db, config)

    calls = get_session_tool_calls(db.conn, "sess-001")
    assert len(calls) == 1
    assert calls[0]["tool_name"] == "Read"
    assert calls[0]["tool_use_id"] == "toolu_abc"
    assert "main.py" in calls[0]["normalized_input"]


def test_post_tool_use(db, config):
    from episodic_db.store.nodes import insert_session, insert_tool_call
    insert_session(db.conn, "sess-002")
    insert_tool_call(db.conn, "toolu_xyz", "sess-002", 0, "Read", input_hash="h1")

    payload = {
        "session_id": "sess-002",
        "hook_event_name": "PostToolUse",
        "tool_use_id": "toolu_xyz",
        "tool_name": "Read",
        "tool_output": "file content here\nline 2\nline 3",
        "is_error": False,
    }
    handle_post_tool_use(payload, db, config)

    from episodic_db.store.nodes import get_tool_call_results
    results = get_tool_call_results(db.conn, "toolu_xyz")
    assert len(results) == 1
    assert results[0]["is_error"] == 0
    assert results[0]["output_lines"] == 3


def test_large_output_goes_to_blob(db, config):
    from episodic_db.store.nodes import insert_session, insert_tool_call
    insert_session(db.conn, "sess-003")
    insert_tool_call(db.conn, "toolu_big", "sess-003", 0, "Bash", input_hash="h2")

    large_output = "x" * 5000  # > blob_inline_max_chars (4000)
    payload = {
        "session_id": "sess-003",
        "hook_event_name": "PostToolUse",
        "tool_use_id": "toolu_big",
        "tool_name": "Bash",
        "tool_output": large_output,
    }
    handle_post_tool_use(payload, db, config)

    from episodic_db.store.nodes import get_tool_call_results
    results = get_tool_call_results(db.conn, "toolu_big")
    assert len(results) == 1
    assert results[0]["inline_content"] is None
    assert results[0]["digest_handle"] is not None

    # Verify blob is retrievable
    from episodic_db.store.blob_store import BlobStore
    store = BlobStore(config.blob_dir)
    content = store.retrieve(results[0]["digest_handle"])
    assert content == large_output
