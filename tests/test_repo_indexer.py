import os
import sqlite3
import tempfile

from app.storage.artifact_store import ArtifactStore
from app.context.repo_indexer import RepoIndexer


def test_repo_indexer_records_functions_and_calls(tmp_path):
    repo_dir = tmp_path / "repo"
    repo_dir.mkdir()

    # create a small python module that defines a function and calls another name
    p = repo_dir / "mod.py"
    p.write_text('''def foo():\n    bar()\n\n''')

    db_path = str(tmp_path / "index.db")
    store = ArtifactStore(db_path)
    store.init_db()

    indexer = RepoIndexer(str(repo_dir), db_path)
    indexer.index_repo()

    conn = sqlite3.connect(db_path)
    cur = conn.cursor()

    cur.execute("SELECT function_name FROM repo_graph_nodes")
    funcs = {r[0] for r in cur.fetchall()}

    cur.execute("SELECT callee_func FROM repo_graph_edges")
    callees = {r[0] for r in cur.fetchall()}

    assert 'foo' in funcs
    assert 'bar' in callees

    conn.close()
