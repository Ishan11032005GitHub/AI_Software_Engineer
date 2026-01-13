# app/storage/vector_memory.py
from __future__ import annotations

import json
import math
from dataclasses import dataclass
from typing import List, Tuple

from app.storage.artifact_store import ArtifactStore


@dataclass
class MemoryItem:
    id: int
    issue_number: int
    summary: str
    embedding: list[float]


def _simple_embed(text: str, vocab: dict[str, int]) -> list[float]:
    """
    Dumb bag-of-words embedding: each token -> index, frequency count.
    This is NOT SOTA, but it is deterministic and local.
    """
    vec = [0.0] * len(vocab)
    for token in text.lower().split():
        if token in vocab:
            vec[vocab[token]] += 1.0
    norm = math.sqrt(sum(x * x for x in vec)) or 1.0
    return [x / norm for x in vec]


def cosine(a: list[float], b: list[float]) -> float:
    return sum(x * y for x, y in zip(a, b))


def store_memory_item(
    store: ArtifactStore,
    owner: str,
    repo: str,
    issue_number: int,
    summary: str,
) -> None:
    vocab = store.get_vocab()
    emb = _simple_embed(summary, vocab)
    store.insert_memory(owner, repo, issue_number, summary, json.dumps(emb))


def retrieve_similar_memories(
    store: ArtifactStore,
    owner: str,
    repo: str,
    query: str,
    top_k: int = 5,
) -> List[MemoryItem]:
    vocab = store.get_vocab()
    q_emb = _simple_embed(query, vocab)

    rows = store.get_all_memories(owner, repo)
    scored: List[Tuple[float, MemoryItem]] = []
    for r in rows:
        emb = json.loads(r.embedding_json)
        score = cosine(q_emb, emb)
        scored.append(
            (score, MemoryItem(
                id=r.id,
                issue_number=r.issue_number,
                summary=r.summary,
                embedding=emb,
            ))
        )

    scored.sort(key=lambda x: x[0], reverse=True)
    return [item for score, item in scored[:top_k]]
