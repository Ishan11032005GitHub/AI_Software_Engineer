# app/agents/plan_auditor.py
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Set, Tuple

from app.agents.strict_planner import ExecutionPlan, PlanStep
from app.agents.engineering_mode import ModePolicy


FILE_MUTATION_OPS: Set[str] = {
    "CREATE_FILE",
    "EDIT_FILE",
    "APPLY_PATCH",
    "DELETE_FILE",
    "APPEND_FILE",
}

CONTROL_OPS: Set[str] = {
    "SET_STATUS",
    "WAIT_FOR_APPROVAL",
    "COMMIT_PUSH_PR",
}

VERIFY_OPS: Set[str] = {
    "VERIFY_CMD",
    "VERIFY_FILE_EXISTS",
    "VERIFY_HTTP_ENDPOINT",
}

READ_OPS: Set[str] = {"ANALYZE_REPO", "CAPTURE_DIFF", "RUN_TESTS_SAFE", "FORMAT_BLACK", "UPDATE_README", "ADD_ENV_EXAMPLE", "SCAFFOLD_NODE_BACKEND"}


@dataclass
class AuditResult:
    ok: bool
    reason: str
    plan: ExecutionPlan


def _plan_has_op(plan: ExecutionPlan, op: str) -> bool:
    return any(s.op == op for s in plan.steps)


def _collect_mutation_paths(plan: ExecutionPlan) -> Tuple[int, Set[str]]:
    """
    Returns:
      - mutation_step_count
      - unique_paths_mutated
    """
    count = 0
    paths: Set[str] = set()

    for s in plan.steps:
        if s.op not in FILE_MUTATION_OPS:
            continue
        count += 1
        path = (s.args or {}).get("path")
        if isinstance(path, str) and path.strip():
            paths.add(path.strip())
        # APPLY_PATCH doesn't have a single file path, so we count it but cannot add a path reliably.

    return count, paths


def _ensure_pr_approval_gate(plan: ExecutionPlan) -> ExecutionPlan:
    """
    Ensures the plan is in the standard governance shape:
      ... CAPTURE_DIFF
      SET_STATUS(PROPOSED)
      WAIT_FOR_APPROVAL
      COMMIT_PUSH_PR
    without removing any other behavior.

    If COMMIT_PUSH_PR exists but WAIT_FOR_APPROVAL doesn't, inject it just before COMMIT_PUSH_PR.
    Also ensure SET_STATUS(PROPOSED) exists before WAIT_FOR_APPROVAL.
    """
    if not _plan_has_op(plan, "COMMIT_PUSH_PR"):
        return plan

    steps = list(plan.steps)

    # Find first COMMIT_PUSH_PR index
    pr_idx = next((i for i, s in enumerate(steps) if s.op == "COMMIT_PUSH_PR"), None)
    if pr_idx is None:
        return plan

    has_wait = any(s.op == "WAIT_FOR_APPROVAL" for s in steps[: pr_idx + 1])
    if has_wait:
        # Still ensure SET_STATUS(PROPOSED) exists before the first WAIT_FOR_APPROVAL
        wait_idx = next((i for i, s in enumerate(steps) if s.op == "WAIT_FOR_APPROVAL"), None)
        if wait_idx is not None:
            has_proposed_before = any(
                s.op == "SET_STATUS" and str((s.args or {}).get("status")) == "PROPOSED"
                for s in steps[:wait_idx]
            )
            if not has_proposed_before:
                steps.insert(wait_idx, PlanStep("SET_STATUS", {"status": "PROPOSED"}))
        return ExecutionPlan(intent=plan.intent, action=plan.action, steps=steps, notes=plan.notes)

    # Inject WAIT + PROPOSED right before PR
    # Also insert CAPTURE_DIFF before gating if missing (harmless and useful)
    insert: List[PlanStep] = []
    if not _plan_has_op(plan, "CAPTURE_DIFF"):
        insert.append(PlanStep("CAPTURE_DIFF", {}))

    insert.append(PlanStep("SET_STATUS", {"status": "PROPOSED"}))
    insert.append(PlanStep("WAIT_FOR_APPROVAL", {}))

    steps = steps[:pr_idx] + insert + steps[pr_idx:]
    return ExecutionPlan(intent=plan.intent, action=plan.action, steps=steps, notes=plan.notes)


def _force_fail(plan: ExecutionPlan, reason: str) -> AuditResult:
    """
    Force plan to fail loudly, without pretending work was done.
    Executor will set FAILED.
    """
    steps = [
        # Keep analyze first if present (useful in logs)
        plan.steps[0] if plan.steps and plan.steps[0].op == "ANALYZE_REPO" else PlanStep("ANALYZE_REPO", {"repo_facts": {}}),
        PlanStep("SET_STATUS", {"status": "FAILED"}),
    ]
    notes = (plan.notes or "").strip()
    if notes:
        notes = f"{notes} | AUDIT_FAIL: {reason}"
    else:
        notes = f"AUDIT_FAIL: {reason}"

    failed_plan = ExecutionPlan(intent=plan.intent, action=plan.action, steps=steps, notes=notes)
    return AuditResult(ok=False, reason=reason, plan=failed_plan)


def _strip_disallowed_ops(plan: ExecutionPlan, disallowed: Set[str]) -> Tuple[ExecutionPlan, List[str]]:
    """
    Remove disallowed ops if they appear. This is a safety rewrite.
    Returns new plan and list of removed ops messages.
    """
    removed: List[str] = []
    new_steps: List[PlanStep] = []

    for s in plan.steps:
        if s.op in disallowed:
            removed.append(s.op)
            continue
        new_steps.append(s)

    rewritten = ExecutionPlan(intent=plan.intent, action=plan.action, steps=new_steps, notes=plan.notes)
    return rewritten, removed


def _audit_scope(plan: ExecutionPlan, policy: ModePolicy) -> Optional[str]:
    if len(plan.steps) > policy.max_steps:
        return f"plan too large: steps={len(plan.steps)} > {policy.max_steps}"

    mutation_count, paths = _collect_mutation_paths(plan)
    if mutation_count > policy.max_file_mutations:
        return f"too many file mutations: mutations={mutation_count} > {policy.max_file_mutations}"

    if len(paths) > policy.max_unique_paths_mutated:
        return f"too many unique files touched: unique_paths={len(paths)} > {policy.max_unique_paths_mutated}"

    return None


def _audit_permissions(plan: ExecutionPlan, policy: ModePolicy) -> Optional[str]:
    if (not policy.allow_delete_file) and _plan_has_op(plan, "DELETE_FILE"):
        return "DELETE_FILE not allowed in this mode"
    if (not policy.allow_apply_patch) and _plan_has_op(plan, "APPLY_PATCH"):
        return "APPLY_PATCH not allowed in this mode"
    return None


def _audit_pr_governance(plan: ExecutionPlan, policy: ModePolicy) -> ExecutionPlan:
    if policy.require_approval_before_pr:
        return _ensure_pr_approval_gate(plan)
    return plan


def audit_plan(plan: ExecutionPlan, policy: ModePolicy, repo_facts: Optional[Dict[str, Any]] = None) -> ExecutionPlan:
    """
    Audit + rewrite plan to satisfy ENGINEERING_MODE policy.

    Strategy:
    1) Fail-fast on gross scope violations (suspicious / runaway plans)
    2) If disallowed ops exist:
       - In SAFE/STANDARD: try to strip them ONLY if plan still contains meaningful work
       - Otherwise fail loudly
    3) Ensure PR approval gate if required
    4) Re-check scope after rewrites
    """
    # 1) Scope
    scope_err = _audit_scope(plan, policy)
    if scope_err:
        return _force_fail(plan, scope_err).plan

    # 2) Permissions
    perm_err = _audit_permissions(plan, policy)
    if perm_err:
        # Try rewrite for SAFE/STANDARD: strip disallowed ops.
        disallowed: Set[str] = set()
        if not policy.allow_delete_file:
            disallowed.add("DELETE_FILE")
        if not policy.allow_apply_patch:
            disallowed.add("APPLY_PATCH")

        rewritten, removed = _strip_disallowed_ops(plan, disallowed)

        # If we removed anything but now the plan is basically empty, fail.
        meaningful = any(s.op not in ("ANALYZE_REPO", "SET_STATUS") for s in rewritten.steps)
        if (not meaningful) or (len(rewritten.steps) <= 2):
            return _force_fail(plan, f"{perm_err}; removed={removed} left no meaningful steps").plan

        # After stripping, add a note (keep existing depth; do not change functionality)
        note = rewritten.notes or ""
        suffix = f" | AUDIT_REWRITE: stripped={removed} mode={policy.name}"
        rewritten = ExecutionPlan(
            intent=rewritten.intent,
            action=rewritten.action,
            steps=rewritten.steps,
            notes=(note + suffix).strip(),
        )

        plan = rewritten

    # 3) PR governance
    plan = _audit_pr_governance(plan, policy)

    # 4) Re-check scope after governance injection
    scope_err2 = _audit_scope(plan, policy)
    if scope_err2:
        return _force_fail(plan, f"post-rewrite: {scope_err2}").plan

    # Optional: verification expectations (best-effort)
    # We DO NOT invent verify commands. We only ensure approval gating is present when policy requires it.
    if policy.require_verification_for_pr and _plan_has_op(plan, "COMMIT_PUSH_PR"):
        has_verify = any(s.op in VERIFY_OPS for s in plan.steps)
        if not has_verify and policy.require_approval_before_pr:
            # Already gated; that's acceptable. Don't fail, don't invent.
            note = plan.notes or ""
            plan = ExecutionPlan(
                intent=plan.intent,
                action=plan.action,
                steps=plan.steps,
                notes=(note + " | AUDIT_NOTE: no VERIFY ops present; relying on approval gate").strip(),
            )

    return plan
