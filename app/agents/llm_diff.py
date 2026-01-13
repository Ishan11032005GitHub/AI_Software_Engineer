from __future__ import annotations

import os
import re
import subprocess
import tempfile
from dataclasses import dataclass
from typing import List, Tuple

import google.generativeai as genai


# ------------------------------
# Config
# ------------------------------
_DIFF_RE = re.compile(r"```diff\s*(.*?)```", re.DOTALL | re.IGNORECASE)

GEMINI_MODEL = "gemini-flash-latest"


# ------------------------------
# Utilities
# ------------------------------
@dataclass
class PatchResult:
    ok: bool
    diff: str
    applied: bool
    stdout: str = ""
    stderr: str = ""
    reason: str = ""


def _run(cmd: List[str], cwd: str) -> Tuple[int, str, str]:
    p = subprocess.Popen(
        cmd,
        cwd=cwd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    out, err = p.communicate()
    return p.returncode, out, err


def read_file_safe(path: str, max_bytes: int = 40_000) -> str:
    try:
        with open(path, "rb") as f:
            data = f.read(max_bytes)
        return data.decode("utf-8", errors="replace")
    except Exception:
        return ""


# ------------------------------
# Repo context builder
# ------------------------------
def build_repo_context(repo_path: str, max_bytes: int = 180_000) -> str:
    used = 0
    chunks = []

    rc, out, _ = _run(["git", "ls-files"], cwd=repo_path)
    if rc == 0:
        listing = "\n".join(out.splitlines()[:800])
        blob = f"=== FILE TREE ===\n{listing}\n\n"
        used += len(blob.encode())
        chunks.append(blob)

    for rel in [
        "README.md",
        "pyproject.toml",
        "requirements.txt",
        "package.json",
        "app/main.py",
        "app/api.py",
    ]:
        p = os.path.join(repo_path, rel)
        if not os.path.exists(p):
            continue
        content = read_file_safe(p)
        blob = f"=== FILE: {rel} ===\n{content}\n\n"
        size = len(blob.encode())
        if used + size > max_bytes:
            break
        used += size
        chunks.append(blob)

    return "".join(chunks) or "=== EMPTY CONTEXT ==="


# ------------------------------
# Diff extraction
# ------------------------------
def extract_unified_diff(text: str) -> str:
    m = _DIFF_RE.search(text or "")
    if m:
        return m.group(1).strip()

    if "diff --git " in text:
        return text[text.index("diff --git "):].strip()

    return ""


# ------------------------------
# Gemini diff generation
# ------------------------------
def generate_llm_diff(repo_path: str, prompt: str) -> str:
    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        raise RuntimeError("GEMINI_API_KEY not set")

    genai.configure(api_key=api_key)
    model = genai.GenerativeModel(GEMINI_MODEL)

    context = build_repo_context(repo_path)

    system = (
        "You are a senior software engineer.\n"
        "Output ONLY a unified git diff inside a ```diff fenced block```.\n"
        "Rules:\n"
        "- Use diff --git a/... b/... format\n"
        "- Make minimal, focused changes\n"
        "- No explanations outside the diff\n"
        "- Include new files if needed\n"
    )

    user = f"""
TASK:
{prompt}

REPOSITORY CONTEXT:
{context}

Return ONLY the diff.
"""

    resp = model.generate_content(
        [system, user],
        generation_config={
            "temperature": 0.2,
            "max_output_tokens": 4096,
        },
    )

    text = resp.text or ""
    diff = extract_unified_diff(text)

    if not diff:
        raise RuntimeError("Gemini did not return a valid unified diff")

    return diff


# ------------------------------
# Apply diff safely
# ------------------------------
def apply_unified_diff(repo_path: str, diff_text: str) -> PatchResult:
    if not diff_text.strip():
        return PatchResult(False, diff_text, False, reason="Empty diff")

    with tempfile.NamedTemporaryFile("w", encoding="utf-8", delete=False, suffix=".patch") as f:
        f.write(diff_text)
        patch_path = f.name

    try:
        rc, out, err = _run(["git", "apply", "--check", patch_path], cwd=repo_path)
        if rc != 0:
            return PatchResult(False, diff_text, False, out, err, "Patch check failed")

        rc, out, err = _run(["git", "apply", patch_path], cwd=repo_path)
        if rc != 0:
            return PatchResult(False, diff_text, False, out, err, "Patch apply failed")

        return PatchResult(True, diff_text, True, out, err)

    finally:
        try:
            os.remove(patch_path)
        except Exception:
            pass
