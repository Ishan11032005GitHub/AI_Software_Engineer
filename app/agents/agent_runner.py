# app/agents/agent_runner.py
from __future__ import annotations

import os
import time
import json
import traceback
import requests

from app.core.repo_manager import prepare_repo
from app.agents.utils import run_cmd, git_has_changes
from app.agents.repo_intel import analyze_repo
from app.agents.intent_classifier import classify_intent_llm
from app.agents.strict_planner import build_execution_plan_strict
from app.agents.allowed_ops import ALLOWED_OPS
from app.agents.executors import execute_plan
from app.agents.engineering_mode import resolve_engineering_mode
from app.agents.plan_auditor import audit_plan


class JobControl:
    """
    Single authority over job lifecycle inside agent.
    Worker must NEVER guess status after calling pipeline.
    """

    def __init__(self, store, job_id: int):
        self.store = store
        self.job_id = job_id

    def log(self, typ: str, payload):
        if not isinstance(payload, str):
            payload = json.dumps(payload, ensure_ascii=False)
        self.store.append_job_event(self.job_id, typ, payload)

    def status(self) -> str:
        j = self.store.get_job(self.job_id) or {}
        return j.get("status") or ""

    def aborted(self) -> bool:
        return self.status() == "ABORTED"

    def wait_for_event(self, typ: str):
        while True:
            if self.aborted():
                raise RuntimeError("ABORTED")

            events = self.store.get_job_events(self.job_id)
            if any(e["type"] == typ for e in events):
                return

            time.sleep(1)


def _create_pr(owner: str, repo: str, branch: str, title: str, body: str) -> dict:
    token = os.getenv("GITHUB_TOKEN")
    if not token:
        raise RuntimeError("GITHUB_TOKEN not set")

    resp = requests.post(
        f"https://api.github.com/repos/{owner}/{repo}/pulls",
        headers={
            "Authorization": f"token {token}",
            "Accept": "application/vnd.github+json",
        },
        json={
            "title": title,
            "head": branch,
            "base": "main",
            "body": body,
        },
        timeout=30,
    )

    if resp.status_code >= 300:
        raise RuntimeError(f"GitHub PR failed: {resp.status_code} {resp.text}")

    return resp.json()


def run_agent_pipeline(
    owner: str,
    repo: str,
    action: str,
    prompt: str,
    job_id: int,
    store,
):
    jc = JobControl(store, job_id)

    try:
        # ------------------------------------------------------------
        # Repo preparation + intelligence
        # ------------------------------------------------------------
        repo_path = prepare_repo(owner, repo)

        try:
            intel = analyze_repo(repo_path)
            repo_facts = intel.to_dict()
        except Exception as e:
            repo_facts = {"error": f"analyze_repo failed: {e}"}

        jc.log("ARCH", repo_facts)

        # ------------------------------------------------------------
        # STEP 1: Intent classification
        # ------------------------------------------------------------
        try:
            intent_obj = classify_intent_llm(
                prompt=prompt,
                repo_path=repo_path,
                action=action,
            )
        except Exception as e:
            intent_obj = {
                "intent": "unknown",
                "confidence": 0.0,
                "subtasks": [],
                "notes": f"intent classification failed: {e}",
            }

        jc.log("INTENT", intent_obj)

        # ------------------------------------------------------------
        # ENGINEERING MODE (confidence-driven)
        # ------------------------------------------------------------
        policy = resolve_engineering_mode(intent_obj)
        jc.log(
            "ENGINEERING_MODE",
            {
                "mode": policy.name,
                "confidence": intent_obj.get("confidence", 0.0),
            },
        )

        # ------------------------------------------------------------
        # STEP 2: Build strict plan
        # ------------------------------------------------------------
        raw_plan = build_execution_plan_strict(
            action=action,
            prompt=prompt,
            intent_obj=intent_obj,
            repo_facts=repo_facts,
        )

        jc.log("PLAN_RAW", raw_plan.to_dict())

        # ------------------------------------------------------------
        # STEP 3: Audit plan against ENGINEERING_MODE
        # ------------------------------------------------------------
        plan = audit_plan(raw_plan, policy, repo_facts)

        jc.log("PLAN_V2", plan.to_dict())

        # ------------------------------------------------------------
        # HARD ENFORCEMENT: no invented ops
        # ------------------------------------------------------------
        for step in plan.steps:
            if step.op not in ALLOWED_OPS:
                raise RuntimeError(f"STRICT_PLANNER_VIOLATION: {step.op}")

        # ------------------------------------------------------------
        # Execute plan (Steps 4–8 live inside executors)
        # ------------------------------------------------------------
        execute_plan(
            plan,
            owner=owner,
            repo=repo,
            repo_path=repo_path,
            job_id=job_id,
            store=store,
            jc=jc,
            create_pr_fn=_create_pr,
            action=action,
            prompt=prompt,
        )

    except RuntimeError as e:
        if str(e) == "ABORTED":
            store.update_agent_job_status(job_id, "ABORTED")
            jc.log("LOG", "Job aborted by user")
            return

        jc.log("ERROR", str(e))
        store.update_agent_job_status(job_id, "FAILED")

    except Exception:
        jc.log("ERROR", traceback.format_exc())
        store.update_agent_job_status(job_id, "FAILED")
