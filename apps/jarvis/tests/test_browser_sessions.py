from datetime import datetime, timedelta, timezone
import sqlite3

import pytest

from arda.browser_sessions import BrowserGrantError, BrowserSessionRegistry, close_revoked_browser_tabs
from arda.core import Database
from arda.awareness import ProjectRegistry


@pytest.fixture
def db(tmp_path):
    database = Database(tmp_path / "core.sqlite3")
    database.migrate()
    ProjectRegistry(database).create("project-alpha", "Alpha")
    ProjectRegistry(database).create("project-beta", "Beta")
    try:
        yield database
    finally:
        database.close()


def test_project_browser_grant_is_durable_scoped_and_revocable(db):
    now = datetime(2026, 10, 17, tzinfo=timezone.utc)
    registry = BrowserSessionRegistry(db, clock=lambda: now)
    grant = registry.create("project-alpha", ["Example.COM", "docs.example.com"],
                            created_by="operator", ttl_seconds=600)
    context = registry.authorize(grant["session_id"], "project-alpha",
                                 url="https://sub.example.com/path")
    assert context["allowed_domains"] == ("docs.example.com", "example.com")
    assert context["user_id"].startswith("arda-grant-")
    assert context["session_key"] != grant["session_id"]
    other = registry.create("project-beta", ["example.com"], created_by="operator")
    assert registry.authorize(other["session_id"], "project-beta")["user_id"] != context["user_id"]
    sibling = registry.create("project-alpha", ["example.com"], created_by="operator")
    assert registry.authorize(sibling["session_id"], "project-alpha")["user_id"] != context["user_id"]
    with pytest.raises(BrowserGrantError, match="another project"):
        registry.authorize(grant["session_id"], "project-beta")
    with pytest.raises(BrowserGrantError, match="outside"):
        registry.authorize(grant["session_id"], "project-alpha", url="https://evil.example.net")
    registry.revoke(grant["session_id"], revoked_by="operator")
    with pytest.raises(BrowserGrantError, match="revoked"):
        registry.authorize(grant["session_id"], "project-alpha")
    assert db.scalar("SELECT COUNT(*) FROM events WHERE type='browser.session.revoked'") == 1


def test_grant_persists_cleanup_intent_before_remote_browser_call(db):
    registry = BrowserSessionRegistry(db)
    grant = registry.create("project-alpha", ["example.com"], created_by="operator")
    registry.authorize(grant["session_id"], "project-alpha", url="https://example.com")
    registry.revoke(grant["session_id"], revoked_by="operator")
    assert registry.pending_session_cleanup() == [
        {"session_id": grant["session_id"], "user_id": grant["user_id"]}
    ]

    cleared = []
    result = close_revoked_browser_tabs(
        registry,
        type("Client", (), {"clear_session_storage": lambda _self, **kwargs: cleared.append(kwargs)})(),
    )
    assert result == {"closed": 0, "failed": 0, "pending": 0}
    assert cleared == [{"user_id": grant["user_id"]}]


def test_late_tab_receipt_reopens_session_cleanup_after_revoke(db):
    registry = BrowserSessionRegistry(db)
    grant = registry.create("project-alpha", ["example.com"], created_by="operator")
    registry.authorize(grant["session_id"], "project-alpha", url="https://example.com")
    registry.revoke(grant["session_id"], revoked_by="operator")
    client = type("Client", (), {"clear_session_storage": lambda _self, **_kwargs: None})()
    assert close_revoked_browser_tabs(registry, client)["pending"] == 0

    # The remote create request can finish after revoke/reset. Its receipt must
    # make the session cleanup pending again instead of trusting the earlier reset.
    registry.track_tab(grant["session_id"], "project-alpha", "late-tab")
    assert registry.pending_session_cleanup_count() == 1
    assert close_revoked_browser_tabs(registry, client)["pending"] == 0


def test_browser_grant_expiry_and_immutable_scope(db):
    now = datetime(2026, 10, 17, tzinfo=timezone.utc)
    registry = BrowserSessionRegistry(db, clock=lambda: now)
    grant = registry.create("project-alpha", ["example.com"], created_by="operator",
                            ttl_seconds=60, session_id="bs_" + "a" * 32)
    with pytest.raises(sqlite3.IntegrityError, match="scope is immutable"):
        db.execute("UPDATE browser_session_grants SET domains_json='[\"attacker.example\"]' WHERE session_id=?",
                   (grant["session_id"],))
    registry.clock = lambda: now + timedelta(seconds=61)
    with pytest.raises(BrowserGrantError, match="expired"):
        registry.authorize(grant["session_id"], "project-alpha")


@pytest.mark.parametrize("domain", ["*.example.com", "localhost", "127.0.0.1", "https://example.com"])
def test_browser_grant_rejects_unsafe_domain_scopes(db, domain):
    registry = BrowserSessionRegistry(db)
    with pytest.raises(ValueError):
        registry.create("project", [domain], created_by="operator")


def test_revoked_browser_tabs_retry_after_failure_and_database_restart(tmp_path):
    path = tmp_path / "core.sqlite3"
    database = Database(path)
    database.migrate()
    ProjectRegistry(database).create("project-alpha", "Alpha")
    registry = BrowserSessionRegistry(database)
    grant = registry.create("project-alpha", ["example.com"], created_by="operator")
    registry.track_tab(grant["session_id"], "project-alpha", "tab-1")
    registry.revoke(grant["session_id"], revoked_by="operator")

    class FlakyClient:
        calls = 0

        def clear_session_storage(self, **_kwargs):
            self.calls += 1
            raise ConnectionError("browser service offline")

    flaky = FlakyClient()
    first = close_revoked_browser_tabs(registry, flaky)
    assert first == {"closed": 0, "failed": 1, "pending": 1}
    row = database.execute(
        "SELECT close_attempts, last_close_error FROM browser_session_cleanup WHERE session_id=?",
        (grant["session_id"],),
    ).fetchone()
    assert tuple(row) == (1, "ConnectionError")
    database.close()

    recovered_db = Database(path)
    recovered_db.migrate()
    recovered_registry = BrowserSessionRegistry(recovered_db)
    closed = []
    recovered = close_revoked_browser_tabs(
        recovered_registry,
        type("Client", (), {"clear_session_storage": lambda _self, **kwargs: closed.append(kwargs)})(),
    )
    assert recovered == {"closed": 1, "failed": 0, "pending": 0}
    assert closed == [{"user_id": grant["user_id"]}]
    assert recovered_db.scalar(
        "SELECT closed_at IS NOT NULL FROM browser_session_tabs WHERE session_id=?",
        (grant["session_id"],),
    ) == 1
    assert recovered_db.scalar(
        "SELECT closed_at IS NOT NULL FROM browser_session_cleanup WHERE session_id=?",
        (grant["session_id"],),
    ) == 1
    recovered_db.close()


def test_legacy_shared_browser_grant_uses_tab_cleanup_without_profile_reset(db):
    registry = BrowserSessionRegistry(db)
    grant_id = "bs_" + "c" * 32
    grant_user_id = "arda-project-legacy-shared"
    db.execute(
        """INSERT INTO browser_session_grants(
             session_id, project_id, domains_json, user_id, session_key,
             created_at, expires_at, revoked_at, created_by
           ) VALUES (?, 'project-alpha', '[\"example.com\"]', ?, 'legacy-session',
                     '2026-10-17T00:00:00+00:00', '2099-10-17T00:00:00+00:00', NULL, 'migration-fixture')""",
        (grant_id, grant_user_id),
    )
    registry.track_tab(grant_id, "project-alpha", "legacy-tab")
    registry.revoke(grant_id, revoked_by="operator")
    calls = []

    class LegacyClient:
        def close_tab(self, **kwargs):
            calls.append(("tab", kwargs))

        def clear_session_storage(self, **kwargs):
            calls.append(("session", kwargs))

    result = close_revoked_browser_tabs(registry, LegacyClient())
    assert result == {"closed": 1, "failed": 0, "pending": 0}
    assert calls == [("tab", {"user_id": grant_user_id, "tab_id": "legacy-tab"})]


def test_expired_browser_grant_cleans_isolated_session(db):
    now = datetime(2026, 10, 17, tzinfo=timezone.utc)
    registry = BrowserSessionRegistry(db, clock=lambda: now)
    grant = registry.create("project-alpha", ["example.com"], created_by="operator", ttl_seconds=60)
    registry.track_tab(grant["session_id"], "project-alpha", "tab-expired")
    assert registry.pending_close_count() == 0

    registry.clock = lambda: now + timedelta(seconds=61)
    closed = []

    class Client:
        def clear_session_storage(self, **kwargs):
            closed.append(kwargs)

        def close_tab(self, **kwargs):
            pytest.fail(f"isolated session should be reset as a unit: {kwargs}")

    result = close_revoked_browser_tabs(
        registry, Client(),
    )
    assert result == {"closed": 1, "failed": 0, "pending": 0}
    assert closed == [{"user_id": grant["user_id"]}]
