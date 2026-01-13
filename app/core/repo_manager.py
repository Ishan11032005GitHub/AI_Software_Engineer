# app/core/repo_manager.py
from __future__ import annotations

import os
import subprocess
from config import GITHUB_TOKEN

def prepare_repo(owner: str, repo: str) -> str:
    if not GITHUB_TOKEN:
        raise RuntimeError("Missing GITHUB_TOKEN")

    path = f"./repos/{owner}__{repo}"
    clone_url = f"https://{GITHUB_TOKEN}@github.com/{owner}/{repo}.git"

    if not os.path.exists(path):
        subprocess.run(["git", "clone", clone_url, path], check=True)
    else:
        subprocess.run(["git", "-C", path, "fetch"], check=True)
        subprocess.run(["git", "-C", path, "reset", "--hard", "origin/main"], check=True)

    return path

BASE_REPO_DIR = "./repos"


def _run(cmd, cwd=None):
    subprocess.check_call(cmd, cwd=cwd)


def clone_repo_if_needed(owner: str, repo: str, local_path: str) -> str:
    """
    Production-safe repo bootstrap:
    - Clone if missing
    - Fetch + reset if exists
    - Always ends on a clean main branch
    """
    os.makedirs(os.path.dirname(local_path), exist_ok=True)

    if not os.path.exists(local_path):
        _run([
            "git", "clone",
            f"https://github.com/{owner}/{repo}.git",
            local_path,
        ])
    else:
        _run(["git", "fetch", "origin"], cwd=local_path)
        _run(["git", "checkout", "main"], cwd=local_path)
        _run(["git", "reset", "--hard", "origin/main"], cwd=local_path)

    return local_path


def prepare_repo(owner: str, repo: str) -> str:
    """
    Backward-compatible helper used by agent_runner.
    """
    local_path = os.path.join(BASE_REPO_DIR, f"{owner}__{repo}")
    return clone_repo_if_needed(owner, repo, local_path)
