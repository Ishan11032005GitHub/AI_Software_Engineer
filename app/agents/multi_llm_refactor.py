import os
from typing import List, Tuple
from openai import OpenAI

# uses your existing environment variable OPENAI_API_KEY
llm = OpenAI()

SYSTEM_MSG = """
You are a refactoring engine.
Given multiple related Python files and a change in one,
rewrite ALL affected files to maintain consistency.

Rules:
1. You MUST return only code diff sections.
2. Do NOT invent new behavior unless required.
3. Only modify code related to the function/call change.
4. Maintain original formatting as much as possible.
"""

def generate_multifile_refactor(
    primary_file: str,
    primary_new: str,
    impacted_files: List[Tuple[str, str]],
    fn_name: str
) -> List[Tuple[str, str]]:
    """
    Input:
        primary_file: path
        primary_new: rewritten file content
        impacted_files: [(path, old_content), ...]
        fn_name: name of modified function

    Output:
        list of (path, new_content)
    """

    files_text = f"# PRIMARY FILE ({primary_file})\n{primary_new}\n\n"
    for p, content in impacted_files:
        files_text += f"# FILE: {p}\n{content}\n\n"

    prompt = f"""
Primary function changed: {fn_name}

Rewrite all files where needed so that code compiles and calls remain valid.
Return final file contents as blocks:

<file path="path/to/file.py">
...updated content...
</file>
    """

    resp = llm.chat.completions.create(
        model="gpt-4.1",
        messages=[
            {"role":"system", "content":SYSTEM_MSG},
            {"role":"user", "content":files_text + "\n" + prompt}
        ],
        max_tokens=8000,
        temperature=0.2
    )

    text = resp.choices[0].message.content
    results = []
    blocks = text.split("<file path=")
    for blk in blocks[1:]:
        path = blk.split('">')[0].strip()
        code = blk.split('">')[1].split("</file>")[0].strip()
        results.append((path, code))

    return results
