from fastapi import APIRouter
from pydantic import BaseModel
from app.github.api import create_repo_on_github, fetch_user_repos
from app.sessions import SessionDB
from app.agents.agent_runner import run_agent_pipeline

router = APIRouter()
db=SessionDB()

@router.get("/repos")
def load_repos():
    return fetch_user_repos()

class NewRepo(BaseModel):
    repo_name:str

@router.post("/create-repo")
def new_repo(req:NewRepo):
    return create_repo_on_github(req.repo_name)

class NewSession(BaseModel):
    session_name:str
    repo:str

@router.post("/create-session")
def session(req:NewSession):
    sid=db.create_session(req.session_name,req.repo)
    return {"session_id":sid}

@router.get("/session/{sid}")
def get_session(sid:int):
    return db.get_session(sid)

class RunTask(BaseModel):
    session_id:int
    action:str
    prompt:str

@router.post("/run-task")
def run(req:RunTask):
    s=db.get_session(req.session_id)
    result=run_agent_pipeline(
        owner=s["repo"].split("/")[0],
        repo=s["repo"].split("/")[1],
        action=req.action,
        prompt=req.prompt
    )
    db.log_run(req.session_id,req.action,result)
    return {"ok":True}
