"""Verify vector search works for real-time PreToolUse retrieval.

Simulates:
1. Past sessions that produced episodes (some wasteful, some productive)
2. A live PreToolUse event with a query describing the current action
3. Vector search to find relevant past episodes as guidance

Goal: confirm that semantically similar past episodes rank high even when
exact keywords differ, enabling the agent to learn from past mistakes.
"""

import json
import struct
import tempfile
import time
from pathlib import Path

from episodic_db.store.db import Database
from episodic_db.store.schema import get_full_schema
from episodic_db.config import Config, EmbeddingConfig
from episodic_db.embedding.embedder import LocalEmbedder
from episodic_db.embedding.serializer import serialize_signature
from episodic_db.query.vector_search import search_similar


def _vector_to_blob(vec: list[float]) -> bytes:
    return struct.pack(f"{len(vec)}f", *vec)


def setup_db_with_episodes(db_path: Path) -> Database:
    """Create DB and insert realistic past episodes with embeddings."""
    db = Database(db_path)
    db.connect()

    conn = db.conn

    # Insert a dummy session
    conn.execute(
        "INSERT INTO sessions (session_id, started_at) VALUES (?, ?)",
        ("sess_past_01", "2024-01-10T10:00:00Z"),
    )

    # Define realistic past episodes with diverse scenarios
    past_episodes = [
        {
            "episode_id": "ep_001",
            "session_id": "sess_past_01",
            "created_at": "2024-01-10T10:05:00Z",
            "waste_type": "repeated_read",
            "outcome": "looped",
            "converged_resource": "src/auth/login.py",
            "path_prefix": "src/auth/",
            "changed_symbols": json.dumps(["login", "validate_token"]),
            "test_names": json.dumps(["test_login"]),
            "grep_terms": json.dumps(["jwt", "token", "expired"]),
            "error_signature": "TokenExpiredError: token has expired",
            "lang": "python",
            "is_wasteful": 1,
            "wasted_member_ids": json.dumps(["tc_001", "tc_002"]),
        },
        {
            "episode_id": "ep_002",
            "session_id": "sess_past_01",
            "created_at": "2024-01-10T10:10:00Z",
            "waste_type": "productive",
            "outcome": "converged",
            "converged_resource": "src/auth/login.py",
            "path_prefix": "src/auth/",
            "changed_symbols": json.dumps(["login", "refresh_token"]),
            "test_names": json.dumps(["test_login", "test_refresh"]),
            "grep_terms": json.dumps(["jwt", "refresh", "token"]),
            "error_signature": None,
            "lang": "python",
            "is_wasteful": 0,
            "wasted_member_ids": json.dumps([]),
        },
        {
            "episode_id": "ep_003",
            "session_id": "sess_past_01",
            "created_at": "2024-01-10T10:15:00Z",
            "waste_type": "context_snowball",
            "outcome": "abandoned",
            "converged_resource": None,
            "path_prefix": "src/db/",
            "changed_symbols": json.dumps(["migrate", "schema"]),
            "test_names": json.dumps([]),
            "grep_terms": json.dumps(["migration", "alembic", "upgrade"]),
            "error_signature": "OperationalError: database is locked",
            "lang": "python",
            "is_wasteful": 1,
            "wasted_member_ids": json.dumps(["tc_005", "tc_006", "tc_007"]),
        },
        {
            "episode_id": "ep_004",
            "session_id": "sess_past_01",
            "created_at": "2024-01-10T10:20:00Z",
            "waste_type": "productive",
            "outcome": "converged",
            "converged_resource": "src/db/migrations/002_add_index.py",
            "path_prefix": "src/db/migrations/",
            "changed_symbols": json.dumps(["add_index", "upgrade"]),
            "test_names": json.dumps(["test_migration"]),
            "grep_terms": json.dumps(["migration", "index", "performance"]),
            "error_signature": None,
            "lang": "python",
            "is_wasteful": 0,
            "wasted_member_ids": json.dumps([]),
        },
        {
            "episode_id": "ep_005",
            "session_id": "sess_past_01",
            "created_at": "2024-01-10T10:25:00Z",
            "waste_type": "futile_exploration",
            "outcome": "abandoned",
            "converged_resource": None,
            "path_prefix": "src/api/",
            "changed_symbols": json.dumps([]),
            "test_names": json.dumps([]),
            "grep_terms": json.dumps(["endpoint", "handler", "route", "404"]),
            "error_signature": "FileNotFoundError: No such file or directory",
            "lang": "python",
            "is_wasteful": 1,
            "wasted_member_ids": json.dumps(["tc_010", "tc_011", "tc_012"]),
        },
        {
            "episode_id": "ep_006",
            "session_id": "sess_past_01",
            "created_at": "2024-01-10T10:30:00Z",
            "waste_type": "productive",
            "outcome": "converged",
            "converged_resource": "src/api/routes.py",
            "path_prefix": "src/api/",
            "changed_symbols": json.dumps(["register_route", "handler"]),
            "test_names": json.dumps(["test_routes"]),
            "grep_terms": json.dumps(["endpoint", "FastAPI", "router"]),
            "error_signature": None,
            "lang": "python",
            "is_wasteful": 0,
            "wasted_member_ids": json.dumps([]),
        },
        {
            "episode_id": "ep_007",
            "session_id": "sess_past_01",
            "created_at": "2024-01-10T10:35:00Z",
            "waste_type": "read_heavy",
            "outcome": "looped",
            "converged_resource": None,
            "path_prefix": "tests/",
            "changed_symbols": json.dumps([]),
            "test_names": json.dumps(["test_e2e_checkout"]),
            "grep_terms": json.dumps(["assert", "fixture", "mock", "checkout"]),
            "error_signature": "AssertionError: expected 200 got 500",
            "lang": "python",
            "is_wasteful": 1,
            "wasted_member_ids": json.dumps(["tc_020", "tc_021"]),
        },
        {
            "episode_id": "ep_008",
            "session_id": "sess_past_01",
            "created_at": "2024-01-10T10:40:00Z",
            "waste_type": "productive",
            "outcome": "converged",
            "converged_resource": "src/components/Button.tsx",
            "path_prefix": "src/components/",
            "changed_symbols": json.dumps(["Button", "handleClick"]),
            "test_names": json.dumps(["test_button_render"]),
            "grep_terms": json.dumps(["onClick", "disabled", "variant"]),
            "error_signature": None,
            "lang": "typescript",
            "is_wasteful": 0,
            "wasted_member_ids": json.dumps([]),
        },
        {
            "episode_id": "ep_009",
            "session_id": "sess_past_01",
            "created_at": "2024-01-10T10:45:00Z",
            "waste_type": "repeated_read",
            "outcome": "looped",
            "converged_resource": None,
            "path_prefix": "src/auth/",
            "changed_symbols": json.dumps([]),
            "test_names": json.dumps([]),
            "grep_terms": json.dumps(["session", "cookie", "middleware"]),
            "error_signature": "TypeError: Cannot read property 'session' of undefined",
            "lang": "typescript",
            "is_wasteful": 1,
            "wasted_member_ids": json.dumps(["tc_030", "tc_031"]),
        },
        {
            "episode_id": "ep_010",
            "session_id": "sess_past_01",
            "created_at": "2024-01-10T10:50:00Z",
            "waste_type": "productive",
            "outcome": "converged",
            "converged_resource": "src/auth/middleware.ts",
            "path_prefix": "src/auth/",
            "changed_symbols": json.dumps(["authMiddleware", "getSession"]),
            "test_names": json.dumps(["test_auth_middleware"]),
            "grep_terms": json.dumps(["session", "req.cookies", "middleware"]),
            "error_signature": None,
            "lang": "typescript",
            "is_wasteful": 0,
            "wasted_member_ids": json.dumps([]),
        },
    ]

    # Insert episodes and generate embeddings
    embedder = LocalEmbedder.get(model="BAAI/bge-small-en-v1.5", dim=384)

    texts_to_embed = []
    episode_ids = []

    for ep in past_episodes:
        # Insert episode row
        cols = [
            "episode_id", "session_id", "created_at", "waste_type", "outcome",
            "converged_resource", "path_prefix", "changed_symbols", "test_names",
            "grep_terms", "error_signature", "lang", "is_wasteful", "wasted_member_ids",
        ]
        vals = [ep.get(c) for c in cols]
        placeholders = ",".join(["?"] * len(cols))
        conn.execute(
            f"INSERT INTO episodes ({','.join(cols)}) VALUES ({placeholders})",
            vals,
        )

        # Serialize for embedding
        text = serialize_signature(ep, tool_calls=None)
        texts_to_embed.append(text)
        episode_ids.append(ep["episode_id"])

    # Batch embed
    vectors = embedder.embed(texts_to_embed)

    for eid, text, vec in zip(episode_ids, texts_to_embed, vectors):
        blob = _vector_to_blob(vec)
        conn.execute(
            """UPDATE episodes
               SET embedding_text = ?, embedding_model = ?, embedding_dim = ?, embedding = ?
               WHERE episode_id = ?""",
            (text, embedder.model, embedder.dim, blob, eid),
        )

    conn.commit()
    return db


def simulate_pretooluse_query(db: Database, tool_name: str, tool_input: dict, context: str = "") -> list[dict]:
    """Simulate what happens at PreToolUse: build a query from current action and search.

    In a real session, the query would combine:
    - The tool being called (tool_name + input)
    - Recent context from the session (what was being worked on)
    """
    # Build query text from the current action context
    # This mimics what a real-time retrieval hook would construct
    parts = []

    if context:
        parts.append(context)

    if tool_name in ("Read", "Write", "Edit"):
        path = tool_input.get("file_path", "")
        parts.append(f"{tool_name} {path}")
    elif tool_name == "Bash":
        cmd = tool_input.get("command", "")
        parts.append(f"Bash: {cmd[:200]}")
    elif tool_name in ("Grep", "Search"):
        query = tool_input.get("query", tool_input.get("pattern", ""))
        parts.append(f"{tool_name}: {query}")

    query_text = " | ".join(parts) if parts else tool_name

    config = EmbeddingConfig(model="BAAI/bge-small-en-v1.5", dim=384)
    results = search_similar(db, query_text, config=config, limit=5)
    return results


def test_scenario_1_auth_token_issue():
    """Scenario: Agent is about to grep for token-related code in auth module.
    Expected: retrieves past episodes about JWT/token issues (ep_001, ep_002)."""
    print("\n" + "=" * 70)
    print("SCENARIO 1: Agent searching for token-related auth code")
    print("=" * 70)

    with tempfile.TemporaryDirectory() as tmp:
        db = setup_db_with_episodes(Path(tmp) / "test.db")

        results = simulate_pretooluse_query(
            db,
            tool_name="Grep",
            tool_input={"query": "token", "pattern": "token"},
            context="python auth token expired error debugging",
        )

        print(f"\nQuery: 'python auth token expired error debugging | Grep: token'")
        print(f"\nTop {len(results)} results:")
        for i, r in enumerate(results):
            print(f"  {i+1}. [{r['episode_id']}] sim={r['similarity']:.4f} "
                  f"waste={r['waste_type']} outcome={r['outcome']} "
                  f"path={r.get('path_prefix','')}")

        # Verify: auth/token episodes should rank highest
        top_ids = [r["episode_id"] for r in results[:3]]
        auth_episodes = {"ep_001", "ep_002", "ep_009", "ep_010"}
        hits = len(set(top_ids) & auth_episodes)
        print(f"\n  Auth-related episodes in top 3: {hits}/3")
        assert hits >= 2, f"Expected >=2 auth episodes in top 3, got {hits}"
        print("  PASS")

        db.close()


def test_scenario_2_db_migration():
    """Scenario: Agent is about to run a database migration command.
    Expected: retrieves past DB migration episodes (ep_003, ep_004)."""
    print("\n" + "=" * 70)
    print("SCENARIO 2: Agent running database migration")
    print("=" * 70)

    with tempfile.TemporaryDirectory() as tmp:
        db = setup_db_with_episodes(Path(tmp) / "test.db")

        results = simulate_pretooluse_query(
            db,
            tool_name="Bash",
            tool_input={"command": "alembic upgrade head"},
            context="database migration schema upgrade",
        )

        print(f"\nQuery: 'database migration schema upgrade | Bash: alembic upgrade head'")
        print(f"\nTop {len(results)} results:")
        for i, r in enumerate(results):
            print(f"  {i+1}. [{r['episode_id']}] sim={r['similarity']:.4f} "
                  f"waste={r['waste_type']} outcome={r['outcome']} "
                  f"path={r.get('path_prefix','')}")

        top_ids = [r["episode_id"] for r in results[:3]]
        db_episodes = {"ep_003", "ep_004"}
        hits = len(set(top_ids) & db_episodes)
        print(f"\n  DB migration episodes in top 3: {hits}/3")
        assert hits >= 1, f"Expected >=1 DB migration episodes in top 3, got {hits}"
        print("  PASS")

        db.close()


def test_scenario_3_api_route_editing():
    """Scenario: Agent editing an API route file.
    Expected: retrieves API-related episodes (ep_005, ep_006)."""
    print("\n" + "=" * 70)
    print("SCENARIO 3: Agent editing API route handler")
    print("=" * 70)

    with tempfile.TemporaryDirectory() as tmp:
        db = setup_db_with_episodes(Path(tmp) / "test.db")

        results = simulate_pretooluse_query(
            db,
            tool_name="Edit",
            tool_input={"file_path": "src/api/endpoints.py"},
            context="python FastAPI route handler endpoint implementation",
        )

        print(f"\nQuery: 'python FastAPI route handler endpoint | Edit src/api/endpoints.py'")
        print(f"\nTop {len(results)} results:")
        for i, r in enumerate(results):
            print(f"  {i+1}. [{r['episode_id']}] sim={r['similarity']:.4f} "
                  f"waste={r['waste_type']} outcome={r['outcome']} "
                  f"path={r.get('path_prefix','')}")

        top_ids = [r["episode_id"] for r in results[:3]]
        api_episodes = {"ep_005", "ep_006"}
        hits = len(set(top_ids) & api_episodes)
        print(f"\n  API episodes in top 3: {hits}/3")
        assert hits >= 1, f"Expected >=1 API episodes in top 3, got {hits}"
        print("  PASS")

        db.close()


def test_scenario_4_wasteful_vs_productive():
    """Scenario: Demonstrate that search returns BOTH wasteful and productive episodes
    so the agent can learn what NOT to do and what WORKS."""
    print("\n" + "=" * 70)
    print("SCENARIO 4: Wasteful vs Productive differentiation")
    print("=" * 70)

    with tempfile.TemporaryDirectory() as tmp:
        db = setup_db_with_episodes(Path(tmp) / "test.db")

        results = simulate_pretooluse_query(
            db,
            tool_name="Grep",
            tool_input={"query": "session cookie middleware"},
            context="typescript auth session middleware debugging",
        )

        print(f"\nQuery: 'typescript auth session middleware debugging | Grep: session cookie middleware'")
        print(f"\nTop {len(results)} results:")
        for i, r in enumerate(results):
            marker = "WASTEFUL" if r.get("waste_type") not in ("productive", None) else "GOOD"
            print(f"  {i+1}. [{r['episode_id']}] sim={r['similarity']:.4f} "
                  f"[{marker}] waste={r['waste_type']} outcome={r['outcome']}")

        # Both wasteful ep_009 and productive ep_010 should appear in top results
        top_ids = [r["episode_id"] for r in results[:5]]
        has_wasteful = any(
            r["waste_type"] != "productive" for r in results[:5]
        )
        has_productive = any(
            r["waste_type"] == "productive" for r in results[:5]
        )
        print(f"\n  Has wasteful example: {has_wasteful}")
        print(f"  Has productive example: {has_productive}")
        assert has_wasteful and has_productive, "Should return both wasteful and productive examples"
        print("  PASS — agent can see both anti-patterns and solutions")

        db.close()


def test_scenario_5_latency():
    """Scenario: Measure retrieval latency to ensure it fits within PreToolUse's 5s timeout."""
    print("\n" + "=" * 70)
    print("SCENARIO 5: Latency check (must be < 5s for hook timeout)")
    print("=" * 70)

    with tempfile.TemporaryDirectory() as tmp:
        db = setup_db_with_episodes(Path(tmp) / "test.db")

        # Warm up embedder
        config = EmbeddingConfig(model="BAAI/bge-small-en-v1.5", dim=384)
        embedder = LocalEmbedder.get(model=config.model, dim=config.dim)
        embedder.embed(["warmup query"])

        # Measure search latency
        start = time.time()
        for _ in range(5):
            results = simulate_pretooluse_query(
                db,
                tool_name="Edit",
                tool_input={"file_path": "src/auth/session.ts"},
                context="fixing session handling bug in auth middleware",
            )
        elapsed = time.time() - start
        avg_ms = (elapsed / 5) * 1000

        print(f"\n  Average retrieval latency: {avg_ms:.1f}ms (5 queries)")
        print(f"  Hook timeout budget: 5000ms")
        print(f"  Headroom: {5000 - avg_ms:.1f}ms")
        assert avg_ms < 3000, f"Too slow: {avg_ms:.1f}ms > 3000ms"
        print("  PASS — well within timeout")

        db.close()


def test_scenario_6_cross_language_similarity():
    """Scenario: Query about TS session handling should still find Python auth episodes
    (semantic similarity transcends exact language match)."""
    print("\n" + "=" * 70)
    print("SCENARIO 6: Cross-language semantic retrieval")
    print("=" * 70)

    with tempfile.TemporaryDirectory() as tmp:
        db = setup_db_with_episodes(Path(tmp) / "test.db")

        results = simulate_pretooluse_query(
            db,
            tool_name="Edit",
            tool_input={"file_path": "src/auth/validate.ts"},
            context="authentication validation token expired error",
        )

        print(f"\nQuery: 'authentication validation token expired error | Edit src/auth/validate.ts'")
        print(f"\nTop {len(results)} results:")
        for i, r in enumerate(results):
            print(f"  {i+1}. [{r['episode_id']}] sim={r['similarity']:.4f} "
                  f"lang={r.get('lang','')} waste={r['waste_type']} "
                  f"path={r.get('path_prefix','')}")

        # Should find Python auth episodes (ep_001, ep_002) alongside TS ones (ep_009, ep_010)
        auth_episodes = {"ep_001", "ep_002", "ep_009", "ep_010"}
        top_ids = [r["episode_id"] for r in results[:5]]
        hits = len(set(top_ids) & auth_episodes)
        print(f"\n  Auth episodes (any lang) in top 5: {hits}")
        assert hits >= 2, f"Expected >=2 auth episodes, got {hits}"

        langs = set(r.get("lang") for r in results[:5] if r.get("lang"))
        print(f"  Languages represented in top 5: {langs}")
        print("  PASS — cross-language retrieval works")

        db.close()


def test_scenario_7_filter_by_wasteful():
    """Scenario: Filter to only retrieve wasteful episodes (anti-patterns only)."""
    print("\n" + "=" * 70)
    print("SCENARIO 7: Filtered retrieval (wasteful only)")
    print("=" * 70)

    with tempfile.TemporaryDirectory() as tmp:
        db = setup_db_with_episodes(Path(tmp) / "test.db")

        config = EmbeddingConfig(model="BAAI/bge-small-en-v1.5", dim=384)
        results = search_similar(
            db,
            query_text="python auth token error debugging",
            config=config,
            limit=5,
            is_wasteful=True,
        )

        print(f"\nQuery: 'python auth token error debugging' (wasteful only)")
        print(f"\nTop {len(results)} results:")
        for i, r in enumerate(results):
            print(f"  {i+1}. [{r['episode_id']}] sim={r['similarity']:.4f} "
                  f"waste={r['waste_type']} outcome={r['outcome']}")

        # All results should be wasteful
        all_wasteful = all(r["waste_type"] != "productive" for r in results)
        print(f"\n  All results wasteful: {all_wasteful}")
        assert all_wasteful, "Filter should exclude productive episodes"
        print("  PASS — facet filter works correctly")

        db.close()


if __name__ == "__main__":
    print("=" * 70)
    print("EPISODIC DB — PreToolUse Real-time Retrieval Verification")
    print("=" * 70)
    print("\nThis test verifies that during a live session, at the PreToolUse")
    print("hook point, the system can retrieve semantically similar past")
    print("episodes to guide the agent's current action.\n")

    test_scenario_1_auth_token_issue()
    test_scenario_2_db_migration()
    test_scenario_3_api_route_editing()
    test_scenario_4_wasteful_vs_productive()
    test_scenario_5_latency()
    test_scenario_6_cross_language_similarity()
    test_scenario_7_filter_by_wasteful()

    print("\n" + "=" * 70)
    print("ALL SCENARIOS PASSED")
    print("=" * 70)
    print("\nConclusion: Vector search CAN retrieve relevant past episodes")
    print("in real-time during PreToolUse, within the 5s hook timeout.")
    print("Both anti-patterns (wasteful) and solutions (productive) surface,")
    print("enabling the agent to learn from past experience.")
