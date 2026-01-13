# app/context/graph_store.py
import os
import sqlite3
from contextlib import contextmanager
from typing import Iterator


SCHEMA = """
PRAGMA journal_mode=WAL;
PRAGMA synchronous=NORMAL;

CREATE TABLE IF NOT EXISTS meta (
  key TEXT PRIMARY KEY,
  value TEXT
);

CREATE TABLE IF NOT EXISTS files (
  path TEXT PRIMARY KEY,
  lang TEXT NOT NULL,
  sha1 TEXT NOT NULL,
  mtime REAL NOT NULL
);

-- Top-level defs
CREATE TABLE IF NOT EXISTS defs (
  symbol TEXT NOT NULL,
  kind TEXT NOT NULL,           -- 'function' | 'class'
  path TEXT NOT NULL,
  line_start INTEGER,
  line_end INTEGER
);

-- imports: "import x", "from x import y"
CREATE TABLE IF NOT EXISTS imports (
  path TEXT NOT NULL,
  imported TEXT NOT NULL,       -- module/package name
  kind TEXT NOT NULL            -- 'import' | 'from'
);

-- function calls inside a file (best-effort)
CREATE TABLE IF NOT EXISTS calls (
  path TEXT NOT NULL,
  caller TEXT,                  -- function name if resolvable
  callee TEXT NOT NULL          -- callee name (best-effort)
);

-- edges at file-level (import graph)
CREATE TABLE IF NOT EXISTS edges (
  src_path TEXT NOT NULL,
  dst_path TEXT NOT NULL,
  type TEXT NOT NULL            -- 'import'
);

CREATE INDEX IF NOT EXISTS idx_defs_symbol ON defs(symbol);
CREATE INDEX IF NOT EXISTS idx_defs_path ON defs(path);
CREATE INDEX IF NOT EXISTS idx_imports_path ON imports(path);
CREATE INDEX IF NOT EXISTS idx_calls_path ON calls(path);
CREATE INDEX IF NOT EXISTS idx_edges_src ON edges(src_path);
CREATE INDEX IF NOT EXISTS idx_edges_dst ON edges(dst_path);
"""


def ensure_parent_dir(db_path: str) -> None:
    os.makedirs(os.path.dirname(db_path), exist_ok=True)


@contextmanager
def connect(db_path: str) -> Iterator[sqlite3.Connection]:
    ensure_parent_dir(db_path)
    conn = sqlite3.connect(db_path)
    try:
        conn.execute("PRAGMA foreign_keys=OFF;")
        yield conn
    finally:
        conn.close()


def init_db(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA)
    conn.commit()


def wipe_file_entries(conn: sqlite3.Connection, path: str) -> None:
    conn.execute("DELETE FROM defs WHERE path=?", (path,))
    conn.execute("DELETE FROM imports WHERE path=?", (path,))
    conn.execute("DELETE FROM calls WHERE path=?", (path,))
    conn.execute("DELETE FROM edges WHERE src_path=? OR dst_path=?", (path, path))


def upsert_file(conn: sqlite3.Connection, path: str, lang: str, sha1: str, mtime: float) -> None:
    conn.execute(
        """
        INSERT INTO files(path, lang, sha1, mtime) VALUES(?,?,?,?)
        ON CONFLICT(path) DO UPDATE SET sha1=excluded.sha1, mtime=excluded.mtime, lang=excluded.lang
        """,
        (path, lang, sha1, mtime),
    )


def get_file_row(conn: sqlite3.Connection, path: str):
    cur = conn.execute("SELECT path, lang, sha1, mtime FROM files WHERE path=?", (path,))
    return cur.fetchone()


def set_meta(conn: sqlite3.Connection, key: str, value: str) -> None:
    conn.execute(
        """
        INSERT INTO meta(key,value) VALUES(?,?)
        ON CONFLICT(key) DO UPDATE SET value=excluded.value
        """,
        (key, value),
    )


def get_meta(conn: sqlite3.Connection, key: str):
    cur = conn.execute("SELECT value FROM meta WHERE key=?", (key,))
    row = cur.fetchone()
    return row[0] if row else None
