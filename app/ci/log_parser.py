import re
from typing import List

from app.ci.models import TestFailure


PYTEST_FAIL_RE = re.compile(
    r"_{3,}\s*(.+?)\s*_{3,}.*?E\s+(.*?)(?:\n|$)",
    re.DOTALL,
)

PY_TRACE_RE = re.compile(
    r'File "([^"]+)", line (\d+), in ([\w_]+)'
)


def parse_ci_logs(logs: str) -> List[TestFailure]:
    """
    Extracts test failures from CI logs.
    Python-first (pytest/unittest style).
    """
    failures: List[TestFailure] = []

    if not logs:
        return failures

    for match in PYTEST_FAIL_RE.finditer(logs):
        test_name = match.group(1).strip()
        message = match.group(2).strip()

        file = None
        line = None

        trace_match = PY_TRACE_RE.search(message)
        if trace_match:
            file = trace_match.group(1)
            line = int(trace_match.group(2))

        failures.append(
            TestFailure(
                test_name=test_name,
                file=file,
                line=line,
                message=message,
                raw=match.group(0),
            )
        )

    return failures
