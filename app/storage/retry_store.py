from __future__ import annotations
import sqlite3

class RetryStore:
    def __init__(self, db_path):
        self.db_path = db_path
        self._init()

    def _connect(self):
        return sqlite3.connect(self.db_path)

    def _init(self):
        with self._connect() as c:
            c.execute("""
            CREATE TABLE IF NOT EXISTS retries (
                owner TEXT, repo TEXT, issue INTEGER,
                attempts INTEGER,
                last_outcome TEXT,
                active INTEGER DEFAULT 0,
                PRIMARY KEY(owner,repo,issue)
            )
            """)
            c.commit()

    def get(self, o,r,i):
        with self._connect() as c:
            q=c.execute("SELECT attempts,last_outcome,active FROM retries WHERE owner=? AND repo=? AND issue=?",(o,r,i))
            row=q.fetchone()
            if not row: return {"attempts":0,"active":0,"last_outcome":None}
            return {"attempts":row[0],"last_outcome":row[1],"active":row[2]}

    def save(self,o,r,i,attempts,last_outcome,active):
        with self._connect() as c:
            c.execute("""
            INSERT INTO retries(owner,repo,issue,attempts,last_outcome,active)
            VALUES(?,?,?,?,?,?)
            ON CONFLICT(owner,repo,issue)
            DO UPDATE SET attempts=?, last_outcome=?, active=?
            """,(o,r,i,attempts,last_outcome,active,attempts,last_outcome,active))
            c.commit()
