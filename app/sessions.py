# app/sessions.py

import sqlite3
from config import SQLITE_PATH

class SessionDB:
    def __init__(self):
        self.conn = sqlite3.connect(SQLITE_PATH, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self._init()

    def _init(self):
        cur = self.conn.cursor()
        cur.execute("""
        CREATE TABLE IF NOT EXISTS sessions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        """)
        self.conn.commit()

    def list_sessions(self):
        cur = self.conn.cursor()
        return cur.execute("SELECT * FROM sessions ORDER BY id DESC").fetchall()

    def create_session(self, name: str):
        cur = self.conn.cursor()
        cur.execute("INSERT INTO sessions (name) VALUES (?)", (name,))
        self.conn.commit()
