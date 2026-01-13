from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ProposalDecision:
    """
    Central gatekeeper for whether the agent is allowed to write+push code.

    Modes:
      - APPLY: allowed to commit + PR (Tier-2 autonomy)
      - PROPOSE: no commits, no PR. Only artifacts + engineering doc.
      - REJECT: do nothing (optional; you can still store a doc if you want)
    """
    mode: str  # "APPLY" | "PROPOSE" | "REJECT"
    reason: str


def should_enter_proposal_mode(
    *,
    confidence: float,
    changed_files_count: int,
    impacted_files_count: int,
    used_llm: bool,
    touches_sensitive_area: bool,
) -> ProposalDecision:
    """
    Tier-2 autonomy contract:
      - APPLY only if confidence == 1.0 AND single-file AND not sensitive AND (LLM may be used only if confidence==1.0).
      - Otherwise PROPOSE (no commits).
    """

    if changed_files_count > 1:
        return ProposalDecision(
            mode="PROPOSE",
            reason=f"Multi-file change detected (changed_files_count={changed_files_count}). Proposal-only.",
        )

    if impacted_files_count and impacted_files_count > 3:
        return ProposalDecision(
            mode="PROPOSE",
            reason=f"High dependency impact (impacted_files_count={impacted_files_count}). Proposal-only.",
        )

    if touches_sensitive_area and confidence < 1.0:
        return ProposalDecision(
            mode="PROPOSE",
            reason="Touches sensitive area (auth/infra/ml/data). Confidence < 1.0 => proposal-only.",
        )

    if used_llm and confidence < 1.0:
        return ProposalDecision(
            mode="PROPOSE",
            reason="LLM involved and confidence < 1.0 => proposal-only.",
        )

    if confidence >= 1.0:
        return ProposalDecision(mode="APPLY", reason="Confidence == 1.0 and gates passed => apply changes.")
    else:
        return ProposalDecision(mode="PROPOSE", reason=f"Confidence {confidence:.2f} < 1.0 => proposal-only.")
