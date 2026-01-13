from __future__ import annotations

import sys
import os
import subprocess
from typing import Optional, List, Tuple

from config import GITHUB_TOKEN, SQLITE_PATH, MAX_CHANGED_LINES

# ---- GitHub interaction ----
from app.github.issue_reader import fetch_bug_issues
from app.github.pr_guard import pr_exists
from app.github.pr_creator import create_pr, merge_pr  # NOTE: merge_pr must exist

# ---- Static analysis ----
from app.analysis.stack_trace_parser import parse_stack_trace
from app.analysis.file_finder import search_repo
from app.analysis.ast_verifier import verify_python_ast
from app.analysis.safety_verifier import verify_safe_change

# ---- Agent core ----
from app.agents.patch_generator import generate_fixed_content
from app.agents.confidence import ConfidenceInputs, compute_confidence
from app.agents.proposal_engine import should_enter_proposal_mode
from app.agents.doc_generator import generate_engineering_doc

# ---- Storage ----
from app.storage.artifact_store import ArtifactStore

# ---- Context / dependency graph ----
from app.context.repo_indexer import RepoIndexer
from app.context.graph_ranker import dependency_impact

from app.utils.sensitive import touches_sensitive_area

# ---- Git operations ----
from app.git_ops import create_branch_and_commit, commit_and_push_amend

# ---- CI integration (Step 6.2) ----
from app.ci.actions_client import get_failed_logs_best_effort
from app.ci.test_failure_parser import parse_ci_logs
from app.ci.issue_ci_resolver import resolve_issue_ci_hint

# ---- Step 8 Multi-file APPLY engine (signature-based) ----
from app.agents.multifile_patch_engine import compute_signature_diff, apply_signature_fix

# ---- Step 9/10 CI retry engine (classification + retry policy) ----
from app.ci.retry_engine import classify_ci_outcome, should_retry_from_ci

from app.tests.test_runner import run_tests, TestResult

from threading import Thread
from app.github.comment_watcher import watch_loop

# ---- Review / memory integration ----
from app.github.review_feedback import sync_reviews_into_memory  # make sure this exists
from app.dashboard.jobs import router as jobs_router

import os, subprocess
from config import GITHUB_TOKEN

DRY_RUN = False

# -----------------------
# Phase-4 config knobs
# -----------------------
AUTO_DRAFT_CONF = float(os.getenv("AUTO_DRAFT_CONF", "0.15"))  # below this → no PR at all
AUTO_MERGE_CONF = float(os.getenv("AUTO_MERGE_CONF", "0.85"))  # above this + safe → auto-merge

from fastapi import FastAPI
from routes.agent_routes import router as agent_router

app = FastAPI()
app.include_router(agent_router, prefix="/api", tags=["agent"])
app.include_router(jobs_router)
# ===========================================================
# Phase-5 helper functions for test-gated decision flow
# ===========================================================

# app.include_router(agent_router, prefix="/api", tags=["agent"])

def downgrade_to_proposal(
    store,
    owner,
    repo,
    issue_number,
    relative_target,
    primary_old,
    primary_new,
    test_output,
):
    """Convert fix attempt into a proposal due to failed tests."""
    print("🔻 Converting to PROPOSAL (tests failed)")

    store.store_proposal(
        owner=owner,
        repo=repo,
        issue_number=issue_number,
        file_path=relative_target,
        document_md="Tests failed — converted to manual review proposal.",
        file_snapshots=[(relative_target, primary_old, primary_new)],
        meta={"tests_output": test_output},
    )

    # Mark retry inactive
    if hasattr(store, "store_retry_status"):
        try:
            store.store_retry_status(
                owner=owner,
                repo=repo,
                issue_number=issue_number,
                attempts=None,
                last_outcome="test_fail",
                active=False,
            )
        except Exception:
            pass


def continue_full_PR_flow(
    owner,
    repo,
    repo_path,
    relative_target,
    issue,
    primary_old,
    primary_new,
    store,
    previous_attempts,
    ci_hint,
    ci_outcome,
    confidence,
    safety_verified,
    ci_evidence,
):
    """Executes PR creation + optional auto-merge after tests passed."""
    print("🚀 Tests OK → Creating PR")

    issue_number = issue["number"]

    branch = create_branch_and_commit(
        repo_path,
        relative_target,
        primary_new,
        issue_number,
    )

    # commit retry progress
    if hasattr(store, "store_retry_status"):
        try:
            store.store_retry_status(
                owner=owner,
                repo=repo,
                issue_number=issue_number,
                attempts=previous_attempts + 1,
                last_outcome=getattr(ci_outcome, "category", None) if ci_outcome else None,
                active=True,
            )
        except Exception:
            pass

    pr = create_pr(owner, repo, branch, issue, draft=False)
    pr_number = pr.get("number")
    print(f"✔ PR → {pr.get('html_url')}")

    # 🔥 Store PR↔Issue link in DB
    if pr_number:
        try:
            store.store_pr_link(
                owner,
                repo,
                issue_number,
                pr_number=pr_number,
                pr_url=pr.get("html_url"),
                head_sha=ci_hint.head_sha if ci_hint else None,
            )
        except Exception:
            pass

        # 🧠 Ingest review comments into memory (best-effort)
        try:
            sync_reviews_into_memory(owner, repo, pr_number, issue_number, store)
        except Exception:
            pass

    # Auto-Merge Phase-4 gate
    _maybe_auto_merge(
        owner,
        repo,
        pr,
        confidence=confidence,
        safety_verified=safety_verified,
        ci_evidence=ci_evidence,
    )


# ===============================================
# Helpers (Step 7: multi-file proposal mode)
# ===============================================


def _safe_read(path: str) -> Optional[str]:
    """Best-effort text read with ignore-errors."""
    try:
        with open(path, "r", errors="ignore") as f:
            return f.read()
    except Exception:
        return None


def _normalize_repo_rel(repo_path: str, any_path: str) -> str:
    """
    Normalize a path to repo-relative POSIX style.
    If already relative, just normalize separators.
    """
    if not any_path:
        return any_path
    if os.path.isabs(any_path):
        rel = os.path.relpath(any_path, repo_path)
    else:
        rel = any_path
    return rel.replace("\\", "/")


def _candidate_multifile_targets(
    repo_path: str,
    primary_file_abs: str,
    impacted_files: List[str],
    ci_ranked_files: Optional[List[str]],
    *,
    max_files: int = 6,
) -> List[str]:
    """
    Decide which files are included in a multi-file PROPOSAL bundle.

    Rules:
      - Always include the primary file (the one we auto-edit).
      - Then up to (max_files - 1) more:
          1) CI failing files (strong runtime signal)
          2) Dependency-impacted files (call graph)
    """
    targets: List[str] = []
    seen: set[str] = set()

    def add(path: str):
        if not path:
            return
        norm = os.path.normpath(path)
        if norm in seen:
            return
        if not os.path.exists(norm):
            return
        seen.add(norm)
        targets.append(norm)

    # Primary first
    add(primary_file_abs)

    # CI-ranked failing files
    if ci_ranked_files:
        for rel in ci_ranked_files:
            if len(targets) >= max_files:
                break
            cand = os.path.join(repo_path, rel)
            add(cand)

    # Dependency-impacted files
    for rel in impacted_files:
        if len(targets) >= max_files:
            break
        cand = os.path.join(repo_path, rel)
        add(cand)

    return targets


def _build_multifile_proposal_bundle(
    repo_path: str,
    primary_file_abs: str,
    primary_old: str,
    primary_new: Optional[str],
    targets_abs: List[str],
) -> Tuple[List[str], List[Tuple[str, Optional[str], Optional[str]]]]:
    """
    Build the proposal payload:

      touched_files: repo-relative paths
      file_snapshots: (repo-rel, old_content, new_content_or_None)

    Only the primary file gets new_content. Others are read-only context.
    """
    touched_files_rel: List[str] = []
    snapshots: List[Tuple[str, Optional[str], Optional[str]]] = []

    primary_rel = _normalize_repo_rel(repo_path, primary_file_abs)
    touched_files_rel.append(primary_rel)
    snapshots.append((primary_rel, primary_old, primary_new))

    for abs_path in targets_abs:
        if os.path.normpath(abs_path) == os.path.normpath(primary_file_abs):
            continue

        rel = _normalize_repo_rel(repo_path, abs_path)
        touched_files_rel.append(rel)

        old_c = _safe_read(abs_path)
        snapshots.append((rel, old_c, None))

    return touched_files_rel, snapshots


# ===============================================
# Phase-4: Auto-merge helper
# ===============================================


def _maybe_auto_merge(
    owner: str,
    repo: str,
    pr: dict,
    *,
    confidence: float,
    safety_verified: bool,
    ci_evidence,
) -> None:
    """
    Phase-4: Auto-merge PR if:
      - confidence >= AUTO_MERGE_CONF
      - safety_verified is True
      - no known CI failures (ci_evidence is None or has_failure == False)
      - PR is not draft
    """
    # CI / logic-only runs: don't merge in DRY_RUN
    if DRY_RUN:
        print("ℹ DRY_RUN enabled – auto-merge disabled.")
        return

    if confidence < AUTO_MERGE_CONF:
        print(
            f"ℹ Auto-merge skipped: confidence={confidence:.2f} "
            f"< threshold={AUTO_MERGE_CONF:.2f}"
        )
        return

    if not safety_verified:
        print("ℹ Auto-merge skipped: safety_verified=False")
        return

    if ci_evidence is not None and getattr(ci_evidence, "has_failure", False):
        print("ℹ Auto-merge skipped: CI evidence still has failures.")
        return

    if pr.get("draft"):
        print("ℹ Auto-merge skipped: PR is draft.")
        return

    pr_number = pr.get("number")
    if not pr_number:
        print("ℹ Auto-merge skipped: missing PR number from API response.")
        return

    try:
        merged = merge_pr(owner, repo, pr_number)
        if merged.get("merged"):
            print(f"✅ Auto-merged PR #{pr_number}")
        else:
            print(f"⚠ Auto-merge API returned no 'merged' flag for PR #{pr_number}")
    except Exception as e:
        print(f"⚠ Auto-merge failed for PR #{pr_number}: {e}")


# ===============================================
# Core execution
# ===============================================


def parse_repo() -> tuple[str, str]:
    if len(sys.argv) != 2:
        raise RuntimeError("Usage: python main.py OWNER/REPO")
    owner, repo = sys.argv[1].split("/")
    if not owner or not repo:
        raise RuntimeError("Invalid OWNER/REPO")
    return owner, repo


# def prepare_repo(owner, repo):
#     if not GITHUB_TOKEN:
#         raise RuntimeError("❌ Missing GITHUB_TOKEN env var")

#     path = f"./repos/{owner}__{repo}"
#     clone_url = f"https://{GITHUB_TOKEN}@github.com/{owner}/{repo}.git"

#     if not os.path.exists(path):
#         print(f"📥 Cloning {owner}/{repo}")
#         subprocess.run(["git", "clone", clone_url, path], check=True)
#     else:
#         print(f"🔄 Updating repo...")
#         subprocess.run(["git", "-C", path, "fetch"], check=True)
#         subprocess.run(["git", "-C", path, "checkout", "main"], check=True)
#         subprocess.run(["git", "-C", path, "reset", "--hard", "origin/main"], check=True)

#     return path


def run() -> None:
    owner, repo = parse_repo()
    print(f"\n🚀 Running agent on {owner}/{repo}")

    start_chatops(owner, repo)

    repo_path = prepare_repo(owner, repo)

    # Storage (SQLite)
    store = ArtifactStore(SQLITE_PATH)
    store.init_db()

    # Build/update dependency index once per run
    print("🧠 Indexing repo dependency graph (Python-only)...")
    RepoIndexer(repo_path, store.db_path).index_repo()

    bugs = fetch_bug_issues(owner, repo)
    print(f"🐞 Issues fetched: {len(bugs)}")

    for issue in bugs:
        issue_number = issue["number"]
        print(f"\n━━━━━━━━━━ ISSUE #{issue_number} ━━━━━━━━━━")
        print(f"📄 {issue.get('title', '(no title)')}")

        # Guard: existing PR
        if pr_exists(owner, repo, issue_number):
            print("⏭️ PR already exists – skipping.")
            # Log run for visibility
            try:
                store.store_run(
                    owner=owner,
                    repo=repo,
                    issue_number=issue_number,
                    file_path=None,
                    confidence=0.0,
                    decision="SKIP_PR_EXISTS",
                    meta={"reason": "PR already open"},
                )
            except Exception:
                print("⚠️ Failed to log SKIP_PR_EXISTS (non-fatal).")
            continue

        # ======================================
        # Step 6.2 – issue-aware CI resolution
        # ======================================
        ci_hint = resolve_issue_ci_hint(owner, repo, GITHUB_TOKEN, issue)
        ci_bundle = get_failed_logs_best_effort(
            owner,
            repo,
            GITHUB_TOKEN,
            preferred_head_sha=ci_hint.head_sha,
        )

        ci_evidence = None
        if ci_bundle and getattr(ci_bundle, "text", None):
            ci_evidence = parse_ci_logs(ci_bundle.text)
            if ci_evidence and ci_evidence.has_failure:
                print(
                    f"🧪 CI failures detected | run={ci_bundle.html_url} | "
                    f"top_files={ci_evidence.failing_files_ranked[:3]}"
                )
                # Persist CI evidence if ArtifactStore supports it
                try:
                    store.store_ci_evidence(
                        owner=owner,
                        repo=repo,
                        issue_number=issue_number,
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
            else:
                print("🧪 CI logs present but no Python failures parsed.")
        else:
            print("🧪 No failed CI logs found or Actions disabled.")

        ci_ranked_files = (
            ci_evidence.failing_files_ranked
            if (ci_evidence and ci_evidence.has_failure)
            else None
        )

        # ---- Step 9/10: CI outcome classification + retry state ----
        ci_outcome = None
        try:
            if ci_evidence:
                ci_outcome = classify_ci_outcome(ci_evidence)
        except Exception:
            ci_outcome = None

        previous_attempts = 0
        if hasattr(store, "get_retry_status"):
            try:
                retry_state = store.get_retry_status(owner, repo, issue_number)
                if retry_state and getattr(retry_state, "attempts", None) is not None:
                    previous_attempts = retry_state.attempts
            except Exception:
                previous_attempts = 0

        retry_decision = None
        if ci_outcome is not None:
            try:
                retry_decision = should_retry_from_ci(ci_outcome, previous_attempts)
            except Exception:
                retry_decision = None

        # ======================================
        # File resolution (stack → CI → search)
        # ======================================
        issue_text = (issue.get("title") or "") + "\n" + (issue.get("body") or "")
        trace = parse_stack_trace(issue_text)

        file_path: Optional[str] = None

        # 1) stack trace file
        if trace and trace.get("file"):
            cand = os.path.join(repo_path, trace["file"])
            if os.path.exists(cand):
                file_path = cand
                print(f"🧵 Stack trace resolved file: {file_path}")

        # 2) CI top failing files
        if not file_path and ci_ranked_files:
            for rel in ci_ranked_files[:5]:
                cand = os.path.join(repo_path, rel)
                if os.path.exists(cand):
                    file_path = cand
                    print(f"🧪 CI suggested file: {file_path}")
                    break

        # 3) keyword search fallback
        if not file_path:
            file_path = search_repo((issue.get("title") or "").split(), repo_path)
            print(f"🔎 Keyword search file: {file_path}")

        if not file_path:
            print("❌ No file resolved – proposal-only (no code change).")

            # Retry tracking: mark inactive
            if hasattr(store, "store_retry_status"):
                try:
                    store.store_retry_status(
                        owner=owner,
                        repo=repo,
                        issue_number=issue_number,
                        attempts=previous_attempts,
                        last_outcome=getattr(ci_outcome, "category", None)
                        if ci_outcome
                        else None,
                        active=False,
                    )
                except Exception:
                    pass

            # Step 13: log this run
            try:
                store.store_run(
                    owner=owner,
                    repo=repo,
                    issue_number=issue_number,
                    file_path=None,
                    confidence=0.0,
                    decision="NO_FILE",
                    meta={
                        "ci_outcome": getattr(ci_outcome, "category", None)
                        if ci_outcome
                        else None,
                    },
                )
            except Exception:
                print("⚠️ Failed to log NO_FILE (non-fatal).")

            continue

        primary_old = _safe_read(file_path) or ""
        if not primary_old.strip():
            print("⚠️ Target file is empty or unreadable – skipping.")
            try:
                store.store_run(
                    owner=owner,
                    repo=repo,
                    issue_number=issue_number,
                    file_path=_normalize_repo_rel(repo_path, file_path),
                    confidence=0.0,
                    decision="SKIP_EMPTY_FILE",
                    meta={},
                )
            except Exception:
                print("⚠️ Failed to log SKIP_EMPTY_FILE (non-fatal).")
            continue

        # Repo-relative path for all git operations
        relative_target = _normalize_repo_rel(repo_path, file_path)

        # ======================================
        # AST pre-check (Python-only)
        # ======================================
        ast_verified = True
        entry_fn = None
        if file_path.endswith(".py"):
            entry_fn = trace.get("function") if trace else None
            ast_verified = bool(verify_python_ast(primary_old, function_name=entry_fn))
            if not ast_verified:
                print("🧱 AST verification failed – will likely fall back to PROPOSE.")

        # ======================================
        # Generate primary fix
        # ======================================
        primary_new, used_llm, used_rule_based = generate_fixed_content(
            issue=issue,
            file_content=primary_old,
            file_path=file_path,
            store=store,  # Step 11 – enable FixMemory
        )

        if not primary_new:
            print("⚠️ No fix produced – proposal-only.")
            doc = generate_engineering_doc(
                issue=issue,
                repo_path=repo_path,
                decision="PROPOSE",
                decision_reason="No safe fix could be generated for primary file.",
                confidence=0.0,
                touched_files=[relative_target],
                old_new_files=[(relative_target, primary_old, None)],
                checks={
                    "ast_verified": ast_verified,
                    "ci_hint": getattr(ci_hint, "reason", None),
                    "ci_top_files": (ci_ranked_files or [])[:5],
                    "ci_outcome": getattr(ci_outcome, "category", None)
                    if ci_outcome
                    else None,
                },
                risk_notes=["No fix produced. Agent refused to guess."],
            )
            try:
                store.store_proposal(
                    owner=owner,
                    repo=repo,
                    issue_number=issue_number,
                    file_path=relative_target,
                    document_md=doc,
                    file_snapshots=[(relative_target, primary_old, None)],
                    meta={
                        "reason": "no_fix",
                        "ci_top_files": (ci_ranked_files or [])[:5],
                        "ci_outcome": getattr(ci_outcome, "category", None)
                        if ci_outcome
                        else None,
                    },
                )
                print("📝 Stored proposal (no_fix).")
            except Exception:
                print("⚠️ Failed to store proposal (no_fix).")

            # Step 13: log this PROPOSE run
            try:
                store.store_run(
                    owner=owner,
                    repo=repo,
                    issue_number=issue_number,
                    file_path=relative_target,
                    confidence=0.0,
                    decision="PROPOSE",
                    meta={"reason": "no_fix"},
                )
            except Exception:
                print("⚠️ Failed to log PROPOSE (no_fix) (non-fatal).")

            if hasattr(store, "store_retry_status"):
                try:
                    store.store_retry_status(
                        owner=owner,
                        repo=repo,
                        issue_number=issue_number,
                        attempts=previous_attempts,
                        last_outcome=getattr(ci_outcome, "category", None)
                        if ci_outcome
                        else None,
                        active=False,
                    )
                except Exception:
                    pass
            continue

        # ======================================
        # Safety verification (Python-only strict)
        # ======================================
        safety_verified = True
        safety_reason = "N/A"
        if file_path.endswith(".py"):
            safety_verified, safety_reason = verify_safe_change(
                old_content=primary_old,
                new_content=primary_new,
                max_changed_lines=MAX_CHANGED_LINES,
            )
            if not safety_verified:
                print(f"🛑 Safety verifier failed: {safety_reason}")

        # ======================================
        # Dependency impact from repo graph
        # ======================================
        impacted_count, impacted_files = dependency_impact(store.db_path, entry_fn)
        if impacted_count:
            print(
                f"🧬 Dependency impact: {impacted_count} file(s) call "
                f"{entry_fn or '(unknown function)'}"
            )

        # STEP 7: multi-file PROPOSAL signal
        multifile_signal = False
        if ci_ranked_files and len(ci_ranked_files) >= 2:
            multifile_signal = True
        if impacted_count and impacted_count > 0:
            multifile_signal = True

        # If multi-file signal exists, we tell the gate that changed_files_count > 1
        changed_files_count_for_gate = 1
        if multifile_signal:
            changed_files_count_for_gate = 2  # minimal >1 marker → tends to force PROPOSE

        # ======================================
        # Confidence computation
        # ======================================
        confidence = compute_confidence(
            ConfidenceInputs(
                used_stack_trace=bool(trace or ci_ranked_files),
                stack_trace_function_resolved=bool(entry_fn),
                changed_files_count=changed_files_count_for_gate,
                impacted_files_count=impacted_count or 0,
                ast_verified=ast_verified,
                safety_verified=safety_verified,
                used_llm=used_llm,
                used_rule_based=used_rule_based,
                file_lines=len(primary_old.splitlines()),
            )
        )

        decision = should_enter_proposal_mode(
            confidence=confidence,
            changed_files_count=changed_files_count_for_gate,
            impacted_files_count=impacted_count or 0,
            used_llm=used_llm,
            touches_sensitive_area=touches_sensitive_area(
                file_path,
                issue.get("title", ""),
                issue.get("body", ""),
            ),
        )

        print(
            f"📈 Confidence={confidence:.2f} | "
            f"mode={decision.mode} | multifile={multifile_signal}"
        )

        # ---- Step 13: persist run metadata for every processed issue ----
        try:
            store.store_run(
                owner=owner,
                repo=repo,
                issue_number=issue_number,
                file_path=relative_target,
                confidence=confidence,
                decision=decision.mode,
                meta={
                    "ast_verified": ast_verified,
                    "safety_verified": safety_verified,
                    "safety_reason": safety_reason,
                    "ci_outcome": getattr(ci_outcome, "category", None)
                    if ci_outcome
                    else None,
                    "multifile_signal": multifile_signal,
                    "used_llm": used_llm,
                    "used_rule_based": used_rule_based,
                    "impacted_files_count": impacted_count or 0,
                    "ci_top_files": (ci_ranked_files or [])[:5],
                    "previous_attempts": previous_attempts,
                },
            )
        except Exception:
            print("⚠️ Failed to store agent_runs row (non-fatal).")

        # ======================================
        # PROPOSAL → Decide PR or Manual Proposal
        # ======================================
        if decision.mode != "APPLY":

            pr_draft_allowed = primary_new is not None and confidence >= AUTO_DRAFT_CONF
            pr_full_allowed = primary_new is not None and confidence >= 0.55

            if pr_full_allowed:
                print(f"🟢 Auto-Fix → Full PR (confidence={confidence:.2f})")

                # ---------------- Phase-5: Local test validation ----------------
                print("🧪 Running local tests before PR...")
                test_result = run_tests(repo_path)

                if not test_result.success:
                    print("❌ Tests failed. Converting to proposal instead of PR.")
                    store.store_proposal(
                        owner,
                        repo,
                        issue_number,
                        relative_target,
                        document_md="Tests failed — auto-converted to proposal.",
                        file_snapshots=[(relative_target, primary_old, primary_new)],
                        meta={"tests_output": test_result.output},
                    )
                    continue  # DO NOT create PR

                print("✅ Tests passed. Continuing to PR...")

                # mark retry state as active attempt
                if hasattr(store, "store_retry_status"):
                    try:
                        store.store_retry_status(
                            owner=owner,
                            repo=repo,
                            issue_number=issue_number,
                            attempts=previous_attempts + 1,
                            last_outcome=getattr(ci_outcome, "category", None)
                            if ci_outcome
                            else None,
                            active=True,
                        )
                    except Exception:
                        pass

                continue_full_PR_flow(
                    owner,
                    repo,
                    repo_path,
                    relative_target,
                    issue,
                    primary_old,
                    primary_new,
                    store,
                    previous_attempts,
                    ci_hint,
                    ci_outcome,
                    confidence,
                    safety_verified,
                    ci_evidence,
                )
                continue

            if pr_draft_allowed:
                print(f"🟡 Low-confidence → Draft PR auto-opened")
                branch = create_branch_and_commit(
                    repo_path,
                    relative_target,
                    primary_new,
                    issue_number,
                )

                if hasattr(store, "store_retry_status"):
                    try:
                        store.store_retry_status(
                            owner=owner,
                            repo=repo,
                            issue_number=issue_number,
                            attempts=previous_attempts + 1,
                            last_outcome=getattr(ci_outcome, "category", None)
                            if ci_outcome
                            else None,
                            active=True,
                        )
                    except Exception:
                        pass

                pr = create_pr(owner, repo, branch, issue, draft=True)
                pr_number = pr.get("number")
                print(f"📩 Draft PR → {pr.get('html_url')}")

                if pr_number:
                    try:
                        store.store_pr_link(
                            owner,
                            repo,
                            issue_number,
                            pr_number=pr_number,
                            pr_url=pr.get("html_url"),
                            head_sha=ci_hint.head_sha if ci_hint else None,
                        )
                    except Exception:
                        pass

                # Never auto-merge draft
                continue

            print("⚠ Very low confidence → Proposal saved only")
            store.store_proposal(
                owner,
                repo,
                issue_number,
                relative_target,
                document_md="Low-confidence proposed; manual review needed.",
                file_snapshots=[(relative_target, primary_old, primary_new)],
                meta={"confidence": confidence, "no_pr": True},
            )
            continue

        # ======================================
        # APPLY path (single-file or Step-8A multi-file)
        # ======================================
        if DRY_RUN:
            print("🧪 DRY RUN enabled – would apply, but skipping commit/PR.")
            continue

        # Hard safety stop, even if gate says APPLY
        if not safety_verified:
            print("🛑 Final safety gate failed – downgrade to PROPOSE.")
            doc = generate_engineering_doc(
                issue=issue,
                repo_path=repo_path,
                decision="PROPOSE",
                decision_reason=f"Final safety gate failed: {safety_reason}",
                confidence=confidence,
                touched_files=[relative_target],
                old_new_files=[(relative_target, primary_old, primary_new)],
                checks={
                    "safety_verified": safety_verified,
                    "safety_reason": safety_reason,
                    "ci_outcome": getattr(ci_outcome, "category", None)
                    if ci_outcome
                    else None,
                },
                risk_notes=[safety_reason],
            )
            try:
                store.store_proposal(
                    owner=owner,
                    repo=repo,
                    issue_number=issue_number,
                    file_path=relative_target,
                    document_md=doc,
                    file_snapshots=[(relative_target, primary_old, primary_new)],
                )
                print("📝 Stored downgraded PROPOSE (safety gate).")
            except Exception:
                print("⚠️ Failed to store downgraded proposal.")

            if hasattr(store, "store_retry_status"):
                try:
                    store.store_retry_status(
                        owner=owner,
                        repo=repo,
                        issue_number=issue_number,
                        attempts=previous_attempts,
                        last_outcome=getattr(ci_outcome, "category", None)
                        if ci_outcome
                        else None,
                        active=False,
                    )
                except Exception:
                    pass
            continue

        # ---------- NEW SECTION: TEST BEFORE APPLY ----------
        print("🧪 Running local tests before APPLY+PR...")
        test_result = run_tests(repo_path)

        if not test_result.success:
            print("❌ Tests failed → downgrade APPLY → PROPOSE instead of PR")
            downgrade_to_proposal(
                store,
                owner,
                repo,
                issue_number,
                relative_target,
                primary_old,
                primary_new,
                test_result.output,
            )
            continue

        print("✔ Tests passed → continuing with APPLY commit+PR")

        # ---- Step-8A: Multi-file APPLY attempt (signature-based) ----
        sig = (
            compute_signature_diff(primary_old, primary_new, entry_fn)
            if entry_fn
            else None
        )
        multi_patches: List[Tuple[str, str]] = []

        if sig and impacted_files:
            for rel in impacted_files[:5]:
                abs_path = os.path.join(repo_path, rel)
                if os.path.normpath(abs_path) == os.path.normpath(file_path):
                    continue
                old_impacted = _safe_read(abs_path) or ""
                if not old_impacted:
                    continue
                new_impacted = apply_signature_fix(old_impacted, sig)
                if new_impacted != old_impacted:
                    rel_path = _normalize_repo_rel(repo_path, abs_path)
                    multi_patches.append((rel_path, new_impacted))

        # Multi-file apply allowed only if confidence is high enough
        multi_allowed = bool(multi_patches) and (confidence >= 0.65)

        # If retry engine explicitly says "no retry" for this CI outcome and
        # we've already attempted before, be conservative and block multi-file.
        if (
            retry_decision is not None
            and previous_attempts > 0
            and not retry_decision.should_retry
        ):
            multi_allowed = False

        if multi_allowed:
            print(f"🔥 Multi-file APPLY: {1 + len(multi_patches)} files")

            # Create base branch with primary change
            branch = create_branch_and_commit(
                repo_path,
                relative_target,
                primary_new,
                issue_number,
            )

            # Amend commits with secondary patches using proper amend helper
            for rel_path, new_src in multi_patches:
                commit_and_push_amend(
                    repo_path=repo_path,
                    branch=branch,
                    file_path=rel_path,
                    new_content=new_src,
                )

            if hasattr(store, "store_retry_status"):
                try:
                    store.store_retry_status(
                        owner=owner,
                        repo=repo,
                        issue_number=issue_number,
                        attempts=previous_attempts + 1,
                        last_outcome=getattr(ci_outcome, "category", None)
                        if ci_outcome
                        else None,
                        active=True,
                    )
                except Exception:
                    pass

            pr = create_pr(owner, repo, branch, issue, draft=False)
            pr_number = pr.get("number")
            print(f"🚀 Multi-file PR Created → {pr.get('html_url')}")

            if pr_number:
                try:
                    store.store_pr_link(
                        owner,
                        repo,
                        issue_number,
                        pr_number=pr_number,
                        pr_url=pr.get("html_url"),
                        head_sha=ci_hint.head_sha if ci_hint else None,
                    )
                except Exception:
                    pass

                try:
                    sync_reviews_into_memory(owner, repo, pr_number, issue_number, store)
                except Exception:
                    pass

            _maybe_auto_merge(
                owner,
                repo,
                pr,
                confidence=confidence,
                safety_verified=safety_verified,
                ci_evidence=ci_evidence,
            )
            continue

        # Default single-file APPLY fallback
        branch = create_branch_and_commit(
            repo_path,
            relative_target,
            primary_new,
            issue_number,
        )

        if hasattr(store, "store_retry_status"):
            try:
                store.store_retry_status(
                    owner=owner,
                    repo=repo,
                    issue_number=issue_number,
                    attempts=previous_attempts + 1,
                    last_outcome=getattr(ci_outcome, "category", None)
                    if ci_outcome
                    else None,
                    active=True,
                )
            except Exception:
                pass

        pr = create_pr(owner, repo, branch, issue, draft=False)
        pr_number = pr.get("number")
        print(f"✔ Single-file PR → {pr.get('html_url')}")

        if pr_number:
            try:
                store.store_pr_link(
                    owner,
                    repo,
                    issue_number,
                    pr_number=pr_number,
                    pr_url=pr.get("html_url"),
                    head_sha=ci_hint.head_sha if ci_hint else None,
                )
            except Exception:
                pass

            try:
                sync_reviews_into_memory(owner, repo, pr_number, issue_number, store)
            except Exception:
                pass

        _maybe_auto_merge(
            owner,
            repo,
            pr,
            confidence=confidence,
            safety_verified=safety_verified,
            ci_evidence=ci_evidence,
        )


# OPTIONAL:
# Run CI bot + ChatOps watcher in parallel mode
# python main.py repo & python chatops.py repo


def start_chatops(owner, repo):
    t = Thread(target=watch_loop, args=(owner, repo), daemon=True)
    t.start()
    print("🤖 ChatOps background loop started.")

def run_agent_pipeline(owner, repo, action, prompt):
    repo_path = prepare_repo(owner, repo)      # you already have this in main.py
    return run_full_agent(repo_path, owner, repo, action, prompt)

store=ArtifactStore(SQLITE_PATH)

# def run_full_agent(repo_path, owner, repo, action, prompt):
#     """
#     Full autonomous AI engineer pipeline.
#     Works whether user selects fix-bugs, refactor, add-feature or project-gen.
#     """

#     # 1. ANALYZE CODEBASE
#     issues = analyze_repository(repo_path)              # TODO: you already implemented
#     files = load_all_files(repo_path)                   # TODO: you already implemented

#     # 2. GENERATE ENGINEERING PLAN + PATCHES
#     document_md, patches, confidence = generate_proposal(
#         repo_path=repo_path,
#         prompt=prompt,
#         action=action
#     )

#     if not patches:
#         print("⚠ No change determined, nothing to commit.")
#         return {"status": "no_change"}

#     mode = (
#         "PROPOSE" if confidence < PR_DRAFT_THRESHOLD else
#         "APPLY" if confidence < PR_FULL_THRESHOLD else
#         "AUTO"
#     )

#     # 3. APPLY PATCHES LOCALLY
#     modified_files = apply_patches(repo_path, patches)  # returns list of (file,newContent,oldContent)

#     # 4. STORE PROPOSAL IN DB
#     proposal_id = store.store_proposal(
#         owner=owner,
#         repo=repo,
#         issue_number=None,
#         file_path=patches[0]["file"],
#         document_md=document_md,
#         patches=modified_files,
#         confidence=confidence,
#         mode=mode
#     )

#     # 5. COMMIT to branch
#     branch = create_branch(repo_path)   # "ai/fix-{timestamp}" etc
#     commit_hash = commit_changes(repo_path, modified_files)

#     # 6. PUSH & PR if required
#     if mode == "PROPOSE":
#         pr_url = create_pull_request(owner, repo, branch, draft=True)

#     elif mode == "APPLY":
#         pr_url = create_pull_request(owner, repo, branch)

#     elif mode == "AUTO":
#         pr_url = create_pull_request(owner, repo, branch)
#         merge_pr_if_green(pr_url)

#     # 7. LOG RUN
#     store.record_run(
#         owner=owner,
#         repo=repo,
#         issue_number=None,
#         file_path=patches[0]["file"],
#         confidence=confidence,
#         decision=mode,
#         pr_url=pr_url
#     )

#     return {
#         "proposal_id": proposal_id,
#         "pr_url": pr_url,
#         "confidence": confidence,
#         "mode": mode
#     }

def run_agent_pipeline(owner, repo, action, prompt):
    """
    Core AI Software Engineer logic.
    Your full pipeline runs here.
    This function replaces CLI execution.
    """

    # reuse your existing preparation
    repo_path = prepare_repo(owner, repo)

    # you already have logic inside main that:
    # 1) finds bugs / issues
    # 2) generates proposal
    # 3) patches + PR depending on confidence
    # 4) writes to SQLite

    result = run_full_agent(
        repo_path=repo_path,
        owner=owner,
        repo=repo,
        action=action,
        prompt=prompt
    )

    return result

import os, subprocess
from config import GITHUB_TOKEN

def prepare_repo(owner: str, repo: str) -> str:
    if not GITHUB_TOKEN:
        raise RuntimeError("Missing GITHUB_TOKEN in environment.")

    repo_path = f"./repos/{owner}__{repo}"
    clone_url = f"https://{GITHUB_TOKEN}@github.com/{owner}/{repo}.git"

    if not os.path.exists(repo_path):
        print(f"📥 Cloning fresh {owner}/{repo}")
        subprocess.run(["git", "clone", clone_url, repo_path], check=True)
    else:
        print(f"🔄 Updating {owner}/{repo}")
        subprocess.run(["git", "-C", repo_path, "fetch"], check=True)
        subprocess.run(["git", "-C", repo_path, "checkout", "main"], check=True)
        subprocess.run(["git", "-C", repo_path, "reset", "--hard", "origin/main"], check=True)

    return repo_path

from app.storage.artifact_store import ArtifactStore
from config import SQLITE_PATH, PR_DRAFT_THRESHOLD, PR_FULL_THRESHOLD
from app.agents.proposal_engine import generate_proposal
from app.agents.patch_generator import apply_patches
from app.git_ops import create_branch_and_commit, commit_and_push_amend
from app.github.pr_creator import create_pr, merge_pr

def run_full_agent(repo_path, owner, repo, action, prompt):
    store = ArtifactStore(SQLITE_PATH)
    store.init_db()

    # 1. Generate plan + patches
    document_md, patches, confidence = generate_proposal(
        repo_path=repo_path,
        action=action,
        prompt=prompt
    )

    if not patches:
        return {"status": "no_change"}

    # Decide mode
    if confidence < PR_DRAFT_THRESHOLD:
        mode = "PROPOSE"
    elif confidence < PR_FULL_THRESHOLD:
        mode = "APPLY"
    else:
        mode = "AUTO"

    # 2. Apply patch to repo locally
    modified_files = apply_patches(repo_path, patches)

    # 3. Store proposal
    proposal_id = store.store_proposal(
        owner=owner,
        repo=repo,
        issue=0,
        file_path=patches[0]["file"],
        doc=document_md,
        meta={"confidence": confidence, "mode": mode},
        file_snapshots=modified_files,
    )

    # 4. Commit branch
    branch = create_branch_and_commit(repo_path, patches[0]["file"], patches[0]["new"], "AI-PATCH")

    # 5. Push → PR handling
    if mode == "PROPOSE":
        pr_url = create_pr(owner, repo, branch, draft=True)

    elif mode == "APPLY":
        pr_url = create_pr(owner, repo, branch)

    elif mode == "AUTO":
        pr_url = create_pr(owner, repo, branch)
        merge_pr(owner, repo, pr_url)  # If green

    # 6. Log summary
    store.store_run(
        owner=owner,
        repo=repo,
        issue_number=0,
        file_path=patches[0]["file"],
        confidence=confidence,
        decision=mode,
        meta={"pr_url": pr_url},
    )

    return {
        "proposal_id": proposal_id,
        "pr_url": pr_url,
        "confidence": confidence,
        "mode": mode,
        "status": "done"
    }




if __name__ == "__main__":
    run()
