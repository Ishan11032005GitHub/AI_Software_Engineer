from __future__ import annotations

import os
import ast
from typing import Optional

from app.storage.artifact_store import ArtifactStore


SKIP_DIRS = {".git", "venv", ".venv", "__pycache__", "node_modules", "dist", "build", "repos"}


class RepoIndexer:
    """
    Python-only dependency indexer.

    Stores:
      - function defs per file
      - calls from function -> callee name (best-effort)
    """

    def __init__(self, repo_root: str, db_path: str):
        self.repo_root = os.path.normpath(repo_root)
        self.store = ArtifactStore(db_path)

    def index_repo(self) -> None:
        self.store.init_db()

        # fresh index
        self.store.clear_repo_graph(self.repo_root)

        for root, dirs, files in os.walk(self.repo_root):
            dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
            for fname in files:
                if not fname.endswith(".py"):
                    continue
                fpath = os.path.join(root, fname)
                self._index_file(fpath)

    def _index_file(self, file_path: str) -> None:
        try:
            with open(file_path, "r", errors="ignore") as f:
                src = f.read()
            tree = ast.parse(src)
        except Exception:
            return

        # Find function defs
        funcs: dict[str, ast.FunctionDef] = {}
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef):
                funcs[node.name] = node
                self.store.insert_function(self.repo_root, file_path, node.name)

        # For each function: find calls (best-effort)
        for fn_name, fn_node in funcs.items():
            for call in [n for n in ast.walk(fn_node) if isinstance(n, ast.Call)]:
                callee = self._callee_name(call.func)
                if callee:
                    self.store.insert_call(self.repo_root, file_path, fn_name, callee)

    def _callee_name(self, node: ast.AST) -> Optional[str]:
        # foo(...)
        if isinstance(node, ast.Name):
            return node.id

        # obj.foo(...) -> take 'foo' as callee name (best effort)
        if isinstance(node, ast.Attribute):
            return node.attr

        return None
