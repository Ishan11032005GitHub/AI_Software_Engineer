# app/agents/patch_generator.py
from __future__ import annotations

import re
from typing import Optional, Tuple, List

from app.agents.fix_memory import FixMemory
from app.agents.patch_generator_llm import propose_fix_with_llm


# ---------------- Rule-based patterns (fast & deterministic) ---------------- #

RISKY_RETURN = re.compile(
    r'^(\s*)return\s+([a-zA-Z_]\w*)\.([a-zA-Z_]\w*)\.([a-zA-Z_]\w*)(?:\s+#.*)?\s*$'
)

def rule_based_fix(file_content: str) -> Optional[str]:
    """
    Basic refactor rule:
    return a.b.c   =>  safe guarded return

    if a.b:
        return a.b.c
    return None
    """
    if not file_content:
        return None

    lines = file_content.splitlines()
    new_lines: List[str] = []
    applied = False

    for line in lines:
        match = RISKY_RETURN.match(line)
        if match and not applied:
            indent, obj, mid, attr = match.groups()
            new_lines.append(f"{indent}if {obj}.{mid}:")
            new_lines.append(f"{indent}    return {obj}.{mid}.{attr}")
            new_lines.append(f"{indent}return None")
            applied = True
        else:
            new_lines.append(line)

    return "\n".join(new_lines) + "\n" if applied else None


# ------------------- MAIN CONTENT-GENERATION ENTRYPOINT ------------------- #

def generate_fixed_content(
    *,
    issue: dict,
    file_content: str,
    file_path: str,
    store=None,                       # <-- REQUIRED
) -> Tuple[Optional[str], bool, bool]:
    """
    RETURNS:
        (new_content | None, used_llm, used_rule_based)

    Pipeline:
        1. Try rule-based patch (fast, deterministic)
        2. Query FixMemory for similar patches
        3. LLM generates change (with memory injected for guidance)
    """

    # 1) Rule based
    fixed = rule_based_fix(file_content)
    if fixed:
        print("🧠 Rule-based fix applied")
        return fixed, False, True

    # 2) Memory-assisted Retrieval
    memory_hint = ""
    if store:
        fix_mem = FixMemory(store)
        memories = fix_mem.retrieve_similar(file_content)

        if memories:
            memory_hint = "\n".join(
                f"\n--- Memory Patch ---\nOLD:\n{m['old'][:400]}\nNEW:\n{m['new'][:400]}"
                for m in memories
            )
            print(f"🔎 Memory use: {len(memories)} related patches found")

    # prepare combined issue text
    issue_text = (issue.get("title") or "") + "\n" + (issue.get("body") or "")

    # 3) LLM Patch generation — memory injected in prompt
    llm_prompt = f"""
Act as a senior software engineer. Create the *minimal safe fix*.

Issue:
{issue_text}

File content:
{file_content[:5000]}

Relevant prior successful patches (use pattern if applicable):
{memory_hint or "None"}

Rules:
- produce full updated file content
- keep code style consistent
- do NOT hallucinate large rewrites
- must compile after change
"""

    llm_fixed = propose_fix_with_llm(llm_prompt, file_path, file_content)

    if llm_fixed and llm_fixed.strip() != file_content.strip():
        print("✨ LLM fix generated")
        return llm_fixed, True, False

    return None, False, False
