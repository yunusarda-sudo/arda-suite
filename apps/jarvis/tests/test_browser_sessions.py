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
    assert context["user_id"].startswith("arda-project-")
    assert context["session_key"] != grant["session_id"]
    other = registry.create("project-beta", ["example.com"], created_by="operator")
    assert registry.authorize(other["session_id"], "project-beta")["user_id"] != context["user_id"]
    with pytest.raises(BrowserGrantError, match="another project"):
        registry.authorize(grant["session_id"], "project-beta")
    with pytest.raises(BrowserGrantError, match="outside"):
        registry.authorize(grant["session_id"], "project-alpha", url="https://evil.example.net")
    registry.revoke(grant["session_id"], revoked_by="operator")
    with pytest.raises(BrowserGrantError, match="revoked"):
        registry.authorize(grant["session_id"], "project-alpha")
    assert db.scalar("SELECT COUNT(*) FROM events WHERE type='browser.session.revoked'") == 1


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

        def close_tab(self, **_kwargs):
            self.calls += 1
            raise ConnectionError("browser service offline")

    flaky = FlakyClient()
    first = close_revoked_browser_tabs(registry, flaky)
    assert first == {"closed": 0, "failed": 1, "pending": 1}
    row = database.execute(
        "SELECT close_attempts, last_close_error FROM browser_session_tabs WHERE session_id=?",
        (grant["session_id"],),
    ).fetchone()
    assert tuple(row) == (1, "ConnectionError")
    database.close()

    recovered_db = Database(path)
    recovered_db.migrate()
    recovered_registry = BrowserSessionRegistry(recovered_db)
    closed = []
    recovered = close_revoked_browser_tabs(
        recovered_registry, type("Client", (), {"close_tab": lambda _self, **kwargs: closed.append(kwargs)})(),
    )
    assert recovered == {"closed": 1, "failed": 0, "pending": 0}
    assert closed == [{"user_id": grant["user_id"], "tab_id": "tab-1"}]
    assert recovered_db.scalar(
        "SELECT closed_at IS NOT NULL FROM browser_session_tabs WHERE session_id=?",
        (grant["session_id"],),
    ) == 1
    recovered_db.close()


def test_expired_browser_session_tabs_are_pending_cleanup(db):
    now = datetime(2026, 10, 17, tzinfo=timezone.utc)
    registry = BrowserSessionRegistry(db, clock=lambda: now)
    grant = registry.create("project-alpha", ["example.com"], created_by="operator", ttl_seconds=60)
    registry.track_tab(grant["session_id"], "project-alpha", "tab-expired")
    assert registry.pending_close_count() == 0

    registry.clock = lambda: now + timedelta(seconds=61)
    closed = []
    result = close_revoked_browser_tabs(
        registry, type("Client", (), {"close_tab": lambda _self, **kwargs: closed.append(kwargs)})(),
    )
    assert result == {"closed": 1, "failed": 0, "pending": 0}
    assert closed == [{"user_id": grant["user_id"], "tab_id": "tab-expired"}]
