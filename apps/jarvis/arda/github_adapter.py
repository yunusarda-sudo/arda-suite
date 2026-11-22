"""Read-only GitHub adapter and local metrics projection sync."""
from __future__ import annotations

import json
from typing import Any, Callable
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen

from arda.core import Database, EventLog, _iso, _utc_now


class GitHubAdapterError(RuntimeError):
    """GitHub could not provide a valid read response."""


Transport = Callable[[str, dict[str, str]], Any]


def _repo_path(repo: str) -> str:
    parts = repo.strip("/").split("/")
    if len(parts) != 2 or not all(parts):
        raise ValueError("repo must be in owner/name form")
    return "/repos/" + "/".join(quote(part, safe="") for part in parts)


def _normalized_item(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "number": item.get("number"),
        "title": item.get("title", ""),
        "state": item.get("state", "unknown"),
        "updated_at": item.get("updated_at"),
    }


def _max_timestamp(values: list[str | None]) -> str | None:
    candidates = [value for value in values if value]
    return max(candidates) if candidates else None


class GitHubReadOnlyAdapter:
    """Small GET-only GitHub API adapter with injectable transport."""

    def __init__(
        self,
        *,
        token: str | None = None,
        base_url: str = "https://api.github.com",
        transport: Transport | None = None,
        timeout_seconds: int = 15,
    ):
        self.token = token
        self.base_url = base_url.rstrip("/")
        self.timeout_seconds = timeout_seconds
        self._transport = transport or self._http_get

    def list_repositories(self, owner: str) -> list[dict[str, Any]]:
        raw = self._get(f"/users/{quote(owner, safe='')}/repos", {"per_page": "100"})
        return [self._repository(item) for item in self._expect_list(raw)]

    def get_repository(self, repo: str) -> dict[str, Any]:
        return self._repository(self._expect_dict(self._get(_repo_path(repo), {})))

    def list_issues(self, repo: str, *, state: str = "open") -> list[dict[str, Any]]:
        raw = self._expect_list(self._get(f"{_repo_path(repo)}/issues", {"state": state, "per_page": "100"}))
        return [_normalized_item(item) for item in raw if "pull_request" not in item]

    def list_pull_requests(self, repo: str, *, state: str = "open") -> list[dict[str, Any]]:
        raw = self._expect_list(self._get(f"{_repo_path(repo)}/pulls", {"state": state, "per_page": "100"}))
        return [_normalized_item(item) for item in raw]

    def get_ci_status(self, repo: str, ref: str) -> dict[str, Any]:
        raw = self._expect_dict(
            self._get(f"{_repo_path(repo)}/commits/{quote(ref, safe='')}/status", {})
        )
        statuses = raw.get("statuses") or []
        failed_count = sum(
            1 for status in statuses if status.get("state") in {"failure", "error"}
        )
        if not statuses and raw.get("state") in {"failure", "error"}:
            failed_count = 1
        return {"state": raw.get("state", "unknown"), "failed_count": failed_count}

    def list_workflow_runs(self, repo: str, *, per_page: int = 25) -> list[dict[str, Any]]:
        if not isinstance(per_page, int) or isinstance(per_page, bool) or not 1 <= per_page <= 100:
            raise ValueError("per_page must be between 1 and 100")
        raw = self._expect_dict(self._get(
            f"{_repo_path(repo)}/actions/runs", {"per_page": str(per_page)}))
        runs = self._expect_list(raw.get("workflow_runs"))
        return [{
            "id": item.get("id"),
            "name": item.get("name", ""),
            "status": item.get("status", "unknown"),
            "conclusion": item.get("conclusion"),
            "head_branch": item.get("head_branch", ""),
            "head_sha": item.get("head_sha", ""),
            "html_url": item.get("html_url"),
            "run_attempt": item.get("run_attempt", 1),
            "updated_at": item.get("updated_at"),
        } for item in runs]

    def _get(self, path: str, params: dict[str, str]) -> Any:
        return self._transport(path, params)

    def _http_get(self, path: str, params: dict[str, str]) -> Any:
        url = f"{self.base_url}{path}"
        if params:
            url = f"{url}?{urlencode(params)}"
        headers = {"Accept": "application/vnd.github+json", "User-Agent": "arda-core"}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        request = Request(url, headers=headers, method="GET")
        try:
            with urlopen(request, timeout=self.timeout_seconds) as response:
                return json.loads(response.read().decode("utf-8"))
        except (HTTPError, URLError, TimeoutError, json.JSONDecodeError) as exc:
            raise GitHubAdapterError(f"GitHub GET failed for {path}: {exc}") from exc

    @staticmethod
    def _repository(item: dict[str, Any]) -> dict[str, Any]:
        return {
            "name": item.get("name", ""),
            "full_name": item.get("full_name", ""),
            "html_url": item.get("html_url"),
            "default_branch": item.get("default_branch", "main"),
            "updated_at": item.get("updated_at"),
            "private": bool(item.get("private", False)),
        }

    @staticmethod
    def _expect_list(value: Any) -> list[dict[str, Any]]:
        if not isinstance(value, list) or not all(isinstance(item, dict) for item in value):
            raise GitHubAdapterError("GitHub response was not a list of objects")
        return value

    @staticmethod
    def _expect_dict(value: Any) -> dict[str, Any]:
        if not isinstance(value, dict):
            raise GitHubAdapterError("GitHub response was not an object")
        return value


class GitHubSyncService:
    """Project-scoped provider sync into nullable local metric fields."""

    def __init__(self, db: Database, adapter: GitHubReadOnlyAdapter, *, clock=_utc_now):
        self.db = db
        self.adapter = adapter
        self.clock = clock
        self.events = EventLog(db, clock)

    def sync_project(self, project_id: str, repo: str) -> dict[str, Any]:
        if self.db.execute("SELECT 1 FROM projects WHERE id=?", (project_id,)).fetchone() is None:
            raise KeyError(project_id)

        repository = self.adapter.get_repository(repo)
        issues = self.adapter.list_issues(repo)
        pull_requests = self.adapter.list_pull_requests(repo)
        ci = self.adapter.get_ci_status(repo, repository["default_branch"])
        now = self.clock()
        summary = {
            "repo": repo,
            "open_issue_count": len(issues),
            "open_pr_count": len(pull_requests),
            "failed_ci_count": int(ci.get("failed_count", 0)),
        }
        last_activity = _max_timestamp(
            [
                repository.get("updated_at"),
                *(issue.get("updated_at") for issue in issues),
                *(pull_request.get("updated_at") for pull_request in pull_requests),
            ]
        )
        with self.db.transaction():
            self.db.execute(
                """INSERT INTO project_metrics(
                    project_id, last_github_activity_at, open_pr_count,
                    failed_ci_count, last_sync_at, calculated_at, version
                ) VALUES (?, ?, ?, ?, ?, ?, 1)
                ON CONFLICT(project_id) DO UPDATE SET
                    last_github_activity_at=excluded.last_github_activity_at,
                    open_pr_count=excluded.open_pr_count,
                    failed_ci_count=excluded.failed_ci_count,
                    last_sync_at=excluded.last_sync_at,
                    version=project_metrics.version+1""",
                (project_id, last_activity, summary["open_pr_count"], summary["failed_ci_count"],
                 _iso(now), _iso(now)),
            )
            self.events.append(
                "github.sync.completed",
                project_id=project_id,
                source="github",
                source_id=repo,
                payload=summary,
            )
        return summary

    def try_sync_project(self, project_id: str, repo: str) -> dict[str, Any]:
        """Sync without erasing a last-known projection when GitHub is down."""
        try:
            return {"status": "succeeded", **self.sync_project(project_id, repo)}
        except GitHubAdapterError as exc:
            now = self.clock()
            self.events.append(
                "github.sync.failed",
                project_id=project_id,
                source="github",
                source_id=repo,
                payload={
                    "repo": repo,
                    "error": str(exc),
                    "error_class": exc.__class__.__name__,
                    "stale": True,
                },
            )
            return {
                "status": "failed",
                "repo": repo,
                "stale": True,
                "error": str(exc),
                "observed_at": _iso(now),
            }
