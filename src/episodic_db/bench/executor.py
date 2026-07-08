"""Benchmark executor — runs Claude Code with episodic-db hooks + proxy attached."""

import asyncio
import json
import os
import shutil
import uuid
from pathlib import Path

import aiohttp

from .base import Benchmark, BenchmarkResult
from episodic_db.store.db import Database
from episodic_db.capture.settings_factory import create_settings


class BenchmarkExecutor:
    def __init__(
        self,
        db: Database,
        proxy_port: int,
        log_dir: Path,
        model: str | None = None,
        use_bedrock: bool = False,
    ):
        self.db = db
        self.proxy_port = proxy_port
        self.log_dir = log_dir
        self.model = model
        self.use_bedrock = use_bedrock

    async def run_benchmark(self, benchmark: Benchmark) -> BenchmarkResult:
        session_id = self._generate_session_id(benchmark.name)
        await self._set_proxy_session(session_id)

        session_dir = self.log_dir / f"session_{session_id}"
        session_dir.mkdir(parents=True, exist_ok=True)
        workspace_dir = session_dir / "workspace"
        workspace_dir.mkdir(exist_ok=True)

        benchmark.setup(workspace_base=workspace_dir)

        work_dir = benchmark.get_working_directory()
        if work_dir is not None:
            work_dir.mkdir(parents=True, exist_ok=True)
            actual_work_dir = work_dir
        else:
            actual_work_dir = workspace_dir

        # Generate ephemeral settings.json with hooks in the work directory
        settings_path = (actual_work_dir / ".claude" / "settings.json").resolve()
        create_settings(
            output_path=settings_path,
            db_path=self.db.db_path,
            proxy_port=self.proxy_port,
            proxy_mode="bedrock" if self.use_bedrock else "direct",
        )

        cmd = self._build_command(benchmark, settings_path)
        env = self._build_env(session_id)

        try:
            process = await asyncio.create_subprocess_exec(
                *cmd,
                cwd=str(actual_work_dir),
                env=env,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await process.communicate()
        except FileNotFoundError:
            return BenchmarkResult(
                session_id=session_id,
                benchmark_name=benchmark.name,
                metadata={"error": "claude command not found"},
            )

        (session_dir / "stdout.txt").write_bytes(stdout)
        if stderr:
            (session_dir / "stderr.txt").write_bytes(stderr)

        if work_dir is not None and work_dir != workspace_dir:
            try:
                work_dir.relative_to(workspace_dir)
            except ValueError:
                shutil.copytree(work_dir, workspace_dir, dirs_exist_ok=True)

        score = benchmark.score(session_dir)
        benchmark.cleanup()

        return BenchmarkResult(
            session_id=session_id,
            benchmark_name=benchmark.name,
            score=score,
            metadata={
                "exit_code": process.returncode,
                "stdout_len": len(stdout),
                "stderr_len": len(stderr),
            },
        )

    async def _set_proxy_session(self, session_id: str):
        url = f"http://127.0.0.1:{self.proxy_port}/control/set-session"
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(url, json={"session_id": session_id}) as resp:
                    await resp.json()
        except aiohttp.ClientError:
            pass

    def _build_command(self, benchmark: Benchmark, settings_path: Path) -> list[str]:
        cmd = [
            "claude",
            "--print",
            "--no-session-persistence",
            "--dangerously-skip-permissions",
            "--output-format", "text",
            "--settings", str(settings_path),
        ]
        if self.model:
            cmd.extend(["--model", self.model])
        cmd.extend(["-p", benchmark.get_prompt()])
        cmd.extend(benchmark.get_claude_args())
        return cmd

    def _build_env(self, session_id: str) -> dict:
        env = os.environ.copy()

        # Hook handler env vars
        env["EPISODIC_DB_ACTIVE"] = "1"
        env["EPISODIC_DB_PATH"] = str(self.db.db_path)
        env["EPISODIC_DB_SESSION_ID"] = session_id
        env["EPISODIC_DB_DEBUG_LOG"] = str(self.log_dir / "hook_debug.log")

        # Proxy env vars
        if self.use_bedrock:
            env["ANTHROPIC_BEDROCK_BASE_URL"] = f"http://127.0.0.1:{self.proxy_port}"
            env.setdefault("CLAUDE_CODE_USE_BEDROCK", "1")
        else:
            env["ANTHROPIC_BASE_URL"] = f"http://127.0.0.1:{self.proxy_port}"
            if "ANTHROPIC_API_KEY" not in env:
                env_file = Path(__file__).parent.parent.parent.parent / ".env"
                if env_file.exists():
                    for line in env_file.read_text().splitlines():
                        line = line.strip()
                        if line and not line.startswith("#") and "=" in line:
                            key, _, value = line.partition("=")
                            env[key.strip()] = value.strip()
                if "ANTHROPIC_API_KEY" not in env:
                    raise RuntimeError(
                        "ANTHROPIC_API_KEY must be set. Either export it or add to .env file."
                    )
        return env

    def _generate_session_id(self, benchmark_name: str) -> str:
        short_id = uuid.uuid4().hex[:8]
        safe_name = benchmark_name.replace(" ", "-").replace("/", "-")
        return f"{safe_name}_{short_id}"
