"""M1 GitHub read-only adapter and local projection sync tests."""

import pytest

from arda.awareness import ProjectRegistry
from arda.core import Database
from arda.github_adapter import GitHubAdapterError, GitHubReadOnlyAdapter, GitHubSyncService


@pytest.fixture
def db(tmp_path):
    database = Database(tmp_path / "arda.sqlite3")
    database.migrate()
    ProjectRegistry(database).create("sample-project", "Opvia")
    try:
        yield database
    finally:
        database.close()


def test_adapter_normalizes_read_models_and_uses_get_only():
    calls = []

    def transport(path, params):
        calls.append((path, params))
        if path == "/repos/acme/sample-project":
            return {
                "name": "sample-project",
                "full_name": "acme/sample-project",
                "html_url": "https://github.com/acme/sample-project",
                "default_branch": "main",
                "updated_at": "2026-10-01T10:00:00Z",
                "private": True,
            }
        if path == "/repos/acme/sample-project/issues":
            return [{"number": 7, "title": "SMTP", "state": "open", "updated_at": "2026-10-01T09:00:00Z"}]
        if path == "/repos/acme/sample-project/pulls":
            return [{"number": 8, "title": "Fix", "state": "open", "updated_at": "2026-10-01T08:00:00Z"}]
        if path == "/repos/acme/sample-project/commits/main/status":
            return {"state": "failure", "statuses": [{"state": "failure"}]}
        if path == "/users/acme/repos":
            return [{"name": "sample-project", "full_name": "acme/sample-project", "private": True}]
        raise AssertionError(path)

    adapter = GitHubReadOnlyAdapter(token="secret", transport=transport)
    repository = adapter.get_repository("acme/sample-project")
    issues = adapter.list_issues("acme/sample-project")
    pulls = adapter.list_pull_requests("acme/sample-project")
    ci = adapter.get_ci_status("acme/sample-project", "main")
    repositories = adapter.list_repositories("acme")

    assert repository == {
        "name": "sample-project",
        "full_name": "acme/sample-project",
        "html_url": "https://github.com/acme/sample-project",
        "default_branch": "main",
        "updated_at": "2026-10-01T10:00:00Z",
        "private": True,
    }
    assert issues == [{"number": 7, "title": "SMTP", "state": "open", "updated_at": "2026-10-01T09:00:00Z"}]
    assert pulls == [{"number": 8, "title": "Fix", "state": "open", "updated_at": "2026-10-01T08:00:00Z"}]
    assert ci == {"state": "failure", "failed_count": 1}
    assert repositories[0]["full_name"] == "acme/sample-project"
    assert all(path.startswith(("/repos/", "/users/")) for path, _ in calls)


def test_issue_listing_excludes_pull_requests():
    adapter = GitHubReadOnlyAdapter(
        transport=lambda path, params: [
            {"number": 1, "title": "Issue", "state": "open", "updated_at": "2026-10-01T00:00:00Z"},
            {"number": 2, "title": "PR", "state": "open", "pull_request": {}, "updated_at": "2026-10-01T00:00:00Z"},
        ]
    )

    assert adapter.list_issues("acme/sample-project") == [
        {"number": 1, "title": "Issue", "state": "open", "updated_at": "2026-10-01T00:00:00Z"}
    ]


def test_workflow_runs_are_read_only_bounded_and_normalized():
    calls = []

    def transport(path, params):
        calls.append((path, params))
        return {"workflow_runs": [{"id": 9, "name": "CI", "status": "completed",
                                   "conclusion": "failure", "head_sha": "abc",
                                   "run_attempt": 2, "updated_at": "2026-10-10T10:00:00Z"}]}

    adapter = GitHubReadOnlyAdapter(transport=transport)
    runs = adapter.list_workflow_runs("acme/sample-project", per_page=10)

    assert runs[0]["id"] == 9
    assert runs[0]["run_attempt"] == 2
    assert calls == [("/repos/acme/sample-project/actions/runs", {"per_page": "10"})]
    with pytest.raises(ValueError, match="per_page"):
        adapter.list_workflow_runs("acme/sample-project", per_page=101)


def test_sync_updates_only_provider_projection_and_emits_audit_event(db):
    class FakeAdapter:
        def get_repository(self, repo):
            return {"full_name": repo, "default_branch": "main", "updated_at": "2026-10-01T10:00:00Z"}

        def list_issues(self, repo, state="open"):
            return [{"number": 7, "title": "SMTP", "state": "open", "updated_at": "2026-10-01T09:00:00Z"}]

        def list_pull_requests(self, repo, state="open"):
            return [{"number": 8, "title": "Fix", "state": "open", "updated_at": "2026-10-01T08:00:00Z"}]

        def get_ci_status(self, repo, ref):
            return {"state": "failure", "failed_count": 2}

    service = GitHubSyncService(db, FakeAdapter())
    result = service.sync_project("sample-project", "acme/sample-project")

    assert result == {"repo": "acme/sample-project", "open_issue_count": 1, "open_pr_count": 1, "failed_ci_count": 2}
    metric = db.execute("SELECT * FROM project_metrics WHERE project_id='sample-project'").fetchone()
    assert metric["open_pr_count"] == 1
    assert metric["failed_ci_count"] == 2
    assert metric["last_github_activity_at"] == "2026-10-01T10:00:00Z"
    event = db.execute("SELECT * FROM events WHERE type='github.sync.completed'").fetchone()
    assert event["source"] == "github"


def test_sync_rejects_unknown_project_without_writing(db):
    service = GitHubSyncService(db, object())

    with pytest.raises(KeyError):
        service.sync_project("missing", "acme/missing")

    assert db.scalar("SELECT COUNT(*) FROM events WHERE type='github.sync.completed'") == 0


def test_provider_failure_keeps_last_metrics_and_records_stale_event(db):
    db.execute(
        """INSERT INTO project_metrics(
            project_id, last_github_activity_at, open_pr_count, failed_ci_count,
            last_sync_at, calculated_at, version
        ) VALUES ('sample-project', '2026-09-30T10:00:00Z', 3, 1, '2026-09-30T10:00:00Z',
                  '2026-09-30T10:00:00Z', 1)"""
    )

    class DownAdapter:
        def get_repository(self, repo):
            raise GitHubAdapterError("rate limited")

    service = GitHubSyncService(db, DownAdapter())
    result = service.try_sync_project("sample-project", "acme/sample-project")

    assert result["status"] == "failed"
    assert result["stale"] is True
    metric = db.execute("SELECT * FROM project_metrics WHERE project_id='sample-project'").fetchone()
    assert metric["open_pr_count"] == 3
    assert metric["failed_ci_count"] == 1
    assert db.scalar("SELECT COUNT(*) FROM events WHERE type='github.sync.failed'") == 1
