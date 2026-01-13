# app/github/pr_creator.py
import requests
from config import GITHUB_TOKEN, GITHUB_API
from app.ci.merge_watcher import wait_for_ci_result
from app.github.rollback import revert_commit,reopen_issue

# ---------------- Basic PR Creation ---------------- #

# def create_pr(owner, repo, branch, issue, draft=False):
#     headers = {
#         "Authorization": f"token {GITHUB_TOKEN}",
#         "Accept": "application/vnd.github+json",
#     }

#     title = f"Auto fix issue #{issue['number']}: {issue.get('title','')}"
#     body = issue.get("body") or ""

#     url = f"{GITHUB_API}/repos/{owner}/{repo}/pulls"
#     payload = {
#         "title": title,
#         "head": branch,
#         "base": "main",
#         "body": body,
#         "draft": draft,
#     }

#     resp = requests.post(url, json=payload, headers=headers)
#     resp.raise_for_status()
#     return resp.json()  

def create_pr(owner, repo, branch, issue, draft=False):
    headers = {
        "Authorization": f"token {GITHUB_TOKEN}",
        "Accept": "application/vnd.github+json",
    }
    title = f"Auto fix issue #{issue['number']}: {issue.get('title','')}"
    body = issue.get("body") or ""
    data = {
        "title": title,
        "head": branch,
        "base": "main",
        "body": body,
        "draft": draft,
    }
    url = f"{GITHUB_API}/repos/{owner}/{repo}/pulls"
    resp = requests.post(url, json=data, headers=headers)
    resp.raise_for_status()
    return resp.json()  # → {number, html_url, head …}



# ---------------- PR Comments ---------------- #

def post_pr_comment(owner, repo, pr_number: int, body: str):
    """
    Normal PR message (summary / retry logs / status updates).
    """
    url = f"{GITHUB_API}/repos/{owner}/{repo}/issues/{pr_number}/comments"
    headers = {
        "Authorization": f"token {GITHUB_TOKEN}",
        "Accept": "application/vnd.github+json",
    }
    requests.post(url, json={"body": body}, headers=headers)


# ---------------- Inline Code Review (LLM+CI annotations) ---------------- #

def post_pr_review_inline(owner, repo, pr_number: int, comments: list[dict]):
    """
    comments format:
    [
        { "path": "file.py", "position": 12, "body": "Why divide by zero?" },
        ...
    ]
    """
    if not comments:
        return

    url = f"{GITHUB_API}/repos/{owner}/{repo}/pulls/{pr_number}/reviews"
    headers = {
        "Authorization": f"token {GITHUB_TOKEN}",
        "Accept": "application/vnd.github+json",
    }

    payload = {
        "event": "COMMENT",
        "comments": [
            {
                "path": c["path"],
                "position": c["position"],
                "body": c["body"],
            }
            for c in comments
        ],
    }

    requests.post(url, json=payload, headers=headers)


# ---------------- PR Review Summary (Phase-2 Review Mode) ---------------- #

def post_review_summary(owner, repo, pr_number: int, summary_md: str, approve=False):
    """
    approve=True → bot approves PR if confidence≥0.75 & safety OK*
    otherwise requests changes only.

    Enables:
        🔹 Review Mode PR patches
        🔹 CI-Retry agent feedback
        🔹 Auto approve/deny PRs in future
    """
    url = f"{GITHUB_API}/repos/{owner}/{repo}/pulls/{pr_number}/reviews"
    headers = {
        "Authorization": f"token {GITHUB_TOKEN}",
        "Accept": "application/vnd.github+json",
    }

    event = "APPROVE" if approve else "REQUEST_CHANGES"

    payload = {
        "body": summary_md,
        "event": event,  # COMMENT | APPROVE | REQUEST_CHANGES
    }

    requests.post(url, json=payload, headers=headers)


# ---------------- PR Status Reporter for CI Self-Healing ---------------- #

def post_ci_retry_status(owner, repo, pr_number, attempt, confidence, failing_tests, url=None):
    """
    Used by CI Watcher during auto-retry.
    Leaves traceable breadcrumb on PR.
    """

    msg = f"""### 🤖 CI Retry Attempt {attempt}

| Metric | Value |
|--|--|
| Confidence | **{confidence:.2f}** |
| Tests failing | `{', '.join(failing_tests[:5]) if failing_tests else 'N/A'}` |
| Workflow run | {url if url else 'no URL'} |

Agent is attempting self-healing patch commit.
"""

    post_pr_comment(owner, repo, pr_number, msg)


# ──────────────────────────────────────────────
#   🔥 Phase-4 Auto Merge for High Confidence PRs
# ──────────────────────────────────────────────

def merge_pr(owner: str, repo: str, pr_number: int, method: str = "squash") -> dict:
    """
    Auto-merge PR if conditions are met.
    method: 'merge', 'squash', or 'rebase'
    """
    if method not in ("merge", "squash", "rebase"):
        raise ValueError("method must be 'merge', 'squash', or 'rebase'")

    url = f"{GITHUB_API}/repos/{owner}/{repo}/pulls/{pr_number}/merge"
    headers = {
        "Authorization": f"token {GITHUB_TOKEN}",
        "Accept": "application/vnd.github+json"
    }
    payload = {
        "commit_title": f"Auto-merged PR #{pr_number}",
        "merge_method": method
    }

    r = requests.put(url, json=payload, headers=headers)

    # GitHub returns 200 on success, 405/409 on failure
    if r.status_code == 200:
        print(f"🔗 GitHub Merge OK → PR #{pr_number}")
        return r.json()

    # Useful debugging cases
    if r.status_code in (405, 409):
        print(
            f"⚠ GitHub Merge blocked → Status {r.status_code}\n"
            f"   Reason: {r.json().get('message')}"
        )
        return {"merged": False, "reason": r.json().get("message")}

    r.raise_for_status()
    return r.json()

AUTO_MERGE_CONF = 0.85  # confidence threshold for auto-merge

def auto_merge_with_validation(owner,repo,pr,repo_path,issue_number,merge_confidence):
    
    # Merge only if safe confidence threshold
    if merge_confidence < AUTO_MERGE_CONF:
        print("⚠ Confidence below auto-merge threshold — skipping auto-merge.")
        return

    number = pr["number"]
    sha = pr["head"]["sha"]
    merged = merge_pr(owner,repo,number)

    if not merged.get("merged"):
        print("❌ Merge failed – manual review required.")
        return

    print(f"🟢 Auto-Merged PR #{number}, validating CI…")
    status = wait_for_ci_result(owner,repo,sha)

    if status == "success":
        print("🏆 CI Passed — fix confirmed.")
    else:
        print(f"❌ CI Failed [{status}] → Rolling back commit…")
        revert_commit(repo_path,sha)

        reopen_issue(
            owner,repo,issue_number,
            f"CI failed post-merge. Commit reverted automatically.\nStatus={status}"
        )
