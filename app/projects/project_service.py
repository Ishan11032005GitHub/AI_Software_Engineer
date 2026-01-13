from app.database import db
from datetime import datetime

def create_project(user_email: str, name: str, repo_url: str | None):
    project = {
        "name": name,
        "repo_url": repo_url,
        "owner": user_email,
        "created_at": datetime.utcnow()
    }
    return db.projects.insert_one(project).inserted_id
