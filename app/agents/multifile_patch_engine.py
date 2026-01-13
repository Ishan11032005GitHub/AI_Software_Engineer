import ast
from typing import List, Tuple, Optional


class FunctionSignatureDiff:
    def __init__(self, name: str, old_params: List[str], new_params: List[str]):
        self.name = name
        self.old_params = old_params
        self.new_params = new_params

    @property
    def added(self):
        return [p for p in self.new_params if p not in self.old_params]

    @property
    def removed(self):
        return [p for p in self.old_params if p not in self.new_params]


def extract_signature(code: str, fn_name: str) -> Optional[List[str]]:
    try:
        tree = ast.parse(code)
    except:
        return None

    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == fn_name:
            return [arg.arg for arg in node.args.args]
    return None


def compute_signature_diff(old: str, new: str, fn_name: str) -> Optional[FunctionSignatureDiff]:
    old_p = extract_signature(old, fn_name)
    new_p = extract_signature(new, fn_name)
    if not old_p or not new_p or old_p == new_p:
        return None
    return FunctionSignatureDiff(fn_name, old_p, new_p)


def apply_signature_fix(code: str, diff: FunctionSignatureDiff) -> str:
    """
    Naive transformation:
    - For every call: foo(x,y) → foo(x,y,<default?>)
    - Added params default to None for now.
    TODO: integrate LLM fill later with context.
    """
    lines = code.splitlines()
    patched = []
    for ln in lines:
        if f"{diff.name}(" in ln:
            missing = ", ".join(["None" for _ in diff.added])
            ln = ln.replace(f"{diff.name}(", f"{diff.name}({missing}," if missing else f"{diff.name}(")
        patched.append(ln)
    return "\n".join(patched)
