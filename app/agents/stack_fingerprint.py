# app/agents/stack_fingerprint.py
from __future__ import annotations

import os
import json
from dataclasses import dataclass, asdict
from typing import Any, Dict, Optional


@dataclass
class StackFingerprint:
    # frontend
    frontend_framework: str  # "react" | "nextjs" | "vanilla" | "unknown"
    frontend_build: str      # "vite" | "next" | "none" | "unknown"
    styling: str             # "tailwind" | "css" | "unknown"

    # backend
    backend_framework: str   # "express" | "none" | "unknown"
    backend_language: str    # "node" | "none" | "unknown"

    # quick entrypoints (best-effort)
    primary_html: Optional[str] = None
    primary_css: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def _read_json(path: str) -> Dict[str, Any]:
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def fingerprint_from_repo_facts(repo_path: str, repo_facts: Dict[str, Any]) -> StackFingerprint:
    pkg_path = os.path.join(repo_path, "package.json")
    pkg = _read_json(pkg_path) if os.path.exists(pkg_path) else {}

    deps = {}
    deps.update(pkg.get("dependencies") or {})
    deps.update(pkg.get("devDependencies") or {})

    has_next = "next" in deps or bool(repo_facts.get("has_nextjs"))
    has_react = "react" in deps or bool(repo_facts.get("has_react"))
    has_vite = "vite" in deps or bool(repo_facts.get("has_vite"))
    has_tailwind = "tailwindcss" in deps or bool(repo_facts.get("has_tailwind"))

    # frontend framework/build
    if has_next:
        frontend_framework = "nextjs"
        frontend_build = "next"
    elif has_react:
        frontend_framework = "react"
        frontend_build = "vite" if has_vite else "none"
    elif bool(repo_facts.get("has_html")):
        frontend_framework = "vanilla"
        frontend_build = "none"
    else:
        frontend_framework = "unknown"
        frontend_build = "unknown"

    styling = "tailwind" if has_tailwind else ("css" if frontend_framework in ("react", "nextjs", "vanilla") else "unknown")

    # backend detection (existing backend dir or deps)
    backend_framework = "none"
    backend_language = "none"

    backend_pkg = _read_json(os.path.join(repo_path, "backend", "package.json"))
    backend_deps = {}
    backend_deps.update(backend_pkg.get("dependencies") or {})
    backend_deps.update(backend_pkg.get("devDependencies") or {})

    if os.path.isdir(os.path.join(repo_path, "backend")) and ("express" in backend_deps or os.path.exists(os.path.join(repo_path, "backend", "server.js"))):
        backend_framework = "express"
        backend_language = "node"

    # pick a primary html/css for vanilla flows
    primary_html = None
    if repo_facts.get("html_files"):
        # repo_facts paths may be absolute; convert to relative if needed
        p0 = str(repo_facts["html_files"][0])
        if p0.startswith(repo_path):
            primary_html = os.path.relpath(p0, repo_path).replace("\\", "/")
        else:
            primary_html = p0.replace("\\", "/")

    primary_css = None
    # best effort: look for common css names
    for cand in ("styles.css", "style.css", "css/style.css", "assets/style.css", "src/index.css", "src/App.css"):
        if os.path.exists(os.path.join(repo_path, cand)):
            primary_css = cand
            break

    return StackFingerprint(
        frontend_framework=frontend_framework,
        frontend_build=frontend_build,
        styling=styling,
        backend_framework=backend_framework,
        backend_language=backend_language,
        primary_html=primary_html,
        primary_css=primary_css,
    )
