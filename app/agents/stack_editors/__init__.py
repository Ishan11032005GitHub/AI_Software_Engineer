# app/agents/stack_editors/__init__.py
from __future__ import annotations

from typing import Dict, Any

from app.agents.stack_fingerprint import StackFingerprint
from app.agents.stack_editors.react_editor import apply_react_frontend_edits
from app.agents.stack_editors.nextjs_editor import apply_nextjs_frontend_edits
from app.agents.stack_editors.vanilla_html_editor import apply_vanilla_frontend_edits


def apply_stack_edits(repo_path: str, fp: StackFingerprint, goal: str) -> Dict[str, Any]:
    """
    Entry point for STEP 8.
    Dispatches to stack-specific editor.
    """
    fw = fp.frontend_framework

    if fw == "nextjs":
        return apply_nextjs_frontend_edits(repo_path, fp, goal)

    if fw == "react":
        return apply_react_frontend_edits(repo_path, fp, goal)

    if fw == "vanilla":
        return apply_vanilla_frontend_edits(repo_path, fp, goal)

    # Unknown: do not write FEATURE.md (you explicitly want real edits)
    # We still make a minimal safe improvement if index.html exists.
    if fp.primary_html:
        return apply_vanilla_frontend_edits(repo_path, fp, goal)

    return {
        "status": "no_op",
        "reason": f"Unsupported/unknown frontend stack for goal={goal!r}. No safe deterministic edit target found.",
        "changed_files": [],
    }
