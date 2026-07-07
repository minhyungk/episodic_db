"""Pluggable embedding API — OpenAI default, configurable model/dim."""

import os
from typing import Protocol


class Embedder(Protocol):
    model: str
    dim: int

    def embed(self, texts: list[str]) -> list[list[float]]: ...


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

    def __init__(self, dim: int = 1536):
        self.model = "noop"
        self.dim = dim

    def embed(self, texts: list[str]) -> list[list[float]]:
        return [[0.0] * self.dim for _ in texts]
