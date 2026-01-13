import time
import requests
from config import GITHUB_TOKEN, GITHUB_API

def wait_for_ci_result(owner, repo, sha, timeout=900, interval=20):
    """
    Poll GitHub checks API for commit result.
    timeout=15min default, interval=20s.
    """
    url = f"{GITHUB_API}/repos/{owner}/{repo}/commits/{sha}/status"
    headers = {"Authorization": f"token {GITHUB_TOKEN}"}

    waited = 0
    while waited < timeout:
        r = requests.get(url, headers=headers).json()
        state = r.get("state")             # success/failure/pending/error
        statuses = r.get("statuses", [])
        desc = statuses[0]["description"] if statuses else "no status"

        print(f"⏳ CI={state} | {desc} | waited={waited}s")

        if state in ("success", "failure", "error"):
            return state

        time.sleep(interval)
        waited += interval

    return "timeout"
