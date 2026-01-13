# app/agents/agent_runner.py
from __future__ import annotations

import os
import json
import time
import traceback
import requests

from app.core.repo_manager import prepare_repo
from app.agents.repo_intel import analyze_repo
from app.agents.intent_classifier import classify_intent_llm
from app.agents.strict_planner import build_execution_plan_strict
from app.agents.allowed_ops import ALLOWED_OPS
from app.agents.executors import execute_plan
from app.agents.engineering_mode import resolve_engineering_mode
from app.agents.plan_auditor import audit_plan


class JobControl:
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
            if any(e["type"] == typ for e in self.store.get_job_events(self.job_id)):
                return
            time.sleep(1)


def _create_pr(owner: str, repo: str, branch: str, title: str, body: str) -> dict:
    token = os.getenv("GITHUB_TOKEN")
    if not token:
        raise RuntimeError("GITHUB_TOKEN not set")

    r = requests.post(
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

    if r.status_code >= 300:
        raise RuntimeError(f"PR creation failed: {r.status_code} {r.text}")

    return r.json()


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
        job = store.get_job(job_id) or {}

        # ─────────────────────────────────────
        # Repo lifecycle (CRITICAL FIX)
        # ─────────────────────────────────────
        if job.get("status") != "RUNNING":
            store.update_agent_job_status(job_id, "RUNNING")

            # First time ONLY → hard reset
            repo_path = prepare_repo(owner, repo, reset=True)
            store.set_job_repo_path(job_id, repo_path)
        else:
            # Re-entrant calls → DO NOT RESET
            repo_path = job["repo_path"]

        # ─────────────────────────────────────
        # Repo analysis
        # ─────────────────────────────────────
        try:
            facts = analyze_repo(repo_path).to_dict()
        except Exception as e:
            facts = {"error": f"analyze_repo failed: {e}"}

        jc.log("ARCH", facts)

        # ─────────────────────────────────────
        # Intent classification
        # ─────────────────────────────────────
        try:
            intent = classify_intent_llm(
                prompt=prompt,
                repo_path=repo_path,
                action=action,
            )
        except Exception as e:
            intent = {
                "intent": "unknown",
                "confidence": 0.0,
                "subtasks": [],
                "notes": f"intent classification failed: {e}",
            }

        jc.log("INTENT", intent)

        # ─────────────────────────────────────
        # Engineering mode
        # ─────────────────────────────────────
        policy = resolve_engineering_mode(intent)
        jc.log(
            "ENGINEERING_MODE",
            {
                "mode": getattr(policy, "name", str(policy)),
                "confidence": float(intent.get("confidence", 0.0) or 0.0),
            },
        )

        # ─────────────────────────────────────
        # Build + audit plan
        # ─────────────────────────────────────
        raw_plan = build_execution_plan_strict(
            action=action,
            prompt=prompt,
            intent_obj=intent,
            repo_facts=facts,
        )

        jc.log("PLAN_RAW", raw_plan.to_dict())

        plan = audit_plan(raw_plan, policy, facts)
        jc.log("PLAN_V2", plan.to_dict())

        # ─────────────────────────────────────
        # Enforce allowed ops
        # ─────────────────────────────────────
        for s in plan.steps:
            if s.op not in ALLOWED_OPS:
                raise RuntimeError(f"INVALID_OP: {s.op}")

        # ─────────────────────────────────────
        # Execute
        # ─────────────────────────────────────
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

        # ─────────────────────────────────────
        # PR policy (NO fake failures)
        # ─────────────────────────────────────
        if action in ("fix_bugs", "add_feature", "refactor", "create_pr"):
            created = any(
                e["type"] == "PR_CREATED"
                for e in store.get_job_events(job_id)
            )
            if not created:
                jc.log(
                    "LOG",
                    "No PR created — no meaningful code changes were produced.",
                )

        store.update_agent_job_status(job_id, "COMPLETED")
        jc.log("LOG", "Job completed successfully")

    except RuntimeError as e:
        if str(e) == "ABORTED":
            store.update_agent_job_status(job_id, "ABORTED")
            jc.log("LOG", "Job aborted")
            return

        jc.log("ERROR", str(e))
        store.update_agent_job_status(job_id, "FAILED")

    except Exception:
        jc.log("ERROR", traceback.format_exc())
        store.update_agent_job_status(job_id, "FAILED")

def set_job_repo_path(self, job_id: int, path: str):
    self.update_job(job_id, {"repo_path": path})
