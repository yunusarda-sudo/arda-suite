from __future__ import annotations

import subprocess
import sys
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from arda.execution import ExecutionRequest, PodmanRuntime
from arda.autonomous_work_loop import WorkTask
from arda.hermes_sandbox import HermesPodmanTaskExecutor, SandboxMCPServer


IMAGE = "localhost/arda-worker@sha256:" + "b" * 64


def test_runtime_mounts_only_the_explicit_bounded_writable_workspace(tmp_path):
    source = tmp_path / "source"
    work = tmp_path / "work"
    source.mkdir()
    work.mkdir()
    request = ExecutionRequest(
        execution_id="bounded-workspace",
        repository_snapshot=source,
        working_directory=work,
        disk_limit_bytes=1024,
        argv=("/bin/sh", "-lc", "true"),
    )

    argv = PodmanRuntime(image=IMAGE).build_argv(request, tmp_path / "outputs")

    assert f"{source}:/workspace/source:ro" in argv
    assert f"{work}:/workspace/work:rw" in argv
    assert f"--ulimit=fsize=1024:1024" in argv
    assert "--network=none" in argv
    assert not any(str(tmp_path) + "/.." in value for value in argv)


def test_runtime_kills_command_when_aggregate_workspace_limit_is_exceeded(tmp_path):
    source = tmp_path / "source"
    work = tmp_path / "work"
    source.mkdir()
    work.mkdir()

    def process_factory(_argv, **kwargs):
        env = {**kwargs["env"], "ARDATEST_WORK": str(work)}
        return subprocess.Popen(
            [sys.executable, "-c", "import os,time; open(os.environ['ARDATEST_WORK']+'/large','wb').write(b'x'*4096); time.sleep(2)"],
            env=env, **{key: value for key, value in kwargs.items() if key != "env"},
        )

    request = ExecutionRequest(
        execution_id="disk-cap",
        repository_snapshot=source,
        working_directory=work,
        disk_limit_bytes=1024,
        argv=("/bin/sh", "-lc", "write large file"),
        timeout_seconds=2,
    )
    result = PodmanRuntime(image=IMAGE, process_factory=process_factory).run(request)

    assert result.status == "failed"
    assert result.termination_reason == "disk_limit"


def test_sandbox_mcp_exposes_only_exec_and_pins_workspace_and_policy(tmp_path):
    source = tmp_path / "source"
    work = tmp_path / "work"
    source.mkdir()
    work.mkdir()
    requests = []

    class Runtime:
        def run(self, request):
            requests.append(request)
            return SimpleNamespace(
                status="succeeded", exit_code=0, stdout="ok", stderr="",
                termination_reason=None, cleanup_ok=True,
            )

    server = SandboxMCPServer(
        runtime=Runtime(), repository_snapshot=source, working_directory=work,
        image=IMAGE, max_tool_calls=2, max_duration_seconds=30,
    )
    listed = server.handle_request({"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
    names = {item["name"] for item in listed["result"]["tools"]}
    assert names == {"sandbox_exec"}

    called = server.handle_request({
        "jsonrpc": "2.0", "id": 2, "method": "tools/call",
        "params": {"name": "sandbox_exec", "arguments": {"command": "printf ok"}},
    })

    assert called["result"]["isError"] is False
    request = requests[0]
    assert request.repository_snapshot == source
    assert request.working_directory == work
    assert request.network_policy == "none"
    assert request.environment_allowlist == {}
    assert request.argv == ("/bin/sh", "-lc", "printf ok")


def test_sandbox_mcp_rejects_other_tools_and_unbounded_commands(tmp_path):
    source = tmp_path / "source"
    work = tmp_path / "work"
    source.mkdir()
    work.mkdir()
    server = SandboxMCPServer(
        runtime=SimpleNamespace(run=lambda _request: pytest.fail("must not execute")),
        repository_snapshot=source, working_directory=work, image=IMAGE,
        max_tool_calls=2, max_duration_seconds=30, max_command_bytes=16,
    )

    other = server.handle_request({
        "jsonrpc": "2.0", "id": 3, "method": "tools/call",
        "params": {"name": "read_file", "arguments": {"path": "/etc/passwd"}},
    })
    oversized = server.handle_request({
        "jsonrpc": "2.0", "id": 4, "method": "tools/call",
        "params": {"name": "sandbox_exec", "arguments": {"command": "x" * 17}},
    })

    assert other["result"]["isError"] is True
    assert oversized["result"]["isError"] is True


def test_trusted_candidate_import_uses_exact_baseline_parent_without_moving_ref(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    subprocess.run(["git", "-C", str(repo), "config", "user.name", "Test"], check=True)
    subprocess.run(["git", "-C", str(repo), "config", "user.email", "test@example.invalid"], check=True)
    (repo / "file.txt").write_text("baseline\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(repo), "add", "file.txt"], check=True)
    subprocess.run(["git", "-C", str(repo), "commit", "-qm", "baseline"], check=True)
    baseline = subprocess.check_output(["git", "-C", str(repo), "rev-parse", "HEAD"], text=True).strip()
    (repo / "refs").mkdir()
    subprocess.run(["git", "-C", str(repo), "update-ref", "refs/heads/accepted", baseline], check=True)
    work = tmp_path / "work"
    work.mkdir()
    (work / "file.txt").write_text("candidate\n", encoding="utf-8")
    (work / "new.txt").write_text("new\n", encoding="utf-8")
    task = SimpleNamespace(task_id="T-1", baseline_sha=baseline)
    executor = HermesPodmanTaskExecutor(
        runtime=SimpleNamespace(capabilities=SimpleNamespace(rootless=True, memory_limit=True)),
        image=IMAGE, hermes_cli="hermes", evaluator_commands={"maintenance": [["true"]]},
        provider_env_keys=[])

    candidate = executor._candidate_commit(repo, task, work)

    assert subprocess.check_output(["git", "-C", str(repo), "rev-parse", f"{candidate}^"], text=True).strip() == baseline
    assert subprocess.check_output(["git", "-C", str(repo), "rev-parse", "refs/heads/accepted"], text=True).strip() == baseline
    assert subprocess.check_output(["git", "-C", str(repo), "show", f"{candidate}:new.txt"], text=True) == "new\n"


def test_candidate_import_rejects_worker_symlinks(tmp_path):
    work = tmp_path / "work"
    work.mkdir()
    (work / "escape").symlink_to("/etc/passwd")

    with pytest.raises(ValueError, match="symlink"):
        HermesPodmanTaskExecutor._walk_regular_files(work)


def test_evaluator_uses_separate_readonly_source_and_writable_work(tmp_path):
    source, work = tmp_path / "source", tmp_path / "work"
    source.mkdir(); work.mkdir()
    requests = []

    class Runtime:
        capabilities = SimpleNamespace(rootless=True, memory_limit=True)
        def run(self, request):
            requests.append(request)
            return SimpleNamespace(status="succeeded", exit_code=0, termination_reason=None,
                                   cleanup_ok=True, stdout="ok", stderr="")

    executor = HermesPodmanTaskExecutor(runtime=Runtime(), image=IMAGE, hermes_cli="hermes",
                                         evaluator_commands={"maintenance": [["pytest", "-q"]]},
                                         provider_env_keys=[])
    task = SimpleNamespace(task_id="T-2", hard_timeout_seconds=60)

    result = executor._evaluate(task, source, work, [["pytest", "-q"]], remaining_seconds=60)

    assert result["passed"] is True
    assert requests[0].repository_snapshot == source
    assert requests[0].working_directory == work
    assert requests[0].require_rootless is True
    assert requests[0].network_policy == "none"


def test_executor_mocked_hermes_path_builds_baseline_parent_candidate(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    subprocess.run(["git", "-C", str(repo), "config", "user.name", "Test"], check=True)
    subprocess.run(["git", "-C", str(repo), "config", "user.email", "test@example.invalid"], check=True)
    (repo / "file.txt").write_text("baseline\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(repo), "add", "file.txt"], check=True)
    subprocess.run(["git", "-C", str(repo), "commit", "-qm", "baseline"], check=True)
    baseline = subprocess.check_output(["git", "-C", str(repo), "rev-parse", "HEAD"], text=True).strip()
    task = WorkTask(task_id="T-3", repo=str(repo), baseline_ref="HEAD", baseline_sha=baseline,
                    objective="Update file", acceptance=("file content updated",), allowed_paths=("file.txt",),
                    soft_budget_seconds=30, hard_timeout_seconds=60)
    runtime = SimpleNamespace(capabilities=SimpleNamespace(rootless=True, memory_limit=True))
    executor = HermesPodmanTaskExecutor(runtime=runtime, image=IMAGE, hermes_cli="hermes",
                                         evaluator_commands={"maintenance": [["true"]]},
                                         provider_env_keys=[])

    def fake_hermes(_task, root, _source, work, audit_path):
        (work / "file.txt").write_text("candidate\n", encoding="utf-8")
        audit_path.write_text('{"tool":"sandbox_exec","outcome":"succeeded"}\n', encoding="utf-8")
        return {"error": None, "exit_code": 0, "tool_calls": 1}

    monkeypatch.setattr(executor, "_run_hermes", fake_hermes)
    monkeypatch.setattr(executor, "_evaluate", lambda *_args, **_kwargs: {"passed": True, "checks": [{"exit_code": 0}]})

    result = executor.execute(task)

    assert result.result == "PASS"
    assert result.candidate_sha
    assert subprocess.check_output(["git", "-C", str(repo), "rev-parse", f"{result.candidate_sha}^"], text=True).strip() == baseline
    assert (repo / "file.txt").read_text(encoding="utf-8") == "baseline\n"


def test_hermes_subprocess_has_isolated_config_and_provider_allowlist(tmp_path, monkeypatch):
    source, work, root = tmp_path / "source", tmp_path / "work", tmp_path / "run"
    source.mkdir(); work.mkdir(); root.mkdir()
    fake_cli = tmp_path / "fake-hermes"
    fake_cli.write_text(
        "#!/usr/bin/env python3\n"
        "import json, os\n"
        "from pathlib import Path\n"
        "home = Path(os.environ['HERMES_HOME'])\n"
        "payload = {'config': json.loads((home/'config.yaml').read_text()), 'env_keys': sorted(os.environ)}\n"
        "(home/'captured.json').write_text(json.dumps(payload))\n"
        "print(json.dumps({'type':'tool_use','name':'mcp__arda_sandbox__sandbox_exec'}))\n"
        "print(json.dumps({'type':'result','exit_code':0}))\n",
        encoding="utf-8")
    fake_cli.chmod(0o755)
    monkeypatch.setenv("TEST_PROVIDER_API_KEY", "test-secret")
    monkeypatch.setenv("ARDA_TEST_UNLISTED_SECRET", "must-not-inherit")
    executor = HermesPodmanTaskExecutor(
        runtime=SimpleNamespace(capabilities=SimpleNamespace(rootless=True, memory_limit=True)),
        image=IMAGE, hermes_cli=str(fake_cli), evaluator_commands={"maintenance": [["true"]]},
        provider_env_keys=["TEST_PROVIDER_API_KEY"])
    task = SimpleNamespace(provider="openai-api", model="test-model", max_turns=4,
                           hard_timeout_seconds=10, objective="Change one file", acceptance=("check it",),
                           allowed_paths=("a.txt",), forbidden_paths=("secrets/**",))

    report = executor._run_hermes(task, root, source, work, root / "audit.jsonl")

    captured = json.loads((root / "hermes-home" / "captured.json").read_text())
    config = captured["config"]
    assert report["error"] is None
    assert report["tool_calls"] == 1
    assert set(config["mcp_servers"]) == {"arda-sandbox"}
    assert config["mcp_servers"]["arda-sandbox"]["tools"]["include"] == ["sandbox_exec"]
    assert config["platform_toolsets"]["cli"] == ["arda-sandbox"]
    assert "TEST_PROVIDER_API_KEY" not in config["mcp_servers"]["arda-sandbox"]["env"]
    assert "TEST_PROVIDER_API_KEY" in captured["env_keys"]
    assert "ARDA_TEST_UNLISTED_SECRET" not in captured["env_keys"]
