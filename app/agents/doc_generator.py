# app/agents/doc_generator.py
from __future__ import annotations

import os
import datetime
from typing import Any, Dict, List, Optional, Tuple


def _now_iso() -> str:
    return datetime.datetime.utcnow().replace(microsecond=0).isoformat() + "Z"


def _md_escape(s: str) -> str:
    # minimal safety for headings / inline
    return (s or "").replace("\r\n", "\n").replace("\r", "\n")


def _relpath(repo_path: str, file_path: str) -> str:
    try:
        return os.path.relpath(file_path, repo_path).replace("\\", "/")
    except Exception:
        return file_path


def _fmt_kv_block(d: Dict[str, Any]) -> str:
    if not d:
        return "_None_\n"
    lines = []
    for k in sorted(d.keys()):
        v = d[k]
        if isinstance(v, (list, tuple)):
            # keep lists short in the doc unless caller already trimmed
            if len(v) > 50:
                v = list(v[:50]) + ["...(truncated)"]
        lines.append(f"- **{k}**: `{v}`")
    return "\n".join(lines) + "\n"


def _code_block(lang: str, content: Optional[str]) -> str:
    if content is None:
        return "```text\n<NO PROPOSAL>\n```\n"
    content = _md_escape(content)
    # Avoid closing fence injection
    content = content.replace("```", "``\\`")
    return f"```{lang}\n{content}\n```\n"


def _infer_lang(file_path: str) -> str:
    fp = (file_path or "").lower()
    if fp.endswith(".py"):
        return "python"
    if fp.endswith(".js"):
        return "javascript"
    if fp.endswith(".ts"):
        return "typescript"
    if fp.endswith(".java"):
        return "java"
    if fp.endswith(".c"):
        return "c"
    if fp.endswith(".cpp") or fp.endswith(".cc") or fp.endswith(".cxx") or fp.endswith(".hpp") or fp.endswith(".h"):
        return "cpp"
    return "text"


def generate_engineering_doc(
    *,
    issue: dict,
    repo_path: str,
    decision: str,
    decision_reason: str,
    confidence: float,
    touched_files: List[str],
    old_new_files: List[Tuple[str, Optional[str], Optional[str]]],
    checks: Dict[str, Any],
    risk_notes: List[str],
) -> str:
    """
    Generates an Engineering Document (Markdown) with a strict, reviewable structure.

    Inputs:
      - touched_files: list of file paths (absolute or relative); used in Proposed Changes section.
      - old_new_files: list of tuples (file_path, old_content, new_content)
            old_content or new_content may be None when proposal could not be generated.
      - checks: arbitrary structured data (ast/safety/dep impact/ci etc). Keep big lists trimmed BEFORE passing.
      - risk_notes: list of human-readable risk strings.

    Output:
      - Markdown string.
    """

    issue_number = issue.get("number")
    title = issue.get("title") or "(no title)"
    body = issue.get("body") or ""
    issue_url = issue.get("html_url") or ""
    created_at = issue.get("created_at") or ""
    updated_at = issue.get("updated_at") or ""

    # Optional extras from checks (best-effort)
    trace = checks.get("trace") or checks.get("stack_trace")  # you can pass this in later
    entry_fn = checks.get("entry_fn") or checks.get("function")
    impacted_files = checks.get("impacted_files")
    impacted_count = checks.get("impacted_files_count") or checks.get("impacted_count")
    ci_failures = checks.get("ci_failures")  # if you pass it
    safety_reason = checks.get("safety_reason")
    ast_info = checks.get("ast_info")

    rel_touched = [_relpath(repo_path, p) for p in (touched_files or [])]
    rel_touched = sorted(list(dict.fromkeys(rel_touched)))  # uniq preserve order-ish

    # --- Document starts ---
    out: List[str] = []
    out.append(f"# Engineering Report — Issue #{issue_number}")
    out.append("")
    out.append(f"- **Generated**: `{_now_iso()}`")
    out.append(f"- **Decision**: `{decision}`")
    out.append(f"- **Confidence**: `{confidence:.2f}`")
    out.append(f"- **Reason**: {_md_escape(decision_reason)}")
    out.append("")

    # 1. Executive Summary
    out.append("## 1. Executive Summary")
    out.append("")
    out.append(f"**Issue**: {_md_escape(title)}")
    if issue_url:
        out.append(f"- **Issue URL**: {_md_escape(issue_url)}")
    if created_at:
        out.append(f"- **Created**: `{created_at}`")
    if updated_at:
        out.append(f"- **Updated**: `{updated_at}`")
    out.append("")
    out.append("**Impact (inferred)**:")
    out.append("- The bug is treated as a runtime correctness/stability issue based on issue labeling and agent heuristics.")
    out.append("")
    out.append("**High-level fix idea**:")
    out.append("- Add minimal safety guards / early returns to prevent null/None crashes while preserving behavior.")
    out.append("")

    # 2. Root Cause Analysis
    out.append("## 2. Root Cause Analysis")
    out.append("")
    out.append("**Where the bug originates**:")
    if rel_touched:
        out.append(f"- Primary suspect file: `{rel_touched[0]}`")
    else:
        out.append("- Primary suspect file: `<unknown>`")
    out.append("")

    out.append("**Runtime path (stack trace / call chain)**:")
    if trace:
        out.append(_code_block("text", str(trace)))
    else:
        # if your stack parser wasn’t available or user didn’t paste trace
        out.append("- No stack trace resolved from issue text.")
        if entry_fn:
            out.append(f"- Entry function hint: `{entry_fn}`")
        out.append("")

    if impacted_count is not None:
        out.append("**Dependency blast radius**:")
        out.append(f"- Impacted files count: `{impacted_count}`")
        if isinstance(impacted_files, (list, tuple)) and impacted_files:
            shown = list(impacted_files[:30])
            out.append("- Sample impacted files:")
            for p in shown:
                out.append(f"  - `{_relpath(repo_path, p)}`")
            if len(impacted_files) > 30:
                out.append("  - `...(truncated)`")
        out.append("")

    out.append("**Why existing guards failed**:")
    out.append("- The agent assumes missing/insufficient null/None guards around chained attribute access or unsafe dereference.")
    out.append("- Verification is based on AST/safety heuristics, not full execution replay.")
    out.append("")

    # 3. Proposed Changes
    out.append("## 3. Proposed Changes")
    out.append("")
    if rel_touched:
        out.append("**Files affected:**")
        for p in rel_touched:
            out.append(f"- `{p}`")
    else:
        out.append("**Files affected:** _None_")
    out.append("")
    out.append("**Scope justification:**")
    out.append("- Changes are intentionally minimal and localized to reduce regression risk.")
    if impacted_count:
        out.append(f"- Dependency impact detected (`{impacted_count}`), so proposal includes blast-radius awareness.")
    out.append("")

    # 4. Code Comparison (Full Context)
    out.append("## 4. Code Comparison (Full Context)")
    out.append("")
    if not old_new_files:
        out.append("_No file content available._")
        out.append("")
    else:
        for (fp, old_content, new_content) in old_new_files:
            rel = _relpath(repo_path, fp)
            lang = _infer_lang(fp)
            out.append(f"### File: `{rel}`")
            out.append("")
            out.append("**--- OLD ---**")
            out.append(_code_block(lang, old_content))
            out.append("**--- NEW ---**")
            out.append(_code_block(lang, new_content))
            out.append("")

    # 5. Safety & Confidence Evaluation
    out.append("## 5. Safety & Confidence Evaluation")
    out.append("")
    out.append("**Checks**:")
    out.append(_fmt_kv_block(checks or {}))
    if safety_reason:
        out.append(f"- **Safety reason**: `{safety_reason}`")
    out.append("")

    if ast_info:
        out.append("**AST notes**:")
        out.append(_code_block("json", str(ast_info)))
        out.append("")

    if ci_failures:
        out.append("**CI/Test signals**:")
        out.append(_code_block("text", str(ci_failures)))
        out.append("")

    out.append("**Confidence score rationale (high-level)**:")
    out.append("- Confidence is derived from: stack-trace resolution, AST verification, safety verification, blast radius, and LLM involvement.")
    out.append("")

    # 6. Risk Assessment
    out.append("## 6. Risk Assessment")
    out.append("")
    if risk_notes:
        out.append("**What could still break / risks:**")
        for r in risk_notes:
            out.append(f"- {_md_escape(str(r))}")
    else:
        out.append("**What could still break / risks:**")
        out.append("- _None reported by the agent._")
    out.append("")
    out.append("**What was intentionally not modified:**")
    out.append("- No refactors, no new imports, no new top-level definitions, no behavior redesign.")
    out.append("")
    out.append("**Edge cases not covered:**")
    out.append("- The agent does not fully execute tests locally; it relies on static gates and external CI for runtime assurance.")
    out.append("")

    # 7. Decision Outcome
    out.append("## 7. Decision Outcome")
    out.append("")
    out.append(f"**Outcome**: `{decision}`")
    out.append("")
    out.append("Interpretation:")
    out.append("- ✅ `APPLY`: Safe for merge under Tier-2 autonomy gates (confidence must be 1.0).")
    out.append("- ⚠️ `PROPOSE`: Draft/proposal only. No commits. Human review required.")
    out.append("- 🚫 `REJECT`: No changes should be applied; proposal may exist for reference only.")
    out.append("")

    # Add raw issue body for traceability (optional but helpful)
    out.append("## Appendix — Issue Body (for traceability)")
    out.append("")
    out.append(_code_block("text", body))

    return "\n".join(out)
