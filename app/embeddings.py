"""Prompt embeddings for the semantic cache.

Two backends, picked at runtime via env. No backend is loaded until the first
`embed()` call so the import path stays cheap for tests / non-semantic setups.

Activation:
    CLEARVIEW_EMBEDDING_BACKEND=openai   # default when CLEARVIEW_EMBEDDING_MODEL points OpenAI
                                = local    # lazy-imports sentence-transformers (optional dep)
                                = disabled # short-circuit: embed() returns None
    CLEARVIEW_EMBEDDING_MODEL=text-embedding-3-small         (openai)
                            = sentence-transformers/all-MiniLM-L6-v2 (local default)

OpenAI path uses `litellm.embedding`, so any provider litellm supports
(`openai/`, `bedrock/`, etc.) plugs in for free — no new pip dep.

Local path imports `sentence_transformers` only when first invoked. Operators
who don't want the dep just leave the backend at openai or disabled.
"""
from __future__ import annotations

import logging
import math
import os
from typing import Any

log = logging.getLogger("clearview.embeddings")

_DEFAULT_OPENAI_MODEL = "text-embedding-3-small"
_DEFAULT_LOCAL_MODEL = "sentence-transformers/all-MiniLM-L6-v2"

# Lazy singletons. Reset by tests via reset_cache().
_LOCAL_MODEL: Any = None
_LOCAL_MODEL_NAME: str | None = None


def backend() -> str:
    """Resolve the active backend. `disabled` short-circuits all embed calls."""
    return (os.environ.get("CLEARVIEW_EMBEDDING_BACKEND") or "openai").lower()


def model_id() -> str:
    """Resolved model id for the active backend."""
    override = os.environ.get("CLEARVIEW_EMBEDDING_MODEL")
    if override:
        return override
    if backend() == "local":
        return _DEFAULT_LOCAL_MODEL
    return _DEFAULT_OPENAI_MODEL


def is_enabled() -> bool:
    return backend() != "disabled"


# ---------------------------------------------------------------------------
# Backends
# ---------------------------------------------------------------------------

def _embed_openai(text: str) -> list[float] | None:
    """Call litellm.embedding for the OpenAI-style backend.

    Returns None on any failure — callers treat None as "no embedding,
    skip semantic lookup". Keeps the cache path resilient when keys/network
    blip without breaking the chat flow.
    """
    try:
        import litellm
        resp = litellm.embedding(model=model_id(), input=[text])
    except Exception as e:  # noqa: BLE001
        log.warning("openai embedding failed: %s", e)
        return None

    try:
        data = resp.get("data") if isinstance(resp, dict) else getattr(resp, "data", None)
        if not data:
            return None
        first = data[0]
        vec = first.get("embedding") if isinstance(first, dict) else getattr(first, "embedding", None)
        if vec is None:
            return None
        return [float(x) for x in vec]
    except Exception as e:  # noqa: BLE001
        log.warning("openai embedding parse failed: %s", e)
        return None


def _embed_local(text: str) -> list[float] | None:
    """Lazy-load sentence-transformers + cache the model in-process."""
    global _LOCAL_MODEL, _LOCAL_MODEL_NAME
    target = model_id()
    if _LOCAL_MODEL is None or _LOCAL_MODEL_NAME != target:
        try:
            from sentence_transformers import SentenceTransformer
        except ImportError:
            log.warning(
                "local embedding backend requested but sentence-transformers "
                "not installed; pip install sentence-transformers"
            )
            return None
        try:
            _LOCAL_MODEL = SentenceTransformer(target)
            _LOCAL_MODEL_NAME = target
        except Exception as e:  # noqa: BLE001
            log.warning("local embedding model load failed: %s", e)
            _LOCAL_MODEL = None
            _LOCAL_MODEL_NAME = None
            return None

    try:
        vec = _LOCAL_MODEL.encode(text, normalize_embeddings=False).tolist()
        return [float(x) for x in vec]
    except Exception as e:  # noqa: BLE001
        log.warning("local embedding encode failed: %s", e)
        return None


def embed(text: str) -> list[float] | None:
    """Dispatch to the active backend. Returns None when embeddings are
    disabled or the backend fails — caller falls back to no-semantic-lookup."""
    if not text or not text.strip():
        return None
    b = backend()
    if b == "disabled":
        return None
    if b == "local":
        return _embed_local(text)
    return _embed_openai(text)


# ---------------------------------------------------------------------------
# Math helpers
# ---------------------------------------------------------------------------

def cosine(a: list[float] | None, b: list[float] | None) -> float:
    """Cosine similarity in [-1, 1]. Returns 0.0 when either side is empty or
    has zero norm — these are non-matches by definition."""
    if not a or not b:
        return 0.0
    if len(a) != len(b):
        return 0.0
    dot = 0.0
    na = 0.0
    nb = 0.0
    for x, y in zip(a, b):
        dot += x * y
        na += x * x
        nb += y * y
    if na <= 0.0 or nb <= 0.0:
        return 0.0
    return dot / (math.sqrt(na) * math.sqrt(nb))


# ---------------------------------------------------------------------------
# Serialization for SQLite BLOB storage
# ---------------------------------------------------------------------------

def to_blob(vec: list[float] | None) -> bytes:
    """Pack a vector as little-endian float32 bytes. 4 bytes/dim. Returns
    empty bytes when input is None/empty so callers can store unconditionally."""
    if not vec:
        return b""
    import struct
    return struct.pack(f"<{len(vec)}f", *vec)


def from_blob(blob: bytes | None) -> list[float] | None:
    """Inverse of to_blob. None / empty → None."""
    if not blob:
        return None
    import struct
    n = len(blob) // 4
    if n == 0:
        return None
    try:
        return list(struct.unpack(f"<{n}f", blob[: n * 4]))
    except Exception:  # noqa: BLE001
        return None


# ---------------------------------------------------------------------------
# Test seam
# ---------------------------------------------------------------------------

def _reset_local_model_cache() -> None:
    """Drop the in-process sentence-transformers singleton. Tests only."""
    global _LOCAL_MODEL, _LOCAL_MODEL_NAME
    _LOCAL_MODEL = None
    _LOCAL_MODEL_NAME = None
