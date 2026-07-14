"""SWE-bench Lite benchmark implementation."""

import shutil
import subprocess
from pathlib import Path
from typing import Optional

from ...base import Benchmark


class SWEBenchBenchmark(Benchmark):
    def __init__(self, instance_id: str, instance_data: dict):
        self._instance_id = instance_id
        self._data = instance_data
        self._work_dir: Optional[Path] = None
        self._workspace_base: Optional[Path] = None

    @property
    def name(self) -> str:
        return f"swebench-{self._instance_id}"

    @property
    def benchmark_type(self) -> str:
        return "scored"

    @property
    def repo(self) -> str:
        return self._data.get("repo", "")

    @property
    def base_commit(self) -> str:
        return self._data.get("base_commit", "")

    def get_prompt(self) -> str:
        problem = self._data.get("problem_statement", "")
        hints = self._data.get("hints_text", "")
        prompt = f"""Fix the following issue in the {self.repo} repository.

## Issue
{problem}
"""
        if hints:
            prompt += f"""
## Hints
{hints}
"""
        prompt += """
Please identify and fix the bug. Apply the fix directly to the repository files.
"""
        return prompt

    def get_working_directory(self) -> Optional[Path]:
        return self._work_dir

    def setup(self, workspace_base: Optional[Path] = None) -> None:
        if workspace_base:
            self._workspace_base = workspace_base
            swebench_dir = workspace_base / "swebench_workspaces"
            swebench_dir.mkdir(parents=True, exist_ok=True)
            self._work_dir = swebench_dir / self._instance_id.replace("/", "_")
            self._work_dir.mkdir(parents=True, exist_ok=True)
        else:
            import tempfile
            self._work_dir = Path(tempfile.mkdtemp(prefix=f"swebench_{self._instance_id}_"))

        repo_url = f"https://github.com/{self.repo}.git"
        clone_result = subprocess.run(
            ["git", "clone", "--no-checkout", "--filter=blob:none", repo_url, str(self._work_dir)],
            capture_output=True,
            timeout=600,
        )
        if clone_result.returncode != 0:
            raise RuntimeError(f"git clone failed: {clone_result.stderr.decode()}")

        checkout_result = subprocess.run(
            ["git", "checkout", self.base_commit],
            cwd=str(self._work_dir),
            capture_output=True,
            timeout=120,
        )
        if checkout_result.returncode != 0:
            raise RuntimeError(
                f"git checkout {self.base_commit} failed: {checkout_result.stderr.decode()}"
            )

    def score(self, session_dir: Path) -> Optional[float]:
        return None

    def cleanup(self) -> None:
        if self._work_dir and self._work_dir.exists():
            shutil.rmtree(self._work_dir, ignore_errors=True)
            self._work_dir = None
