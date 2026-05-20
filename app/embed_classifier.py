"""Embedding-based tier classifier (routing-accuracy Layer 3).

A k-nearest-neighbour classifier over a labelled corpus of (prompt, tier)
examples. Cheaper and faster than the LLM classifier once the corpus is
embedded — no per-call completion. Used as an *added signal*, never as the
sole authority: the router falls back to it only when the LLM classifier is
disabled/unavailable, and otherwise its output is recorded as another
disagreement signal.

Corpus seeding:
  - eval/fixtures.json ({prompt, expected_tier}) ships as the baseline corpus.
  - Operators can append learned examples (e.g. from the feedback corpus) by
    passing extra (prompt, tier) pairs to `build_index`.

Embeddings reuse app.embeddings (same backend/env as the semantic cache), so
when CLEARVIEW_EMBEDDING_BACKEND=disabled this classifier disables itself.
The index is built lazily on first classify() and cached in-process; call
reset() in tests to rebuild.
"""
from __future__ import annotations

import json
import logging
import os
from collections import defaultdict
from pathlib import Path

from . import embeddings as _emb

log = logging.getLogger("clearview.embed_classifier")

_DEFAULT_K = 5
_FIXTURES = Path(__file__).resolve().parent.parent / "eval" / "fixtures.json"

# In-process index: list of (tier, vector). Rebuilt by build_index / reset.
_INDEX: list[tuple[str, list[float]]] | None = None


def enabled() -> bool:
    """On only when explicitly opted in AND an embedding backend is live."""
    return (os.environ.get("CLEARVIEW_EMBED_CLASSIFIER", "0").strip() == "1"
            and _emb.is_enabled())


def _k() -> int:
    try:
        return max(1, int(os.environ.get("CLEARVIEW_EMBED_CLASSIFIER_K", _DEFAULT_K)))
    except ValueError:
        return _DEFAULT_K


def _load_fixture_corpus() -> list[tuple[str, str]]:
    """Return [(prompt, tier), ...] from eval/fixtures.json. Empty on any error."""
    try:
        data = json.loads(_FIXTURES.read_text())
    except Exception as e:  # noqa: BLE001
        log.warning("embed classifier: could not load fixtures corpus: %s", e)
        return []
    out = []
    for row in data:
        prompt = row.get("prompt")
        tier = row.get("expected_tier")
        if prompt and tier:
            out.append((prompt, tier))
    return out


def build_index(extra: list[tuple[str, str]] | None = None) -> int:
    """Embed the corpus (fixtures + any extra pairs) and cache the index.
    Returns the number of indexed examples. Examples that fail to embed are
    skipped (the backend may be down or rate-limited)."""
    global _INDEX
    corpus = _load_fixture_corpus() + list(extra or [])
    index: list[tuple[str, list[float]]] = []
    for prompt, tier in corpus:
        vec = _emb.embed(prompt)
        if vec:
            index.append((tier, vec))
    _INDEX = index
    log.info("embed classifier index built: %d/%d examples embedded",
             len(index), len(corpus))
    return len(index)


def reset() -> None:
    """Drop the cached index (tests / corpus refresh)."""
    global _INDEX
    _INDEX = None


def classify(prompt: str) -> tuple[str, float] | None:
    """Return (tier, confidence) via cosine-weighted kNN vote, or None when the
    classifier is unavailable (disabled, empty corpus, or prompt won't embed).

    confidence = winning tier's vote weight / total vote weight, in (0, 1].
    """
    if not enabled():
        return None
    global _INDEX
    if _INDEX is None:
        build_index()
    if not _INDEX:
        return None
    qv = _emb.embed(prompt)
    if not qv:
        return None

    scored = sorted(
        (( _emb.cosine(qv, vec), tier) for tier, vec in _INDEX),
        key=lambda t: t[0], reverse=True,
    )[: _k()]
    if not scored:
        return None

    weights: dict[str, float] = defaultdict(float)
    total = 0.0
    for sim, tier in scored:
        w = max(0.0, sim)  # ignore anti-correlated neighbours
        weights[tier] += w
        total += w
    if total <= 0.0:
        return None
    winner = max(weights.items(), key=lambda kv: kv[1])
    return winner[0], round(winner[1] / total, 4)
