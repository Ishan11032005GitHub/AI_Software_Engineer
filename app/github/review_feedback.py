# app/github/review_feedback.py
from __future__ import annotations

import requests
from dataclasses import dataclass
from typing import List, Optional

from config import GITHUB_TOKEN, GITHUB_API
from app.storage.artifact_store import ArtifactStore


@dataclass
class ReviewNote:
    pr_number: int
    reviewer: str
    state: str
    body: str
    submitted_at: Optional[str] = None


def fetch_pr_reviews(owner: str, repo: str, pr_number: int) -> List[ReviewNote]:
    url = f"{GITHUB_API}/repos/{owner}/{repo}/pulls/{pr_number}/reviews"
    headers = {
        "Authorization": f"token {GITHUB_TOKEN}",
        "Accept": "application/vnd.github+json",
    }
    resp = requests.get(url, headers=headers)
    resp.raise_for_status()
    data = resp.json()

    notes: List[ReviewNote] = []
    for r in data:
        notes.append(
            ReviewNote(
                pr_number=pr_number,
                reviewer=r.get("user", {}).get("login", ""),
                state=r.get("state", ""),
                body=r.get("body") or "",
                submitted_at=r.get("submitted_at"),
            )
        )
    return notes


def sync_reviews_into_memory(
    owner: str,
    repo: str,
    pr_number: int,
    issue_number: int,
    store: ArtifactStore,
):
    """
    Fetch review comments → store as feedback → usable later for re-patch loops.
    This is how Phase-10 (Learning Self-Healing) later learns from humans.
    """

    reviews = fetch_pr_reviews(owner, repo, pr_number)
    if not reviews:
        print("📝 No review feedback found.")
        return

    for r in reviews:
        # Dedupe — avoids duplicate storage when main re-runs
        if store.feedback_exists(owner, repo, pr_number, r.body):
            continue

        store.store_feedback(
            owner=owner,
            repo=repo,
            issue_number=issue_number,
            pr_number=pr_number,
            reviewer=r.reviewer,
            state=r.state,
            body=r.body,
            submitted_at=r.submitted_at,
        )

    print(f"🧠 Stored {len(reviews)} review feedback items → memory OK")
