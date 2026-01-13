from __future__ import annotations

import os
import threading
import json
from typing import Optional, Any, Dict

from fastapi import FastAPI, Depends, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from config import SQLITE_PATH
from app.storage.artifact_store import ArtifactStore
from app.dashboard.router import router as dashboard_router
from app.agents.agent_runner import run_agent_pipeline

# ------------------------------
# FastAPI App
# ------------------------------
app = FastAPI(
    title="AutoTriage PR Agent API",
    description="Dashboard + HTTP interface for proposals, artifacts & CI runs",
    version="1.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
    allow_credentials=True,
)

# ------------------------------
# DB Access
# ------------------------------
def get_store() -> ArtifactStore:
    store = ArtifactStore(SQLITE_PATH)
    try:
        store.init_db()
    except Exception:
        pass
    return store


# ------------------------------
# Job tables (additive)
# ------------------------------
def ensure_job_tables(store: ArtifactStore) -> None:
    with store._conn() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS agent_jobs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                owner TEXT NOT NULL,
                repo TEXT NOT NULL,
                action TEXT NOT NULL,
                prompt TEXT DEFAULT '',
                status TEXT NOT NULL DEFAULT 'QUEUED',
                created_at TEXT DEFAULT (datetime('now')),
                updated_at TEXT DEFAULT (datetime('now')),
                pr_number INTEGER,
                pr_url TEXT,
                pr_head TEXT,
                pr_base TEXT DEFAULT 'main',
                blocked_reason TEXT
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS job_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                job_id INTEGER NOT NULL,
                type TEXT NOT NULL,
                payload TEXT DEFAULT '',
                created_at TEXT DEFAULT (datetime('now')),
                FOREIGN KEY(job_id) REFERENCES agent_jobs(id)
            )
            """
        )


def job_append_event(store: ArtifactStore, job_id: int, typ: str, payload: Any = "") -> None:
    if not isinstance(payload, str):
        payload = json.dumps(payload, ensure_ascii=False)
    with store._conn() as conn:
        conn.execute(
            "INSERT INTO job_events(job_id, type, payload) VALUES(?, ?, ?)",
            (job_id, typ, payload),
        )
        conn.execute(
            "UPDATE agent_jobs SET updated_at=datetime('now') WHERE id=?",
            (job_id,),
        )


def job_get(store: ArtifactStore, job_id: int) -> Optional[Dict[str, Any]]:
    with store._conn() as conn:
        row = conn.execute(
            "SELECT * FROM agent_jobs WHERE id=?", (job_id,)
        ).fetchone()
    return dict(row) if row else None


def job_update_status(
    store: ArtifactStore,
    job_id: int,
    status: str,
    blocked_reason: Optional[str] = None,
) -> None:
    with store._conn() as conn:
        conn.execute(
            """
            UPDATE agent_jobs
            SET status=?, blocked_reason=COALESCE(?, blocked_reason),
                updated_at=datetime('now')
            WHERE id=?
            """,
            (status, blocked_reason, job_id),
        )


# ------------------------------
# API ROUTES
# ------------------------------
@app.get("/")
async def root():
    return {"status": "running", "service": "AutoTriage PR Agent API"}


# ------------------------------
# Job Control Plane
# ------------------------------
@app.post("/api/jobs")
async def create_job(payload: Dict[str, Any], store: ArtifactStore = Depends(get_store)):
    ensure_job_tables(store)

    owner = (payload.get("owner") or "").strip()
    repo = (payload.get("repo") or "").strip()
    action = (payload.get("action") or "").strip()
    prompt = (payload.get("prompt") or "").strip()

    if not owner or not repo or not action:
        raise HTTPException(status_code=400, detail="owner, repo, action required")

    with store._conn() as conn:
        cur = conn.execute(
            """
            INSERT INTO agent_jobs(owner, repo, action, prompt, status)
            VALUES (?, ?, ?, ?, 'QUEUED')
            """,
            (owner, repo, action, prompt),
        )
        job_id = cur.lastrowid

    job_append_event(store, job_id, "LOG", "Job created")
    return {"job_id": job_id}


@app.post("/api/jobs/{job_id}/run")
async def run_job(job_id: int, store: ArtifactStore = Depends(get_store)):
    ensure_job_tables(store)
    job = job_get(store, job_id)

    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    if job["status"] not in ("QUEUED", "FAILED", "PAUSED", "BLOCKED", "PROPOSED"):
        return {"started": False, "reason": f"Invalid state {job['status']}"}

    job_update_status(store, job_id, "RUNNING")
    job_append_event(store, job_id, "LOG", "Job started")

    def bg():
        try:
            run_agent_pipeline(
                owner=job["owner"],
                repo=job["repo"],
                action=job["action"],
                prompt=job.get("prompt") or "",
                job_id=job_id,
                store=store,
            )
        except Exception as e:
            job_append_event(store, job_id, "ERROR", str(e))
            job_update_status(store, job_id, "FAILED")

    threading.Thread(target=bg, daemon=True).start()
    return {"started": True, "job_id": job_id}


@app.post("/api/jobs/{job_id}/action")
async def job_action(job_id: int, payload: Dict[str, Any], store: ArtifactStore = Depends(get_store)):
    ensure_job_tables(store)
    job = job_get(store, job_id)

    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    act = (payload.get("action") or "").strip()
    data = payload.get("payload") or {}

    if act == "pause":
        job_append_event(store, job_id, "PAUSE_REQUESTED")
        job_update_status(store, job_id, "PAUSED")
        return {"ok": True}

    if act == "resume":
        job_append_event(store, job_id, "RESUME_REQUESTED")
        job_update_status(store, job_id, "RUNNING")
        return {"ok": True}

    if act == "abort":
        job_append_event(store, job_id, "ABORT_REQUESTED")
        job_update_status(store, job_id, "ABORTED")
        return {"ok": True}

    if act == "retry":
        job_append_event(store, job_id, "RETRY_REQUESTED", data)
        job_update_status(store, job_id, "QUEUED")
        return {"ok": True}

    if act == "provide_input":
        job_append_event(store, job_id, "USER_INPUT", data)
        if job["status"] == "BLOCKED":
            job_update_status(store, job_id, "RUNNING")
        return {"ok": True}

    # 🔥 FIXED: PROPOSED → RUNNING → agent resumes
    if act == "approve":
        job_append_event(store, job_id, "APPROVED")
        if job["status"] in ("PROPOSED", "NEEDS_REVIEW"):
            job_update_status(store, job_id, "RUNNING")
        return {"ok": True}

    raise HTTPException(status_code=400, detail="Invalid action")


# ------------------------------
# ✅ NEW: Detach Repository
# ------------------------------
# ------------------------------
# Detach Repository (FIXED)
# ------------------------------
@app.delete("/api/sessions/{session_id}/repos/{repo_id}")
async def detach_repo(
    session_id: int,
    repo_id: int,
    store: ArtifactStore = Depends(get_store),
):
    with store._conn() as conn:
        cur = conn.execute(
            "DELETE FROM session_repos WHERE id=? AND session_id=?",
            (repo_id, session_id),
        )
        if cur.rowcount == 0:
            raise HTTPException(status_code=404, detail="Repo not found")

    return {"ok": True}


# ------------------------------
# Dashboard Router
# ------------------------------
app.include_router(dashboard_router)

if os.path.exists("static"):
    app.mount("/static", StaticFiles(directory="static"), name="static")


@app.on_event("startup")
async def startup_msg():
    print("\n🎉 API Online → http://localhost:8000\n")
