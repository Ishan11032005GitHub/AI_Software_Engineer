# app/ci/test_failure_parser.py
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Dict, List, Optional


PY_FRAME_RE = re.compile(r'File [\'"]([^\'"]+)[\'"], line (\d+), in ([\w_]+)')

# pytest summary lines often look like:
#   FAILED tests/test_auth.py::test_login - AssertionError: ...
#   FAILED tests/test_auth.py::TestAuth::test_login
PYTEST_FAILED_LINE_RE = re.compile(
    r"^\s*FAILED\s+([^\s:]+\.py)(?:::([^\s]+))?",
    re.MULTILINE
)

# Another common footer:
#   === 2 failed, 10 passed in 3.21s ===
PYTEST_FOOTER_RE = re.compile(r"=+\s*\d+\s+failed.*=+", re.IGNORECASE)


@dataclass(frozen=True)
class CIParsedEvidence:
    has_failure: bool
    failing_files_ranked: List[str]
    failing_function: Optional[str]
    failing_tests_ranked: List[str]
    failing_test_files_ranked: List[str]
    excerpt: str
    raw_frame_count: int


def _normalize_path(p: str) -> str:
    p = (p or "").strip().replace("\\", "/")
    parts = p.split("/")
    return "/".join(parts[-6:]) if len(parts) > 6 else p


def parse_ci_logs(log_text: str, *, max_excerpt_chars: int = 2400) -> CIParsedEvidence:
    if not log_text or not isinstance(log_text, str):
        return CIParsedEvidence(False, [], None, [], [], "", 0)

    frames = PY_FRAME_RE.findall(log_text)
    raw_count = len(frames)

    file_hits: Dict[str, int] = {}
    func_hits: Dict[str, int] = {}

    for file_path, line, func in frames:
        fp = _normalize_path(file_path)
        file_hits[fp] = file_hits.get(fp, 0) + 1
        if func:
            func_hits[func] = func_hits.get(func, 0) + 1

    ranked_files = sorted(file_hits.keys(), key=lambda k: file_hits[k], reverse=True)

    failing_func = None
    if func_hits:
        failing_func = max(func_hits.keys(), key=lambda k: func_hits[k])

    # pytest nodeid extraction
    test_hits: Dict[str, int] = {}
    test_file_hits: Dict[str, int] = {}

    for m in PYTEST_FAILED_LINE_RE.finditer(log_text):
        test_file = _normalize_path(m.group(1) or "")
        node_part = (m.group(2) or "").strip()

        nodeid = test_file
        if node_part:
            nodeid = f"{test_file}::{node_part}"

        test_hits[nodeid] = test_hits.get(nodeid, 0) + 1
        test_file_hits[test_file] = test_file_hits.get(test_file, 0) + 1

    failing_tests_ranked = sorted(test_hits.keys(), key=lambda k: test_hits[k], reverse=True)
    failing_test_files_ranked = sorted(test_file_hits.keys(), key=lambda k: test_file_hits[k], reverse=True)

    # excerpt selection: prefer around pytest footer, else around last traceback
    excerpt = ""
    m = PYTEST_FOOTER_RE.search(log_text)
    if m:
        start = max(0, m.start() - 1200)
        excerpt = log_text[start:start + max_excerpt_chars]
    else:
        last = None
        for match in PY_FRAME_RE.finditer(log_text):
            last = match
        if last:
            start = max(0, last.start() - 800)
            excerpt = log_text[start:start + max_excerpt_chars]
        else:
            excerpt = log_text[:max_excerpt_chars]

    excerpt = excerpt.strip()

    has_any_failure = bool(ranked_files or failing_tests_ranked)

    return CIParsedEvidence(
        has_failure=has_any_failure,
        failing_files_ranked=ranked_files[:10],
        failing_function=failing_func,
        failing_tests_ranked=failing_tests_ranked[:10],
        failing_test_files_ranked=failing_test_files_ranked[:10],
        excerpt=excerpt,
        raw_frame_count=raw_count,
    )
