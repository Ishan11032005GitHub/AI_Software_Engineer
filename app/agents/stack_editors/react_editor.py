# app/agents/stack_editors/react_editor.py
from __future__ import annotations

import os
from typing import Dict, Any, List

from app.agents.stack_fingerprint import StackFingerprint


def _read(path: str) -> str:
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            return f.read()
    except Exception:
        return ""


def _write(path: str, content: str) -> None:
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)


def _ensure_responsive_css(css: str) -> str:
    additions: List[str] = []

    if "box-sizing: border-box" not in css:
        additions.append("*, *::before, *::after { box-sizing: border-box; }\n")

    if "img { max-width: 100%" not in css:
        additions.append("img { max-width: 100%; height: auto; }\n")

    if "@media (max-width: 768px)" not in css:
        additions.append(
            "@media (max-width: 768px) {\n"
            "  .container { padding: 0 16px; }\n"
            "  .row, .grid, .columns { display: block; }\n"
            "  .col, .column { width: 100%; }\n"
            "}\n"
        )

    if not additions:
        return css

    return css.rstrip() + "\n\n/* AutoTriage: responsive base */\n" + "\n".join(additions)


def apply_react_frontend_edits(repo_path: str, fp: StackFingerprint, goal: str) -> Dict[str, Any]:
    goal_low = (goal or "").lower()
    wants_responsive = any(k in goal_low for k in ("responsive", "mobile", "layout", "ui"))

    changed: List[str] = []
    notes: List[str] = []

    # Prefer src/index.css then src/App.css, else create src/index.css
    candidates = ["src/index.css", "src/App.css", "src/styles.css"]
    css_rel = None
    for c in candidates:
        if os.path.exists(os.path.join(repo_path, c)):
            css_rel = c
            break
    if not css_rel:
        css_rel = "src/index.css"

    if wants_responsive:
        css_path = os.path.join(repo_path, css_rel)
        css = _read(css_path)
        new_css = _ensure_responsive_css(css)
        if new_css != css:
            _write(css_path, new_css)
            changed.append(css_rel)
            notes.append(f"Updated responsive base CSS at {css_rel}")

    return {
        "status": "stack_edit_applied",
        "stack": "react",
        "goal": goal,
        "changed_files": changed,
        "notes": notes,
    }
