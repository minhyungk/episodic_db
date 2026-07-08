"""Base benchmark interface."""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional


@dataclass
class BenchmarkResult:
    session_id: str
    benchmark_name: str
    score: Optional[float] = None
    metadata: dict = field(default_factory=dict)


class Benchmark(ABC):
    @property
    @abstractmethod
    def name(self) -> str: ...

    @property
    def benchmark_type(self) -> str:
        return "open"

    @abstractmethod
    def get_prompt(self) -> str: ...

    def get_claude_args(self) -> list[str]:
        return []

    def get_working_directory(self) -> Optional[Path]:
        return None

    def setup(self, workspace_base: Optional[Path] = None) -> None:
        pass

    def score(self, session_dir: Path) -> Optional[float]:
        return None

    def cleanup(self) -> None:
        pass
