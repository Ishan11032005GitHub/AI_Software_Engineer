# routes/agent_routes.py
from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel
from app.storage.artifact_store import ArtifactStore
from config import SQLITE_PATH
from app.agents.agent_runner import run_agent_pipeline
from threading import Thread

router = APIRouter()

class RunAgentRequest(BaseModel):
    owner: str
    repo: str
    action: str
    prompt: str


def get_store() -> ArtifactStore:
    store = ArtifactStore(SQLITE_PATH)
    store.init_db()
    return store


@router.post("/run-agent")
def run_agent(
    request: RunAgentRequest,
    store: ArtifactStore = Depends(get_store),
):
    # Create job FIRST
    job_id = store.create_agent_job(
        owner=request.owner,
        repo=request.repo,
        action=request.action,
        prompt=request.prompt,
    )

    # Run agent in background
    def _worker():
        run_agent_pipeline(
            owner=request.owner,
            repo=request.repo,
            action=request.action,
            prompt=request.prompt,
            job_id=job_id,
            store=store,
        )

    Thread(target=_worker, daemon=True).start()

    return {
        "status": "QUEUED",
        "job_id": job_id,
    }
