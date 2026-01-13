# app/git_ops.py

import os
import subprocess

def abs_path(repo_path, file_rel):
    file_rel = file_rel.replace("\\", "/")
    if file_rel.startswith(repo_path):
        file_rel = file_rel.replace(repo_path, "").lstrip("/")
    return os.path.join(repo_path, file_rel).replace("\\", "/")


def create_branch_and_commit(repo_path, file_rel, new_content, issue_number):
    abs_file = abs_path(repo_path, file_rel)
    branch_name = f"auto-fix-{issue_number}"

    # does branch exist locally?
    r = subprocess.run(
        ["git", "-C", repo_path, "rev-parse", "--verify", branch_name],
        capture_output=True,
        text=True,
    )
    branch_exists = (r.returncode == 0)

    if branch_exists:
        subprocess.run(["git", "-C", repo_path, "checkout", branch_name], check=True)
    else:
        subprocess.run(["git", "-C", repo_path, "checkout", "-b", branch_name], check=True)

    os.makedirs(os.path.dirname(abs_file), exist_ok=True)
    with open(abs_file, "w", encoding="utf-8") as f:
        f.write(new_content)

    rel_file = os.path.relpath(abs_file, repo_path).replace("\\", "/")
    subprocess.run(["git", "-C", repo_path, "add", rel_file], check=True)

    if branch_exists:
        subprocess.run(["git", "-C", repo_path, "commit", "--amend", "--no-edit"], check=True)
        subprocess.run(["git", "-C", repo_path, "push", "--force", "origin", branch_name], check=True)
    else:
        subprocess.run(["git", "-C", repo_path, "commit", "-m", f"Auto fix issue #{issue_number}"], check=True)
        subprocess.run(["git", "-C", repo_path, "push", "-u", "origin", branch_name], check=True)

    return branch_name


def commit_and_push_amend(repo_path, file_rel, new_content):
    abs_file = abs_path(repo_path, file_rel)

    with open(abs_file, "w", encoding="utf-8") as f:
        f.write(new_content)

    rel_file = os.path.relpath(abs_file, repo_path).replace("\\", "/")
    subprocess.run(["git", "-C", repo_path, "add", rel_file], check=True)
    subprocess.run(["git", "-C", repo_path, "commit", "--amend", "--no-edit"], check=True)
    subprocess.run(["git", "-C", repo_path, "push", "--force"], check=True)

def get_branch_diff(repo_path: str, branch_name: str) -> str:
    # diff against base branch main
    result = subprocess.run(
        ["git", "-C", repo_path, "diff", "main..."+branch_name],
        capture_output=True,
        text=True,
        check=False,
    )
    return result.stdout or ""
