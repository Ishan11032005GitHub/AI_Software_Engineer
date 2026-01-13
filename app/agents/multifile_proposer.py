# app/agents/multifile_proposer.py
from __future__ import annotations

import os
from dataclasses import dataclass
from typing import List, Optional, Tuple

from app.analysis.safety_verifier import verify_safe_change
from app.agents.patch_generator_llm import propose_fix_with_llm


@dataclass(frozen=True)
class ProposedFileChange:
    path: str
    old_content: str
    new_content: Optional[str]  # None if we couldn't safely propose
    ok: bool
    reason: str


def generate_multifile_proposal(
    *,
    issue: dict,
    repo_path: str,
    primary_file_path: str,
    impacted_files: List[str],
    max_files: int = 3,
    max_changed_lines: int = 25,
) -> List[ProposedFileChange]:
    """
    Proposal-only multi-file preview generator.

    Rules:
    - NO commits, NO PRs.
    - Only for Python files (for now).
    - Uses LLM as a proposer, then runs safety_verifier as a checker.
    - Caps number of extra files to avoid runaway proposals.
    - If GEMINI_API_KEY missing, returns empty list (no multi-file previews).

    Returns:
      List[ProposedFileChange] for extra files ONLY (not including primary).
    """
    if not impacted_files:
        return []

    # Don’t include the primary file again
    normalized_primary = os.path.normpath(primary_file_path)

    # Keep only python files, unique, and existing in repo
    selected: List[str] = []
    seen = set()

    for p in impacted_files:
        if not p:
            continue

        # impacted_files may already be absolute OR repo-relative depending on your graph_ranker
        candidate = p
        if not os.path.isabs(candidate):
            candidate = os.path.join(repo_path, candidate)

        candidate = os.path.normpath(candidate)

        if candidate == normalized_primary:
            continue
        if candidate in seen:
            continue
        if not candidate.endswith(".py"):
            continue
        if not os.path.exists(candidate):
            continue

        selected.append(candidate)
        seen.add(candidate)

        if len(selected) >= max_files:
            break

    if not selected:
        return []

    issue_text = (issue.get("title") or "") + "\n" + (issue.get("body") or "")
    out: List[ProposedFileChange] = []

    for fp in selected:
        try:
            with open(fp, "r", errors="ignore") as f:
                old = f.read()
        except Exception as e:
            out.append(
                ProposedFileChange(
                    path=fp,
                    old_content="",
                    new_content=None,
                    ok=False,
                    reason=f"Failed to read file: {e}",
                )
            )
            continue

        # LLM proposes full updated file content (or None if key missing / failure)
        new = propose_fix_with_llm(issue_text=issue_text, file_path=fp, file_content=old)

        if not new:
            out.append(
                ProposedFileChange(
                    path=fp,
                    old_content=old,
                    new_content=None,
                    ok=False,
                    reason="No LLM proposal produced (or GEMINI_API_KEY missing).",
                )
            )
            continue

        ok, reason = verify_safe_change(old_content=old, new_content=new, max_changed_lines=max_changed_lines)

        if not ok:
            out.append(
                ProposedFileChange(
                    path=fp,
                    old_content=old,
                    new_content=None,
                    ok=False,
                    reason=f"Safety verifier rejected proposal: {reason}",
                )
            )
            continue

        out.append(
            ProposedFileChange(
                path=fp,
                old_content=old,
                new_content=new,
                ok=True,
                reason="OK",
            )
        )

    return out
