import re
from typing import Optional, Dict


STACK_FRAME_RE = re.compile(
    r"File [\"']([^\"']+)[\"'], line (\d+), in ([\w_]+)"
)


def parse_stack_trace(text: str) -> Optional[Dict]:
    """
    Extracts most likely user-code frame from a Python stack trace.

    Returns:
        {"file": "...", "line": int, "function": "..."} or None
    """
    if not text:
        return None

    matches = STACK_FRAME_RE.findall(text)
    if not matches:
        return None

    for file, line, func in reversed(matches):
        if is_user_file(file):
            return {"file": file, "line": int(line), "function": func}

    return None


def is_user_file(file_path: str) -> bool:
    blacklist = (
        "site-packages",
        "lib/python",
        "<string>",
    )
    fp = (file_path or "").lower()
    return fp and not any(b in fp for b in blacklist)
