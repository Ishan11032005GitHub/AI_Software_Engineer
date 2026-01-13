import time
from app.storage.artifact_store import ArtifactStore
from app.agents.agent_runner import run_agent_pipeline

store = ArtifactStore("database.sqlite")  # same SQLITE_PATH

def worker_loop():
    print("⚙ Worker started, waiting for jobs...")
    while True:
        job = store.fetch_next_agent_job()
        if not job:
            time.sleep(3)
            continue

        job_id = job["id"]
        owner = job["owner"]
        repo  = job["repo"]
        action = job["action"]
        prompt = job["prompt"]

        print(f"\n🚀 Running Job #{job_id} | {owner}/{repo} | {action}")

        store.update_agent_job_status(job_id, "running")

        try:
            result = run_agent_pipeline(owner, repo, action, prompt)

            if "pr_url" in result:
                status = "completed"
            else:
                status = "no_change"

            store.update_agent_job_status(job_id, status)

        except Exception as e:
            print(f"❌ Job failed: {e}")
            store.update_agent_job_status(job_id, "failed")

        time.sleep(2)

if __name__ == "__main__":
    worker_loop()
