from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from app.agents.agent_runner import run_agent_pipeline

router = APIRouter()

class RunAgentRequest(BaseModel):
    owner: str
    repo: str
    action: str       # fix_bugs | refactor | add_feature | generate_project | run_tests | create_pr | auto_merge
    prompt: str

@router.post("/run-agent")
def run_agent(request: RunAgentRequest):
    try:
        result = run_agent_pipeline(
            owner=request.owner,
            repo=request.repo,
            action=request.action,
            prompt=request.prompt
        )
        return {"status": "success", "data": result}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
