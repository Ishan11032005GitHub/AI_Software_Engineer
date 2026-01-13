from __future__ import annotations

import ast
from typing import Optional


class RiskyAttributeVisitor(ast.NodeVisitor):
    def __init__(self) -> None:
        self.matches: list[ast.Attribute] = []

    def visit_Attribute(self, node: ast.Attribute) -> None:
        if isinstance(node.value, ast.Attribute):
            self.matches.append(node)
        self.generic_visit(node)


def verify_python_ast(file_content: str, function_name: Optional[str] = None) -> Optional[dict]:
    if not file_content or not isinstance(file_content, str):
        return None

    try:
        tree = ast.parse(file_content)
    except SyntaxError:
        return None

    functions: dict[str, ast.FunctionDef] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef):
            functions[node.name] = node

    target_nodes = [functions[function_name]] if function_name and function_name in functions else [tree]

    risky_attrs: list[dict] = []
    for node in target_nodes:
        visitor = RiskyAttributeVisitor()
        visitor.visit(node)
        for attr_node in visitor.matches:
            risky_attrs.append({
                "lineno": getattr(attr_node, "lineno", None),
                "col": getattr(attr_node, "col_offset", None),
                "attr": getattr(attr_node, "attr", None),
            })

    if not risky_attrs:
        return None

    return {"functions": list(functions.keys()), "risky_attributes": risky_attrs}
