"""Tests for per-team API keys + quotas (multi-tenant slice).

All tests rely on the autouse `tmp_db` fixture in conftest for DB isolation,
and the `client` fixture for HTTP-level checks.
"""
from __future__ import annotations

import sqlite3

import pytest

from tests.conftest import FakeCompletion  # noqa: E402


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _patch_completion(monkeypatch, returns):
    from app import main
    if callable(returns):
        monkeypatch.setattr(main.litellm, "completion", returns)
    else:
        monkeypatch.setattr(main.litellm, "completion", lambda **kw: returns)


def _seed_call(db_path, *, team_id, native, synth=0.0, ts=None):
    """Insert a fake telemetry row directly so spend lookups can read it
    without going through the chat endpoint."""
    import time as _t
    import uuid as _u
    with sqlite3.connect(str(db_path)) as c:
        c.execute(
            """
            INSERT INTO calls (
                request_id, session_id, ts, picked_model, picked_provider,
                tokens_in, tokens_out, native_cost_usd, plan_equiv_cost_usd,
                synth_cost_usd, team_id, status
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'ok')
            """,
            (_u.uuid4().hex, "test", ts or _t.time(), "anthropic/claude-haiku-4-5",
             "anthropic", 10, 10, float(native), 0.0, float(synth), team_id),
        )
        c.commit()


# ---------------------------------------------------------------------------
# Direct module API: create/get/disable/delete/list
# ---------------------------------------------------------------------------

class TestTeamCRUD:
    def test_create_returns_token_and_persists(self, tmp_db):
        from app import teams
        teams.init_db()
        t = teams.create(
            name="alpha",
            daily_usd_cap=5.0,
            allowed_tiers=["cheap", "mid"],
            timezone_name="America/New_York",
        )
        assert t.id.startswith("cv_team_")
        assert len(t.id) == len("cv_team_") + 32
        assert t.name == "alpha"
        assert t.daily_usd_cap == 5.0
        assert t.allowed_tiers == ["cheap", "mid"]
        assert t.timezone == "America/New_York"
        assert t.enabled is True

        again = teams.get(t.id)
        assert again is not None
        assert again.id == t.id
        assert again.allowed_tiers == ["cheap", "mid"]
        assert again.timezone == "America/New_York"

    def test_create_rejects_unknown_timezone(self, tmp_db):
        from app import teams
        teams.init_db()
        with pytest.raises(ValueError):
            teams.create(name="bad-tz", timezone_name="Mars/Olympus")

    def test_get_missing_returns_none(self, tmp_db):
        from app import teams
        teams.init_db()
        assert teams.get("cv_team_does_not_exist") is None
        assert teams.get("") is None

    def test_disable_and_enable_toggle_flag(self, tmp_db):
        from app import teams
        teams.init_db()
        t = teams.create(name="beta")
        assert teams.disable(t.id) is True
        assert teams.get(t.id).enabled is False
        assert teams.enable(t.id) is True
        assert teams.get(t.id).enabled is True

    def test_delete_removes_row(self, tmp_db):
        from app import teams
        teams.init_db()
        t = teams.create(name="gamma")
        assert teams.delete(t.id) is True
        assert teams.get(t.id) is None
        assert teams.delete(t.id) is False  # idempotent: missing → False

    def test_list_all_orders_by_created_desc(self, tmp_db):
        from app import teams
        teams.init_db()
        a = teams.create(name="first")
        b = teams.create(name="second")
        ids = [t.id for t in teams.list_all()]
        assert ids[0] == b.id and ids[1] == a.id


# ---------------------------------------------------------------------------
# Per-team spend isolation
# ---------------------------------------------------------------------------

class TestTodaySpendIsolation:
    def test_today_spend_scopes_to_team(self, tmp_db):
        from app import teams
        teams.init_db()
        t1 = teams.create(name="t1")
        t2 = teams.create(name="t2")
        _seed_call(tmp_db, team_id=t1.id, native=0.10)
        _seed_call(tmp_db, team_id=t1.id, native=0.20)
        _seed_call(tmp_db, team_id=t2.id, native=1.00)
        # Drop any caches set by earlier tests in same process.
        teams.invalidate_spend_cache()

        assert round(teams.today_spend(t1.id), 4) == 0.30
        assert round(teams.today_spend(t2.id), 4) == 1.00

    def test_today_spend_includes_synth(self, tmp_db):
        from app import teams
        teams.init_db()
        t = teams.create(name="solo")
        _seed_call(tmp_db, team_id=t.id, native=0.0, synth=0.05)
        teams.invalidate_spend_cache()
        assert round(teams.today_spend(t.id), 4) == 0.05


# ---------------------------------------------------------------------------
# Quota gating end-to-end
# ---------------------------------------------------------------------------

class TestQuotaGating:
    def test_team_daily_cap_rejects_with_scope(self, client, monkeypatch, tmp_db):
        from app import teams
        # Heavy-cap policy disabled so we don't accidentally hit global first.
        teams.init_db()
        t = teams.create(name="capped", daily_usd_cap=0.50)
        _seed_call(tmp_db, team_id=t.id, native=0.75)  # already over
        teams.invalidate_spend_cache()

        # Patch upstream so any leak past the cap is detectable as a 200.
        _patch_completion(monkeypatch, FakeCompletion())

        r = client.post(
            "/v1/chat/completions",
            json={"messages": [{"role": "user", "content": "hi"}]},
            headers={"Authorization": f"Bearer {t.id}"},
        )
        assert r.status_code == 429
        body = r.json()
        assert body["scope"] == "team_daily"
        assert body["cap"] == 0.50

    def test_team_monthly_cap_rejects(self, client, monkeypatch, tmp_db):
        from app import teams
        teams.init_db()
        t = teams.create(name="month-capped", monthly_usd_cap=1.0)
        _seed_call(tmp_db, team_id=t.id, native=1.50)
        teams.invalidate_spend_cache()

        _patch_completion(monkeypatch, FakeCompletion())
        r = client.post(
            "/v1/chat/completions",
            json={"messages": [{"role": "user", "content": "hi"}]},
            headers={"Authorization": f"Bearer {t.id}"},
        )
        assert r.status_code == 429
        assert r.json()["scope"] == "team_monthly"

    def test_no_team_header_uses_global_only(self, client, monkeypatch):
        """Anonymous requests skip team checks entirely (single-tenant fallback)."""
        _patch_completion(monkeypatch, FakeCompletion())
        r = client.post(
            "/v1/chat/completions",
            json={"messages": [{"role": "user", "content": "hi"}]},
        )
        assert r.status_code == 200

    def test_unknown_team_token_401(self, client, monkeypatch):
        _patch_completion(monkeypatch, FakeCompletion())
        r = client.post(
            "/v1/chat/completions",
            json={"messages": [{"role": "user", "content": "hi"}]},
            headers={"Authorization": "Bearer cv_team_doesnotexist"},
        )
        assert r.status_code == 401

    def test_disabled_team_401(self, client, monkeypatch, tmp_db):
        from app import teams
        teams.init_db()
        t = teams.create(name="suspended")
        teams.disable(t.id)
        _patch_completion(monkeypatch, FakeCompletion())
        r = client.post(
            "/v1/chat/completions",
            json={"messages": [{"role": "user", "content": "hi"}]},
            headers={"Authorization": f"Bearer {t.id}"},
        )
        assert r.status_code == 401


# ---------------------------------------------------------------------------
# Tier gating
# ---------------------------------------------------------------------------

class TestTierGating:
    def test_disallowed_tier_returns_403(self, client, monkeypatch, tmp_db):
        from app import teams
        teams.init_db()
        # Allow only mid; force a clearview-frontier call → rejected.
        t = teams.create(name="cheap-only", allowed_tiers=["mid"])

        _patch_completion(monkeypatch, FakeCompletion())
        r = client.post(
            "/v1/chat/completions",
            json={
                "model": "clearview-frontier",
                "messages": [{"role": "user", "content": "hi"}],
            },
            headers={"Authorization": f"Bearer {t.id}"},
        )
        assert r.status_code == 403
        body = r.json()
        assert body["tier"] == "frontier"
        assert "mid" in body["allowed"]

    def test_allowed_tier_passes(self, client, monkeypatch, tmp_db):
        from app import teams
        teams.init_db()
        t = teams.create(name="cheap-only", allowed_tiers=["cheap"])
        _patch_completion(monkeypatch, FakeCompletion())
        r = client.post(
            "/v1/chat/completions",
            json={
                "model": "clearview-cheap",
                "messages": [{"role": "user", "content": "hi"}],
            },
            headers={"Authorization": f"Bearer {t.id}"},
        )
        assert r.status_code == 200

    def test_empty_allowed_tiers_means_all(self, client, monkeypatch, tmp_db):
        from app import teams
        teams.init_db()
        t = teams.create(name="all-tiers")  # allowed_tiers defaults to []
        _patch_completion(monkeypatch, FakeCompletion())
        r = client.post(
            "/v1/chat/completions",
            json={
                "model": "clearview-frontier",
                "messages": [{"role": "user", "content": "hi"}],
            },
            headers={"Authorization": f"Bearer {t.id}"},
        )
        assert r.status_code == 200


# ---------------------------------------------------------------------------
# Cache key scoping
# ---------------------------------------------------------------------------

class TestCacheScoping:
    def test_team_id_changes_hash(self):
        from app import cache
        msgs = [{"role": "user", "content": "hello"}]
        h_anon = cache.hash_key(messages=msgs, virtual_model="clearview-auto", temperature=1.0)
        h_a = cache.hash_key(messages=msgs, virtual_model="clearview-auto", temperature=1.0,
                             team_id="cv_team_aaaa")
        h_b = cache.hash_key(messages=msgs, virtual_model="clearview-auto", temperature=1.0,
                             team_id="cv_team_bbbb")
        assert h_anon != h_a
        assert h_a != h_b

    def test_same_team_id_stable(self):
        from app import cache
        msgs = [{"role": "user", "content": "hello"}]
        h1 = cache.hash_key(messages=msgs, virtual_model="x", temperature=1.0, team_id="cv_team_x")
        h2 = cache.hash_key(messages=msgs, virtual_model="x", temperature=1.0, team_id="cv_team_x")
        assert h1 == h2


# ---------------------------------------------------------------------------
# Admin endpoints
# ---------------------------------------------------------------------------

class TestAdminTeamsCRUD:
    def test_create_returns_full_token(self, client):
        r = client.post("/admin/teams", json={"name": "newteam", "daily_usd_cap": 1.5})
        assert r.status_code == 200
        body = r.json()
        assert body["id"].startswith("cv_team_")
        assert body["name"] == "newteam"
        assert body["daily_usd_cap"] == 1.5

    def test_create_requires_name(self, client):
        r = client.post("/admin/teams", json={})
        assert r.status_code == 400

    def test_list_redacts_token(self, client):
        client.post("/admin/teams", json={"name": "x"})
        r = client.get("/admin/teams")
        assert r.status_code == 200
        items = r.json()["teams"]
        assert len(items) >= 1
        # No full token leaked; only id_short prefix.
        for item in items:
            assert "id" not in item
            assert "id_short" in item
            assert item["id_short"].endswith("...")

    def test_patch_updates_caps(self, client):
        created = client.post("/admin/teams", json={"name": "patchme"}).json()
        team_id = created["id"]
        r = client.patch(f"/admin/teams/{team_id}", json={"daily_usd_cap": 9.99,
                                                          "allowed_tiers": ["cheap"],
                                                          "timezone": "Europe/London"})
        assert r.status_code == 200
        body = r.json()
        assert body["daily_usd_cap"] == 9.99
        assert body["allowed_tiers"] == ["cheap"]
        assert body["timezone"] == "Europe/London"

    def test_patch_rejects_unknown_timezone(self, client):
        created = client.post("/admin/teams", json={"name": "bad-tz"}).json()
        r = client.patch(f"/admin/teams/{created['id']}", json={"timezone": "Mars/Olympus"})
        assert r.status_code == 400

    def test_patch_unknown_404(self, client):
        r = client.patch("/admin/teams/cv_team_nope", json={"daily_usd_cap": 1})
        assert r.status_code == 404

    def test_delete_team(self, client):
        created = client.post("/admin/teams", json={"name": "tmp"}).json()
        team_id = created["id"]
        r = client.delete(f"/admin/teams/{team_id}")
        assert r.status_code == 200
        # Second delete is 404.
        assert client.delete(f"/admin/teams/{team_id}").status_code == 404


class TestAdminAuthGating:
    def test_admin_token_required_when_set(self, client, monkeypatch):
        monkeypatch.setenv("CLEARVIEW_ADMIN_TOKEN", "supersecret")
        # No auth header → 401.
        r = client.post("/admin/teams", json={"name": "x"})
        assert r.status_code == 401
        # Wrong token → 401.
        r2 = client.post("/admin/teams", json={"name": "x"},
                         headers={"Authorization": "Bearer wrong"})
        assert r2.status_code == 401
        # Correct token → 200.
        r3 = client.post("/admin/teams", json={"name": "x"},
                         headers={"Authorization": "Bearer supersecret"})
        assert r3.status_code == 200

    def test_open_when_admin_token_unset(self, client, monkeypatch):
        monkeypatch.delenv("CLEARVIEW_ADMIN_TOKEN", raising=False)
        r = client.get("/admin/teams")
        assert r.status_code == 200


# ---------------------------------------------------------------------------
# Telemetry attribution
# ---------------------------------------------------------------------------

class TestTelemetryAttribution:
    def test_team_id_persisted_on_call(self, client, monkeypatch, tmp_db):
        from app import teams
        teams.init_db()
        t = teams.create(name="attrib")
        _patch_completion(monkeypatch, FakeCompletion(prompt_tokens=4, completion_tokens=6))

        r = client.post(
            "/v1/chat/completions",
            json={"messages": [{"role": "user", "content": "hi"}]},
            headers={"Authorization": f"Bearer {t.id}"},
        )
        assert r.status_code == 200
        with sqlite3.connect(str(tmp_db)) as c:
            c.row_factory = sqlite3.Row
            rows = [dict(r) for r in c.execute("SELECT * FROM calls").fetchall()]
        assert len(rows) == 1
        assert rows[0]["team_id"] == t.id

    def test_anon_call_has_null_team_id(self, client, monkeypatch, tmp_db):
        _patch_completion(monkeypatch, FakeCompletion())
        r = client.post(
            "/v1/chat/completions",
            json={"messages": [{"role": "user", "content": "hi"}]},
        )
        assert r.status_code == 200
        with sqlite3.connect(str(tmp_db)) as c:
            c.row_factory = sqlite3.Row
            rows = [dict(r) for r in c.execute("SELECT * FROM calls").fetchall()]
        assert len(rows) == 1
        assert rows[0]["team_id"] is None
