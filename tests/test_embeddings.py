"""Tests for the embeddings module.

Network-free: both backends are exercised against stubbed sources.
"""
from __future__ import annotations

import pytest


class TestBackendResolution:
    def test_default_is_openai(self, monkeypatch):
        from app import embeddings as E
        monkeypatch.delenv("CLEARVIEW_EMBEDDING_BACKEND", raising=False)
        assert E.backend() == "openai"
        assert E.is_enabled() is True

    def test_disabled_short_circuits(self, monkeypatch):
        from app import embeddings as E
        monkeypatch.setenv("CLEARVIEW_EMBEDDING_BACKEND", "disabled")
        assert E.is_enabled() is False
        assert E.embed("hi") is None

    def test_default_model_per_backend(self, monkeypatch):
        from app import embeddings as E
        monkeypatch.delenv("CLEARVIEW_EMBEDDING_MODEL", raising=False)

        monkeypatch.setenv("CLEARVIEW_EMBEDDING_BACKEND", "openai")
        assert E.model_id() == "text-embedding-3-small"

        monkeypatch.setenv("CLEARVIEW_EMBEDDING_BACKEND", "local")
        assert E.model_id() == "sentence-transformers/all-MiniLM-L6-v2"

    def test_model_override(self, monkeypatch):
        from app import embeddings as E
        monkeypatch.setenv("CLEARVIEW_EMBEDDING_MODEL", "custom-model-id")
        assert E.model_id() == "custom-model-id"


class TestCosine:
    def test_parallel_vectors(self):
        from app.embeddings import cosine
        assert cosine([1.0, 0.0, 0.0], [2.0, 0.0, 0.0]) == pytest.approx(1.0)

    def test_perpendicular(self):
        from app.embeddings import cosine
        assert cosine([1.0, 0.0], [0.0, 1.0]) == pytest.approx(0.0)

    def test_opposite(self):
        from app.embeddings import cosine
        assert cosine([1.0, 0.0], [-1.0, 0.0]) == pytest.approx(-1.0)

    def test_empty_or_mismatched(self):
        from app.embeddings import cosine
        assert cosine([], [1.0]) == 0.0
        assert cosine(None, [1.0]) == 0.0
        assert cosine([1.0, 0.0], [1.0]) == 0.0  # length mismatch
        assert cosine([0.0, 0.0], [1.0, 0.0]) == 0.0  # zero-norm side


class TestBlobRoundTrip:
    def test_round_trip(self):
        from app.embeddings import to_blob, from_blob
        vec = [0.5, -1.0, 2.0, 0.0, 3.14]
        blob = to_blob(vec)
        out = from_blob(blob)
        assert out is not None
        assert len(out) == len(vec)
        for a, b in zip(out, vec):
            assert a == pytest.approx(b, rel=1e-5)

    def test_none_inputs(self):
        from app.embeddings import to_blob, from_blob
        assert to_blob(None) == b""
        assert to_blob([]) == b""
        assert from_blob(None) is None
        assert from_blob(b"") is None


class TestOpenAIBackend:
    def test_embed_returns_vector_from_litellm(self, monkeypatch):
        from app import embeddings as E
        monkeypatch.setenv("CLEARVIEW_EMBEDDING_BACKEND", "openai")

        def fake_embedding(model, input):
            assert input == ["hello world"]
            return {"data": [{"embedding": [0.1, 0.2, 0.3]}]}

        import litellm
        monkeypatch.setattr(litellm, "embedding", fake_embedding)

        out = E.embed("hello world")
        assert out == [pytest.approx(0.1), pytest.approx(0.2), pytest.approx(0.3)]

    def test_embed_returns_none_on_litellm_failure(self, monkeypatch):
        from app import embeddings as E
        monkeypatch.setenv("CLEARVIEW_EMBEDDING_BACKEND", "openai")

        def boom(**kwargs):
            raise RuntimeError("network down")

        import litellm
        monkeypatch.setattr(litellm, "embedding", boom)

        assert E.embed("hi") is None

    def test_embed_returns_none_on_empty_input(self, monkeypatch):
        from app import embeddings as E
        monkeypatch.setenv("CLEARVIEW_EMBEDDING_BACKEND", "openai")
        assert E.embed("") is None
        assert E.embed("   ") is None


class TestLocalBackend:
    def test_returns_none_when_dep_missing(self, monkeypatch):
        from app import embeddings as E
        monkeypatch.setenv("CLEARVIEW_EMBEDDING_BACKEND", "local")
        E._reset_local_model_cache()

        # Force the import to fail by removing sentence_transformers from sys.modules
        # path. We rely on the fact that the dep is genuinely optional and almost
        # certainly not installed in CI.
        import builtins
        real_import = builtins.__import__

        def fail_import(name, *args, **kwargs):
            if name == "sentence_transformers":
                raise ImportError("not installed")
            return real_import(name, *args, **kwargs)

        monkeypatch.setattr(builtins, "__import__", fail_import)

        assert E.embed("hi") is None
