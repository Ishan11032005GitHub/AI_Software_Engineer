# app/review/review_engine.py
from __future__ import annotations
from typing import List, Dict, Any, Optional
import google.generativeai as genai

from config import GEMINI_API_KEY

def _review_model():
    if not GEMINI_API_KEY:
        return None
    genai.configure(api_key=GEMINI_API_KEY)
    return genai.GenerativeModel("gemini-1.5-flash")

class ReviewResult:
    def __init__(self, summary: str, inline: List[dict], verdict: str):
        self.summary = summary
        self.inline = inline
        self.verdict = verdict  # APPROVE / COMMENT / REQUEST_CHANGES

def run_code_review(
    *,
    diff: str,
    files_snapshot: List[tuple[str, str, str | None]],
    confidence: float,
    safety_verified: bool,
    multifile_signal: bool,
    mode: str,   # REVIEW_MODE
) -> Optional[ReviewResult]:
    model = _review_model()
    if not model or mode == "OFF":
        return None

    # Trim diff if huge
    short_diff = diff[:12000]

    prompt = f"""
Act as a strict senior code reviewer.

DIFF:
{short_diff}

CONTEXT FILES (old → new, some new may be None for context-only files):
{_format_files(files_snapshot)}

Signals:
- confidence: {confidence}
- safety_verified: {safety_verified}
- multifile_signal: {multifile_signal}
- review_mode: {mode}

You must respond as strict JSON with this schema:

{{
  "verdict": "APPROVE" | "COMMENT" | "REQUEST_CHANGES",
  "summary": "short high-signal review text",
  "inline": [
    {{
      "path": "relative/file.py",
      "position": <integer unified diff position or best guess>,
      "body": "line-level comment"
    }}
  ]
}}

Guidelines:
- Focus on correctness, edge cases, and safety.
- Do NOT nitpick style unless it affects readability or bugs.
- If changes are risky or incomplete, use REQUEST_CHANGES.
- Keep summary under 10 lines.
""".strip()

    try:
        resp = model.generate_content(prompt, generation_config={"temperature": 0})
        text = getattr(resp, "text", "") or ""
    except Exception:
        return None

    import json
    try:
        data = json.loads(text)
    except Exception:
        return None

    verdict = data.get("verdict", "COMMENT")
    summary = data.get("summary", "").strip()
    inline = data.get("inline") or []

    if not summary:
        summary = "Automated review completed – no major comments generated."

    return ReviewResult(summary, inline, verdict)

def _format_files(files_snapshot):
    out_chunks = []
    for path, old, new in files_snapshot:
        out_chunks.append(
            f"\n### {path}\n\nOLD:\n{(old or '')[:1000]}\n\nNEW:\n{(new or '')[:1000]}"
        )
    return "\n".join(out_chunks)
