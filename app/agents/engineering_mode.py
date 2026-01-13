# app/agents/engineering_mode.py
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Optional


@dataclass(frozen=True)
class ModePolicy:
    """
    ENGINEERING_MODE policy is a pure config derived per-run.
    It is enforced BEFORE executing the plan.

    Keep this object small and explicit; it must remain stable.
    """
    name: str

    # Safety + scope bounds
    max_steps: int
    max_file_mutations: int          # how many file-mutation ops can exist in plan
    max_unique_paths_mutated: int    # distinct file paths mutated across steps

    # Operation permissions
    allow_delete_file: bool
    allow_apply_patch: bool

    # Control requirements
    require_approval_before_pr: bool

    # Optional additional strictness
    require_verification_for_pr: bool  # best-effort: ensure verify ops exist if PR is planned


def _get_confidence(intent_obj: Optional[Dict[str, Any]]) -> float:
    if not isinstance(intent_obj, dict):
        return 0.0
    try:
        return float(intent_obj.get("confidence", 0.0))
    except Exception:
        return 0.0


def resolve_engineering_mode(intent_obj: Optional[Dict[str, Any]]) -> ModePolicy:
    """
    Per-run resolution:
    - SAFE: low confidence → refuse risky changes, require approval, small scope
    - STANDARD: default for most runs
    - AGGRESSIVE: high confidence → allow deep edits (patch/delete) but still bounded
    """
    c = _get_confidence(intent_obj)

    # Thresholds are intentionally conservative.
    # If your intent classifier is weak, you should be in SAFE more often, not less.
    if c < 0.55:
        return ModePolicy(
            name="SAFE",
            max_steps=18,
            max_file_mutations=3,
            max_unique_paths_mutated=2,
            allow_delete_file=False,
            allow_apply_patch=False,
            require_approval_before_pr=True,
            require_verification_for_pr=True,
        )

    if c < 0.80:
        return ModePolicy(
            name="STANDARD",
            max_steps=35,
            max_file_mutations=8,
            max_unique_paths_mutated=6,
            allow_delete_file=False,
            allow_apply_patch=False,
            require_approval_before_pr=True,
            require_verification_for_pr=True,
        )

    return ModePolicy(
        name="AGGRESSIVE",
        max_steps=70,
        max_file_mutations=20,
        max_unique_paths_mutated=15,
        allow_delete_file=True,
        allow_apply_patch=True,
        require_approval_before_pr=False,   # you can flip this to True if you want strict governance always
        require_verification_for_pr=True,
    )
