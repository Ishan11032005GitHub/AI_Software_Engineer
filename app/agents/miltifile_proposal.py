# app/agents/multifile_proposal.py
from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

from config import MAX_CHANGED_LINES
from app.agents.patch_generator import generate_fixed_content
from app.analysis.safety_verifier import verify_safe_change


@dataclass(frozen=True)
class MultiFileProposalResult:
    """
    A proposal bundle that can include multiple files.

    file_snapshots format:
      (repo_rel_path, old_content, new_content_or_none)

    - new_content is only included if we successfully generated a patch AND it passed safety gates.
    - otherwise new_content=None and the reason is captured in skipped_files.
    """
    touched_files: List[str]
    file_snapshots: List[Tuple[str, Optional[str], Optional[str]]]
    skipped_files: Dict[str, str]
    used_llm_any: bool
    used_rule_based_any: bool


SKIP_DIRS = {".git", "venv", ".venv", "__pycache__", "node_modules", "dist", "build"}


def _safe_read(path: str) -> Optional[str]:
    try:
        with open(path, "r", errors="ignore") as f:
            return f.read()
    except Exception:
        return None


def _repo_rel(repo_path: str, any_path: str) -> str:
    if not any_path:
        return any_path
    if os.path.isabs(any_path):
        rel = os.path.relpath(any_path, repo_path)
    else:
        rel = any_path
    return rel.replace("\\", "/")


def _exists_file(path: str) -> bool:
    try:
        return bool(path) and os.path.isfile(path)
    except Exception:
        return False


def select_multifile_targets(
    repo_path: str,
    primary_file_abs: str,
    *,
    ci_ranked_files: Optional[List[str]] = None,
    impacted_files: Optional[List[str]] = None,
    max_files: int = 6,
) -> List[str]:
    """
    Deterministic target selection:
      1) primary file
      2) CI ranked files (strong runtime evidence)
      3) dependency impacted files

    Returns ABSOLUTE paths.
    """
    targets: List[str] = []
    seen = set()

    def add_abs(p: str):
        if not p:
            return
        p = os.path.normpath(p)
        if p in seen:
            return
        if not _exists_file(p):
            return
        targets.append(p)
        seen.add(p)

    add_abs(primary_file_abs)

    if ci_ranked_files:
        for rel in ci_ranked_files:
            if len(targets) >= max_files:
                break
            add_abs(os.path.join(repo_path, rel))

    if impacted_files:
        for rel in impacted_files:
            if len(targets) >= max_files:
                break
            add_abs(os.path.join(repo_path, rel))

    return targets


def _is_supported_source_file(path: str) -> bool:
    """
    Proposal generator only supports these file types for patch generation currently.
    Others can still be included as context (old_content snapshot), but no new_content is generated.
    """
    if not path:
        return False
    return path.endswith((".py", ".js", ".ts", ".java"))


def _python_safety_gate(old_c: str, new_c: str) -> Tuple[bool, str]:
    """
    Uses your existing Python safety policy.
    """
    return verify_safe_change(old_content=old_c, new_content=new_c, max_changed_lines=MAX_CHANGED_LINES)


def generate_multifile_proposal(
    *,
    repo_path: str,
    issue: dict,
    primary_file_abs: str,
    primary_old: str,
    primary_new: Optional[str],
    ci_ranked_files: Optional[List[str]] = None,
    impacted_files: Optional[List[str]] = None,
    max_files: int = 6,
) -> MultiFileProposalResult:
    """
    Generates a multi-file proposal bundle.

    Key contract:
      - NO commits, NO PRs.
      - Only returns NEW content for a file if:
          (a) we generated a patch AND
          (b) for Python files: safety gate passes.

    For non-Python files, we still can propose changes (via LLM/rules in patch_generator),
    but we currently cannot "prove" safety equivalently; those proposals are included,
    but marked with a note in skipped_files if not generated.

    If you want stricter behavior: change to "only include new_content for .py until JS/Java safety exists".
    """
    skipped: Dict[str, str] = {}
    touched_files: List[str] = []
    snapshots: List[Tuple[str, Optional[str], Optional[str]]] = []

    used_llm_any = False
    used_rule_any = False

    targets_abs = select_multifile_targets(
        repo_path=repo_path,
        primary_file_abs=primary_file_abs,
        ci_ranked_files=ci_ranked_files,
        impacted_files=impacted_files,
        max_files=max_files,
    )

    # Primary file snapshot always included
    primary_rel = _repo_rel(repo_path, primary_file_abs)
    touched_files.append(primary_rel)
    snapshots.append((primary_rel, primary_old, primary_new))

    # Secondary files
    for abs_path in targets_abs:
        if os.path.normpath(abs_path) == os.path.normpath(primary_file_abs):
            continue

        rel = _repo_rel(repo_path, abs_path)
        old_c = _safe_read(abs_path)

        touched_files.append(rel)

        if old_c is None:
            snapshots.append((rel, None, None))
            skipped[rel] = "read_error"
            continue

        # If not supported, include context only
        if not _is_supported_source_file(abs_path):
            snapshots.append((rel, old_c, None))
            skipped[rel] = "unsupported_extension_context_only"
            continue

        # Propose a fix for this file (proposal-only)
        new_c, used_llm, used_rule = generate_fixed_content(
            issue=issue,
            file_content=old_c,
            file_path=abs_path,
        )
        used_llm_any = used_llm_any or used_llm
        used_rule_any = used_rule_any or used_rule

        if not new_c:
            snapshots.append((rel, old_c, None))
            skipped[rel] = "no_patch_generated"
            continue

        # Safety gate (Python only)
        if abs_path.endswith(".py"):
            ok, reason = _python_safety_gate(old_c, new_c)
            if not ok:
                snapshots.append((rel, old_c, None))
                skipped[rel] = f"python_safety_failed:{reason}"
                continue

        # Accept proposed patch
        snapshots.append((rel, old_c, new_c))

    return MultiFileProposalResult(
        touched_files=touched_files,
        file_snapshots=snapshots,
        skipped_files=skipped,
        used_llm_any=used_llm_any,
        used_rule_based_any=used_rule_any,
    )
