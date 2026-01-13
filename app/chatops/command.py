# app/chatops/commands.py
from __future__ import annotations
import re
from dataclasses import dataclass
from typing import Optional
from app.storage.artifact_store import ArtifactStore

COMMAND_PREFIXES = ("/fix", "/retry", "/analysis", "/propose", "/status")

@dataclass
class ParsedCommand:
    raw: str
    verb: str
    issue_number: Optional[int]

def parse_command(body: str) -> Optional[ParsedCommand]:
    body = body.strip()
    if not body.startswith(COMMAND_PREFIXES):
        return None

    tokens = body.split()
    verb = tokens[0].lstrip("/")
    issue_number = None

    for tok in tokens[1:]:
        m = re.match(r"#?(\d+)", tok)
        if m:
            issue_number = int(m.group(1))
            break

    return ParsedCommand(raw=body, verb=verb, issue_number=issue_number)


# =========================================================
# Main Routing
# =========================================================
def handle_chatops_command(owner:str, repo:str, store:ArtifactStore, body:str) -> str:
    cmd = parse_command(body)
    if not cmd:
        return "Ignored – no valid command"

    if cmd.verb == "status":
        return _status(owner, repo, store, cmd.issue_number)

    if cmd.issue_number is None:
        return "Issue number required: `/fix #12`"

    issue = cmd.issue_number

    if cmd.verb == "fix":
        store.enqueue_job(owner, repo, issue, mode="fix")
        return f"🔧 Fix queued for #{issue}."

    if cmd.verb == "retry":
        store.enqueue_job(owner, repo, issue, mode="retry")
        return f"♻ Retry scheduled for #{issue}."

    if cmd.verb == "analysis":
        store.enqueue_job(owner, repo, issue, mode="analysis")
        return f"🧠 Analysis queued for #{issue}."

    if cmd.verb == "propose":
        store.enqueue_job(owner, repo, issue, mode="propose")
        return f"📝 Proposal queued for #{issue}."

    return f"Unknown command `{cmd.verb}`"


# =========================================================
# STATUS
# =========================================================
def _status(owner, repo, store, issue):
    if issue is None:
        runs = store.get_recent_runs(owner, repo, limit=6)
        if not runs:
            return "No runs yet."
        return "\n".join([f"Recent:"]+[f"- #{r.issue_number} → {r.decision}({r.confidence:.2f})" for r in runs])

    runs = store.get_runs_for_issue(owner, repo, issue)
    if not runs:
        return f"No runs for issue #{issue}"

    latest = runs[0]
    msg = f"#{issue}: {latest.decision} ({latest.confidence:.2f})"

    retry = None
    try: retry = store.get_retry_status(owner, repo, issue)
    except: retry=None
    if retry:
        msg+=f" retry={retry.active}, attempts={retry.attempts}"

    return msg
