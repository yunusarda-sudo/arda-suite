"""Durable project-scoped grants for persistent browser profiles."""
from __future__ import annotations

import hashlib
import ipaddress
import json
import re
import uuid
from datetime import datetime, timedelta
from typing import Callable
from urllib.parse import urlparse

from arda.core import Database, EventLog, _iso, _utc_now


class BrowserGrantError(PermissionError):
    """A browser session is missing, expired, revoked, or out of scope."""


def normalize_domain(value: str) -> str:
    domain = str(value or "").strip().rstrip(".").lower()
    if not domain or "*" in domain or "/" in domain or ":" in domain or "@" in domain:
        raise ValueError("browser scope must contain exact DNS domains; wildcards are not allowed")
    try:
        ipaddress.ip_address(domain)
    except ValueError:
        pass
    else:
        raise ValueError("browser scope must use DNS names, not IP addresses")
    try:
        domain = domain.encode("idna").decode("ascii")
    except UnicodeError as exc:
        raise ValueError("browser scope domain is invalid") from exc
    if len(domain) > 253 or not re.fullmatch(r"(?=.{1,253}$)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", domain):
        raise ValueError("browser scope must be a fully-qualified DNS domain")
    return domain


def host_in_scope(host: str, domains: tuple[str, ...] | list[str]) -> bool:
    try:
        candidate = normalize_domain(host)
    except ValueError:
        return False
    return any(candidate == domain or candidate.endswith("." + domain) for domain in domains)


class BrowserSessionRegistry:
    """Create/revoke immutable browser scopes stored in Core's durable SQLite DB."""

    def __init__(self, db: Database, *, clock: Callable[[], datetime] = _utc_now):
        self.db = db
        self.clock = clock
        self.events = EventLog(db, clock)

    def create(self, project_id: str, allowed_domains: list[str] | tuple[str, ...], *,
               created_by: str, ttl_seconds: int = 3600, session_id: str | None = None) -> dict:
        project_id = str(project_id or "").strip()
        created_by = str(created_by or "").strip()
        if not project_id or len(project_id) > 128 or not created_by or len(created_by) > 128:
            raise ValueError("project_id and created_by are required and bounded")
        if type(ttl_seconds) is not int or not 60 <= ttl_seconds <= 30 * 24 * 3600:
            raise ValueError("browser session TTL must be between 60 seconds and 30 days")
        domains = tuple(sorted({normalize_domain(item) for item in allowed_domains}))
        if not domains or len(domains) > 64:
            raise ValueError("one to 64 explicit browser domains are required")
        project = self.db.execute("SELECT status FROM projects WHERE id=?", (project_id,)).fetchone()
        if project is None or project["status"] != "active":
            raise BrowserGrantError("browser grant requires an active Core project")
        identifier = session_id or f"bs_{uuid.uuid4().hex}"
        if not re.fullmatch(r"bs_[a-f0-9]{32}", identifier):
            raise ValueError("session_id has an invalid format")
        now = self.clock()
        expires = now + timedelta(seconds=ttl_seconds)
        profile = "arda-project-" + hashlib.sha256(project_id.encode("utf-8")).hexdigest()[:20]
        session_key = "arda-" + hashlib.sha256(project_id.encode("utf-8")).hexdigest()[:20] + "-" + uuid.uuid4().hex
        with self.db.transaction():
            self.db.execute(
                """INSERT INTO browser_session_grants(
                    session_id, project_id, domains_json, user_id, session_key,
                    created_at, expires_at, revoked_at, created_by
                ) VALUES (?, ?, ?, ?, ?, ?, ?, NULL, ?)""",
                (identifier, project_id, json.dumps(domains), profile, session_key,
                 _iso(now), _iso(expires), created_by),
            )
            self.events.append("browser.session.granted", source="core", source_id=identifier,
                               project_id=project_id,
                               payload={"domains": list(domains), "expires_at": _iso(expires),
                                        "created_by": created_by})
        return self.get(identifier)

    def get(self, session_id: str) -> dict:
        row = self.db.execute("SELECT * FROM browser_session_grants WHERE session_id=?", (session_id,)).fetchone()
        if row is None:
            raise BrowserGrantError("browser session grant not found")
        return {**dict(row), "domains": tuple(json.loads(row["domains_json"]))}

    def authorize(self, session_id: str, project_id: str, *, url: str | None = None) -> dict:
        grant = self.get(session_id)
        now = _iso(self.clock())
        if grant["project_id"] != project_id:
            raise BrowserGrantError("browser session belongs to another project")
        if grant["revoked_at"] is not None:
            raise BrowserGrantError("browser session is revoked")
        if grant["expires_at"] <= now:
            raise BrowserGrantError("browser session has expired")
        if url:
            parsed = urlparse(url)
            if parsed.scheme not in {"http", "https"} or not parsed.hostname or not host_in_scope(parsed.hostname, grant["domains"]):
                raise BrowserGrantError("URL is outside the delegated browser domain scope")
        return {"session_id": session_id, "project_id": project_id,
                "user_id": grant["user_id"], "session_key": grant["session_key"],
                "allowed_domains": grant["domains"], "expires_at": grant["expires_at"]}

    def revoke(self, session_id: str, *, revoked_by: str) -> dict:
        now = _iso(self.clock())
        with self.db.transaction():
            grant = self.get(session_id)
            if grant["revoked_at"] is None:
                self.db.execute(
                    "UPDATE browser_session_grants SET revoked_at=?, version=version+1 WHERE session_id=?",
                    (now, session_id),
                )
                self.events.append("browser.session.revoked", source="core", source_id=session_id,
                                   project_id=grant["project_id"],
                                   payload={"revoked_by": str(revoked_by)[:128]})
        return self.get(session_id)

    def track_tab(self, session_id: str, project_id: str, tab_id: str) -> None:
        """Persist an opened Camofox tab, including one racing with revocation."""
        tab_id = str(tab_id or "").strip()
        if not tab_id or len(tab_id) > 256 or any(ord(char) < 32 for char in tab_id):
            raise ValueError("browser tab id is invalid")
        now = _iso(self.clock())
        with self.db.transaction():
            grant = self.db.execute(
                "SELECT project_id FROM browser_session_grants WHERE session_id=?", (session_id,)
            ).fetchone()
            if grant is None or grant["project_id"] != project_id:
                raise BrowserGrantError("browser tab does not match its session project")
            self.db.execute(
                """INSERT INTO browser_session_tabs(session_id, tab_id, opened_at)
                   VALUES (?, ?, ?) ON CONFLICT(session_id, tab_id) DO NOTHING""",
                (session_id, tab_id, now),
            )

    def pending_close_tabs(self, *, session_id: str | None = None) -> list[dict]:
        now = _iso(self.clock())
        where = "t.closed_at IS NULL AND (g.revoked_at IS NOT NULL OR g.expires_at <= ?)"
        params: list[object] = [now]
        if session_id is not None:
            where += " AND t.session_id=?"
            params.append(session_id)
        rows = self.db.execute(
            f"""SELECT t.session_id, t.tab_id, t.close_attempts, g.user_id
                FROM browser_session_tabs t JOIN browser_session_grants g USING(session_id)
                WHERE {where} ORDER BY t.opened_at, t.tab_id LIMIT 8""",
            tuple(params),
        ).fetchall()
        return [dict(row) for row in rows]

    def pending_close_count(self, *, session_id: str | None = None) -> int:
        now = _iso(self.clock())
        where = "t.closed_at IS NULL AND (g.revoked_at IS NOT NULL OR g.expires_at <= ?)"
        params: list[object] = [now]
        if session_id is not None:
            where += " AND t.session_id=?"
            params.append(session_id)
        row = self.db.execute(
            f"""SELECT COUNT(*) FROM browser_session_tabs t
                JOIN browser_session_grants g USING(session_id) WHERE {where}""",
            tuple(params),
        ).fetchone()
        return int(row[0])

    def record_tab_close(self, session_id: str, tab_id: str, *, error_class: str | None = None) -> None:
        now = _iso(self.clock())
        closed_at = None if error_class else now
        with self.db.transaction():
            self.db.execute(
                """UPDATE browser_session_tabs
                   SET closed_at=COALESCE(?, closed_at), close_attempts=close_attempts+1,
                       last_close_error=?
                   WHERE session_id=? AND tab_id=? AND closed_at IS NULL""",
                (closed_at, str(error_class)[:80] if error_class else None, session_id, tab_id),
            )


def close_revoked_browser_tabs(registry: BrowserSessionRegistry, client, *,
                               session_id: str | None = None) -> dict[str, int]:
    """Idempotently close tabs whose durable grant is revoked or expired."""
    closed = failed = 0
    for tab in registry.pending_close_tabs(session_id=session_id):
        try:
            client.close_tab(user_id=tab["user_id"], tab_id=tab["tab_id"])
        except Exception as exc:  # noqa: BLE001 - preserve a retryable durable close receipt
            registry.record_tab_close(tab["session_id"], tab["tab_id"], error_class=type(exc).__name__)
            failed += 1
        else:
            registry.record_tab_close(tab["session_id"], tab["tab_id"])
            closed += 1
    return {"closed": closed, "failed": failed,
            "pending": registry.pending_close_count(session_id=session_id)}
