"""Combined SQL filter + vector similarity search."""

from episodic_db.store.db import Database
from episodic_db.config import EmbeddingConfig
from episodic_db.embedding.indexer import EpisodeIndexer


def search_similar(
    db: Database,
    query_text: str,
    config: EmbeddingConfig | None = None,
    limit: int = 5,
    path_prefix: str | None = None,
    waste_type: str | None = None,
    is_wasteful: bool | None = None,
    lang: str | None = None,
) -> list[dict]:
    """Search episodes by vector similarity with optional facet filters."""
    if config is None:
        config = EmbeddingConfig()

    indexer = EpisodeIndexer(db, config)

    filters = {}
    if path_prefix:
        filters["path_prefix"] = path_prefix
    if waste_type:
        filters["waste_type"] = waste_type
    if is_wasteful is not None:
        filters["is_wasteful"] = is_wasteful
    if lang:
        filters["lang"] = lang

    return indexer.search_similar(query_text, limit=limit, filters=filters or None)
