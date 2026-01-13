# app/context/py_indexer.py
import ast
import hashlib
import os
from dataclasses import dataclass
from typing import Iterable, List, Optional, Tuple

from app.context.graph_store import (
    connect,
    init_db,
    wipe_file_entries,
    upsert_file,
    get_file_row,
)

SKIP_DIRS = {".git", "venv", ".venv", "__pycache__", "node_modules", "dist", "build", ".mypy_cache", ".pytest_cache"}


@dataclass
class PyFileIndex:
    defs: List[Tuple[str, str, str, Optional[int], Optional[int]]]      # (symbol, kind, path, start, end)
    imports: List[Tuple[str, str, str]]                                 # (path, imported, kind)
    calls: List[Tuple[str, Optional[str], str]]                         # (path, caller, callee)


class _CallVisitor(ast.NodeVisitor):
    def __init__(self):
        self.calls = []
        self.current_fn = None

    def visit_FunctionDef(self, node: ast.FunctionDef):
        prev = self.current_fn
        self.current_fn = node.name
        self.generic_visit(node)
        self.current_fn = prev

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef):
        prev = self.current_fn
        self.current_fn = node.name
        self.generic_visit(node)
        self.current_fn = prev

    def visit_Call(self, node: ast.Call):
        name = _callee_name(node.func)
        if name:
            self.calls.append((self.current_fn, name))
        self.generic_visit(node)


def _callee_name(func_node) -> Optional[str]:
    # foo(...)
    if isinstance(func_node, ast.Name):
        return func_node.id
    # obj.foo(...)
    if isinstance(func_node, ast.Attribute):
        return func_node.attr
    return None


def _sha1(text: str) -> str:
    return hashlib.sha1(text.encode("utf-8", errors="ignore")).hexdigest()


def _iter_py_files(repo_path: str) -> Iterable[str]:
    for root, dirs, files in os.walk(repo_path):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
        for f in files:
            if f.endswith(".py"):
                yield os.path.join(root, f)


def _read_file(path: str) -> str:
    with open(path, "r", encoding="utf-8", errors="ignore") as f:
        return f.read()


def index_python_file(abs_path: str, rel_path: str) -> Optional[PyFileIndex]:
    text = _read_file(abs_path)
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return None

    defs = []
    imports = []
    calls = []

    # defs/imports
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef):
            defs.append((node.name, "function", rel_path, getattr(node, "lineno", None), getattr(node, "end_lineno", None)))
        elif isinstance(node, ast.ClassDef):
            defs.append((node.name, "class", rel_path, getattr(node, "lineno", None), getattr(node, "end_lineno", None)))
        elif isinstance(node, ast.Import):
            for alias in node.names:
                imports.append((rel_path, alias.name, "import"))
        elif isinstance(node, ast.ImportFrom):
            mod = node.module or ""
            # store module only; it's enough for graph
            imports.append((rel_path, mod, "from"))

    # calls (best-effort)
    v = _CallVisitor()
    v.visit(tree)
    for caller, callee in v.calls:
        calls.append((rel_path, caller, callee))

    return PyFileIndex(defs=defs, imports=imports, calls=calls)


def _module_to_file_candidates(repo_path: str, module: str) -> List[str]:
    """
    Very crude: module 'a.b.c' -> a/b/c.py and a/b/c/__init__.py.
    Returns repo-relative candidates that exist.
    """
    if not module:
        return []
    parts = module.split(".")
    cand1 = os.path.join(*parts) + ".py"
    cand2 = os.path.join(*parts, "__init__.py")
    out = []
    for c in (cand1, cand2):
        ap = os.path.join(repo_path, c)
        if os.path.exists(ap):
            out.append(c.replace("\\", "/"))
    return out


def ensure_index(repo_path: str, db_path: str) -> None:
    """
    Incremental index:
      - For each .py file, compute sha1
      - If sha1 unchanged, skip
      - Else wipe + reinsert its defs/imports/calls
      - Rebuild import edges for that file
    """
    repo_path = os.path.normpath(repo_path)

    with connect(db_path) as conn:
        init_db(conn)

        for abs_path in _iter_py_files(repo_path):
            rel_path = os.path.relpath(abs_path, repo_path).replace("\\", "/")
            try:
                mtime = os.path.getmtime(abs_path)
            except OSError:
                continue

            text = _read_file(abs_path)
            sha1 = _sha1(text)

            row = get_file_row(conn, rel_path)
            if row and row[2] == sha1:
                continue  # unchanged

            wipe_file_entries(conn, rel_path)
            upsert_file(conn, rel_path, "python", sha1, mtime)

            pf = index_python_file(abs_path, rel_path)
            if not pf:
                conn.commit()
                continue

            conn.executemany(
                "INSERT INTO defs(symbol, kind, path, line_start, line_end) VALUES (?,?,?,?,?)",
                pf.defs,
            )
            conn.executemany(
                "INSERT INTO imports(path, imported, kind) VALUES (?,?,?)",
                pf.imports,
            )
            conn.executemany(
                "INSERT INTO calls(path, caller, callee) VALUES (?,?,?)",
                pf.calls,
            )

            # Import edges (file-level)
            edges = []
            for (p, imported, kind) in pf.imports:
                for dst in _module_to_file_candidates(repo_path, imported):
                    edges.append((rel_path, dst, "import"))

            if edges:
                conn.executemany(
                    "INSERT INTO edges(src_path, dst_path, type) VALUES (?,?,?)",
                    edges,
                )

            conn.commit()
