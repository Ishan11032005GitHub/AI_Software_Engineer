import os
import sqlite3
import tempfile

from app.storage.artifact_store import ArtifactStore


def test_init_creates_graph_tables(tmp_path):
    db_path = str(tmp_path / "test.db")
    store = ArtifactStore(db_path)
    store.init_db()

    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    cur.execute("SELECT name FROM sqlite_master WHERE type='table'")
    tables = {row[0] for row in cur.fetchall()}

    assert 'repo_graph_nodes' in tables
    assert 'repo_graph_edges' in tables
    assert 'agent_runs' in tables
    assert 'proposals' in tables

    conn.close()
