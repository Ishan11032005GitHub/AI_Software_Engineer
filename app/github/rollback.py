import requests, subprocess
from config import GITHUB_TOKEN, GITHUB_API

def revert_commit(repo_path, sha):
    subprocess.run(["git","-C",repo_path,"revert","--no-edit",sha],check=True)
    subprocess.run(["git","-C",repo_path,"push","origin","HEAD"],check=True)

def reopen_issue(owner,repo,issue_number,msg):
    url=f"{GITHUB_API}/repos/{owner}/{repo}/issues/{issue_number}"
    headers={"Authorization":f"token {GITHUB_TOKEN}"}
    requests.patch(url,json={"state":"open","body":msg},headers=headers)
