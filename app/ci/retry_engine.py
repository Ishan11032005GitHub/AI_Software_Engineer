# app/ci/retry_engine.py

"""
Minimal Step-9 Retry Engine Implementation
Works until advanced CI-aware logic is added later.

Main Job:
 - classify CI outcome
 - decide if agent should retry PR
"""

from dataclasses import dataclass
from typing import Optional


# ------------------------------
# CI Outcome Classification
# ------------------------------

@dataclass
class CIOutcome:
    category: str          # "infra", "flaky", "legit", "unknown"
    failing_files: list
    failing_tests: list
    message_excerpt: str


def classify_ci_outcome(ci_evidence) -> CIOutcome:
    """
    Convert parsed CI results into a standard structure.
    Rule-based and intentionally simple at first.
    """

    files = ci_evidence.failing_files_ranked or []
    tests = ci_evidence.failing_tests_ranked or []
    exc = ci_evidence.excerpt or ""

    msg = exc.lower()

    # --- Heuristic classification rules ---
    if any(x in msg for x in ["timeout", "network", "infra", "runner", "cache fail"]):
        cat = "infra"
    elif any(x in msg for x in ["flake", "flaky", "random"]):
        cat = "flaky"
    elif len(files) == 0 and len(tests) > 0:
        cat = "unit_fail"
    elif len(files) > 0:
        cat = "legit"
    else:
        cat = "unknown"

    return CIOutcome(
        category=cat,
        failing_files=files,
        failing_tests=tests,
        message_excerpt=exc[:500],
    )


# ------------------------------
# Retry Decision Policy
# ------------------------------

@dataclass
class RetryDecision:
    should_retry: bool
    reason: str
    backoff_seconds: int = 300  # default 5min backoff for retry-poller


def should_retry_from_ci(outcome: Optional[CIOutcome], previous_attempts: int) -> RetryDecision:
    """
    Primitive retry strategy:
        Infra/flaky failure → Retry up to 3 times
        Legit unit failures → Do NOT auto-retry (needs fix first)
        Unknown → Retry only once
    """

    if outcome is None:
        return RetryDecision(False, "No CI evidence")

    cat = outcome.category

    # retry-friendly failure types
    if cat in ("infra", "flaky"):
        if previous_attempts < 3:
            return RetryDecision(True, f"Retry allowed for {cat}", 300)
        return RetryDecision(False, f"Retry limit reached for {cat}")

    # legit failing tests mean patch likely wrong
    if cat == "legit":
        return RetryDecision(False, "Failure appears real → no auto-retry")

    # unknown category
    if previous_attempts == 0:
        return RetryDecision(True, "Uncertain → One retry allowed")
    return RetryDecision(False, "Unknown + retries already done")

