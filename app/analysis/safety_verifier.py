from __future__ import annotations

import ast
import difflib

FORBIDDEN_SUBSTRINGS = [
    "rm -rf",
    "drop table",
    "alter user",
    "delete from",
    "truncate",
    "chmod 777",
]


def verify_safe_change(old_content: str, new_content: str, *, max_changed_lines=25) -> tuple[bool, str]:
    if not isinstance(new_content, str) or not new_content.strip():
        return False, "Empty new content"

    lower = new_content.lower()
    for bad in FORBIDDEN_SUBSTRINGS:
        if bad in lower:
            return False, f"Forbidden substring detected: {bad}"

    try:
        old_ast = ast.parse(old_content)
    except Exception:
        return False, "Old content failed to parse as Python"

    try:
        new_ast = ast.parse(new_content)
    except Exception:
        return False, "New content failed to parse as Python"

    if _imports_signature(new_ast) != _imports_signature(old_ast):
        return False, "Imports changed (not allowed)"

    if _top_defs_signature(new_ast) != _top_defs_signature(old_ast):
        return False, "Top-level function/class definitions changed (not allowed)"

    udiff = list(difflib.unified_diff(
        old_content.splitlines(),
        new_content.splitlines(),
        lineterm=""
    ))
    changed = sum(1 for line in udiff if line.startswith("+") or line.startswith("-")) - 2
    if changed > max_changed_lines:
        return False, f"Too many changed lines ({changed} > {max_changed_lines})"

    return True, "OK"


def _imports_signature(tree: ast.AST) -> tuple:
    out = []
    for node in tree.body:
        if isinstance(node, ast.Import):
            out.append(("import", tuple(sorted(a.name for a in node.names))))
        elif isinstance(node, ast.ImportFrom):
            out.append(("from", node.module, node.level, tuple(sorted(a.name for a in node.names))))
    return tuple(out)


def _top_defs_signature(tree: ast.AST) -> tuple:
    out = []
    for node in tree.body:
        if isinstance(node, ast.FunctionDef):
            out.append(("fn", node.name))
        elif isinstance(node, ast.AsyncFunctionDef):
            out.append(("afn", node.name))
        elif isinstance(node, ast.ClassDef):
            out.append(("cls", node.name))
    return tuple(out)
