# app/context/ranker.py
import os
import re
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

from app.context.graph_store import connect, init_db

# quick symbol extract: FooBar, foo_bar, fooBar
SYMBOL_RE = re.compile(r"\b[A-Za-z_][A-Za-z0-9_]{2,}\b")


@dataclass
class RankedFile:
    path: str          # repo-relative
    score: float
    reasons: List[str]


def _tokenize(text: str) -> List[str]:
    if not text:
        return []
    toks = [t.lower() for t in SYMBOL_RE.findall(text)]
    # de-dup preserving order
    seen = set()
    out = []
    for t in toks:
        if t not in seen:
            out.append(t)
            seen.add(t)
    return out[:80]


def rank_files(
    db_path: str,
    repo_path: str,
    issue_text: str,
    keywords: List[str],
    trace_file: Optional[str] = None,
    trace_function: Optional[str] = None,
    top_k: int = 8,
) -> List[RankedFile]:
    """
    Ranking signals (high -> low):
      1) exact trace file match
      2) trace function defined in file
      3) symbol hits in defs table
      4) keyword hits in file content (cheap partial read)
      5) graph proximity: files importing/ imported-by the best symbol file
    """
    repo_path = os.path.normpath(repo_path)
    keywords = [k.lower() for k in (keywords or []) if k]
    issue_tokens = _tokenize(issue_text)

    with connect(db_path) as conn:
        init_db(conn)

        # 0) Build candidate pool from defs hits
        candidates: Dict[str, RankedFile] = {}

        def bump(path: str, delta: float, reason: str):
            if not path:
                return
            rf = candidates.get(path)
            if not rf:
                rf = RankedFile(path=path, score=0.0, reasons=[])
                candidates[path] = rf
            rf.score += delta
            rf.reasons.append(reason)

        # 1) Trace file hard boost
        if trace_file:
            tf = trace_file.replace("\\", "/")
            # normalize if it's absolute
            if os.path.isabs(tf):
                tf = os.path.relpath(tf, repo_path).replace("\\", "/")
            bump(tf, 100.0, "stack-trace file match (+100)")

        # 2) Trace function appears in defs
        if trace_function:
            cur = conn.execute("SELECT path FROM defs WHERE kind='function' AND lower(symbol)=lower(?)", (trace_function,))
            for (p,) in cur.fetchall():
                bump(p, 25.0, f"trace function '{trace_function}' defined here (+25)")

        # 3) Symbol hits (defs)
        if issue_tokens:
            # take most meaningful tokens only
            for tok in issue_tokens[:40]:
                cur = conn.execute(
                    "SELECT path, kind, symbol FROM defs WHERE lower(symbol)=?",
                    (tok.lower(),),
                )
                for p, kind, sym in cur.fetchall():
                    bump(p, 6.0, f"def hit '{sym}' ({kind}) (+6)")

        # 4) Keyword hits by partial file read (fast heuristic)
        #    We only scan a limited set of files:
        #    - those already in candidates
        #    - plus the most-recent ~200 python files if candidates empty
        file_list: List[str] = list(candidates.keys())

        if not file_list:
            cur = conn.execute("SELECT path FROM files WHERE lang='python' ORDER BY mtime DESC LIMIT 200")
            file_list = [r[0] for r in cur.fetchall()]

        for p in file_list:
            abs_path = os.path.join(repo_path, p)
            if not os.path.exists(abs_path):
                continue
            try:
                with open(abs_path, "r", encoding="utf-8", errors="ignore") as f:
                    chunk = f.read(5000).lower()
            except Exception:
                continue

            hit = 0
            for kw in keywords:
                if kw and kw in chunk:
                    hit += 1
            if hit:
                bump(p, 2.0 * hit, f"keyword hits x{hit} (+{2.0*hit:.1f})")

        # 5) Graph proximity: if we have a top file, boost its neighbors
        if candidates:
            best = max(candidates.values(), key=lambda x: x.score).path

            # out-neighbors (imports)
            cur = conn.execute("SELECT dst_path FROM edges WHERE src_path=?", (best,))
            for (dst,) in cur.fetchall():
                bump(dst, 3.0, f"import-neighbor of {best} (+3)")

            # in-neighbors (imported by)
            cur = conn.execute("SELECT src_path FROM edges WHERE dst_path=?", (best,))
            for (src,) in cur.fetchall():
                bump(src, 3.0, f"reverse-import neighbor of {best} (+3)")

        ranked = sorted(candidates.values(), key=lambda x: x.score, reverse=True)

        # keep only existing files
        out = []
        for r in ranked:
            abs_path = os.path.join(repo_path, r.path)
            if os.path.exists(abs_path):
                out.append(r)
            if len(out) >= top_k:
                break

        return out
