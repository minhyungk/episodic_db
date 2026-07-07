"""Configuration for Episodic DB."""

from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class WasteThresholds:
    large_read_output_chars: int = 4000
    large_read_output_lines: int = 80
    large_read_output_tokens: int = 1000
    severe_read_output_chars: int = 20000
    severe_read_output_lines: int = 400
    severe_read_output_tokens: int = 5000
    read_heavy_min_calls: int = 10
    read_heavy_warn_ratio: float = 0.60
    read_heavy_severe_ratio: float = 0.70
    repeated_loop_window: int = 20
    repeated_loop_warn_count: int = 2
    repeated_loop_severe_count: int = 3
    context_snowball_consecutive: int = 3
    context_snowball_pct_warn: int = 50
    context_snowball_pct_severe: int = 70
    expensive_failure_warn: int = 2
    expensive_failure_severe: int = 3
    futile_exploration_min_calls: int = 25
    futile_exploration_no_new_info: int = 8
    futile_exploration_no_edit_after: int = 15


@dataclass
class EmbeddingConfig:
    model: str = "text-embedding-3-small"
    dim: int = 1536
    api_key_env: str = "OPENAI_API_KEY"


@dataclass
class Config:
    db_path: Path = field(default_factory=lambda: Path.home() / ".episodic_db" / "episodic.db")
    blob_dir: Path = field(default_factory=lambda: Path.home() / ".episodic_db" / "blobs")
    proxy_port: int = 8080
    proxy_mode: str = "direct"  # "direct" or "bedrock"
    thresholds: WasteThresholds = field(default_factory=WasteThresholds)
    embedding: EmbeddingConfig = field(default_factory=EmbeddingConfig)
    blob_inline_max_chars: int = 4000
