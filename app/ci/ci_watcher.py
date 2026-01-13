# app/ci/ci_watcher.py
from __future__ import annotations

import sys
import os
from typing import Optional, List, Tuple

import requests

from app.github.pr_creator import post_ci_retry_status
from config import (
    GITHUB_TOKEN,
    GITHUB_API,
    SQLITE_PATH,
    MAX_CHANGED_LINES,
    CI_MODE,
)

# Storage / memory
from app.storage.artifact_store import ArtifactStore

# Patch engine
from app.agents.patch_generator import generate_fixed_content
from app.agents.confidence import ConfidenceInputs, compute_confidence

# Static analysis / safety
from app.analysis.ast_verifier import verify_python_ast
from app.analysis.safety_verifier import verify_safe_change

# CI integrations
from app.ci.actions_client import get_failed_logs_best_effort
from app.ci.test_failure_parser import parse_ci_logs
from app.ci.retry_engine import classify_ci_outcome, should_retry_from_ci

# Git operations
from app.git_ops import commit_and_push_amend

# Path utilities reused from main runner
from app.main import _safe_read, _normalize_repo_rel, prepare_repo

from app.utils.sensitive import touches_sensitive_area


# ----------------------------- GitHub helpers ----------------------------- #


def _fetch_open_prs(owner: str, repo: str) -> List[dict]:
    """
    Fetch open PRs from GitHub.

    We keep this minimal on purpose – just enough for CI self-healing:
      - number
      - title/body
      - head.ref (branch)
      - head.sha
    """
    if not GITHUB_TOKEN:
        raise RuntimeError("GITHUB_TOKEN missing; cannot query PRs")

    url = f"{GITHUB_API}/repos/{owner}/{repo}/pulls"
    headers = {
        "Authorization": f"token {GITHUB_TOKEN}",
        "Accept": "application/vnd.github+json",
    }
    # only open PRs
    params = {"state": "open", "per_page": 50}
    resp = requests.get(url, headers=headers, params=params, timeout=30)
    resp.raise_for_status()
    return resp.json()  # list[dict]


# --------------------------- Core CI watcher ------------------------------ #


def _pick_failing_file(ci_evidence) -> Optional[str]:
    """
    Choose the highest-signal failing file from CI evidence.
    """
    if not ci_evidence or not getattr(ci_evidence, "failing_files_ranked", None):
        return None
    files = ci_evidence.failing_files_ranked
    return files[0] if files else None


def _compute_confidence_for_ci_retry(
    *,
    primary_old: str,
    primary_new: str,
    used_llm: bool,
    used_rule_based: bool,
    ast_verified: bool,
    safety_verified: bool,
    ci_evidence,
) -> float:
    """
    Reuse your ConfidenceInputs model for CI retries.
    """
    used_stack = bool(ci_evidence and getattr(ci_evidence, "has_failure", False))
    return compute_confidence(
        ConfidenceInputs(
            used_stack_trace=used_stack,
            stack_trace_function_resolved=False,  # CI logs usually don't give a single fn
            changed_files_count=1,
            impacted_files_count=0,
            ast_verified=ast_verified,
            safety_verified=safety_verified,
            used_llm=used_llm,
            used_rule_based=used_rule_based,
            file_lines=len(primary_old.splitlines()),
        )
    )


def run_ci_watcher(owner: str, repo: str) -> None:
    """
    Phase-3: Self-healing CI loop.

    For each open PR:
      - look up latest failed CI for its head SHA
      - classify outcome
      - consult retry policy
      - if allowed, generate a new patch and push commit to same branch
    """
    if CI_MODE <= 0:
        print("🛑 CI watcher disabled (CI_MODE=0)")
        return

    if not GITHUB_TOKEN:
        raise RuntimeError("Missing GITHUB_TOKEN in environment")

    repo_path = prepare_repo(owner, repo)

    store = ArtifactStore(SQLITE_PATH)
    store.init_db()

    prs = _fetch_open_prs(owner, repo)
    print(f"🔍 CI watcher: found {len(prs)} open PR(s)")

    for pr in prs:
        pr_number = pr.get("number")
        branch = pr.get("head", {}).get("ref")
        head_sha = pr.get("head", {}).get("sha")

        title = pr.get("title") or ""
        body = pr.get("body") or ""

        if not branch or not head_sha:
            print(f"⚠️ PR #{pr_number}: missing head ref/sha – skipping.")
            continue

        print(f"\n━━━━━━━━━ CI WATCHER – PR #{pr_number} ({branch}) ━━━━━━━━━")

        # CI_MODE 1 → only observe, don't patch
        observe_only = (CI_MODE == 1)

        # Lookup previous retry state
        previous_attempts = 0
        if hasattr(store, "get_retry_status"):
            try:
                retry_state = store.get_retry_status(owner, repo, pr_number)
                if retry_state and getattr(retry_state, "attempts", None) is not None:
                    previous_attempts = retry_state.attempts
            except Exception:
                previous_attempts = 0

        # If CI_MODE == 2 and we've already tried once, respect that hard cap
        if CI_MODE == 2 and previous_attempts >= 1:
            print(f"⏭️ PR #{pr_number}: reached CI_MODE=2 retry cap (1 attempt) – skipping.")
            continue

        # Pull CI logs for THIS PR's head SHA
        ci_bundle = get_failed_logs_best_effort(
            owner,
            repo,
            GITHUB_TOKEN,
            preferred_head_sha=head_sha,
        )

        if not ci_bundle or not getattr(ci_bundle, "text", None):
            print("🧪 No failed CI logs for this PR – nothing to fix.")
            continue

        ci_evidence = parse_ci_logs(ci_bundle.text)

        if not ci_evidence or not ci_evidence.has_failure:
            print("✅ CI logs parsed – no actionable Python failures.")
            continue

        print(
            f"🧪 CI failures detected for PR #{pr_number} | "
            f"run={ci_bundle.html_url} | "
            f"top_files={ci_evidence.failing_files_ranked[:3]}"
        )

        # Persist CI evidence if store supports it
        if hasattr(store, "store_ci_evidence"):
            try:
                store.store_ci_evidence(
                    owner=owner,
                    repo=repo,
                    issue_number=pr_number,
                    run_id=ci_bundle.run.run_id,
                    run_url=ci_bundle.html_url,
                    conclusion=ci_bundle.run.conclusion,
                    created_at=ci_bundle.run.created_at,
                    head_sha=ci_bundle.run.head_sha,
                    failing_files=ci_evidence.failing_files_ranked,
                    failing_tests=ci_evidence.failing_tests_ranked,
                    excerpt=ci_evidence.excerpt,
                )
            except Exception:
                print("⚠️ Failed to persist CI evidence (non-fatal).")

        # If CI_MODE == 1 we stop here: just observation + logging
        if observe_only:
            if hasattr(store, "store_retry_status"):
                try:
                    store.store_retry_status(
                        owner=owner,
                        repo=repo,
                        issue_number=pr_number,
                        attempts=previous_attempts,
                        last_outcome="OBSERVE_ONLY",
                        active=False,
                    )
                except Exception:
                    pass
            print("👀 CI_MODE=1: logged evidence only – no patch.")
            continue

        # Classify CI outcome & consult retry policy
        try:
            ci_outcome = classify_ci_outcome(ci_evidence)
        except Exception:
            ci_outcome = None

        retry_decision = None
        if ci_outcome is not None:
            try:
                retry_decision = should_retry_from_ci(ci_outcome, previous_attempts)
            except Exception:
                retry_decision = None

        if not retry_decision or not getattr(retry_decision, "should_retry", False):
            print("⏹ Retry policy says 'no retry' – marking inactive.")
            if hasattr(store, "store_retry_status"):
                try:
                    store.store_retry_status(
                        owner=owner,
                        repo=repo,
                        issue_number=pr_number,
                        attempts=previous_attempts,
                        last_outcome=getattr(ci_outcome, "category", None) if ci_outcome else None,
                        active=False,
                    )
                except Exception:
                    pass
            continue

        print(
            f"🔁 Retry allowed by policy (attempt={previous_attempts + 1}) "
            f"| category={getattr(ci_outcome, 'category', None)}"
        )

        # ------------------ Decide what to patch ------------------ #

        failing_rel = _pick_failing_file(ci_evidence)
        if not failing_rel:
            print("⚠️ No failing file candidates in CI evidence – skipping.")
            continue

        abs_path = os.path.join(repo_path, failing_rel)
        if not os.path.exists(abs_path):
            print(f"⚠️ CI failing file not found locally: {abs_path}")
            continue

        primary_old = _safe_read(abs_path) or ""
        if not primary_old.strip():
            print("⚠️ Target file is empty or unreadable – skipping.")
            continue

        # AST check on current state
        ast_verified = True
        if abs_path.endswith(".py"):
            ast_verified = bool(verify_python_ast(primary_old, function_name=None))
            if not ast_verified:
                print("🧱 AST invalid before retry – will still try to propose but be conservative.")

        # ------------------ Generate new fix via existing agent ------------------ #

        pseudo_issue = {
            "number": pr_number,
            "title": f"[CI Retry] {title}",
            "body": body,
        }

        primary_new, used_llm, used_rule_based = generate_fixed_content(
            issue=pseudo_issue,
            file_content=primary_old,
            file_path=abs_path,
            store=store,
        )

        if not primary_new:
            print("⚠️ CI retry: no new fix produced; marking inactive.")
            if hasattr(store, "store_retry_status"):
                try:
                    store.store_retry_status(
                        owner=owner,
                        repo=repo,
                        issue_number=pr_number,
                        attempts=previous_attempts,
                        last_outcome=getattr(ci_outcome, "category", None) if ci_outcome else None,
                        active=False,
                    )
                except Exception:
                    pass
            continue

        # ------------------ Safety gate for CI retries ------------------ #

        safety_verified = True
        safety_reason = "N/A"
        if abs_path.endswith(".py"):
            safety_verified, safety_reason = verify_safe_change(
                old_content=primary_old,
                new_content=primary_new,
                max_changed_lines=MAX_CHANGED_LINES,
            )
            if not safety_verified:
                print(f"🛑 CI retry safety verifier failed: {safety_reason}")

        confidence = _compute_confidence_for_ci_retry(
            primary_old=primary_old,
            primary_new=primary_new,
            used_llm=used_llm,
            used_rule_based=used_rule_based,
            ast_verified=ast_verified,
            safety_verified=safety_verified,
            ci_evidence=ci_evidence,
        )

        print(
            f"📈 CI retry confidence={confidence:.2f} | "
            f"safety={safety_verified} | "
            f"file={failing_rel}"
        )

        # Hard safety and sanity gates
        if not safety_verified or confidence < 0.25:
            print("⏹ CI retry blocked: low confidence or safety failure.")
            # Optionally we could store a proposal snapshot here
            if hasattr(store, "store_retry_status"):
                try:
                    store.store_retry_status(
                        owner=owner,
                        repo=repo,
                        issue_number=pr_number,
                        attempts=previous_attempts,
                        last_outcome=getattr(ci_outcome, "category", None) if ci_outcome else None,
                        active=False,
                    )
                except Exception:
                    pass
            continue

        # Also block if we are in a clearly sensitive area
        if touches_sensitive_area(abs_path, title, body):
            print("🛑 CI retry blocked: sensitive area detected.")
            if hasattr(store, "store_retry_status"):
                try:
                    store.store_retry_status(
                        owner=owner,
                        repo=repo,
                        issue_number=pr_number,
                        attempts=previous_attempts,
                        last_outcome=getattr(ci_outcome, "category", None) if ci_outcome else None,
                        active=False,
                    )
                except Exception:
                    pass
            continue

        # ------------------ Apply patch via amend commit ------------------ #

        try:
            print(f"🔥 CI retry: committing fix to existing branch {branch}")
            commit_and_push_amend(
                repo_path=repo_path,
                branch=branch,
                file_path=abs_path,
                new_content=primary_new,
            )
            post_ci_retry_status(
                owner, repo, pr_number,
                attempt=previous_attempts + 1,
                confidence=confidence,
                failing_tests=ci_evidence.failing_tests_ranked,
                url=ci_bundle.html_url
            )
        except Exception as e:
            print(f"❌ Failed to commit CI retry patch: {e}")
            # Mark inactive so we don't loop forever
            if hasattr(store, "store_retry_status"):
                try:
                    store.store_retry_status(
                        owner=owner,
                        repo=repo,
                        issue_number=pr_number,
                        attempts=previous_attempts,
                        last_outcome="COMMIT_FAILED",
                        active=False,
                    )
                except Exception:
                    pass
            continue

        # ------------------ Update retry state ------------------ #

        if hasattr(store, "store_retry_status"):
            try:
                store.store_retry_status(
                    owner=owner,
                    repo=repo,
                    issue_number=pr_number,
                    attempts=previous_attempts + 1,
                    last_outcome=getattr(ci_outcome, "category", None) if ci_outcome else None,
                    active=True,
                )
            except Exception:
                print("⚠️ Failed to persist retry status after CI retry (non-fatal).")

        print(
            f"✅ CI retry patch pushed for PR #{pr_number} "
            f"(attempt={previous_attempts + 1})"
        )


# ------------------------------- CLI entry ------------------------------- #


def _parse_repo_arg(arg: str) -> tuple[str, str]:
    if "/" not in arg:
        raise RuntimeError("Usage: python -m app.ci.ci_watcher OWNER/REPO")
    owner, repo = arg.split("/", 1)
    if not owner or not repo:
        raise RuntimeError("Invalid OWNER/REPO")
    return owner, repo


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise RuntimeError("Usage: python -m app.ci.ci_watcher OWNER/REPO")
    owner, repo = _parse_repo_arg(sys.argv[1])
    run_ci_watcher(owner, repo)
