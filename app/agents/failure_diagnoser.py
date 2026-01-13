# app/agents/failure_diagnoser.py
from __future__ import annotations

import os
import traceback
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class FailureContext:
    op: str
    args: Dict[str, Any]
    repo_path: str
    owner: str
    repo: str
    job_id: int
    action: str
    prompt: str
    exception_type: str
    exception_message: str
    trace_tail: str
    extra: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "op": self.op,
            "args": self.args,
            "repo_path": self.repo_path,
            "owner": self.owner,
            "repo": self.repo,
            "job_id": self.job_id,
            "action": self.action,
            "prompt": self.prompt,
            "exception_type": self.exception_type,
            "exception_message": self.exception_message,
            "trace_tail": self.trace_tail,
            "extra": self.extra,
        }


def _tail(text: str, n: int = 80) -> str:
    lines = (text or "").splitlines()
    return "\n".join(lines[-n:])


def build_failure_context(
    *,
    op: str,
    args: Dict[str, Any],
    repo_path: str,
    owner: str,
    repo: str,
    job_id: int,
    action: str,
    prompt: str,
    exc: BaseException,
    extra: Optional[Dict[str, Any]] = None,
) -> FailureContext:
    tb = traceback.format_exc()
    return FailureContext(
        op=op,
        args=args or {},
        repo_path=repo_path,
        owner=owner,
        repo=repo,
        job_id=job_id,
        action=action or "",
        prompt=prompt or "",
        exception_type=exc.__class__.__name__,
        exception_message=str(exc) or exc.__class__.__name__,
        trace_tail=_tail(tb, 120),
        extra=extra or {},
    )


def _env_present(keys: List[str]) -> Dict[str, bool]:
    return {k: bool(os.getenv(k)) for k in keys}


def diagnose_failure(ctx: FailureContext) -> Dict[str, Any]:
    """
    Step 5 diagnosis engine:
    - returns a dict (JSON-friendly) with retryable flag for Step 6
    """
    msg = (ctx.exception_message or "").lower()
    tb = (ctx.trace_tail or "").lower()
    text = f"{msg}\n{tb}"

    signals: Dict[str, Any] = {
        "op": ctx.op,
        "exception_type": ctx.exception_type,
        "exception_message": ctx.exception_message,
        "env": _env_present(["GITHUB_TOKEN", "OPENAI_API_KEY"]),
    }

    # TOOL missing (git/node/npm/pytest/black etc)
    if ("not found" in text) or ("no such file or directory" in text):
        return {
            "category": "TOOL_MISSING",
            "summary": "Required tool/command is missing or not in PATH.",
            "retryable": False,
            "likely_causes": [
                "Command not installed (git/node/npm/pytest/black).",
                "PATH not set for the running service.",
            ],
            "recommended_actions": [
                "Install the missing tool and ensure PATH is correct for the worker environment.",
                "Restart the worker after installing.",
            ],
            "signals": signals,
        }

    # network / transient
    if any(x in text for x in ("timed out", "dns", "name resolution", "connection error", "temporarily unavailable")):
        return {
            "category": "NETWORK",
            "summary": "Network connectivity or transient failure.",
            "retryable": True,
            "likely_causes": [
                "Transient DNS issue or rate limiting.",
                "Temporary GitHub/API outage.",
            ],
            "recommended_actions": [
                "Retry with bounded backoff (Step 6 does this).",
                "Check connectivity from the worker host.",
            ],
            "signals": signals,
        }

    # GitHub auth
    if "github" in text and any(x in text for x in ("401", "403", "bad credentials", "requires authentication")):
        return {
            "category": "GITHUB_AUTH",
            "summary": "GitHub authentication/authorization failed.",
            "retryable": False,
            "likely_causes": [
                "GITHUB_TOKEN missing/invalid/expired.",
                "Token lacks repo scope / SSO not authorized.",
            ],
            "recommended_actions": [
                "Set/refresh GITHUB_TOKEN where the worker runs.",
                "Verify token repo access and org SSO authorization.",
            ],
            "signals": signals,
        }

    # git push rejected
    if any(x in text for x in ("non-fast-forward", "protected branch", "permission denied", "rejected")):
        return {
            "category": "GIT_PUSH_REJECTED",
            "summary": "Git push rejected by permissions/protection/diverged history.",
            "retryable": False,
            "likely_causes": [
                "Branch protection rules or missing permission.",
                "Remote history diverged.",
            ],
            "recommended_actions": [
                "Ensure pushes go to a fresh branch (not main).",
                "Confirm token has write permission.",
                "Fetch/reset repo to origin/main before work starts.",
            ],
            "signals": signals,
        }

    # verification failures are usually non-retryable unless timing
    if "verify_http_endpoint" in ctx.op.lower() or "http" in text:
        retryable = any(x in text for x in ("connection refused", "econnrefused", "socket hang up"))
        return {
            "category": "VERIFICATION_HTTP",
            "summary": "HTTP verification failed (service not reachable or returned error).",
            "retryable": retryable,
            "likely_causes": [
                "Server failed to start.",
                "Wrong port or endpoint path.",
                "Startup time too short.",
            ],
            "recommended_actions": [
                "Check server logs output.",
                "Increase wait_seconds or ensure PORT matches.",
                "Run the start command manually in the backend directory.",
            ],
            "signals": signals,
        }

    # patch/apply/edit failures: generally non-retryable (logic issue)
    if any(x in text for x in ("target snippet not found", "apply_patch failed", "patch failed", "edit_file failed")):
        return {
            "category": "EDIT_ANCHOR_MISMATCH",
            "summary": "File edit failed because the anchor/snippet did not match the current file.",
            "retryable": False,
            "likely_causes": [
                "Planner assumed an anchor that isn't present.",
                "Repo content differs from heuristics.",
            ],
            "recommended_actions": [
                "Improve repo intel to extract stronger anchors (exact <head...> tag, stylesheet hrefs).",
                "Use APPLY_PATCH with correct context or update the EDIT_FILE old snippet.",
            ],
            "signals": signals,
        }

    if "append_file" in text:
        return {
            "category": "APPEND_FAILED",
            "summary": "Append operation failed (missing file or IO error).",
            "retryable": False,
            "likely_causes": [
                "Target file path incorrect or missing.",
                "Filesystem permission issue.",
            ],
            "recommended_actions": [
                "Verify file exists before APPEND_FILE.",
                "Ensure repo_path has write permissions.",
            ],
            "signals": signals,
        }

    return {
        "category": "UNKNOWN",
        "summary": "Unhandled failure mode. Needs inspection of FAILURE_CTX trace_tail.",
        "retryable": False,
        "likely_causes": [
            "Unexpected runtime error or edge-case repo layout.",
        ],
        "recommended_actions": [
            "Inspect FAILURE_CTX.trace_tail and reproduce failing command locally in repo_path.",
            "Add a new diagnosis rule for this pattern.",
        ],
        "signals": signals,
    }
