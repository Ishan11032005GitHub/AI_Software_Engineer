import os
import subprocess
from typing import List


def run_cmd(cmd: List[str], cwd: str | None = None) -> str:
    """Run shell command safely and return stdout or raise."""
    p = subprocess.run(
        cmd,
        cwd=cwd,
        capture_output=True,
        text=True,
        check=False
    )
    if p.returncode != 0:
        raise RuntimeError(f"Command failed: {' '.join(cmd)}\n{p.stderr}")
    return p.stdout.strip()


def git_has_changes(repo_path: str) -> bool:
    out = run_cmd(["git", "status", "--porcelain"], cwd=repo_path)
    return bool(out.strip())
