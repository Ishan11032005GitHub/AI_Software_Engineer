from __future__ import annotations

from typing import Optional

from app.storage.artifact_store import ArtifactStore


def dependency_impact(db_path: str, entry_fn: Optional[str]) -> tuple[int, list[str]]:
    """
    Returns (impacted_count, impacted_files) for callers of entry_fn.
    Python-only best-effort.

    If entry_fn is None/empty => (0, [])
    """
    if not entry_fn:
        return 0, []

    store = ArtifactStore(db_path)
    store.init_db()

    # We index by repo_root; but here we don't know repo_root.
    # Practical approach: allow graph_ranker to just use callee_name match across all repo_roots.
    # Since you run one repo at a time, DB typically contains one repo_root.
    # We'll query by scanning all repo_root values present.
    import sqlite3
    conn = sqlite3.connect(db_path)
    roots = conn.execute("SELECT DISTINCT repo_root FROM py_calls").fetchall()
    conn.close()

    impacted_files = set()
    for (root,) in roots:
        callers = store.list_callers_of(root, entry_fn)
        for caller_file, _caller_func in callers:
            impacted_files.add(caller_file)

    impacted_list = sorted(list(impacted_files))
    return len(impacted_list), impacted_list
