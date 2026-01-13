# app/core/repo_manager.py
from __future__ import annotations

import os
import subprocess
from config import GITHUB_TOKEN

BASE_REPO_DIR = "./repos"


def _run(cmd, cwd=None):
    subprocess.check_call(cmd, cwd=cwd)


def clone_repo_if_needed(owner: str, repo: str, local_path: str) -> str:
    """
    Clone once, reset ONLY when explicitly requested.
    This function is safe to call multiple times.
    """
    os.makedirs(os.path.dirname(local_path), exist_ok=True)

    if not os.path.exists(local_path):
        if not GITHUB_TOKEN:
            raise RuntimeError("Missing GITHUB_TOKEN")

        clone_url = f"https://{GITHUB_TOKEN}@github.com/{owner}/{repo}.git"
        _run(["git", "clone", clone_url, local_path])
    else:
        # IMPORTANT:
        # Only fetch updates. Do NOT reset here.
        _run(["git", "fetch", "origin"], cwd=local_path)

    return local_path


def reset_repo_to_main(local_path: str) -> None:
    """
    Explicit hard reset. Call ONLY once per job start.
    """
    _run(["git", "checkout", "main"], cwd=local_path)
    _run(["git", "reset", "--hard", "origin/main"], cwd=local_path)


def prepare_repo(owner: str, repo: str, *, reset: bool = False) -> str:
    """
    Job-safe repo preparation.
    - Clone if missing
    - Fetch always
    - Reset ONLY if reset=True
    """
    local_path = os.path.join(BASE_REPO_DIR, f"{owner}__{repo}")
    clone_repo_if_needed(owner, repo, local_path)

    if reset:
        reset_repo_to_main(local_path)

    return local_path
