# app/eval/offline_eval.py

from __future__ import annotations

import csv
import json
import sqlite3
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Tuple


# -----------------------------
# Data structures
# -----------------------------

@dataclass
class RunRecord:
    owner: str
    repo: str
    issue_number: int
    confidence: float
    decision: str
    meta: dict


@dataclass
class GroundTruth:
    owner: str
    repo: str
    issue_number: int
    expected_decision: str       # e.g. "APPLY", "PROPOSE", "NO_ACTION"
    accepted: Optional[bool] = None  # whether maintainers accepted the bot fix


@dataclass
class EvalConfig:
    db_path: Path
    labels_csv: Optional[Path] = None  # optional ground-truth file


@dataclass
class EvalResult:
    total_runs: int
    by_decision: Dict[str, int]
    by_bucket: Dict[str, int]
    auto_apply_accuracy: Optional[float]
    auto_propose_accuracy: Optional[float]


# -----------------------------
# Loading from SQLite
# -----------------------------

def load_runs(db_path: Path) -> List[RunRecord]:
    """
    Load agent_runs from the SQLite DB.

    Expected schema (what ArtifactStore.store_run writes):
      agent_runs(
        id INTEGER PK,
        owner TEXT,
        repo TEXT,
        issue_number INTEGER,
        file_path TEXT,
        confidence REAL,
        decision TEXT,
        meta_json TEXT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
      )
    """
    if not db_path.exists():
        raise FileNotFoundError(f"SQLite db not found at {db_path}")

    conn = sqlite3.connect(str(db_path))
    try:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT owner, repo, issue_number, confidence, decision, meta_json
            FROM agent_runs
            ORDER BY created_at DESC
            """
        )
        rows = cur.fetchall()
    finally:
        conn.close()

    runs: List[RunRecord] = []
    for owner, repo, issue, conf, decision, meta_json in rows:
        try:
            meta = json.loads(meta_json) if meta_json else {}
        except Exception:
            meta = {}

        runs.append(
            RunRecord(
                owner=owner,
                repo=repo,
                issue_number=int(issue),
                confidence=float(conf or 0.0),
                decision=decision or "UNKNOWN",
                meta=meta,
            )
        )
    return runs


# -----------------------------
# Ground truth CSV loader
# -----------------------------

def load_ground_truth(csv_path: Path) -> Dict[Tuple[str, str, int], GroundTruth]:
    """
    CSV format:

      owner,repo,issue_number,expected_decision,accepted
      Ishan11032005GitHub,agent-testrepo,1,APPLY,true

    - expected_decision: "APPLY" | "PROPOSE" | "NO_ACTION" | ...
    - accepted: optional "true"/"false"/""  (case-insensitive)
    """
    truth: Dict[Tuple[str, str, int], GroundTruth] = {}

    with csv_path.open("r", newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            try:
                owner = row["owner"].strip()
                repo = row["repo"].strip()
                issue_number = int(row["issue_number"])
                expected = row["expected_decision"].strip().upper()

                accepted_raw = row.get("accepted", "").strip().lower()
                accepted: Optional[bool]
                if accepted_raw in ("true", "1", "yes", "y"):
                    accepted = True
                elif accepted_raw in ("false", "0", "no", "n"):
                    accepted = False
                else:
                    accepted = None

                truth[(owner, repo, issue_number)] = GroundTruth(
                    owner=owner,
                    repo=repo,
                    issue_number=issue_number,
                    expected_decision=expected,
                    accepted=accepted,
                )
            except Exception:
                # Skip bad rows instead of crashing the eval
                continue

    return truth


# -----------------------------
# Metric computation
# -----------------------------

def _bucket_for_conf(conf: float) -> str:
    """
    Cheap reliability histogram buckets.
    """
    if conf < 0.2:
        return "[0.0, 0.2)"
    if conf < 0.4:
        return "[0.2, 0.4)"
    if conf < 0.6:
        return "[0.4, 0.6)"
    if conf < 0.8:
        return "[0.6, 0.8)"
    if conf < 0.9:
        return "[0.8, 0.9)"
    return "[0.9, 1.0]"


def evaluate_runs(
    runs: List[RunRecord],
    truth_map: Optional[Dict[Tuple[str, str, int], GroundTruth]] = None,
) -> EvalResult:
    by_decision: Dict[str, int] = {}
    by_bucket: Dict[str, int] = {}

    total_runs = len(runs)

    # Accuracy metrics (only if we have labels)
    auto_apply_correct = 0
    auto_apply_total = 0

    auto_propose_correct = 0
    auto_propose_total = 0

    for r in runs:
        by_decision[r.decision] = by_decision.get(r.decision, 0) + 1

        bucket = _bucket_for_conf(r.confidence)
        by_bucket[bucket] = by_bucket.get(bucket, 0) + 1

        if truth_map:
            key = (r.owner, r.repo, r.issue_number)
            gt = truth_map.get(key)
            if not gt:
                continue

            expected = gt.expected_decision.upper()

            if r.decision == "APPLY":
                auto_apply_total += 1
                if r.decision == expected:
                    auto_apply_correct += 1

            if r.decision == "PROPOSE":
                auto_propose_total += 1
                if r.decision == expected:
                    auto_propose_correct += 1

    auto_apply_accuracy = (
        auto_apply_correct / auto_apply_total if auto_apply_total > 0 else None
    )
    auto_propose_accuracy = (
        auto_propose_correct / auto_propose_total if auto_propose_total > 0 else None
    )

    return EvalResult(
        total_runs=total_runs,
        by_decision=by_decision,
        by_bucket=by_bucket,
        auto_apply_accuracy=auto_apply_accuracy,
        auto_propose_accuracy=auto_propose_accuracy,
    )


# -----------------------------
# Pretty printing
# -----------------------------

def print_eval_report(result: EvalResult) -> None:
    print("\n================ OFFLINE EVAL REPORT ================")
    print(f"Total agent runs: {result.total_runs}")
    print("\nBy decision:")
    for dec, count in sorted(result.by_decision.items(), key=lambda kv: kv[0]):
        print(f"  {dec:10s} → {count}")

    print("\nConfidence buckets:")
    for bucket, count in sorted(result.by_bucket.items()):
        print(f"  {bucket:10s} → {count}")

    print("\nAccuracy (if labels provided):")
    if result.auto_apply_accuracy is not None:
        print(f"  APPLY decisions   → {result.auto_apply_accuracy * 100:.1f}% correct")
    else:
        print("  APPLY decisions   → n/a (no or zero ground-truth)")

    if result.auto_propose_accuracy is not None:
        print(f"  PROPOSE decisions → {result.auto_propose_accuracy * 100:.1f}% correct")
    else:
        print("  PROPOSE decisions → n/a (no or zero ground-truth)")

    print("=====================================================\n")


# -----------------------------
# CLI entrypoint
# -----------------------------

def main(argv: List[str] | None = None) -> None:
    """
    Usage:

      python -m app.eval.offline_eval path/to/agent.sqlite
      python -m app.eval.offline_eval path/to/agent.sqlite path/to/labels.csv

    - If labels.csv is omitted, you still get decision / confidence histograms.
    - If labels.csv is present, you also get basic accuracy numbers.
    """
    if argv is None:
        argv = sys.argv[1:]

    if not (1 <= len(argv) <= 2):
        print(
            "Usage:\n"
            "  python -m app.eval.offline_eval <sqlite_path> [labels_csv]\n"
        )
        sys.exit(1)

    db_path = Path(argv[0]).expanduser().resolve()
    labels_path: Optional[Path] = None
    if len(argv) == 2:
        labels_path = Path(argv[1]).expanduser().resolve()

    print(f"📊 Loading runs from {db_path}")
    runs = load_runs(db_path)

    truth_map = None
    if labels_path:
        print(f"📘 Loading ground-truth labels from {labels_path}")
        truth_map = load_ground_truth(labels_path)

    result = evaluate_runs(runs, truth_map)
    print_eval_report(result)


if __name__ == "__main__":
    main()
