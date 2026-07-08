"""Pluggable embedding API — local sentence-transformers by default."""

import os
from typing import Protocol


class Embedder(Protocol):
    model: str
    dim: int

    def embed(self, texts: list[str]) -> list[list[float]]: ...


class LocalEmbedder:
    """Local embedding using sentence-transformers. No API key needed."""

    _instances: dict[str, "LocalEmbedder"] = {}

    def __init__(self, model: str = "all-MiniLM-L6-v2", dim: int = 384):
        self.model = model
        self.dim = dim
        self._st_model = None

    @classmethod
    def get(cls, model: str = "all-MiniLM-L6-v2", dim: int = 384) -> "LocalEmbedder":
        """Reuse loaded model across calls to avoid repeated loading."""
        if model not in cls._instances:
            cls._instances[model] = cls(model=model, dim=dim)
        return cls._instances[model]

    def _load(self):
        if self._st_model is None:
            from sentence_transformers import SentenceTransformer
            self._st_model = SentenceTransformer(self.model)

    def embed(self, texts: list[str]) -> list[list[float]]:
        self._load()
        embeddings = self._st_model.encode(texts, normalize_embeddings=True)
        return [vec.tolist() for vec in embeddings]


class OpenAIEmbedder:
    def __init__(self, model: str = "text-embedding-3-small", dim: int = 1536, api_key: str | None = None):
        self.model = model
        self.dim = dim
        self._api_key = api_key or os.environ.get("OPENAI_API_KEY", "")

    def embed(self, texts: list[str]) -> list[list[float]]:
        if not self._api_key:
            raise RuntimeError("OPENAI_API_KEY not set")

        from openai import OpenAI
        client = OpenAI(api_key=self._api_key)

        response = client.embeddings.create(
            input=texts,
            model=self.model,
            dimensions=self.dim,
        )

        return [item.embedding for item in response.data]


class NoOpEmbedder:
    """Placeholder embedder that returns zero vectors (for testing without API)."""

    def __init__(self, dim: int = 384):
        self.model = "noop"
        self.dim = dim

    def embed(self, texts: list[str]) -> list[list[float]]:
        return [[0.0] * self.dim for _ in texts]
