"""Safety contract for the Raspberry Pi installer."""
from __future__ import annotations

import os
from pathlib import Path
import shutil
import stat
import subprocess
import tempfile
import unittest


SERVICE_ROOT = Path(__file__).parent
TOKEN_SENTINEL = "test-only-" + "value-" * 8


class PiInstallerSafetyTests(unittest.TestCase):
    def test_installer_avoids_unpinned_images_and_remote_shell_pipes(self):
        script = (SERVICE_ROOT / "scripts" / "install-pi.sh").read_text(encoding="utf-8")
        self.assertIn("qdrant/qdrant:v1.19.2", script)
        self.assertNotIn("qdrant/qdrant:latest", script)
        self.assertNotRegex(script, r"curl[^\n]*\|\s*(sudo\s+)?sh")
        self.assertNotIn("get.docker.com", script)

    def test_fresh_install_is_loopback_only_and_does_not_print_token(self):
        with tempfile.TemporaryDirectory(prefix="memory-install-test-") as tmp:
            root = Path(tmp)
            service = root / "service"
            shutil.copytree(
                SERVICE_ROOT,
                service,
                ignore=shutil.ignore_patterns(".venv", "__pycache__", ".pytest_cache"),
            )
            home = root / "user-home"
            home.mkdir()
            fake_bin = root / "bin"
            fake_bin.mkdir()
            systemd_dir = root / "systemd"
            systemd_dir.mkdir()
            self._make_fake_commands(fake_bin)
            venv_bin = service / ".venv" / "bin"
            venv_bin.mkdir(parents=True)
            for name in ("pip", "python"):
                path = venv_bin / name
                body = "#!/bin/sh\n[ \"${HEALTH_FAIL:-}\" != 1 ] || exit 1\nexit 0\n" if name == "python" else "#!/bin/sh\nexit 0\n"
                path.write_text(body, encoding="utf-8")
                path.chmod(0o755)

            env = os.environ.copy()
            env.update({
                "PATH": f"{fake_bin}:{env['PATH']}",
                "HOME": str(home),
                "USER": "testuser",
                "SUDO_USER": "testuser",
                "MEM0_INSTALL_HOME": str(home),
                "MEM0_SYSTEMD_DIR": str(systemd_dir),
                "DOCKER_ARGS_FILE": str(root / "docker-args.txt"),
                "OLLAMA_PULL_LOG": str(root / "ollama-pulls.txt"),
            })
            result = subprocess.run(
                ["bash", str(service / "scripts" / "install-pi.sh")],
                cwd=service,
                env=env,
                capture_output=True,
                text=True,
                timeout=30,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertNotIn(TOKEN_SENTINEL, result.stdout)
            self.assertNotIn(TOKEN_SENTINEL, result.stderr)

            config = (home / ".mem0" / "config.env").read_text(encoding="utf-8")
            self.assertIn("MEM0_MCP_HOST=127.0.0.1", config)
            self.assertIn("MEM0_DASHBOARD_HOST=127.0.0.1", config)
            self.assertIn("MEM0_LLM_PROVIDER=ollama", config)
            self.assertIn("MEM0_LLM_MODEL=qwen2.5:7b-instruct", config)
            self.assertNotIn("MEM0_MCP_ALLOW_NONLOCAL=1", config)

            mcp_unit = (systemd_dir / "mem0-mcp.service").read_text(encoding="utf-8")
            dashboard_unit = (systemd_dir / "mem0-dashboard.service").read_text(encoding="utf-8")
            self.assertIn(f"WorkingDirectory={service}", mcp_unit)
            self.assertIn(f"ExecStart={service}/.venv/bin/python", mcp_unit)
            self.assertIn("MEM0_MCP_HOST=127.0.0.1", mcp_unit)
            self.assertNotIn("0.0.0.0", mcp_unit + dashboard_unit)
            self.assertNotIn("/mem0-local", mcp_unit + dashboard_unit)
            self.assertIn("MEM0_DASHBOARD_HOST=127.0.0.1", dashboard_unit)
            docker_args = (root / "docker-args.txt").read_text(encoding="utf-8")
            self.assertIn("127.0.0.1:6333:6333", docker_args)

            token_file = home / ".mem0" / "mcp-token"
            self.assertEqual(token_file.read_text(encoding="utf-8").strip(), TOKEN_SENTINEL)
            self.assertEqual(stat.S_IMODE(token_file.stat().st_mode), 0o600)
            config_file = home / ".mem0" / "config.env"
            self.assertEqual(stat.S_IMODE(config_file.stat().st_mode), 0o600)

            pull_log = (root / "ollama-pulls.txt").read_text(encoding="utf-8")
            self.assertIn("pull nomic-embed-text", pull_log)
            self.assertIn("pull qwen2.5:7b-instruct", pull_log)

            env["OLLAMA_PULL_FAIL"] = "1"
            failed_pull = subprocess.run(
                ["bash", str(service / "scripts" / "install-pi.sh")],
                cwd=service,
                env=env,
                capture_output=True,
                text=True,
                timeout=30,
            )
            self.assertNotEqual(failed_pull.returncode, 0)
            self.assertNotIn("SUCCESS!", failed_pull.stdout)
            self.assertIn("Could not download Ollama embedding model", failed_pull.stderr)
            env.pop("OLLAMA_PULL_FAIL")

            env["DEPENDENCY_FAIL"] = "1"
            failed_dependency = subprocess.run(
                ["bash", str(service / "scripts" / "install-pi.sh")],
                cwd=service,
                env=env,
                capture_output=True,
                text=True,
                timeout=30,
            )
            self.assertNotEqual(failed_dependency.returncode, 0)
            self.assertNotIn("SUCCESS!", failed_dependency.stdout)
            self.assertIn("Qdrant did not pass its local health check", failed_dependency.stderr)
            env.pop("DEPENDENCY_FAIL")

            env["HEALTH_FAIL"] = "1"
            failed_health = subprocess.run(
                ["bash", str(service / "scripts" / "install-pi.sh")],
                cwd=service,
                env=env,
                capture_output=True,
                text=True,
                timeout=30,
            )
            self.assertNotEqual(failed_health.returncode, 0)
            self.assertNotIn("SUCCESS!", failed_health.stdout)
            self.assertIn("Initial memory health check failed", failed_health.stderr)
            env.pop("HEALTH_FAIL")

            env["DOCKER_EXISTING_UNSAFE"] = "1"
            unsafe = subprocess.run(
                ["bash", str(service / "scripts" / "install-pi.sh")],
                cwd=service,
                env=env,
                capture_output=True,
                text=True,
                timeout=30,
            )
            self.assertNotEqual(unsafe.returncode, 0)
            self.assertIn("not confirmed loopback-only", unsafe.stderr)

    @staticmethod
    def _make_fake_commands(fake_bin: Path) -> None:
        commands = {
            "sudo": "#!/bin/sh\n# Execute only harmless file capture/copy commands in the temp test environment.\ncase \"$1\" in tee) shift; exec tee \"$@\";; cp) shift; exec cp \"$@\";; docker) shift; exec docker \"$@\";; systemctl) shift; exec systemctl \"$@\";; esac\ncat >/dev/null 2>/dev/null || true\nexit 0\n",
            "docker": "#!/bin/sh\ncase \"$1\" in\n  ps) [ \"${DOCKER_EXISTING_UNSAFE:-}\" = 1 ] && echo mem0-qdrant;;\n  port) echo 0.0.0.0:6333;;\n  run) printf '%s\\n' \"$@\" > \"$DOCKER_ARGS_FILE\";;\nesac\nexit 0\n",
            "ollama": "#!/bin/sh\nprintf '%s\\n' \"$*\" >> \"$OLLAMA_PULL_LOG\"\n[ \"${OLLAMA_PULL_FAIL:-}\" != 1 ]\n",
            "curl": "#!/bin/sh\n[ \"${DEPENDENCY_FAIL:-}\" != 1 ]\n",
            "systemctl": "#!/bin/sh\nexit 0\n",
            "chown": "#!/bin/sh\nexit 0\n",
            "openssl": f"#!/bin/sh\nprintf '%s\\n' '{TOKEN_SENTINEL}'\n",
        }
        for name, body in commands.items():
            path = fake_bin / name
            path.write_text(body, encoding="utf-8")
            path.chmod(0o755)


if __name__ == "__main__":
    unittest.main()
