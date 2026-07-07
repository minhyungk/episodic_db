"""Content-addressed blob store for large tool outputs."""

import gzip
import hashlib
from pathlib import Path


class BlobStore:
    def __init__(self, blob_dir: Path):
        self.blob_dir = blob_dir
        self.blob_dir.mkdir(parents=True, exist_ok=True)

    def _path_for_hash(self, content_hash: str) -> Path:
        prefix = content_hash[:2]
        return self.blob_dir / prefix / f"{content_hash}.gz"

    def store(self, content: str) -> str:
        content_bytes = content.encode("utf-8")
        content_hash = hashlib.sha256(content_bytes).hexdigest()
        blob_path = self._path_for_hash(content_hash)

        if not blob_path.exists():
            blob_path.parent.mkdir(parents=True, exist_ok=True)
            with gzip.open(blob_path, "wb") as f:
                f.write(content_bytes)

        return content_hash

    def retrieve(self, content_hash: str) -> str | None:
        blob_path = self._path_for_hash(content_hash)
        if not blob_path.exists():
            return None
        with gzip.open(blob_path, "rb") as f:
            return f.read().decode("utf-8")

    def exists(self, content_hash: str) -> bool:
        return self._path_for_hash(content_hash).exists()
