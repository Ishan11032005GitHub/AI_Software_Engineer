from __future__ import annotations

import requests
from config import GITHUB_TOKEN, GITHUB_API

HEADERS = {
    "Authorization": f"Bearer {GITHUB_TOKEN}",
    "Accept": "application/vnd.github+json",
    "X-GitHub-Api-Version": "2022-11-28",
    "User-Agent": "AutoTriage-PR-Agent",
}


def fetch_bug_issues(owner: str, repo: str, *, state="open", per_page=50):
    if not owner or not repo:
        raise RuntimeError("Invalid repo owner/name")
    if not GITHUB_TOKEN:
        raise RuntimeError("Missing GITHUB_TOKEN")

    url = f"{GITHUB_API}/repos/{owner}/{repo}/issues"
    params = {"state": state, "labels": "bug", "per_page": min(max(int(per_page), 1), 100), "page": 1}

    bugs = []
    while True:
        res = requests.get(url, headers=HEADERS, params=params, timeout=20)
        if res.status_code != 200:
            raise RuntimeError(f"GitHub API error {res.status_code}: {res.text}")

        data = res.json()
        if not isinstance(data, list):
            raise RuntimeError(f"Unexpected GitHub API response: {data}")

        for item in data:
            if "pull_request" in item:
                continue
            bugs.append(item)

        if len(data) < params["per_page"]:
            break
        params["page"] += 1
        if params["page"] > 10:
            break

    return bugs

def fetch_single_issue(owner: str, repo: str, number: int, token: str | None = None) -> dict:
    """
    Fetch a single GitHub issue by number.

    This is used by ChatOps (app.chatops.command) to look up the
    issue/PR context from an inline command like `/fix #12`.
    """
    headers = {
        "Accept": "application/vnd.github+json",
    }

    auth_token = token or GITHUB_TOKEN
    if auth_token:
        headers["Authorization"] = f"token {auth_token}"

    url = f"{GITHUB_API}/repos/{owner}/{repo}/issues/{number}"
    resp = requests.get(url, headers=headers)
    resp.raise_for_status()
    return resp.json()
