# app/eval/harness.py
from __future__ import annotations

import subprocess
import time
import yaml
from dataclasses import dataclass
from typing import List

from app.storage.artifact_store import ArtifactStore
from config import SQLITE_PATH


@dataclass
class ScenarioResult:
    scenario_id: str
    issue_number: int
    success: bool
    reason: str


def run_scenario(owner: str, repo: str, issue_number: int) -> None:
    """
    Naive: call your main CLI once per repo.
    For now we just run: python -m app.main OWNER/REPO
    (Your main already loops all issues, which is fine for MVP.)
    """
    cmd = ["python", "-m", "app.main", f"{owner}/{repo}"]
    subprocess.run(cmd, check=False)


def evaluate_scenarios(config_path: str) -> List[ScenarioResult]:
    with open(config_path, "r", encoding="utf-8") as f:
        scenarios = yaml.safe_load(f)

    store = ArtifactStore(SQLITE_PATH)
    store.init_db()

    results: List[ScenarioResult] = []

    for sc in scenarios:
        sc_id = sc["id"]
        owner = sc["owner"]
        repo = sc["repo"]
        issue_number = int(sc["issue"])
        expected_status = sc.get("expected_status", "PR_CREATED")

        print(f"\n▶ Running scenario {sc_id} (#{issue_number})")
        before = time.time()
        run_scenario(owner, repo, issue_number)
        after = time.time()
        elapsed = after - before

        runs = store.get_runs_for_issue(owner, repo, issue_number)
        if not runs:
            results.append(
                ScenarioResult(
                    scenario_id=sc_id,
                    issue_number=issue_number,
                    success=False,
                    reason="no_agent_run",
                )
            )
            continue

        latest = runs[0]
        ok = False
        reason = latest.decision

        if expected_status == "PR_CREATED" and latest.decision in ("APPLY", "PROPOSE"):
            # crude: check whether we logged any branch or PR in meta
            pr_created = bool(latest.meta.get("pr_number"))
            ok = pr_created
            reason = f"pr_created={pr_created}"

        elif expected_status == "PROPOSAL":
            ok = latest.decision == "PROPOSE"

        results.append(
            ScenarioResult(
                scenario_id=sc_id,
                issue_number=issue_number,
                success=ok,
                reason=f"{reason}, time={elapsed:.1f}s",
            )
        )

    return results


def print_summary(results: List[ScenarioResult]) -> None:
    total = len(results)
    passed = sum(1 for r in results if r.success)
    print("\n===== OFFLINE BENCHMARK SUMMARY =====")
    print(f"Total scenarios: {total}")
    print(f"Passed:         {passed}")
    print(f"Failed:         {total - passed}")
    for r in results:
        status = "✅" if r.success else "❌"
        print(f"{status} {r.scenario_id} (#{r.issue_number}) → {r.reason}")
