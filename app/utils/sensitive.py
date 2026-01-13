from __future__ import annotations

import re

SENSITIVE_PATTERNS = [
    r"\bauth\b",
    r"\boauth\b",
    r"\btoken\b",
    r"\bidentity\b",
    r"\bml\b",
    r"\bmodel\b",
    r"\binfra\b",
    r"\bterraform\b",
    r"\bk8s\b",
    r"\bkubernetes\b",
    r"\bpipeline\b",
    r"\betl\b",
    r"\bschema\b",
    r"\bdatabase\b",
    r"\bmigration\b",
]

_sensitive_re = re.compile("|".join(SENSITIVE_PATTERNS), re.IGNORECASE)


def touches_sensitive_area(file_path: str, title: str, body: str) -> bool:
    text = f"{file_path}\n{title}\n{body}"
    return bool(_sensitive_re.search(text))
