from __future__ import annotations
from dataclasses import dataclass
from typing import Optional, List


@dataclass(frozen=True)
class TestFailure:
    test_name: Optional[str]
    file: Optional[str]
    line: Optional[int]
    message: str
    raw: str


@dataclass(frozen=True)
class CIResult:
    workflow_name: str
    run_id: int
    commit_sha: str
    failures: List[TestFailure]
    raw_logs: str
