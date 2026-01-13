# app/agents/stack_editors/vanilla_html_editor.py
from __future__ import annotations

import os
import re
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


def _ensure_meta_viewport(html: str) -> str:
    if re.search(r'name=["\']viewport["\']', html, flags=re.IGNORECASE):
        return html

    # insert inside <head> if possible
    m = re.search(r"<head[^>]*>", html, flags=re.IGNORECASE)
    tag = '<meta name="viewport" content="width=device-width, initial-scale=1" />'
    if m:
        i = m.end()
        return html[:i] + "\n  " + tag + html[i:]
    return tag + "\n" + html


def _ensure_responsive_css(css: str) -> str:
    """
    Minimal, safe responsive base:
    - box-sizing
    - images fluid
    - container max-width
    - mobile media query for typical row layouts
    """
    additions = []

    if "box-sizing: border-box" not in css:
        additions.append(
            "*, *::before, *::after { box-sizing: border-box; }\n"
        )

    if "img { max-width: 100%" not in css and "img{max-width:100%" not in css.replace(" ", ""):
        additions.append(
            "img { max-width: 100%; height: auto; display: block; }\n"
        )

    if ".container" not in css:
        additions.append(
            ".container { width: 100%; max-width: 1200px; margin: 0 auto; padding: 0 16px; }\n"
        )

    # media query safety net
    if "@media" not in css or "max-width: 768px" not in css:
        additions.append(
            "@media (max-width: 768px) {\n"
            "  .row, .grid, .columns { display: block; }\n"
            "  .col, .column { width: 100%; }\n"
            "}\n"
        )

    if not additions:
        return css

    return css.rstrip() + "\n\n/* AutoTriage: responsive base */\n" + "\n".join(additions)


def apply_vanilla_frontend_edits(repo_path: str, fp: StackFingerprint, goal: str) -> Dict[str, Any]:
    goal_low = (goal or "").lower()
    wants_responsive = any(k in goal_low for k in ("responsive", "mobile", "layout", "ui"))

    changed: List[str] = []
    notes: List[str] = []

    # 1) viewport meta
    if fp.primary_html and wants_responsive:
        html_path = os.path.join(repo_path, fp.primary_html)
        html = _read(html_path)
        if html:
            new_html = _ensure_meta_viewport(html)
            if new_html != html:
                _write(html_path, new_html)
                changed.append(fp.primary_html)
                notes.append(f"Added viewport meta to {fp.primary_html}")

    # 2) CSS improvements (create if missing but only if safe target exists)
    css_rel = fp.primary_css or "styles.css"
    css_path = os.path.join(repo_path, css_rel)

    if wants_responsive:
        css = _read(css_path)
        new_css = _ensure_responsive_css(css)
        if new_css != css:
            _write(css_path, new_css)
            changed.append(css_rel)
            notes.append(f"Updated responsive base CSS at {css_rel}")

        # If HTML exists but doesn't link CSS, add a link tag (best-effort)
        if fp.primary_html and fp.primary_html in changed:
            html_path = os.path.join(repo_path, fp.primary_html)
            html = _read(html_path)
            # naive link insertion if no stylesheet link present
            if html and ("rel=\"stylesheet\"" not in html and "rel='stylesheet'" not in html):
                m = re.search(r"</head>", html, flags=re.IGNORECASE)
                if m:
                    link = f'<link rel="stylesheet" href="{css_rel}"/>'
                    new_html = html[:m.start()] + "  " + link + "\n" + html[m.start():]
                    _write(html_path, new_html)
                    if fp.primary_html not in changed:
                        changed.append(fp.primary_html)
                    notes.append(f"Linked {css_rel} from {fp.primary_html}")

    return {
        "status": "stack_edit_applied",
        "stack": "vanilla",
        "goal": goal,
        "changed_files": changed,
        "notes": notes,
    }
