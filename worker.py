from __future__ import annotations
import time

from app.storage.artifact_store import ArtifactStore
from config import SQLITE_PATH

# your main AI processing function must be exposed as a python function
from app.agents.agent_runner import run_agent_pipeline      # <= you will create this wrapper

store = ArtifactStore(SQLITE_PATH)
store.init_db()

print("🚨 Worker started – background AI engineer active")


def process_job(job: dict):
    jid = job["id"]
    owner = job["owner"]
    repo = job["repo"]
    action = job["action"]
    prompt = job["prompt"]

    print(f"\n━━━━━━━━━━ JOB #{jid} ━━━━━━━━━━")
    store.update_agent_job_status(jid, "running")

    try:
        # Core agent execution from backend (no CLI)
        result = run_agent_pipeline(
            owner=owner,
            repo=repo,
            action=action,
            prompt=prompt
        )

        store.update_agent_job_status(jid, "completed")
        print(f"✔ Completed Job #{jid}")
        return result

    except Exception as e:
        store.update_agent_job_status(jid, "failed")
        print(f"❌ Job #{jid} FAILED\n{e}")


def loop():
    while True:
        job = store.fetch_next_agent_job()
        if job:
            process_job(job)
        else:
            time.sleep(2)


if __name__ == "__main__":
    loop()
