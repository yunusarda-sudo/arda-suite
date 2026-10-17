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
