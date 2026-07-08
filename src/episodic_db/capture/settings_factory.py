"""Generate settings.json for Claude Code plugin with hook registration."""

import json
import sys
from pathlib import Path

HOOK_EVENTS = [
    "SessionStart",
    "SessionEnd",
    "PreToolUse",
    "PostToolUse",
    "Stop",
]


def create_settings(
    output_path: Path,
    db_path: Path,
    proxy_port: int | None = None,
    proxy_mode: str = "direct",
) -> Path:
    """Create a settings.json with episodic-db hooks registered.

    The hook command is a bare python invocation — env vars (EPISODIC_DB_ACTIVE,
    EPISODIC_DB_PATH) must be set in the parent process so they propagate to
    Claude Code and then to the hook subprocess.

    Returns the path to the written settings file.
    """
    python_exe = sys.executable
    handler_cmd = f"{python_exe} -m episodic_db.capture.hook_handler"

    hooks = {}
    for event in HOOK_EVENTS:
        timeout = 30 if event == "SessionEnd" else 5
        hooks[event] = [
            {
                "hooks": [
                    {
                        "type": "command",
                        "command": handler_cmd,
                        "timeout": timeout,
                    }
                ]
            }
        ]

    settings = {"hooks": hooks}

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w") as f:
        json.dump(settings, f, indent=2)

    return output_path


def create_settings_with_proxy_env(
    output_path: Path,
    db_path: Path,
    proxy_port: int,
    proxy_mode: str = "direct",
) -> dict:
    """Create settings and return env vars to set for Claude Code process."""
    create_settings(output_path, db_path, proxy_port, proxy_mode)

    env = {}
    if proxy_mode == "bedrock":
        env["ANTHROPIC_BEDROCK_BASE_URL"] = f"http://127.0.0.1:{proxy_port}"
    else:
        env["ANTHROPIC_BASE_URL"] = f"http://127.0.0.1:{proxy_port}"

    return env
