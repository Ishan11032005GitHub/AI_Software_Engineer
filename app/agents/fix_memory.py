from __future__ import annotations
import difflib
import hashlib
import sqlite3
import json
import datetime
from typing import Optional, List, Tuple, Dict, Any

# We reuse artifact_store DB
class FixMemory:
    def __init__(self, db_path_or_store: Any):
        # Accept either a DB path string or an ArtifactStore-like object with `db_path`
        if hasattr(db_path_or_store, "db_path"):
            self.db = db_path_or_store.db_path
        elif isinstance(db_path_or_store, str):
            self.db = db_path_or_store
        else:
            raise ValueError("FixMemory requires a path or an object with 'db_path' attribute")
        self._init_table()

    def _connect(self):
        conn = sqlite3.connect(self.db)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_table(self):
        with self._connect() as conn:
            # Legacy compact summary table (kept for backwards compat)
            conn.execute("""
            CREATE TABLE IF NOT EXISTS fix_memory (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                error_sig TEXT,
                patch TEXT,
                success_count INTEGER DEFAULT 1,
                created_at TEXT
            )
            """)

            # New detailed records table (stores full before/after + meta)
            conn.execute("""
            CREATE TABLE IF NOT EXISTS fix_memory_records (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                before TEXT,
                after TEXT,
                meta_json TEXT,
                success_count INTEGER DEFAULT 1,
                created_at TEXT
            )
            """)
            conn.commit()

    def _signature(self, text: str) -> str:
        return hashlib.sha1(text.encode()).hexdigest()[:16]

    def store_patch(self, before: str, after: str, error: str):
        """Store diff when PR merged + CI passed (legacy helper)."""
        diff = "\n".join(difflib.unified_diff(before.splitlines(), after.splitlines()))

        sig = self._signature(error)

        with self._connect() as conn:
            cur = conn.execute("SELECT id FROM fix_memory WHERE error_sig=?", (sig,))
            row = cur.fetchone()

            if row:
                conn.execute("UPDATE fix_memory SET success_count=success_count+1 WHERE id=?", (row["id"],))
            else:
                conn.execute("INSERT INTO fix_memory(error_sig, patch, created_at) VALUES(?,?,?)",
                             (sig, diff, datetime.datetime.utcnow().isoformat() + "Z"))
            conn.commit()

    def save_memory(self, before: str, after: str, meta: Optional[Dict[str, Any]] = None) -> None:
        """Save a detailed memory record (called when a PR is created/succeeded)."""
        meta_json = json.dumps(meta or {})
        now = datetime.datetime.utcnow().isoformat() + "Z"
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO fix_memory_records(before, after, meta_json, created_at) VALUES(?,?,?,?)",
                (before, after, meta_json, now),
            )
            conn.commit()

    def retrieve_similar(self, query_text: str, *, limit: int = 3) -> List[Dict[str, str]]:
        """Return up to `limit` prior patches whose `before` text matches the query snippet (best-effort)."""
        if not query_text:
            return []

        snippet = query_text.strip()[:200]
        like = f"%{snippet}%"

        with self._connect() as conn:
            cur = conn.execute(
                "SELECT before, after FROM fix_memory_records WHERE before LIKE ? ORDER BY created_at DESC LIMIT ?",
                (like, limit),
            )
            rows = cur.fetchall()

        return [{"old": r["before"], "new": r["after"]} for r in rows]
