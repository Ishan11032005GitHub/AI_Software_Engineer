from __future__ import annotations

from typing import Dict, List

from app.context.graph_ranker import dependency_impact
from app.agents.patch_generator import generate_fixed_content
from app.analysis.safety_verifier import verify_safe_change


def generate_multi_file_proposal(
    *,
    entry_file: str,
    entry_function: str | None,
    repo_path: str,
    issue: dict,
    max_files: int = 5,
) -> Dict:
    """
    Generates a multi-file CHANGE PROPOSAL.
    NO commits. NO writes. NO PRs.

    Returns:
        {
            "files": {
                "<path>": {
                    "old": "<content>",
                    "new": "<proposed>",
                    "used_llm": bool,
                    "safe": bool,
                    "reason": str
                }
            }
        }
    """

    impacted_count, impacted_files = dependency_impact(entry_function)

    affected_files: List[str] = [entry_file]
    for f in impacted_files:
        if f != entry_file:
            affected_files.append(f)

    affected_files = affected_files[:max_files]

    proposals = {}

    for file_path in affected_files:
        abs_path = f"{repo_path}/{file_path}"
        try:
            with open(abs_path, "r", errors="ignore") as f:
                old_content = f.read()
        except Exception:
            continue

        new_content, used_llm, _ = generate_fixed_content(
            issue=issue,
            file_content=old_content,
            file_path=file_path,
        )

        if not new_content:
            proposals[file_path] = {
                "old": old_content,
                "new": None,
                "used_llm": False,
                "safe": False,
                "reason": "No safe proposal generated",
            }
            continue

        safe, reason = verify_safe_change(
            old_content=old_content,
            new_content=new_content,
        )

        proposals[file_path] = {
            "old": old_content,
            "new": new_content,
            "used_llm": used_llm,
            "safe": safe,
            "reason": reason,
        }

    return {
        "entry_file": entry_file,
        "entry_function": entry_function,
        "files": proposals,
    }
