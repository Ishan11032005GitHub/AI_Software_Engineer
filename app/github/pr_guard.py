from __future__ import annotations

import requests
from config import GITHUB_TOKEN, GITHUB_API

HEADERS = {
    "Authorization": f"Bearer {GITHUB_TOKEN}",
    "Accept": "application/vnd.github+json",
    "X-GitHub-Api-Version": "2022-11-28",
    "User-Agent": "AutoTriage-PR-Agent",
}


def pr_exists(owner: str, repo: str, issue_number: int) -> bool:
    if not GITHUB_TOKEN:
        raise RuntimeError("Missing GITHUB_TOKEN")

    url = f"{GITHUB_API}/repos/{owner}/{repo}/pulls"
    params = {"state": "open", "per_page": 100}

    res = requests.get(url, headers=HEADERS, params=params, timeout=20)
    if res.status_code != 200:
        raise RuntimeError(f"Failed to list PRs: {res.text}")

    needle = f"Fixes #{issue_number}"
    for pr in res.json():
        body = pr.get("body") or ""
        if needle in body:
            return True

    return False
