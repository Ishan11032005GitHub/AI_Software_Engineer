# app/agents/repo_intel.py
from __future__ import annotations

import os
import re
import json
from dataclasses import dataclass, asdict
from typing import List, Dict, Any, Optional


@dataclass
class RepoIntel:
    repo_path: str
    has_package_json: bool
    has_node_modules: bool
    has_python: bool
    has_html: bool
    has_react: bool

    # Step 8 signals
    has_nextjs: bool
    has_vite: bool
    has_tailwind: bool

    html_files: List[str]
    css_files: List[str]
    js_files: List[str]

    uses_fetch: bool
    mentions_cart: bool
    mentions_checkout: bool

    # Step 7/8 support (safe edit anchors)
    entry_html: Optional[str]
    entry_html_head_tag: Optional[str]          # exact opening tag e.g. <head> or <head lang="en">
    entry_html_has_head: bool
    entry_html_has_viewport: bool
    entry_html_stylesheet_hrefs: List[str]      # hrefs from rel=stylesheet
    entry_html_links_local_css: bool            # True if at least one href resolves to a file inside repo

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def _walk_files(repo_path: str, exts: tuple[str, ...], max_files: int = 400) -> List[str]:
    out: List[str] = []
    for root, dirs, files in os.walk(repo_path):
        # skip heavy/noisy dirs
        dirs[:] = [
            d for d in dirs
            if d not in (".git", "node_modules", ".venv", "venv", "__pycache__", "dist", "build", ".next")
        ]
        for fn in files:
            if fn.lower().endswith(exts):
                out.append(os.path.join(root, fn))
                if len(out) >= max_files:
                    return out
    return out


def _read_small(path: str, max_bytes: int = 200_000) -> str:
    try:
        with open(path, "rb") as f:
            b = f.read(max_bytes)
        return b.decode("utf-8", errors="ignore")
    except Exception:
        return ""


def _rel(repo_path: str, p: str) -> str:
    try:
        return os.path.relpath(p, repo_path).replace("\\", "/")
    except Exception:
        return p.replace("\\", "/")


def _pick_entry_html(repo_path: str, html_files_abs: List[str]) -> Optional[str]:
    """
    Prefer common entrypoints to increase reliability of Step 7/8 edits.
    Returns a REL path.
    """
    if not html_files_abs:
        return None

    candidates = [_rel(repo_path, p) for p in html_files_abs]

    preferred = [
        "index.html",
        "public/index.html",
        "src/index.html",
        "app/index.html",
    ]
    for pref in preferred:
        if pref in candidates:
            return pref

    return candidates[0]


def _read_package_json(repo_path: str) -> Dict[str, Any]:
    p = os.path.join(repo_path, "package.json")
    if not os.path.exists(p):
        return {}
    try:
        with open(p, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _detect_stack_from_package(repo_path: str) -> Dict[str, bool]:
    pkg = _read_package_json(repo_path)
    deps = {}
    deps.update(pkg.get("dependencies") or {})
    deps.update(pkg.get("devDependencies") or {})

    has_nextjs = "next" in deps
    has_vite = "vite" in deps
    has_tailwind = "tailwindcss" in deps

    # React via package.json is stronger than source heuristic alone
    has_react_pkg = "react" in deps or "react-dom" in deps

    return {
        "has_nextjs": bool(has_nextjs),
        "has_vite": bool(has_vite),
        "has_tailwind": bool(has_tailwind),
        "has_react_pkg": bool(has_react_pkg),
    }


def analyze_repo(repo_path: str) -> RepoIntel:
    pkg_exists = os.path.exists(os.path.join(repo_path, "package.json"))
    nm = os.path.exists(os.path.join(repo_path, "node_modules"))
    py = any(
        os.path.exists(os.path.join(repo_path, x))
        for x in ("requirements.txt", "pyproject.toml", "setup.py")
    )

    html_files_abs = _walk_files(repo_path, (".html", ".htm"))
    css_files_abs = _walk_files(repo_path, (".css",))
    js_files_abs = _walk_files(repo_path, (".js", ".mjs", ".cjs", ".jsx", ".ts", ".tsx"))

    html_files = [_rel(repo_path, p) for p in html_files_abs]
    css_files = [_rel(repo_path, p) for p in css_files_abs]
    js_files = [_rel(repo_path, p) for p in js_files_abs]

    has_html = len(html_files) > 0

    stack = _detect_stack_from_package(repo_path)
    has_nextjs = stack["has_nextjs"]
    has_vite = stack["has_vite"]
    has_tailwind = stack["has_tailwind"]

    # fallback heuristics
    has_react = bool(stack["has_react_pkg"])
    uses_fetch = False
    mentions_cart = False
    mentions_checkout = False

    # cheap heuristics: scan a few files for signals
    sample_abs = (js_files_abs[:30] + html_files_abs[:20])[:40]
    for p in sample_abs:
        t = _read_small(p)
        if not t:
            continue

        low = t.lower()

        if ("from 'react'" in low) or ('from "react"' in low) or "createroot" in low:
            has_react = True

        if re.search(r"\bfetch\s*\(", t):
            uses_fetch = True

        if re.search(r"\bcart\b", t, flags=re.IGNORECASE):
            mentions_cart = True

        if re.search(r"\bcheckout\b", t, flags=re.IGNORECASE):
            mentions_checkout = True

    # Next.js implies React
    if has_nextjs:
        has_react = True

    # Entry HTML + anchors for deterministic Step 7/8 edits
    entry_html = _pick_entry_html(repo_path, html_files_abs)
    entry_html_head_tag: Optional[str] = None
    entry_html_has_head = False
    entry_html_has_viewport = False
    entry_html_stylesheet_hrefs: List[str] = []
    entry_html_links_local_css = False

    if entry_html:
        abs_entry = os.path.join(repo_path, entry_html)
        txt = _read_small(abs_entry, 200_000)

        m = re.search(r"<head[^>]*>", txt, flags=re.IGNORECASE)
        if m:
            entry_html_has_head = True
            entry_html_head_tag = m.group(0)

        low = txt.lower()
        entry_html_has_viewport = "name=\"viewport\"" in low or "name='viewport'" in low

        # extract rel=stylesheet hrefs
        # best-effort, but deterministic
        for mm in re.finditer(
            r"<link[^>]*rel\s*=\s*['\"]stylesheet['\"][^>]*href\s*=\s*['\"]([^'\"]+)['\"][^>]*>",
            txt,
            flags=re.IGNORECASE,
        ):
            href = (mm.group(1) or "").strip()
            if href:
                entry_html_stylesheet_hrefs.append(href)

        # resolve if local
        base_dir = os.path.dirname(entry_html).replace("\\", "/")
        for href in entry_html_stylesheet_hrefs:
            if href.startswith("http://") or href.startswith("https://") or href.startswith("//"):
                continue
            # ignore data URIs
            if href.startswith("data:"):
                continue
            rel_css = os.path.normpath(os.path.join(base_dir, href)).replace("\\", "/")
            if os.path.exists(os.path.join(repo_path, rel_css)):
                entry_html_links_local_css = True
                break

    return RepoIntel(
        repo_path=repo_path,
        has_package_json=pkg_exists,
        has_node_modules=nm,
        has_python=py,
        has_html=has_html,
        has_react=has_react,
        has_nextjs=has_nextjs,
        has_vite=has_vite,
        has_tailwind=has_tailwind,
        html_files=html_files,
        css_files=css_files,
        js_files=js_files,
        uses_fetch=uses_fetch,
        mentions_cart=mentions_cart,
        mentions_checkout=mentions_checkout,
        entry_html=entry_html,
        entry_html_head_tag=entry_html_head_tag,
        entry_html_has_head=entry_html_has_head,
        entry_html_has_viewport=entry_html_has_viewport,
        entry_html_stylesheet_hrefs=entry_html_stylesheet_hrefs,
        entry_html_links_local_css=entry_html_links_local_css,
    )


def infer_request_kind(prompt: str) -> str:
    """
    Convert vague human prompt into a system intent bucket.
    Keep it deterministic (no LLM hallucination).
    """
    p = (prompt or "").strip().lower()

    backend_signals = (
        "backend", "api", "express", "server", "database", "auth",
        "login", "signup", "checkout", "payment", "orders"
    )
    if any(s in p for s in backend_signals):
        return "backend"

    frontend_signals = (
        "responsive", "ui", "layout", "css", "mobile", "tablet", "frontend",
        "react", "tailwind", "bootstrap"
    )
    if any(s in p for s in frontend_signals):
        return "frontend"

    return "generic_feature"
