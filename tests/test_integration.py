"""Integration test: simulate a full session lifecycle through the pipeline."""

import json
from datetime import datetime, timezone

from episodic_db.config import Config
from episodic_db.store.db import Database
from episodic_db.store.nodes import (
    insert_session, insert_tool_call, insert_result, get_session_tool_calls, get_episodes_by_session,
)
from episodic_db.store.edges import insert_touches, get_wrote_resources
from episodic_db.label.pipeline import run_labeling
from episodic_db.episode.assembler import assemble_episodes
from episodic_db.embedding.serializer import serialize_signature
from episodic_db.query.facet_search import search_episodes


def test_full_pipeline(db, config):
    """Simulate a session with wasteful exploration followed by a successful edit."""
    conn = db.conn
    session_id = "test-session-001"

    # Create session
    insert_session(conn, session_id, {"cwd": "/workspace/app", "platform": "linux"})

    # Simulate tool calls: 3 grep calls (exploration) + 1 read + 1 edit (convergence)
    calls = [
        ("tid_01", "Grep", "6a1b", "Grep: login"),
        ("tid_02", "Grep", "7b2c", "Grep: authenticate"),
        ("tid_03", "Read", "8c3d", "Read /workspace/app/config.py"),
        ("tid_04", "Read", "8c3d", "Read /workspace/app/config.py"),  # duplicate
        ("tid_05", "Edit", "9d4e", "Edit /workspace/app/auth/session.py"),
    ]

    for i, (tid, tool, ihash, norm) in enumerate(calls):
        insert_tool_call(conn, tid, session_id, i, tool, input_hash=ihash, normalized_input=norm)

    # Add resources and touches
    from episodic_db.store.nodes import insert_resource
    insert_resource(conn, "path:/workspace/app/config.py", "path")
    insert_resource(conn, "path:/workspace/app/auth/session.py", "path")

    insert_touches(conn, "tid_03", "path:/workspace/app/config.py", "READ")
    insert_touches(conn, "tid_04", "path:/workspace/app/config.py", "READ")
    insert_touches(conn, "tid_05", "path:/workspace/app/auth/session.py", "WROTE")

    # Add results (tid_03 and tid_04 have same result hash = duplicate)
    insert_result(conn, "tid_01", result_hash="res_a", inline_content="no matches")
    insert_result(conn, "tid_02", result_hash="res_b", inline_content="found: auth.py:12")
    insert_result(conn, "tid_03", result_hash="res_c", inline_content="config content here " * 10)
    insert_result(conn, "tid_04", result_hash="res_c", inline_content="config content here " * 10)  # same hash
    insert_result(conn, "tid_05", result_hash="res_d", inline_content="edit applied")

    # Run labeling pipeline
    run_labeling(db, session_id)

    # Verify contributions
    tool_calls = get_session_tool_calls(conn, session_id)
    tc_map = {tc["tool_use_id"]: tc for tc in tool_calls}

    # tid_05 wrote, so CONTRIBUTED
    assert tc_map["tid_05"]["contributed_to"] == "CONTRIBUTED"
    # tid_03, tid_04 read config.py which is read by the session (but not directly to wrote resource)
    # tid_01, tid_02 are grep calls with no path relation to final edit

    # Run episode assembly
    assemble_episodes(db, session_id, config)

    # Verify episodes were created
    episodes = get_episodes_by_session(conn, session_id)
    assert len(episodes) >= 1

    ep = episodes[0]
    assert ep["waste_type"] is not None
    assert ep["outcome"] in ("converged", "abandoned", "looped")
    assert ep["session_id"] == session_id

    # Test serializer
    text = serialize_signature(ep)
    assert len(text) > 0
    assert ep["waste_type"] in text

    # Test facet search
    results = search_episodes(db, waste_type=ep["waste_type"])
    assert len(results) >= 1
    assert results[0]["episode_id"] == ep["episode_id"]


def test_empty_session(db, config):
    """Session with no tool calls should not create episodes."""
    conn = db.conn
    session_id = "test-empty"
    insert_session(conn, session_id)

    run_labeling(db, session_id)
    assemble_episodes(db, session_id, config)

    episodes = get_episodes_by_session(conn, session_id)
    assert len(episodes) == 0


def test_all_contributed_no_episode(db, config):
    """Session where all calls contributed should not create wasteful episodes."""
    conn = db.conn
    session_id = "test-all-good"
    insert_session(conn, session_id)

    insert_tool_call(conn, "tid_a1", session_id, 0, "Read", input_hash="h1", normalized_input="Read auth.py")
    insert_tool_call(conn, "tid_a2", session_id, 1, "Edit", input_hash="h2", normalized_input="Edit auth.py")

    from episodic_db.store.nodes import insert_resource
    insert_resource(conn, "path:auth.py", "path")
    insert_touches(conn, "tid_a1", "path:auth.py", "READ")
    insert_touches(conn, "tid_a2", "path:auth.py", "WROTE")

    insert_result(conn, "tid_a1", result_hash="r1", inline_content="file content")
    insert_result(conn, "tid_a2", result_hash="r2", inline_content="edit done")

    run_labeling(db, session_id)
    assemble_episodes(db, session_id, config)

    episodes = get_episodes_by_session(conn, session_id)
    # All calls contributed, so no wasteful episode should be created
    assert len(episodes) == 0


def test_blob_store(config):
    """Large outputs go to blob store."""
    from episodic_db.store.blob_store import BlobStore
    store = BlobStore(config.blob_dir)

    content = "x" * 10000
    h = store.store(content)
    assert store.exists(h)
    assert store.retrieve(h) == content
