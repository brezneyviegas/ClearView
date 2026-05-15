"""Tests for the /chat surface (cookie auth, conversation CRUD, send flow).

Network-free: every litellm.completion call is monkeypatched to a FakeCompletion.
"""
from __future__ import annotations

import sqlite3

import pytest

from tests.conftest import FakeCompletion


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _patch_completion(monkeypatch, returns):
    from app import main
    if callable(returns):
        monkeypatch.setattr(main.litellm, "completion", returns)
    else:
        monkeypatch.setattr(main.litellm, "completion", lambda **kw: returns)


def _mint_team(name="chat", **caps):
    from app import teams
    teams.init_db()
    return teams.create(name=name, **caps)


# ---------------------------------------------------------------------------
# chat module unit tests
# ---------------------------------------------------------------------------

class TestChatStore:
    def test_create_and_list_conversation(self):
        from app import chat as chat_store
        chat_store.init_db()

        conv = chat_store.create_conversation("team_a", "hello world")
        assert conv.id.startswith("cv_chat_")
        assert conv.team_id == "team_a"
        assert conv.title == "hello world"

        listed = chat_store.list_conversations("team_a")
        assert any(c["id"] == conv.id for c in listed)

    def test_conversations_scoped_per_team(self):
        from app import chat as chat_store
        chat_store.init_db()

        chat_store.create_conversation("team_a", "alpha")
        chat_store.create_conversation("team_b", "bravo")

        a = chat_store.list_conversations("team_a")
        b = chat_store.list_conversations("team_b")
        assert all(c["title"] != "bravo" for c in a)
        assert all(c["title"] != "alpha" for c in b)

    def test_get_conversation_team_scope(self):
        from app import chat as chat_store
        chat_store.init_db()
        conv = chat_store.create_conversation("team_a", "x")

        assert chat_store.get_conversation(conv.id, "team_a") is not None
        # Wrong team must not be able to fetch.
        assert chat_store.get_conversation(conv.id, "team_b") is None

    def test_append_and_list_messages(self):
        from app import chat as chat_store
        chat_store.init_db()
        conv = chat_store.create_conversation("team_a", "x")

        chat_store.append_message(conv.id, "user", "hi")
        chat_store.append_message(
            conv.id, "assistant", "hello back",
            picked_tier="cheap", picked_model="openai/gpt-4o-mini",
            native_cost_usd=0.001, tokens_in=2, tokens_out=3, latency_ms=42,
        )

        msgs = chat_store.list_messages(conv.id, "team_a")
        assert [m["role"] for m in msgs] == ["user", "assistant"]
        assert msgs[1]["picked_model"] == "openai/gpt-4o-mini"
        assert msgs[1]["native_cost_usd"] == pytest.approx(0.001)
        assert msgs[1]["latency_ms"] == 42

    def test_list_messages_wrong_team_returns_empty(self):
        from app import chat as chat_store
        chat_store.init_db()
        conv = chat_store.create_conversation("team_a", "x")
        chat_store.append_message(conv.id, "user", "secret")

        assert chat_store.list_messages(conv.id, "team_b") == []

    def test_delete_cascades_messages(self, tmp_db):
        from app import chat as chat_store
        chat_store.init_db()
        conv = chat_store.create_conversation("team_a", "x")
        chat_store.append_message(conv.id, "user", "u")
        chat_store.append_message(conv.id, "assistant", "a")

        assert chat_store.delete_conversation(conv.id, "team_a") is True
        assert chat_store.list_messages(conv.id, "team_a") == []
        # Sanity: rows really gone from DB.
        with sqlite3.connect(str(tmp_db)) as c:
            n = c.execute(
                "SELECT COUNT(*) FROM chat_messages WHERE conversation_id = ?",
                (conv.id,),
            ).fetchone()[0]
        assert n == 0

    def test_delete_wrong_team_is_noop(self):
        from app import chat as chat_store
        chat_store.init_db()
        conv = chat_store.create_conversation("team_a", "x")

        assert chat_store.delete_conversation(conv.id, "team_b") is False
        assert chat_store.get_conversation(conv.id, "team_a") is not None

    def test_messages_for_upstream_shape(self):
        from app import chat as chat_store
        chat_store.init_db()
        conv = chat_store.create_conversation("team_a", "x")
        chat_store.append_message(conv.id, "user", "1")
        chat_store.append_message(conv.id, "assistant", "2")
        chat_store.append_message(conv.id, "user", "3")

        out = chat_store.messages_for_upstream(conv.id)
        assert out == [
            {"role": "user", "content": "1"},
            {"role": "assistant", "content": "2"},
            {"role": "user", "content": "3"},
        ]


# ---------------------------------------------------------------------------
# Cookie auth
# ---------------------------------------------------------------------------

class TestChatLoginLogout:
    def test_login_with_valid_token_sets_cookie(self, client):
        t = _mint_team("login-test")
        r = client.post("/chat/login", json={"token": t.id})
        assert r.status_code == 200
        assert r.json()["team_name"] == "login-test"
        assert "cv_session" in r.cookies
        assert r.cookies["cv_session"] == t.id

    def test_login_rejects_unknown_token(self, client):
        r = client.post("/chat/login", json={"token": "cv_team_" + "0" * 32})
        assert r.status_code == 401

    def test_login_rejects_malformed_token(self, client):
        r = client.post("/chat/login", json={"token": "not-a-team-token"})
        assert r.status_code == 400

    def test_logout_clears_cookie(self, client):
        t = _mint_team("logout-test")
        client.post("/chat/login", json={"token": t.id})
        r = client.post("/chat/logout")
        assert r.status_code == 200
        # Cookie cleared. The TestClient drops cleared cookies from its jar.
        assert "cv_session" not in client.cookies

    def test_cookie_auth_works_on_v1_endpoint(self, client, monkeypatch, tmp_db):
        """Cookie session should authorise /v1/chat/completions just like Bearer."""
        _patch_completion(monkeypatch, FakeCompletion(content="hi", prompt_tokens=2, completion_tokens=1))
        t = _mint_team("cookie-v1")
        client.post("/chat/login", json={"token": t.id})

        r = client.post(
            "/v1/chat/completions",
            json={"model": "clearview-cheap",
                  "messages": [{"role": "user", "content": "ping"}]},
        )
        assert r.status_code == 200

        # Telemetry row should be attributed to the team.
        with sqlite3.connect(str(tmp_db)) as c:
            team_ids = [row[0] for row in c.execute("SELECT team_id FROM calls").fetchall()]
        assert t.id in team_ids


# ---------------------------------------------------------------------------
# Conversation routes
# ---------------------------------------------------------------------------

class TestChatConversations:
    def _login(self, client, name="conv-test"):
        t = _mint_team(name)
        client.post("/chat/login", json={"token": t.id})
        return t

    def test_requires_login(self, client):
        # No cookie / Bearer → 401.
        r = client.get("/chat/conversations")
        assert r.status_code == 401

    def test_create_then_list(self, client):
        self._login(client)
        r = client.post("/chat/conversations", json={"title": "first"})
        assert r.status_code == 200
        cid = r.json()["id"]

        listed = client.get("/chat/conversations").json()
        assert any(c["id"] == cid and c["title"] == "first"
                   for c in listed["conversations"])

    def test_messages_404_for_unknown_conv(self, client):
        self._login(client)
        r = client.get("/chat/conversations/cv_chat_doesnotexist/messages")
        assert r.status_code == 404

    def test_delete_conversation(self, client):
        self._login(client)
        cid = client.post("/chat/conversations", json={"title": "del"}).json()["id"]

        r = client.delete(f"/chat/conversations/{cid}")
        assert r.status_code == 200
        listed = client.get("/chat/conversations").json()["conversations"]
        assert not any(c["id"] == cid for c in listed)

    def test_teams_cannot_see_each_others_conversations(self, client):
        t_a = self._login(client, "team-a")
        cid_a = client.post("/chat/conversations", json={"title": "secret"}).json()["id"]

        # Log out + log in as team B.
        client.post("/chat/logout")
        t_b = _mint_team("team-b")
        client.post("/chat/login", json={"token": t_b.id})

        listed = client.get("/chat/conversations").json()["conversations"]
        assert not any(c["id"] == cid_a for c in listed)
        # Direct fetch attempt → 404 (scoping enforced).
        assert client.get(f"/chat/conversations/{cid_a}/messages").status_code == 404


# ---------------------------------------------------------------------------
# Send flow
# ---------------------------------------------------------------------------

class TestChatSend:
    def _setup(self, client, monkeypatch, **fake_kwargs):
        _patch_completion(monkeypatch, FakeCompletion(**fake_kwargs))
        t = _mint_team("send-test")
        client.post("/chat/login", json={"token": t.id})
        cid = client.post("/chat/conversations", json={"title": "s"}).json()["id"]
        return t, cid

    def test_send_persists_user_and_assistant(self, client, monkeypatch):
        _, cid = self._setup(client, monkeypatch, content="hello back", prompt_tokens=5, completion_tokens=2)

        r = client.post(
            f"/chat/conversations/{cid}/send",
            json={"content": "hi there", "tier": "cheap"},
        )
        assert r.status_code == 200
        body = r.json()
        assert body["content"] == "hello back"
        assert body["picked_tier"] == "cheap"
        assert body["tokens_in"] == 5
        assert body["tokens_out"] == 2
        assert body["request_id"]

        msgs = client.get(f"/chat/conversations/{cid}/messages").json()["messages"]
        assert [m["role"] for m in msgs] == ["user", "assistant"]
        assert msgs[0]["content"] == "hi there"
        assert msgs[1]["content"] == "hello back"
        assert msgs[1]["picked_tier"] == "cheap"

    def test_send_400_on_empty_content(self, client, monkeypatch):
        _, cid = self._setup(client, monkeypatch)
        r = client.post(
            f"/chat/conversations/{cid}/send",
            json={"content": "   "},
        )
        assert r.status_code == 400

    def test_send_404_on_unknown_conv(self, client, monkeypatch):
        _patch_completion(monkeypatch, FakeCompletion())
        t = _mint_team("s2")
        client.post("/chat/login", json={"token": t.id})

        r = client.post(
            "/chat/conversations/cv_chat_nope/send",
            json={"content": "hi"},
        )
        assert r.status_code == 404

    def test_send_history_replayed_to_upstream(self, client, monkeypatch):
        """The next send should include prior messages so the model has context."""
        captured: dict = {}

        def fake_completion(**kwargs):
            captured["messages"] = kwargs.get("messages")
            return FakeCompletion(content="ack", prompt_tokens=1, completion_tokens=1)

        from app import main
        monkeypatch.setattr(main.litellm, "completion", fake_completion)

        t = _mint_team("history")
        client.post("/chat/login", json={"token": t.id})
        cid = client.post("/chat/conversations", json={"title": "h"}).json()["id"]

        client.post(f"/chat/conversations/{cid}/send", json={"content": "one"})
        client.post(f"/chat/conversations/{cid}/send", json={"content": "two"})

        # Second send should have seen the first turn's user+assistant.
        roles = [m["role"] for m in captured["messages"]]
        assert roles == ["user", "assistant", "user"]
        assert captured["messages"][0]["content"] == "one"
        assert captured["messages"][-1]["content"] == "two"
