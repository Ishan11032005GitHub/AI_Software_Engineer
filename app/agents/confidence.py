from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ConfidenceInputs:
    used_stack_trace: bool
    stack_trace_function_resolved: bool
    changed_files_count: int
    impacted_files_count: int
    ast_verified: bool
    safety_verified: bool
    used_llm: bool
    used_rule_based: bool
    file_lines: int


def compute_confidence(x: ConfidenceInputs) -> float:
    """
    Conservative heuristic.

    Tier-2 target:
      - 1.0 only when:
          stack trace used (or very strong evidence),
          single file,
          low/no impact,
          ast+safety pass,
          no LLM or LLM but still extremely constrained,
          small file.

    Output: 0.0 .. 1.0
    """
    score = 0.0

    # Strong evidence
    if x.used_stack_trace:
        score += 0.30
    else:
        score += 0.10  # keyword search is weaker

    if x.stack_trace_function_resolved:
        score += 0.10

    # Safety + AST are huge
    if x.ast_verified:
        score += 0.20
    if x.safety_verified:
        score += 0.20

    # Fix type
    if x.used_rule_based:
        score += 0.15
    if x.used_llm:
        score -= 0.15  # LLM adds uncertainty

    # Blast radius
    if x.changed_files_count == 1:
        score += 0.05
    else:
        score -= 0.30

    if x.impacted_files_count == 0:
        score += 0.05
    elif x.impacted_files_count <= 3:
        score += 0.00
    else:
        score -= 0.20

    # File size penalty
    if x.file_lines <= 300:
        score += 0.05
    elif x.file_lines <= 800:
        score += 0.00
    else:
        score -= 0.10

    # Clamp
    if score < 0.0:
        score = 0.0
    if score > 1.0:
        score = 1.0

    # Make 1.0 truly strict
    # Only allow perfect when all gates match
    perfect = (
        x.used_stack_trace
        and x.changed_files_count == 1
        and (x.impacted_files_count == 0)
        and x.ast_verified
        and x.safety_verified
        and x.used_rule_based
        and (not x.used_llm)
        and x.file_lines <= 300
    )
    return 1.0 if perfect else score
