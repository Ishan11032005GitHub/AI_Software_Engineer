from __future__ import annotations

import sqlite3
import json
import time
import datetime
from werkzeug.security import generate_password_hash, check_password_hash


def now() -> str:
    return datetime.datetime.utcnow().replace(microsecond=0).isoformat() + "Z"


class ArtifactStore:
    def __init__(self, db_path: str):
        self.db_path = db_path

    def _connect(self):
        conn = sqlite3.connect(self.db_path, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        return conn

    # ----------------- INIT + MIGRATION -----------------

    def init_db(self):
        with self._connect() as db:
            db.executescript(
                """
                CREATE TABLE IF NOT EXISTS users(
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    username TEXT UNIQUE,
                    password_hash TEXT,
                    created_ts INT
                );

                CREATE TABLE IF NOT EXISTS sessions(
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user TEXT,
                    name TEXT,
                    created_at TEXT
                );

                CREATE TABLE IF NOT EXISTS session_repos(
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_id INT,
                    owner TEXT,
                    repo TEXT,
                    local_path TEXT,
                    created_at TEXT
                );

                CREATE TABLE IF NOT EXISTS agent_jobs(
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_id INT,
                    owner TEXT,
                    repo TEXT,
                    action TEXT,
                    prompt TEXT,
                    status TEXT,
                    blocked_reason TEXT,
                    pr_number INT,
                    pr_url TEXT,
                    pr_head TEXT,
                    pr_base TEXT,
                    result_json TEXT,
                    created_at TEXT,
                    updated_at TEXT
                );

                CREATE TABLE IF NOT EXISTS job_events(
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    job_id INT,
                    type TEXT,
                    payload TEXT,
                    created_at TEXT
                );
                """
            )

            # 🔒 FIX 1 — schema migration for old DBs
            cols = [
                r["name"]
                for r in db.execute("PRAGMA table_info(agent_jobs)")
            ]
            if "blocked_reason" not in cols:
                db.execute("ALTER TABLE agent_jobs ADD COLUMN blocked_reason TEXT")

            # 🔒 FIX 3 — clean up zombie RUNNING jobs
            db.execute(
                """
                UPDATE agent_jobs
                SET status='FAILED', updated_at=?
                WHERE status='RUNNING'
                """,
                (now(),),
            )

            db.commit()

    # ----------------- AUTH -----------------

    def create_user(self, username: str, password: str):
        with self._connect() as db:
            db.execute(
                "INSERT INTO users(username,password_hash,created_ts) VALUES(?,?,?)",
                (username, generate_password_hash(password), int(time.time())),
            )
            db.commit()

    def validate_user(self, username: str, password: str) -> bool:
        with self._connect() as db:
            row = db.execute(
                "SELECT password_hash FROM users WHERE username=?",
                (username,),
            ).fetchone()
        return check_password_hash(row["password_hash"], password) if row else False

    # ----------------- SESSIONS -----------------

    def create_session(self, user: str, name: str) -> int:
        with self._connect() as db:
            cur = db.execute(
                "INSERT INTO sessions(user,name,created_at) VALUES(?,?,?)",
                (user, name, now()),
            )
            db.commit()
            return cur.lastrowid

    def get_sessions(self, user: str):
        with self._connect() as db:
            return [dict(r) for r in db.execute(
                "SELECT * FROM sessions WHERE user=? ORDER BY id DESC",
                (user,),
            )]

    def get_session(self, sid: int):
        with self._connect() as db:
            row = db.execute(
                "SELECT * FROM sessions WHERE id=?",
                (sid,),
            ).fetchone()
        return dict(row) if row else None

    # ----------------- REPOS -----------------

    def attach_repo(self, sid: int, owner: str, repo: str, path: str):
        with self._connect() as db:
            db.execute(
                """
                INSERT INTO session_repos
                (session_id,owner,repo,local_path,created_at)
                VALUES(?,?,?,?,?)
                """,
                (sid, owner, repo, path, now()),
            )
            db.commit()

    def get_repos_for_session(self, sid: int):
        with self._connect() as db:
            return [dict(r) for r in db.execute(
                "SELECT * FROM session_repos WHERE session_id=?",
                (sid,),
            )]

    # ----------------- JOBS -----------------

    def enqueue_agent_job(self, session_id, owner, repo, action, prompt) -> int:
        with self._connect() as db:
            cur = db.execute(
                """
                INSERT INTO agent_jobs
                (session_id,owner,repo,action,prompt,status,created_at,updated_at)
                VALUES(?,?,?,?,?,'QUEUED',?,?)
                """,
                (session_id, owner, repo, action, prompt, now(), now()),
            )
            db.commit()
            jid = cur.lastrowid

        self.append_job_event(jid, "QUEUED", "{}")
        return jid

    def fetch_next_agent_job(self):
        with self._connect() as db:
            row = db.execute(
                """
                SELECT * FROM agent_jobs
                WHERE status='QUEUED'
                ORDER BY created_at ASC
                LIMIT 1
                """
            ).fetchone()
        return dict(row) if row else None

    def update_agent_job_status(self, jid: int, status: str):
        with self._connect() as db:
            db.execute(
                "UPDATE agent_jobs SET status=?, updated_at=? WHERE id=?",
                (status, now(), jid),
            )
            db.commit()

        self.append_job_event(jid, "STATUS", json.dumps({"status": status}))

    def get_job(self, jid: int):
        with self._connect() as db:
            row = db.execute(
                "SELECT * FROM agent_jobs WHERE id=?",
                (jid,),
            ).fetchone()
        return dict(row) if row else None

    def get_agent_jobs_for_session(self, sid: int):
        with self._connect() as db:
            return [dict(r) for r in db.execute(
                "SELECT * FROM agent_jobs WHERE session_id=? ORDER BY created_at DESC",
                (sid,),
            )]

    def get_jobs(self, limit: int = 20):
        with self._connect() as db:
            return [dict(r) for r in db.execute(
                "SELECT * FROM agent_jobs ORDER BY created_at DESC LIMIT ?",
                (limit,),
            )]

    # ----------------- EVENTS -----------------

    def append_job_event(self, jid: int, typ: str, payload: str):
        with self._connect() as db:
            db.execute(
                "INSERT INTO job_events(job_id,type,payload,created_at) VALUES(?,?,?,?)",
                (jid, typ, payload, now()),
            )
            db.commit()

    def get_job_events(self, jid: int):
        with self._connect() as db:
            return [dict(r) for r in db.execute(
                "SELECT * FROM job_events WHERE job_id=? ORDER BY created_at",
                (jid,),
            )]

    # ----------------- STATS -----------------

    def get_dashboard_stats(self):
        with self._connect() as db:
            return {
                "jobs": db.execute("SELECT COUNT(*) FROM agent_jobs").fetchone()[0],
                "sessions": db.execute("SELECT COUNT(*) FROM sessions").fetchone()[0],
            }
