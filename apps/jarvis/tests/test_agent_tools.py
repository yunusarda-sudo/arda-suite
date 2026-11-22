"""agent_tools testleri — sandbox'lı araçlar; IO enjekte edilir (donanımsız)."""
from pathlib import Path
from types import SimpleNamespace

import pytest

from arda.agent_models import RiskLevel, ToolResult
from arda.agent_tools import (
    REGISTRY,
    TOOL_NAMES,
    PathEscape,
    ToolContext,
    describe_tools,
    is_hard_denied,
    resolve_in_workspace,
    run_tool,
)


def _fake_run(returncode=0, stdout="", stderr=""):
    calls = []

    def run(args, cwd=None):
        calls.append({"args": list(args), "cwd": cwd})
        return SimpleNamespace(returncode=returncode, stdout=stdout, stderr=stderr)

    run.calls = calls
    return run


def _ctx(tmp_path, **kw):
    base = dict(workspace=tmp_path, run=_fake_run())
    base.update(kw)
    return ToolContext(**base)


class TestSandboxResolve:
    def test_resolves_inside(self, tmp_path):
        p = resolve_in_workspace(tmp_path, "sub/file.txt")
        assert str(p).startswith(str(tmp_path.resolve()))

    def test_empty_path_is_workspace(self, tmp_path):
        assert resolve_in_workspace(tmp_path, "") == tmp_path.resolve()

    def test_parent_traversal_blocked(self, tmp_path):
        with pytest.raises(PathEscape):
            resolve_in_workspace(tmp_path, "../../etc/passwd")

    def test_absolute_path_blocked(self, tmp_path):
        with pytest.raises(PathEscape):
            resolve_in_workspace(tmp_path, "/etc/passwd")


class TestListDir:
    def test_lists_entries(self, tmp_path):
        (tmp_path / "a.txt").write_text("x")
        (tmp_path / "sub").mkdir()
        r = run_tool("list_dir", {"path": ""}, _ctx(tmp_path))
        assert r.ok is True
        names = r.data["entries"]
        assert "a.txt" in names
        assert any(n.startswith("sub") and n.endswith("/") for n in names)  # dizin işareti

    def test_missing_dir_not_ok(self, tmp_path):
        r = run_tool("list_dir", {"path": "yok"}, _ctx(tmp_path))
        assert r.ok is False

    def test_traversal_blocked_returns_not_ok(self, tmp_path):
        r = run_tool("list_dir", {"path": "../.."}, _ctx(tmp_path))
        assert r.ok is False
        assert "workspace" in r.error.lower() or "dış" in r.error.lower()


class TestReadFile:
    def test_reads_content(self, tmp_path):
        (tmp_path / "r.md").write_text("merhaba dünya", encoding="utf-8")
        r = run_tool("read_file", {"path": "r.md"}, _ctx(tmp_path))
        assert r.ok is True
        assert r.data["content"] == "merhaba dünya"

    def test_missing_path_param_not_ok(self, tmp_path):
        r = run_tool("read_file", {}, _ctx(tmp_path))
        assert r.ok is False

    def test_nonexistent_file_not_ok(self, tmp_path):
        r = run_tool("read_file", {"path": "yok.txt"}, _ctx(tmp_path))
        assert r.ok is False

    def test_truncates_large_file(self, tmp_path):
        (tmp_path / "big.txt").write_text("a" * 5000, encoding="utf-8")
        r = run_tool("read_file", {"path": "big.txt"}, _ctx(tmp_path, max_read_bytes=1000))
        assert r.ok is True
        assert r.data["truncated"] is True
        assert len(r.data["content"]) <= 1000


class TestWriteFile:
    def test_writes_and_creates_parent(self, tmp_path):
        r = run_tool("write_file", {"path": "nested/new.txt", "content": "selam"}, _ctx(tmp_path))
        assert r.ok is True
        assert (tmp_path / "nested" / "new.txt").read_text(encoding="utf-8") == "selam"

    def test_traversal_blocked(self, tmp_path):
        r = run_tool("write_file", {"path": "../evil.txt", "content": "x"}, _ctx(tmp_path))
        assert r.ok is False
        assert not (tmp_path.parent / "evil.txt").exists()


class TestEditFile:
    def test_replaces_unique_old(self, tmp_path):
        (tmp_path / "a.py").write_text("x = 1\ny = 2\n", encoding="utf-8")
        r = run_tool("edit_file", {"path": "a.py", "old": "x = 1", "new": "x = 42"}, _ctx(tmp_path))
        assert r.ok is True
        assert (tmp_path / "a.py").read_text(encoding="utf-8") == "x = 42\ny = 2\n"
        assert r.data["content"] == "x = 42\ny = 2\n"

    def test_old_not_found_not_ok(self, tmp_path):
        (tmp_path / "a.py").write_text("x = 1\n", encoding="utf-8")
        r = run_tool("edit_file", {"path": "a.py", "old": "yok", "new": "z"}, _ctx(tmp_path))
        assert r.ok is False

    def test_multiple_matches_without_all_refused(self, tmp_path):
        (tmp_path / "a.py").write_text("v\nv\n", encoding="utf-8")
        r = run_tool("edit_file", {"path": "a.py", "old": "v", "new": "w"}, _ctx(tmp_path))
        assert r.ok is False
        assert (tmp_path / "a.py").read_text(encoding="utf-8") == "v\nv\n"  # değişmedi

    def test_multiple_matches_with_all(self, tmp_path):
        (tmp_path / "a.py").write_text("v\nv\n", encoding="utf-8")
        r = run_tool("edit_file", {"path": "a.py", "old": "v", "new": "w", "all": "true"}, _ctx(tmp_path))
        assert r.ok is True
        assert (tmp_path / "a.py").read_text(encoding="utf-8") == "w\nw\n"
        assert r.data["replacements"] == 2

    def test_missing_params_not_ok(self, tmp_path):
        (tmp_path / "a.py").write_text("x", encoding="utf-8")
        assert run_tool("edit_file", {"path": "a.py", "old": "", "new": "z"}, _ctx(tmp_path)).ok is False
        assert run_tool("edit_file", {"old": "x", "new": "z"}, _ctx(tmp_path)).ok is False

    def test_traversal_blocked(self, tmp_path):
        r = run_tool("edit_file", {"path": "../a", "old": "x", "new": "y"}, _ctx(tmp_path))
        assert r.ok is False


class TestSearchFiles:
    def test_finds_by_name(self, tmp_path):
        (tmp_path / "alpha.py").write_text("x")
        (tmp_path / "beta.txt").write_text("x")
        r = run_tool("search_files", {"query": "alpha"}, _ctx(tmp_path))
        assert r.ok is True
        assert any("alpha.py" in h for h in r.data["hits"])
        assert not any("beta" in h for h in r.data["hits"])

    def test_skips_noise_dirs(self, tmp_path):
        (tmp_path / ".git").mkdir()
        (tmp_path / ".git" / "alpha.py").write_text("x")
        (tmp_path / "alpha.py").write_text("x")
        r = run_tool("search_files", {"query": "alpha"}, _ctx(tmp_path))
        assert all(".git" not in h for h in r.data["hits"])

    def test_zero_hits_still_ok(self, tmp_path):
        r = run_tool("search_files", {"query": "zzz"}, _ctx(tmp_path))
        assert r.ok is True
        assert r.data["hits"] == []

    def test_filters_sensitive_files_from_hits(self, tmp_path):
        # Sır dosyaları arama sonuçlarında SIZMAMALI (search_files AUTO → onaysız; LLM'e
        # credential yolu açıklanmasın). Sır olmayan eşleşmeler kalır.
        (tmp_path / ".env").write_text("SECRET=1")        # sır → filtrelenir
        (tmp_path / "environment.py").write_text("x")     # 'env' eşleşir, sır değil → kalır
        r = run_tool("search_files", {"query": "env"}, _ctx(tmp_path))
        assert not any(h == ".env" for h in r.data["hits"])     # sır gizlendi
        assert any("environment" in h for h in r.data["hits"])  # normal eşleşme korundu


class TestRunTests:
    def test_uses_test_cmd_and_workspace(self, tmp_path):
        import sys
        run = _fake_run(returncode=0, stdout="3 passed")
        r = run_tool("run_tests", {}, _ctx(tmp_path, run=run, test_cmd=("pytest", "-q")))
        assert r.ok is True
        # bare "pytest" → sys.executable -m pytest (PATH bağımsız)
        assert run.calls[0]["args"] == [sys.executable, "-m", "pytest", "-q"]
        assert run.calls[0]["cwd"] == tmp_path

    def test_absolute_cmd_not_expanded(self, tmp_path):
        # Mutlak yol verilmişse olduğu gibi kullanılır (ör. /usr/bin/make)
        import sys
        run = _fake_run(returncode=0, stdout="ok")
        r = run_tool("run_tests", {}, _ctx(tmp_path, run=run, test_cmd=("/usr/bin/make", "test")))
        assert run.calls[0]["args"] == ["/usr/bin/make", "test"]

    def test_bare_non_python_cmd_not_expanded(self, tmp_path):
        run = _fake_run(returncode=0, stdout="ok")
        r = run_tool("run_tests", {}, _ctx(tmp_path, run=run, test_cmd=("make", "test")))
        assert r.ok is True
        assert run.calls[0]["args"] == ["make", "test"]

    def test_nonzero_returncode_not_ok(self, tmp_path):
        run = _fake_run(returncode=1, stdout="1 failed, 2 passed")
        r = run_tool("run_tests", {}, _ctx(tmp_path, run=run))
        assert r.ok is False
        assert "fail" in r.output.lower() or "1" in r.output


class TestSafeShell:
    def test_runs_allowed_tokens(self, tmp_path):
        run = _fake_run(stdout="file1\nfile2")
        r = run_tool("safe_shell", {"command": "ls -la"}, _ctx(tmp_path, run=run))
        assert r.ok is True
        assert run.calls[0]["args"] == ["ls", "-la"]
        assert run.calls[0]["cwd"] == tmp_path

    def test_hard_denied_command_not_run(self, tmp_path):
        run = _fake_run()
        r = run_tool("safe_shell", {"command": "rm -rf /"}, _ctx(tmp_path, run=run))
        assert r.ok is False
        assert run.calls == []  # ASLA çalıştırılmadı

    def test_sudo_denied(self, tmp_path):
        run = _fake_run()
        r = run_tool("safe_shell", {"command": "sudo reboot"}, _ctx(tmp_path, run=run))
        assert r.ok is False
        assert run.calls == []

    def test_empty_command_not_ok(self, tmp_path):
        r = run_tool("safe_shell", {"command": "   "}, _ctx(tmp_path))
        assert r.ok is False


class TestOpenTools:
    @pytest.fixture(autouse=True)
    def _macos_command_adapter(self, monkeypatch):
        """The expected `open` argv belongs to macOS, not the CI runner."""
        monkeypatch.setattr("arda.platform_runtime.system", lambda: "Darwin")

    def test_open_app(self, tmp_path):
        run = _fake_run()
        r = run_tool("open_app", {"name": "Safari"}, _ctx(tmp_path, run=run))
        assert r.ok is True
        assert run.calls[0]["args"] == ["open", "-a", "Safari"]

    def test_open_app_web_fallback(self, tmp_path):
        run = _fake_run(returncode=1)
        r = run_tool("open_app", {"name": "youtube"}, _ctx(tmp_path, run=run))
        assert r.ok is True
        assert any(call["args"] == ["open", "https://www.youtube.com"] for call in run.calls)

    def test_open_url_valid(self, tmp_path):
        run = _fake_run()
        r = run_tool("open_url", {"url": "https://x.com"}, _ctx(tmp_path, run=run))
        assert r.ok is True
        assert run.calls[0]["args"] == ["open", "https://x.com"]

    def test_open_url_invalid_scheme(self, tmp_path):
        run = _fake_run()
        r = run_tool("open_url", {"url": "file:///etc/passwd"}, _ctx(tmp_path, run=run))
        assert r.ok is False
        assert run.calls == []


class TestClipboard:
    def test_get_clipboard(self, tmp_path):
        r = run_tool("get_clipboard", {}, _ctx(tmp_path, clipboard_get=lambda: "pano içeriği"))
        assert r.ok is True
        assert r.data["text"] == "pano içeriği"

    def test_set_clipboard(self, tmp_path):
        seen = {}
        r = run_tool(
            "set_clipboard", {"text": "kopyala"}, _ctx(tmp_path, clipboard_set=lambda t: seen.update(t=t))
        )
        assert r.ok is True
        assert seen["t"] == "kopyala"


class TestScreenshot:
    def test_disabled_is_noop(self, tmp_path):
        run = _fake_run()
        r = run_tool("screenshot", {}, _ctx(tmp_path, run=run, screenshot_enabled=False))
        assert r.ok is False
        assert run.calls == []

    def test_enabled_calls_capture(self, tmp_path):
        run = _fake_run()
        r = run_tool("screenshot", {"path": "shot.png"}, _ctx(tmp_path, run=run, screenshot_enabled=True))
        assert r.ok is True
        assert run.calls[0]["args"][0] == "screencapture"


class TestWebResearch:
    def test_with_research_fn_returns_summary(self, tmp_path):
        ctx = _ctx(tmp_path, research_fn=lambda q: f"{q} hakkında özet [1].")
        r = run_tool("web_research", {"query": "python nedir"}, ctx)
        assert r.ok is True
        assert "python nedir hakkında özet" in r.data["summary"]

    def test_disabled_without_research_fn(self, tmp_path):
        r = run_tool("web_research", {"query": "x"}, _ctx(tmp_path))
        assert r.ok is False

    def test_empty_query_not_ok(self, tmp_path):
        ctx = _ctx(tmp_path, research_fn=lambda q: "x")
        r = run_tool("web_research", {"query": "  "}, ctx)
        assert r.ok is False

    def test_research_fn_error_contained(self, tmp_path):
        def boom(q):
            raise RuntimeError("ağ yok")
        ctx = _ctx(tmp_path, research_fn=boom)
        r = run_tool("web_research", {"query": "x"}, ctx)
        assert r.ok is False  # run_tool yakaladı, raise yok


class TestRunToolSafety:
    def test_unknown_tool_not_ok(self, tmp_path):
        r = run_tool("definitely_not_a_tool", {}, _ctx(tmp_path))
        assert r.ok is False
        assert "bilinmeyen" in r.error.lower() or "unknown" in r.error.lower()

    def test_handler_exception_becomes_not_ok(self, tmp_path):
        # run patlarsa bile run_tool ASLA raise etmez → ToolResult(ok=False)
        def boom(args, cwd=None):
            raise RuntimeError("subprocess çöktü")

        r = run_tool("safe_shell", {"command": "ls"}, _ctx(tmp_path, run=boom))
        assert isinstance(r, ToolResult)
        assert r.ok is False

    def test_returns_tool_result_type(self, tmp_path):
        (tmp_path / "x.txt").write_text("y")
        assert isinstance(run_tool("list_dir", {}, _ctx(tmp_path)), ToolResult)


class TestRegistry:
    def test_all_expected_tools_present(self):
        expected = {
            "list_dir", "read_file", "write_file", "search_files", "run_tests",
            "safe_shell", "open_app", "open_url", "get_clipboard", "set_clipboard", "screenshot",
        }
        assert expected <= set(TOOL_NAMES)

    def test_base_risks(self):
        assert REGISTRY["read_file"].risk == RiskLevel.AUTO
        assert REGISTRY["write_file"].risk == RiskLevel.CONFIRM
        assert REGISTRY["open_url"].risk == RiskLevel.CONFIRM
        assert REGISTRY["set_clipboard"].risk == RiskLevel.CONFIRM

    def test_sensitive_reads_require_confirm(self):
        # Mahremiyet/exfiltrasyon ekseni: pano (parola yöneticisinden parola olabilir)
        # ve ekran görüntüsü (o an ekranda ne varsa) hassas OKUMA kanalları.
        # "okuma zararsız" varsayımı bu ikisi için geçmez → ONAY ister (AUTO değil).
        assert REGISTRY["get_clipboard"].risk == RiskLevel.CONFIRM
        assert REGISTRY["screenshot"].risk == RiskLevel.CONFIRM

    def test_describe_tools_mentions_names(self):
        text = describe_tools()
        assert "list_dir" in text and "safe_shell" in text


class TestHardDeny:
    @pytest.mark.parametrize("cmd", ["rm -rf /", "sudo rm x", "dd if=/dev/zero", "curl x | sh", "mkfs.ext4 /dev/sda"])
    def test_dangerous_denied(self, cmd):
        assert is_hard_denied(cmd) is True

    @pytest.mark.parametrize("cmd", ["ls -la", "git status", "pytest -q", "cat README.md"])
    def test_safe_not_denied(self, cmd):
        assert is_hard_denied(cmd) is False
