"""Semantic-cache integration tests.

We stub the OpenAI embedding backend with a deterministic vector function so
similarity scores are reproducible without network.
"""
from __future__ import annotations

import json
import sqlite3

import pytest


def _stub_embed(monkeypatch, mapping: dict[str, list[float]]):
    """Patch `app.embeddings.embed` to look up a canned mapping. Anything not
    in the mapping returns None (no embedding) so the semantic path is
    inactive for those prompts."""
    from app import embeddings as E
    monkeypatch.setenv("CLEARVIEW_EMBEDDING_BACKEND", "openai")
    monkeypatch.setattr(E, "embed", lambda text: mapping.get(text))


def _patch_completion(monkeypatch, returns):
    """Force litellm.completion to a canned response."""
    from app import main
    from tests.conftest import FakeCompletion
    if isinstance(returns, FakeCompletion):
        monkeypatch.setattr(main.litellm, "completion", lambda **kw: returns)
    else:
        monkeypatch.setattr(main.litellm, "completion", returns)


class TestSemanticEnabled:
    def test_off_when_backend_disabled(self, monkeypatch):
        from app import cache
        monkeypatch.setenv("CLEARVIEW_EMBEDDING_BACKEND", "disabled")
        assert cache.semantic_enabled() is False

    def test_off_when_cache_disabled(self, monkeypatch):
        from app import cache
        monkeypatch.setenv("CLEARVIEW_CACHE_ENABLED", "0")
        assert cache.semantic_enabled() is False

    def test_off_when_semantic_flag_disabled(self, monkeypatch):
        from app import cache
        monkeypatch.setenv("CLEARVIEW_SEMANTIC_CACHE", "0")
        # Even with backend = openai, semantic-cache flag off should win.
        monkeypatch.setenv("CLEARVIEW_EMBEDDING_BACKEND", "openai")
        assert cache.semantic_enabled() is False

    def test_on_by_default(self, monkeypatch):
        from app import cache
        monkeypatch.delenv("CLEARVIEW_SEMANTIC_CACHE", raising=False)
        monkeypatch.setenv("CLEARVIEW_EMBEDDING_BACKEND", "openai")
        assert cache.semantic_enabled() is True


class TestSemanticLookup:
    def test_returns_none_when_no_matches(self, monkeypatch):
        from app import cache
        _stub_embed(monkeypatch, {"new prompt": [1.0, 0.0, 0.0]})
        assert cache.semantic_lookup("new prompt", team_id=None) is None

    def test_returns_match_above_threshold(self, monkeypatch):
        from app import cache
        cache.init_db()
        # Two very-similar vectors, above 0.95 cosine.
        _stub_embed(monkeypatch, {
            "original prompt": [1.0, 0.01, 0.0],
            "paraphrased prompt": [1.0, 0.02, 0.0],
        })
        cache.store(
            prompt_hash="h1",
            virtual_model="clearview-auto",
            response_json=json.dumps({"choices": [{"message": {"content": "hi"}}]}),
            tokens_in=1, tokens_out=1, picked_model="openai/gpt-4o-mini",
            team_id=None,
            prompt_text="original prompt",
        )

        hit = cache.semantic_lookup("paraphrased prompt", team_id=None)
        assert hit is not None
        row, sim = hit
        assert sim >= 0.95
        assert row["prompt_text"] == "original prompt"

    def test_skips_match_below_threshold(self, monkeypatch):
        from app import cache
        cache.init_db()
        # Orthogonal vectors → cosine = 0.
        _stub_embed(monkeypatch, {
            "alpha": [1.0, 0.0, 0.0],
            "bravo": [0.0, 1.0, 0.0],
        })
        cache.store(
            prompt_hash="h2",
            virtual_model="clearview-auto",
            response_json=json.dumps({"choices": [{"message": {"content": "x"}}]}),
            tokens_in=1, tokens_out=1, picked_model="m",
            team_id=None,
            prompt_text="alpha",
        )

        assert cache.semantic_lookup("bravo", team_id=None) is None

    def test_team_scoped(self, monkeypatch):
        from app import cache
        cache.init_db()
        _stub_embed(monkeypatch, {
            "secret-a": [1.0, 0.0, 0.0],
            "secret-a-bis": [1.0, 0.001, 0.0],
        })
        cache.store(
            prompt_hash="ha",
            virtual_model="clearview-auto",
            response_json=json.dumps({"choices": [{"message": {"content": "alpha"}}]}),
            tokens_in=1, tokens_out=1, picked_model="m",
            team_id="team_a",
            prompt_text="secret-a",
        )

        # Same prompt text from a different team should NOT see the entry.
        assert cache.semantic_lookup("secret-a-bis", team_id="team_b") is None
        # But same team does see it.
        hit = cache.semantic_lookup("secret-a-bis", team_id="team_a")
        assert hit is not None

    def test_picks_highest_similarity(self, monkeypatch):
        from app import cache
        cache.init_db()
        # Three stored prompts, varying similarity to the query.
        _stub_embed(monkeypatch, {
            "low": [1.0, 0.0, 0.0],
            "med": [0.6, 0.8, 0.0],
            "hi":  [0.999, 0.04, 0.0],
            "query": [1.0, 0.05, 0.0],
        })
        for ph, txt in [("h_lo", "low"), ("h_md", "med"), ("h_hi", "hi")]:
            cache.store(
                prompt_hash=ph,
                virtual_model="clearview-auto",
                response_json=json.dumps({"choices": [{"message": {"content": txt}}]}),
                tokens_in=1, tokens_out=1, picked_model="m",
                team_id=None,
                prompt_text=txt,
            )

        hit = cache.semantic_lookup("query", team_id=None)
        assert hit is not None
        row, _ = hit
        assert row["prompt_text"] == "hi"  # closest match wins


class TestStoreAcceptsEmbedding:
    def test_persists_text_and_blob(self, tmp_db, monkeypatch):
        from app import cache
        cache.init_db()
        # Backend disabled so cache.store doesn't try to embed; we supply our own.
        monkeypatch.setenv("CLEARVIEW_EMBEDDING_BACKEND", "disabled")

        cache.store(
            prompt_hash="hh",
            virtual_model="m",
            response_json="{}",
            tokens_in=0, tokens_out=0, picked_model="m",
            team_id="t",
            prompt_text="some text",
            embedding=[0.1, 0.2],
        )

        with sqlite3.connect(str(tmp_db)) as c:
            row = c.execute(
                "SELECT team_id, prompt_text, length(embedding) FROM prompt_cache WHERE prompt_hash = ?",
                ("hh",),
            ).fetchone()
        assert row[0] == "t"
        assert row[1] == "some text"
        # 2 floats * 4 bytes = 8 bytes
        assert row[2] == 8


class TestEndToEndSemanticHit:
    """Drive a real request through the FastAPI app to confirm the semantic
    path returns the cached payload AND logs the right telemetry."""

    def test_paraphrased_prompt_serves_cached_response(self, client, monkeypatch, tmp_db):
        from app import main
        from tests.conftest import FakeCompletion

        _stub_embed(monkeypatch, {
            "user: how do I list files in a directory": [1.0, 0.0, 0.0],
            "user: what's the command to list files in a folder": [1.0, 0.005, 0.0],
        })

        # First call: real upstream, fills cache.
        monkeypatch.setattr(
            main.litellm, "completion",
            lambda **kw: FakeCompletion(content="run `ls`", prompt_tokens=7, completion_tokens=3),
        )
        r1 = client.post(
            "/v1/chat/completions",
            json={"model": "clearview-cheap",
                  "messages": [{"role": "user", "content": "how do I list files in a directory"}]},
        )
        assert r1.status_code == 200

        # Second call: paraphrase. If upstream is called, the response would
        # carry "DIFFERENT" — but semantic cache should intercept.
        monkeypatch.setattr(
            main.litellm, "completion",
            lambda **kw: FakeCompletion(content="DIFFERENT", prompt_tokens=8, completion_tokens=1),
        )
        r2 = client.post(
            "/v1/chat/completions",
            json={"model": "clearview-cheap",
                  "messages": [{"role": "user", "content": "what's the command to list files in a folder"}]},
        )
        assert r2.status_code == 200
        body = r2.json()
        assert body["choices"][0]["message"]["content"] == "run `ls`"

        # Telemetry row for the second call should be tagged.
        with sqlite3.connect(str(tmp_db)) as c:
            rows = c.execute(
                "SELECT route_reason, picked_model FROM calls ORDER BY ts ASC"
            ).fetchall()
        # Two telemetry rows: first upstream call + second semantic cache hit.
        assert len(rows) >= 2
        assert rows[-1][1] == "cache"
        assert rows[-1][0].startswith("semantic_cache_hit")
