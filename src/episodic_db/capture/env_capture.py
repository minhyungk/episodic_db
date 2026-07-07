"""Environment capture module with secret masking."""

import math
import os
import re
import shutil
import subprocess
from pathlib import Path

SECRET_PATTERN = re.compile(
    r"(KEY|TOKEN|SECRET|PASSWORD|CRED|AUTH|PRIVATE)", re.IGNORECASE
)

_last_snapshot = None


def _is_high_entropy(value: str, threshold: float = 4.0) -> bool:
    if len(value) < 16:
        return False
    freq = {}
    for ch in value:
        freq[ch] = freq.get(ch, 0) + 1
    length = len(value)
    entropy = -sum((c / length) * math.log2(c / length) for c in freq.values())
    return entropy > threshold


def _mask_value(key: str, value: str) -> str:
    if SECRET_PATTERN.search(key):
        return "***MASKED***"
    if _is_high_entropy(value):
        return "***MASKED_ENTROPY***"
    return value


def _detect_runtimes() -> dict:
    runtimes = {}
    checks = {
        "node": ["node", "--version"],
        "python": ["python3", "--version"],
        "cargo": ["cargo", "--version"],
        "go": ["go", "version"],
        "ruby": ["ruby", "--version"],
        "java": ["java", "-version"],
    }
    for name, cmd in checks.items():
        if shutil.which(cmd[0]):
            try:
                result = subprocess.run(
                    cmd, capture_output=True, text=True, timeout=2
                )
                output = (result.stdout or result.stderr).strip().split("\n")[0]
                runtimes[name] = output
            except (subprocess.TimeoutExpired, OSError):
                pass
    return runtimes


def _detect_project_markers(cwd: str) -> list:
    markers = [
        "package.json", "pyproject.toml", "Cargo.toml", "go.mod",
        "Gemfile", "pom.xml", "build.gradle", "Makefile",
    ]
    found = []
    p = Path(cwd)
    for m in markers:
        if (p / m).exists():
            found.append(m)
    return found


def _git_info(cwd: str) -> dict:
    info = {"branch": None, "head": None, "dirty": None}
    try:
        branch = subprocess.run(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"],
            capture_output=True, text=True, timeout=2, cwd=cwd,
        )
        if branch.returncode == 0:
            info["branch"] = branch.stdout.strip()
        head = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            capture_output=True, text=True, timeout=2, cwd=cwd,
        )
        if head.returncode == 0:
            info["head"] = head.stdout.strip()
        dirty = subprocess.run(
            ["git", "status", "--porcelain"],
            capture_output=True, text=True, timeout=2, cwd=cwd,
        )
        if dirty.returncode == 0:
            info["dirty"] = len(dirty.stdout.strip()) > 0
    except (subprocess.TimeoutExpired, OSError):
        pass
    return info


def full_snapshot(cwd: str) -> dict:
    global _last_snapshot
    import platform

    git = _git_info(cwd)
    snapshot = {
        "os": f"{platform.system()} {platform.release()}",
        "shell": os.environ.get("SHELL", "unknown"),
        "runtimes": _detect_runtimes(),
        "project_markers": _detect_project_markers(cwd),
        "git_branch": git.get("branch"),
        "git_head": git.get("head"),
        "git_dirty": git.get("dirty"),
        "cwd": cwd,
        "platform": platform.system().lower(),
    }
    _last_snapshot = snapshot.copy()
    return snapshot


def delta(cwd: str) -> dict | None:
    global _last_snapshot
    if _last_snapshot is None:
        return full_snapshot(cwd)

    changes = {}
    if cwd != _last_snapshot.get("cwd"):
        changes["cwd"] = cwd
        changes["project_markers"] = _detect_project_markers(cwd)

    git = _git_info(cwd)
    if git["branch"] != _last_snapshot.get("git_branch"):
        changes["git_branch"] = git["branch"]
    if git["head"] != _last_snapshot.get("git_head"):
        changes["git_head"] = git["head"]
    if git["dirty"] != _last_snapshot.get("git_dirty"):
        changes["git_dirty"] = git["dirty"]

    if changes:
        _last_snapshot.update(changes)
        return changes
    return None
