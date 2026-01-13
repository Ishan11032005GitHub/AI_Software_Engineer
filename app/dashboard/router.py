# app/dashboard/router.py
from __future__ import annotations

from typing import List, Dict, Any

from fastapi import APIRouter, Depends, Request, HTTPException
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from config import SQLITE_PATH
from app.storage.artifact_store import ArtifactStore
from app.dashboard.routes.jobs import router as jobs_router

router = APIRouter(prefix="/dashboard", tags=["dashboard"])

templates = Jinja2Templates(directory="templates")


def get_store() -> ArtifactStore:
    return ArtifactStore(SQLITE_PATH)


@router.get("/proposals", response_class=HTMLResponse)
async def list_proposals_page(
    request: Request,
    store: ArtifactStore = Depends(get_store),
):
    proposals = store.list_proposals(limit=100)

    # Small convenience: unpack some known meta fields
    for p in proposals:
        meta = p.get("meta") or {}
        p["mode"] = meta.get("mode")
        p["confidence"] = meta.get("confidence")
        p["ci_outcome"] = meta.get("ci_outcome")
        p["multifile"] = meta.get("multifile")

    return templates.TemplateResponse(
        "dashboard/proposals.html",
        {
            "request": request,
            "proposals": proposals,
        },
    )


@router.get("/proposals/{proposal_id}", response_class=HTMLResponse)
async def proposal_detail_page(
    proposal_id: int,
    request: Request,
    store: ArtifactStore = Depends(get_store),
):
    result = store.get_proposal_with_files(proposal_id)
    if not result:
        raise HTTPException(status_code=404, detail="Proposal not found")

    proposal, snapshots = result

    # Convenience unpack
    meta = proposal.get("meta") or {}
    proposal["mode"] = meta.get("mode")
    proposal["confidence"] = meta.get("confidence")
    proposal["ci_outcome"] = meta.get("ci_outcome")
    proposal["multifile"] = meta.get("multifile")

    return templates.TemplateResponse(
        "dashboard/proposal_detail.html",
        {
            "request": request,
            "proposal": proposal,
            "snapshots": snapshots,
        },
    )
