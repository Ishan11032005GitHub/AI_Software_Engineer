from fastapi import APIRouter, Depends
from app.projects.project_service import create_project

router = APIRouter(prefix="/projects")

@router.post("/")
def new_project(name: str, repo_url: str | None = None, user=Depends()):
    return create_project(user["email"], name, repo_url)
