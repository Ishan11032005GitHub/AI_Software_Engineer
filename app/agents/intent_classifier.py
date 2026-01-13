from __future__ import annotations

import os
import json
import re
from typing import Dict, Any, Optional

import requests


def _repo_hint(repo_path: str) -> Dict[str, Any]:
    """
    Cheap signals to help intent classification.
    No heavy scanning. No git calls here.
    """
    hints = {
        "has_backend_dir": os.path.isdir(os.path.join(repo_path, "backend")),
        "has_server_js": os.path.exists(os.path.join(repo_path, "server.js")),
        "has_package_json": os.path.exists(os.path.join(repo_path, "package.json")),
        "has_requirements": os.path.exists(os.path.join(repo_path, "requirements.txt")),
        "has_pyproject": os.path.exists(os.path.join(repo_path, "pyproject.toml")),
        "has_dockerfile": os.path.exists(os.path.join(repo_path, "Dockerfile")),
        "has_github_workflows": os.path.isdir(os.path.join(repo_path, ".github", "workflows")),
        "has_index_html": os.path.exists(os.path.join(repo_path, "index.html")),
    }
    return hints


def classify_intent_llm(
    prompt: str,
    repo_path: Optional[str] = None,
    action: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Step 1: LLM-based intent classification.

    Returns:
      {
        "intent": "backend|frontend|bugfix|refactor|docs|tests|pr|unknown",
        "confidence": 0.0-1.0,
        "subtasks": [..],
        "notes": "...",
        "raw": {...optional...}
      }

    Env:
      OPENAI_API_KEY (required for LLM mode)
      OPENAI_BASE_URL (optional, default https://api.openai.com/v1)
      OPENAI_MODEL (optional, default gpt-4o-mini)
    """
    p = (prompt or "").strip()
    if not p:
        return {
            "intent": "unknown",
            "confidence": 0.0,
            "subtasks": [],
            "notes": "Empty prompt",
        }

    api_key = os.getenv("OPENAI_API_KEY") or os.getenv("GITHUB_COPILOT_TOKEN")
    if not api_key:
        # Don’t crash the worker if user forgot env vars.
        return {
            "intent": "unknown",
            "confidence": 0.0,
            "subtasks": [],
            "notes": "OPENAI_API_KEY not set; skipping LLM intent classification",
        }

    base_url = os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1").rstrip("/")
    model = os.getenv("OPENAI_MODEL", "gpt-4o-mini")

    hints = _repo_hint(repo_path) if repo_path else {}
    system = (
        "You are a senior software engineer triage system. "
        "Classify the user's request into one intent category and propose subtasks. "
        "Return ONLY valid JSON."
    )

    user = {
        "prompt": p,
        "requested_action": action or "",
        "repo_hints": hints,
        "labels_allowed": ["backend", "frontend", "bugfix", "refactor", "docs", "tests", "pr", "unknown"],
        "output_schema": {
            "intent": "one of labels_allowed",
            "confidence": "float 0..1",
            "subtasks": "list of short strings",
            "notes": "short explanation",
        },
    }

    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": json.dumps(user, ensure_ascii=False)},
        ],
        "temperature": 0.1,
    }

    try:
        resp = requests.post(
            f"{base_url}/chat/completions",
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            json=payload,
            timeout=30,
        )
    except Exception as e:
        return {
            "intent": "unknown",
            "confidence": 0.0,
            "subtasks": [],
            "notes": f"LLM request failed: {e}",
        }

    if resp.status_code >= 300:
        return {
            "intent": "unknown",
            "confidence": 0.0,
            "subtasks": [],
            "notes": f"LLM error {resp.status_code}: {resp.text[:400]}",
        }

    data = resp.json()
    content = (
        data.get("choices", [{}])[0]
        .get("message", {})
        .get("content", "")
        .strip()
    )

    # Some models wrap JSON in ```json ...```
    content = re.sub(r"^```json\s*", "", content, flags=re.IGNORECASE)
    content = re.sub(r"^```\s*", "", content)
    content = re.sub(r"\s*```$", "", content)

    try:
        obj = json.loads(content)
    except Exception:
        return {
            "intent": "unknown",
            "confidence": 0.0,
            "subtasks": [],
            "notes": "LLM returned non-JSON output",
            "raw": {"content": content[:800]},
        }

    intent = str(obj.get("intent", "unknown")).strip().lower()
    if intent not in {"backend", "frontend", "bugfix", "refactor", "docs", "tests", "pr", "unknown"}:
        intent = "unknown"

    conf = obj.get("confidence", 0.0)
    try:
        conf = float(conf)
    except Exception:
        conf = 0.0
    conf = max(0.0, min(1.0, conf))

    subtasks = obj.get("subtasks", [])
    if not isinstance(subtasks, list):
        subtasks = []
    subtasks = [str(x).strip() for x in subtasks if str(x).strip()]

    notes = str(obj.get("notes", "")).strip()

    return {
        "intent": intent,
        "confidence": conf,
        "subtasks": subtasks,
        "notes": notes,
    }
