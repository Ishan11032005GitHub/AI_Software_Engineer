# app/ci/issue_ci_resolver.py
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional, Tuple

import requests

API = "https://api.github.com"

SHA_RE = re.compile(r"\b[a-f0-9]{7,40}\b", re.IGNORECASE)
PR_RE = re.compile(r"(?:#|/pull/)(\d+)\b")


@dataclass(frozen=True)
class IssueCIHint:
    pr_number: Optional[int]
    head_sha: Optional[str]
    reason: str


def _headers(token: str) -> dict:
    return {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "AutoTriage-PR-Agent",
    }


def _get(url: str, token: str, *, params: dict | None = None, timeout: int = 25) -> requests.Response:
    return requests.get(url, headers=_headers(token), params=params, timeout=timeout)


def extract_pr_or_sha_from_issue(issue: dict) -> IssueCIHint:
    """
    Heuristics:
      - Look for PR numbers in title/body: "#123" or "/pull/123"
      - Look for commit SHA in title/body
    """
    text = (issue.get("title") or "") + "\n" + (issue.get("body") or "")

    pr = None
    m = PR_RE.search(text)
    if m:
        try:
            pr = int(m.group(1))
        except Exception:
            pr = None

    sha = None
    # pick the longest sha-like token (often full 40)
    shas = SHA_RE.findall(text)
    if shas:
        sha = max(shas, key=len)

    if pr:
        return IssueCIHint(pr_number=pr, head_sha=None, reason=f"Found PR reference #{pr} in issue text.")
    if sha:
        return IssueCIHint(pr_number=None, head_sha=sha, reason=f"Found commit SHA {sha[:10]}… in issue text.")
    return IssueCIHint(pr_number=None, head_sha=None, reason="No PR/SHA found in issue text.")


def resolve_head_sha_from_pr(owner: str, repo: str, token: str, pr_number: int) -> Optional[str]:
    url = f"{API}/repos/{owner}/{repo}/pulls/{pr_number}"
    res = _get(url, token)
    if res.status_code != 200:
        return None
    data = res.json() or {}
    return (data.get("head") or {}).get("sha")


def resolve_issue_ci_hint(owner: str, repo: str, token: str, issue: dict) -> IssueCIHint:
    """
    Returns best available hint:
      - PR -> head_sha (best)
      - SHA -> itself
      - none
    """
    base = extract_pr_or_sha_from_issue(issue)

    if base.pr_number:
        sha = resolve_head_sha_from_pr(owner, repo, token, base.pr_number)
        if sha:
            return IssueCIHint(pr_number=base.pr_number, head_sha=sha, reason=base.reason + " Resolved head_sha from PR.")
        return IssueCIHint(pr_number=base.pr_number, head_sha=None, reason=base.reason + " Failed to resolve head_sha from PR.")

    if base.head_sha:
        return base

    return base
