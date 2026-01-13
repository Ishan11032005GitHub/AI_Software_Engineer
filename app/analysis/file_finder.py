from __future__ import annotations

import os

SKIP_DIRS = {".git", "venv", ".venv", "__pycache__", "node_modules", "dist", "build", "repos"}


def search_repo(keywords, repo_path, early_stop_hits=3):
    if not repo_path or not os.path.isdir(repo_path):
        return None

    keywords = [k.strip().lower() for k in (keywords or []) if k and k.strip()]
    if not keywords:
        return None

    candidates = {}

    for root, dirs, files in os.walk(repo_path):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS]

        for fname in files:
            if not fname.endswith((".py", ".js", ".ts", ".java")):
                continue

            path = os.path.join(root, fname)
            try:
                with open(path, "r", errors="ignore") as f:
                    text = f.read(3000)

                low = text.lower()
                hits = sum(1 for kw in keywords if kw in low)
                if hits:
                    candidates[path] = hits
                    if hits >= early_stop_hits:
                        return path
            except Exception:
                continue

    return max(candidates, key=candidates.get) if candidates else None
