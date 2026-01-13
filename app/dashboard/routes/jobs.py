from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from starlette.requests import Request

from config import SQLITE_PATH
from app.storage.artifact_store import ArtifactStore

router = APIRouter()
templates = Jinja2Templates(directory="app/dashboard/templates")


def get_store() -> ArtifactStore:
    store = ArtifactStore(SQLITE_PATH)
    try:
        store.init_db()
    except Exception:
        pass
    return store

@router.get("/jobs/{job_id}")
def inspect_job(job_id: int):
    job = store.get_agent_job(job_id)
    if not job:
        abort(404)

    proposal = store.get_latest_proposal(
        owner=job["owner"],
        repo=job["repo"]
    )

    files = store.get_files_for_proposal(proposal["id"])

    return render_template(
        "job_detail.html",
        job=job,
        proposal=proposal,
        files=files
    )

@router.get("/dashboard/jobs/{job_id}", response_class=HTMLResponse)
async def job_detail(
    request: Request,
    job_id: int,
    store: ArtifactStore = Depends(get_store),
):
    with store._connect() as conn:
        job = conn.execute(
            "SELECT * FROM agent_jobs WHERE id=?",
            (job_id,),
        ).fetchone()

        if not job:
            raise HTTPException(status_code=404, detail="Job not found")

        events = conn.execute(
            """
            SELECT type, payload, created_at
            FROM job_events
            WHERE job_id=?
            ORDER BY id ASC
            """,
            (job_id,),
        ).fetchall()

    # Convert rows to dicts
    job = dict(job)
    events = [dict(e) for e in events]

    # Extract DIFF event if present
    diff_event = None
    for e in events:
        if e["type"] == "DIFF":
            try:
                diff_event = {
                    "meta": e,
                    "data": __safe_json(e["payload"]),
                }
            except Exception:
                diff_event = {
                    "meta": e,
                    "data": {"diff": e["payload"]},
                }

    return templates.TemplateResponse(
        "job_detail.html",
        {
            "request": request,
            "job": job,
            "events": events,
            "diff_event": diff_event,
        },
    )


def __safe_json(val: str):
    import json
    try:
        return json.loads(val)
    except Exception:
        return {"raw": val}
