# app/github/pr_merge.py
import requests
from config import GITHUB_TOKEN, GITHUB_API

headers = {
    "Authorization": f"token {GITHUB_TOKEN}",
    "Accept": "application/vnd.github+json",
}


def merge_pr(owner, repo, pr_number, message="Auto-merged by AI agent"):
    url = f"{GITHUB_API}/repos/{owner}/{repo}/pulls/{pr_number}/merge"
    payload = {
        "commit_title": message,
        "merge_method": "squash"
    }
    r = requests.put(url, json=payload, headers=headers)
    r.raise_for_status()
    return r.json()


def close_issue(owner, repo, issue_number):
    url = f"{GITHUB_API}/repos/{owner}/{repo}/issues/{issue_number}"
    payload = {"state": "closed"}
    requests.patch(url, json=payload, headers=headers)


def delete_branch(owner, repo, branch):
    url = f"{GITHUB_API}/repos/{owner}/{repo}/git/refs/heads/{branch}"
    r = requests.delete(url, headers=headers)
    return r.status_code == 204
