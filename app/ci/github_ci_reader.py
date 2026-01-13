import requests
from typing import Optional

from config import GITHUB_TOKEN
from app.ci.models import CIResult
from app.ci.log_parser import parse_ci_logs

API = "https://api.github.com"

HEADERS = {
    "Authorization": f"Bearer {GITHUB_TOKEN}",
    "Accept": "application/vnd.github+json",
    "X-GitHub-Api-Version": "2022-11-28",
    "User-Agent": "AutoTriage-PR-Agent",
}


def fetch_latest_failed_ci(owner: str, repo: str) -> Optional[CIResult]:
    """
    Fetches the most recent FAILED GitHub Actions run (if any).
    """
    runs_url = f"{API}/repos/{owner}/{repo}/actions/runs"
    res = requests.get(
        runs_url,
        headers=HEADERS,
        params={"status": "failure", "per_page": 1},
        timeout=20,
    )

    if res.status_code != 200:
        return None

    data = res.json()
    runs = data.get("workflow_runs", [])
    if not runs:
        return None

    run = runs[0]
    run_id = run["id"]

    logs_url = f"{API}/repos/{owner}/{repo}/actions/runs/{run_id}/logs"
    logs_res = requests.get(logs_url, headers=HEADERS, timeout=30)

    if logs_res.status_code != 200:
        return None

    raw_logs = logs_res.text
    failures = parse_ci_logs(raw_logs)

    return CIResult(
        workflow_name=run.get("name", "unknown"),
        run_id=run_id,
        commit_sha=run.get("head_sha", ""),
        failures=failures,
        raw_logs=raw_logs,
    )
