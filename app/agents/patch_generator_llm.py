from __future__ import annotations
import os
import google.generativeai as genai
from config import GEMINI_API_KEY


def _model():
    if not GEMINI_API_KEY:
        print("❌ Missing GEMINI_API_KEY")
        return None

    genai.configure(api_key=GEMINI_API_KEY)
    return genai.GenerativeModel("gemini-flash-latest")   # Cheap + stable


def propose_fix_with_llm(issue_text: str, file_path: str, file_content: str) -> str | None:
    """
    LLM produces **full updated file**.
    We enforce structured output → no markdown fences.
    """

    model = _model()
    if not model:
        return None

    # Reduce overload if file huge
    snippet = file_content[:12000]

    prompt = f"""
You are an automated patch-generation agent.

TASK:
Fix the bug with minimum safe code modifications.
Return the FULL UPDATED FILE content ONLY.

Constraints:
- No refactors or design changes.
- Preserve logic and formatting style.
- Fix must compile.
- No extra commentary.
- Output must be ONLY raw code.

Issue:
{issue_text}

File: {file_path}

Original Source:
{snippet}
""".strip()

    try:
        resp = model.generate_content(prompt)
        out = _extract_text(resp)

        if not out: return None

        cleaned = out.replace("```python","").replace("```","").strip()

        if cleaned == file_content.strip():  # No change
            return None

        return cleaned

    except Exception as e:
        print(f"[LLM ERROR] {e}")
        return None


def _extract_text(resp):
    """ Extract best-effort raw text from Gemini response """
    if hasattr(resp,"text") and isinstance(resp.text,str) and resp.text.strip():
        return resp.text.strip()

    parts = []
    if hasattr(resp,"candidates"):
        for c in resp.candidates:
            if hasattr(c,"content"):
                for p in c.content.parts:
                    if hasattr(p,"text") and p.text.strip():
                        parts.append(p.text)
    return "\n".join(parts).strip() if parts else None
