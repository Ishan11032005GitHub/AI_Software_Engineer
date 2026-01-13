import subprocess
from app.github.pr_creator import merge_pr, create_pr
from app.agents.patch_generator import generate_fixed_content
from config import GITHUB_TOKEN, GITHUB_API
import requests, os


def comment_reply(owner, repo, issue_number, body):
    url = f"{GITHUB_API}/repos/{owner}/{repo}/issues/{issue_number}/comments"
    headers = {
        "Authorization": f"token {GITHUB_TOKEN}",
        "Accept": "application/vnd.github+json",
    }
    requests.post(url, json={"body": body}, headers=headers)


def handle_command(command, owner, repo, pr, repo_path, file_path, content_old):
    pr_number = pr["number"]

    if command == "/status":
        comment_reply(owner, repo, pr_number, "🟢 Bot online — PR is active and monitored.")
        return

    if command == "/merge":
        result = merge_pr(owner, repo, pr_number)
        if result.get("merged"):
            comment_reply(owner, repo, pr_number, "✅ Auto-merged successfully.")
        else:
            comment_reply(owner, repo, pr_number, "❌ Merge attempt failed. Manual review required.")
        return

    if command == "/close":
        requests.patch(
            f"{GITHUB_API}/repos/{owner}/{repo}/pulls/{pr_number}",
            headers={"Authorization": f"token {GITHUB_TOKEN}"},
            json={"state": "closed"},
        )
        comment_reply(owner, repo, pr_number, "🔒 Closed by ChatOps command.")
        return

    if command == "/retest":
        comment_reply(owner, repo, pr_number, "🔄 Trigger CI — re-run initiated.")
        subprocess.run(["gh", "workflow", "run", "ci.yml"])
        return

    if command == "/fix":
        new_content, *_ = generate_fixed_content(
            issue={"title":"ChatOps refix","body":""},
            file_content=content_old,
            file_path=file_path,
            store=None
        )
        if not new_content:
            comment_reply(owner, repo, pr_number, "⚠ No fix generated.")
            return
        
        with open(os.path.join(repo_path, file_path),"w") as f:
            f.write(new_content)

        subprocess.run(["git","-C",repo_path,"add",file_path])
        subprocess.run(["git","-C",repo_path,"commit","-m","ChatOps fix"])
        subprocess.run(["git","-C",repo_path,"push"])
        comment_reply(owner, repo, pr_number, "🔧 Fix applied & pushed.")
