"""Test embedding pipeline with numpy-based vector search."""

import struct

import numpy as np

from episodic_db.store.db import Database
from episodic_db.store.nodes import insert_session, insert_episode
from episodic_db.embedding.serializer import serialize_signature
from episodic_db.embedding.embedder import NoOpEmbedder
from episodic_db.embedding.indexer import EpisodeIndexer, _vector_to_blob, _blob_to_vector
from episodic_db.config import EmbeddingConfig


def test_serialize_signature():
    episode = {
        "waste_type": "futile-exploration",
        "outcome": "converged",
        "lang": "python",
        "path_prefix": "auth/",
        "converged_resource": "auth/session.py",
        "grep_terms": '["login", "authenticate"]',
        "changed_symbols": '["check_expiry"]',
        "test_names": '["test_session"]',
        "error_signature": "AssertionError:token-expiry",
    }
    text = serialize_signature(episode)
    assert "futile-exploration" in text
    assert "python" in text
    assert "auth/" in text
    assert "conv=auth/session.py" in text
    assert "grep(login,authenticate)" in text
    assert "changed(check_expiry)" in text
    assert "test(test_session)" in text
    assert "err(AssertionError:token-expiry)" in text


def test_vector_blob_roundtrip():
    vec = [0.1, 0.2, 0.3, 0.4, 0.5]
    blob = _vector_to_blob(vec)
    result = _blob_to_vector(blob, 5)
    np.testing.assert_allclose(result, vec, rtol=1e-6)


def test_embed_and_search(db, config):
    """Test full embed → search cycle with NoOpEmbedder."""
    conn = db.conn
    session_id = "embed-test"
    insert_session(conn, session_id)

    # Insert two episodes with different signatures
    insert_episode(conn, {
        "episode_id": "ep_auth",
        "session_id": session_id,
        "created_at": "2026-01-01T00:00:00Z",
        "waste_type": "futile-exploration",
        "outcome": "converged",
        "path_prefix": "auth/",
        "lang": "python",
        "is_wasteful": 1,
    })
    insert_episode(conn, {
        "episode_id": "ep_db",
        "session_id": session_id,
        "created_at": "2026-01-01T01:00:00Z",
        "waste_type": "read-heavy",
        "outcome": "abandoned",
        "path_prefix": "db/",
        "lang": "python",
        "is_wasteful": 1,
    })

    # Use a deterministic embedder that produces different vectors per text
    class DeterministicEmbedder:
        model = "test"
        dim = 4

        def embed(self, texts: list[str]) -> list[list[float]]:
            results = []
            for t in texts:
                h = hash(t) % 1000
                vec = [h / 1000, (h + 100) / 1000, (h + 200) / 1000, (h + 300) / 1000]
                results.append(vec)
            return results

    embed_config = EmbeddingConfig(model="test", dim=4)
    embedder = DeterministicEmbedder()
    indexer = EpisodeIndexer(db, embed_config, embedder=embedder)

    # Embed
    indexer.embed_episodes(session_id=session_id)

    # Verify embeddings were stored
    cur = conn.execute("SELECT episode_id, embedding FROM episodes WHERE embedding IS NOT NULL")
    rows = cur.fetchall()
    assert len(rows) == 2

    # Search
    results = indexer.search_similar("futile-exploration | converged | python | auth/", limit=2)
    assert len(results) == 2
    assert all("similarity" in r for r in results)
    # Results should be sorted by similarity (descending)
    assert results[0]["similarity"] >= results[1]["similarity"]
