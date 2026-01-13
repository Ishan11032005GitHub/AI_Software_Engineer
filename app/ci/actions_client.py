# app/ci/actions_client.py
from __future__ import annotations

import io
from dataclasses import dataclass
from typing import Dict, List, Optional

import requests

API = "https://api.github.com"


@dataclass(frozen=True)
class CIRunRef:
    run_id: int
    html_url: str
    conclusion: str
    created_at: str
    head_sha: str


@dataclass(frozen=True)
class CILogBundle:
    run: CIRunRef
    text: str
    html_url: str


def _headers(token: str) -> Dict[str, str]:
    return {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "AutoTriage-PR-Agent",
    }


def _safe_get(url: str, token: str, *, params: Optional[dict] = None, timeout: int = 25) -> requests.Response:
    return requests.get(url, headers=_headers(token), params=params, timeout=timeout)


def get_default_branch(owner: str, repo: str, token: str) -> str:
    url = f"{API}/repos/{owner}/{repo}"
    res = _safe_get(url, token)
    if res.status_code != 200:
        raise RuntimeError(f"Failed to get repo info: {res.status_code}: {res.text}")
    data = res.json()
    return data.get("default_branch") or "main"


def _runs(owner: str, repo: str, token: str, *, params: dict) -> Optional[list]:
    url = f"{API}/repos/{owner}/{repo}/actions/runs"
    res = _safe_get(url, token, params=params)
    if res.status_code != 200:
        return None
    return (res.json() or {}).get("workflow_runs") or []


def find_latest_failed_run_on_branch(owner: str, repo: str, token: str, *, branch: str, per_page: int = 30) -> Optional[CIRunRef]:
    runs = _runs(owner, repo, token, params={"branch": branch, "per_page": max(1, min(int(per_page), 100))})
    if not runs:
        return None

    for r in runs:
        if (r.get("conclusion") or "").lower() == "failure":
            return CIRunRef(
                run_id=int(r["id"]),
                html_url=r.get("html_url") or "",
                conclusion=r.get("conclusion") or "failure",
                created_at=r.get("created_at") or "",
                head_sha=r.get("head_sha") or "",
            )
    return None


def find_latest_failed_run_for_sha(owner: str, repo: str, token: str, *, head_sha: str, per_page: int = 30) -> Optional[CIRunRef]:
    """
    GitHub supports head_sha filtering for workflow runs.
    This is the best match when we can tie an issue -> PR -> head_sha.
    """
    if not head_sha:
        return None

    runs = _runs(owner, repo, token, params={"head_sha": head_sha, "per_page": max(1, min(int(per_page), 100))})
    if not runs:
        return None

    for r in runs:
        if (r.get("conclusion") or "").lower() == "failure":
            return CIRunRef(
                run_id=int(r["id"]),
                html_url=r.get("html_url") or "",
                conclusion=r.get("conclusion") or "failure",
                created_at=r.get("created_at") or "",
                head_sha=r.get("head_sha") or "",
            )
    return None


def fetch_run_logs(owner: str, repo: str, token: str, run_id: int) -> Optional[str]:
    url = f"{API}/repos/{owner}/{repo}/actions/runs/{run_id}/logs"
    res = _safe_get(url, token, timeout=45)
    if res.status_code != 200:
        return None

    # response is a zip; combine *.txt
    try:
        import zipfile

        zdata = io.BytesIO(res.content)
        with zipfile.ZipFile(zdata) as zf:
            parts: List[str] = []
            for name in zf.namelist():
                if not name.lower().endswith(".txt"):
                    continue
                try:
                    with zf.open(name) as f:
                        raw = f.read()
                    text = raw.decode("utf-8", errors="ignore")
                    parts.append(f"\n\n===== {name} =====\n{text}")
                except Exception:
                    continue
            combined = "\n".join(parts).strip()
            return combined if combined else None
    except Exception:
        return None


def get_failed_logs_best_effort(owner: str, repo: str, token: str, *, preferred_head_sha: str | None = None) -> Optional[CILogBundle]:
    """
    Priority:
      1) If preferred_head_sha provided: latest failed run for that SHA
      2) Else: latest failed run on default branch
    """
    try:
        branch = get_default_branch(owner, repo, token)
    except Exception:
        branch = "main"

    run = None
    if preferred_head_sha:
        run = find_latest_failed_run_for_sha(owner, repo, token, head_sha=preferred_head_sha)

    if not run:
        run = find_latest_failed_run_on_branch(owner, repo, token, branch=branch)

    if not run:
        return None

    text = fetch_run_logs(owner, repo, token, run.run_id) or ""
    return CILogBundle(run=run, text=text, html_url=run.html_url)
