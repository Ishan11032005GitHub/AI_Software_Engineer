# app/agents/strict_planner.py
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional


# ----------------- Core Plan Models -----------------

@dataclass
class PlanStep:
    op: str
    args: Dict[str, Any]

    def to_dict(self) -> Dict[str, Any]:
        return {"op": self.op, "args": self.args}


@dataclass
class ExecutionPlan:
    intent: str
    action: str
    steps: List[PlanStep]
    notes: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "intent": self.intent,
            "action": self.action,
            "notes": self.notes,
            "steps": [s.to_dict() for s in self.steps],
        }


# ----------------- Strict Planner -----------------

def build_execution_plan_strict(
    action: str,
    prompt: str,
    intent_obj: Optional[dict],
    repo_facts: Optional[dict],
) -> ExecutionPlan:
    """
    STRICT PLANNER (DUMB BY DESIGN)

    - Builds a plan ONLY
    - Does NOT apply confidence, safety, or policy
    - Never executes
    - Never refuses based on "feelings"
    - audit_plan() decides what is allowed

    This is intentional. DO NOT add logic here.
    """

    intent = intent_obj.get("intent") if isinstance(intent_obj, dict) else "unknown"
    steps: List[PlanStep] = []

    # Always record analysis for observability
    steps.append(PlanStep("ANALYZE_REPO", {"repo_facts": repo_facts or {}}))

    # ----------------- FIX / TEST -----------------

    if action in ("fix_bugs", "run_tests"):
        steps.append(PlanStep("RUN_TESTS_SAFE", {"mode": "pytest"}))
        steps.append(PlanStep("SET_STATUS", {"status": "COMPLETED"}))
        return ExecutionPlan(intent, action, steps)

    # ----------------- REFACTOR -----------------

    if action == "refactor":
        steps.append(PlanStep("FORMAT_BLACK", {"path": ".", "best_effort": True}))
        steps.append(PlanStep("CAPTURE_DIFF", {}))
        steps.append(PlanStep("SET_STATUS", {"status": "COMPLETED"}))
        return ExecutionPlan(intent, action, steps)

    # ----------------- ADD FEATURE -----------------

    if action == "add_feature":
        # DO NOT guess.
        # Let audit_plan decide if this is allowed.
        steps.append(PlanStep("SET_STATUS", {"status": "FAILED"}))
        return ExecutionPlan(
            intent=intent,
            action=action,
            steps=steps,
            notes="Strict planner: add_feature requires audit approval",
        )

    # ----------------- GENERATE PROJECT -----------------

    if action == "generate_project":
        steps.append(PlanStep("UPDATE_README", {"kind": "generated_project", "prompt": prompt}))
        steps.append(PlanStep("CAPTURE_DIFF", {}))
        steps.append(PlanStep("SET_STATUS", {"status": "PROPOSED"}))
        steps.append(PlanStep("WAIT_FOR_APPROVAL", {}))
        steps.append(
            PlanStep(
                "COMMIT_PUSH_PR",
                {"title": "AutoTriage: generate project", "prompt": prompt},
            )
        )
        return ExecutionPlan(intent, action, steps)

    # ----------------- CREATE PR -----------------

    if action == "create_pr":
        steps.append(PlanStep("CAPTURE_DIFF", {}))
        steps.append(PlanStep("SET_STATUS", {"status": "PROPOSED"}))
        steps.append(PlanStep("WAIT_FOR_APPROVAL", {}))
        steps.append(
            PlanStep(
                "COMMIT_PUSH_PR",
                {"title": "AutoTriage: create PR", "prompt": prompt},
            )
        )
        return ExecutionPlan(intent, action, steps)

    # ----------------- FALLBACK -----------------

    steps.append(PlanStep("SET_STATUS", {"status": "FAILED"}))
    return ExecutionPlan(
        intent=intent,
        action=action,
        steps=steps,
        notes="Unsupported action",
    )
