"""Episode embedding index — numpy-based brute-force cosine similarity.

Vectors stored as BLOB in the episodes table. Search is brute-force cosine
over all episodes matching SQL filters. Fast enough for thousands of episodes.
"""

import struct
from typing import Any

import numpy as np

from episodic_db.store.db import Database
from episodic_db.config import EmbeddingConfig
from .serializer import serialize_signature
from .embedder import Embedder, LocalEmbedder


def _vector_to_blob(vec: list[float]) -> bytes:
    return struct.pack(f"{len(vec)}f", *vec)


def _blob_to_vector(data: bytes, dim: int) -> np.ndarray:
    return np.array(struct.unpack(f"{dim}f", data), dtype=np.float32)


class EpisodeIndexer:
    def __init__(self, db: Database, config: EmbeddingConfig, embedder: Embedder | None = None):
        self.db = db
        self.config = config
        self.embedder = embedder or LocalEmbedder.get(model=config.model, dim=config.dim)

    def embed_episodes(self, session_id: str | None = None):
        """Generate embeddings for episodes that don't have them yet."""
        conn = self.db.conn

        if session_id:
            cur = conn.execute(
                "SELECT * FROM episodes WHERE session_id = ? AND embedding IS NULL",
                (session_id,),
            )
        else:
            cur = conn.execute("SELECT * FROM episodes WHERE embedding IS NULL")

        episodes = [dict(row) for row in cur.fetchall()]
        if not episodes:
            return

        # Fetch tool_calls for each episode
        texts = []
        episode_ids = []
        for ep in episodes:
            # Get tool calls for this episode
            tool_calls = []
            if ep.get("wasted_member_ids"):
                import json
                member_ids = ep["wasted_member_ids"]
                if isinstance(member_ids, str):
                    member_ids = json.loads(member_ids)
                if member_ids:
                    placeholders = ",".join(["?"] * len(member_ids))
                    tc_cur = conn.execute(
                        f"SELECT tool_use_id, normalized_input FROM tool_calls WHERE tool_use_id IN ({placeholders})",
                        member_ids,
                    )
                    tool_calls = [dict(r) for r in tc_cur.fetchall()]

            text = serialize_signature(ep, tool_calls)
            texts.append(text)
            episode_ids.append(ep["episode_id"])

        batch_size = 100
        for i in range(0, len(texts), batch_size):
            batch_texts = texts[i:i + batch_size]
            batch_ids = episode_ids[i:i + batch_size]

            vectors = self.embedder.embed(batch_texts)

            for eid, text, vec in zip(batch_ids, batch_texts, vectors):
                vec_blob = _vector_to_blob(vec)
                conn.execute(
                    """UPDATE episodes
                       SET embedding_text = ?, embedding_model = ?, embedding_dim = ?, embedding = ?
                       WHERE episode_id = ?""",
                    (text, self.embedder.model, self.embedder.dim, vec_blob, eid),
                )

            conn.commit()

    def search_similar(
        self,
        query_text: str,
        limit: int = 5,
        filters: dict | None = None,
    ) -> list[dict]:
        """Brute-force cosine similarity search with optional SQL filters."""
        vectors = self.embedder.embed([query_text])
        query_vec = np.array(vectors[0], dtype=np.float32)
        query_norm = np.linalg.norm(query_vec)
        if query_norm == 0:
            return []
        query_vec = query_vec / query_norm

        where_clauses = ["embedding IS NOT NULL"]
        params: list[Any] = []

        if filters:
            if "path_prefix" in filters:
                where_clauses.append("path_prefix = ?")
                params.append(filters["path_prefix"])
            if "waste_type" in filters:
                where_clauses.append("waste_type = ?")
                params.append(filters["waste_type"])
            if "is_wasteful" in filters:
                where_clauses.append("is_wasteful = ?")
                params.append(int(filters["is_wasteful"]))
            if "lang" in filters:
                where_clauses.append("lang = ?")
                params.append(filters["lang"])

        where_sql = " AND ".join(where_clauses)
        sql = f"""
            SELECT episode_id, waste_type, outcome, path_prefix,
                   converged_resource, total_cost, lang, embedding_dim, embedding
            FROM episodes
            WHERE {where_sql}
        """

        cur = self.db.conn.execute(sql, params)
        rows = cur.fetchall()

        if not rows:
            return []

        scored = []
        for row in rows:
            row_dict = dict(row)
            dim = row_dict["embedding_dim"] or self.config.dim
            ep_vec = _blob_to_vector(row_dict["embedding"], dim)
            ep_norm = np.linalg.norm(ep_vec)
            if ep_norm == 0:
                continue
            similarity = float(np.dot(query_vec, ep_vec / ep_norm))
            row_dict["similarity"] = similarity
            del row_dict["embedding"]
            del row_dict["embedding_dim"]
            scored.append(row_dict)

        scored.sort(key=lambda x: x["similarity"], reverse=True)
        return scored[:limit]
