#!/usr/bin/env python3
"""Unit tests for the execution layer. Stdlib unittest only.

Every test drives a **fake** command through MEM0_ARCH_CMD. Nothing here ever
launches a real agent, so the suite is safe to run anywhere and does not depend
on `claude` being installed.

Run: .venv/bin/python -m unittest dashboard.test_runner -v
"""

import json
import os
import sys
import tempfile
import time
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import architectures as arch  # noqa: E402
import runner  # noqa: E402


def wait_for(predicate, timeout=10, interval=0.05):
    """Poll until true. Runs are supervised on a thread, so the verdict lands
    slightly after start_run returns."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(interval)
    return False


class RunnerFixture(unittest.TestCase):
    """Temp store, a real repo directory, and a fake agent command."""

    COMMAND = "echo hello from the fake agent; exit 0"

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        base = Path(self._tmp.name).resolve()
        self.arch_dir = base / "architectures"
        self.repo = base / "Projeler" / "demo"
        self.repo.mkdir(parents=True)
        self._orig_dir = arch.ARCH_DIR
        arch.ARCH_DIR = self.arch_dir
        self._orig_env = {k: os.environ.get(k) for k in
                          ("MEM0_ARCH_CMD", "MEM0_ARCH_RUN_TIMEOUT", "MEM0_ARCH_TESTING",
                           "MEM0_ARCH_MEMORY")}
        os.environ["MEM0_ARCH_TESTING"] = "1"     # the fake command is opt-in
        os.environ["MEM0_ARCH_CMD"] = self.COMMAND
        # The bridge is off unless a test asks for it. Otherwise every run here
        # would shell out to the real mem.py — writing test noise into the
        # actual shared brain, and paying a 15s timeout each time it is down.
        os.environ["MEM0_ARCH_MEMORY"] = "0"
        os.environ.pop("MEM0_ARCH_RUN_TIMEOUT", None)
        self.arch_id = arch.create_architecture(
            "Runner Demo", str(self.repo), "", resolve_repo=lambda v: Path(v).resolve(),
            template_id="bugfix")["id"]

    def tearDown(self):
        # Runs are supervised on daemon threads that write their terminal record
        # through arch.ARCH_DIR. Restoring the global while one is still in
        # flight sends that write to the *real* store in ~/.mem0 — which is how
        # a "runner-demo" directory full of fake-agent runs turned up there. So:
        # stop them, then wait for them to finish writing, then restore.
        for arch_id in self.arch_ids():
            for record in runner.history(arch_id, limit=50):
                if record.get("status") == "running" and record.get("pid"):
                    try:
                        os.kill(int(record["pid"]), 9)
                    except OSError:
                        pass
        for arch_id in self.arch_ids():
            wait_for(lambda: not runner.active(arch_id), timeout=5)
            self.assertTrue(runner.wait_for_supervisors(arch_id, timeout=10),
                            f"runner supervisor for {arch_id} did not finish cleanup")
        arch.ARCH_DIR = self._orig_dir
        for key, value in self._orig_env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        self._tmp.cleanup()

    def arch_ids(self):
        """Every architecture this fixture may have started a run for. Subclasses
        that create a second one override this so its threads are waited on too."""
        return [self.arch_id]

    def set_command(self, command):
        os.environ["MEM0_ARCH_CMD"] = command

    def run_to_completion(self, timeout=10, arch_id=None):
        arch_id = arch_id or self.arch_id
        runner.start_run(arch_id)
        wait_for(lambda: (runner.latest(arch_id) or {}).get("status") in runner.TERMINAL,
                 timeout=timeout)
        return runner.latest(arch_id)


# --------------------------------------------------------------------------
# Lifecycle
# --------------------------------------------------------------------------
class TestRunLifecycle(RunnerFixture):

    def test_successful_run_ends_done(self):
        record = self.run_to_completion()
        self.assertEqual(record["status"], "done")
        self.assertEqual(record["exit_code"], 0)
        self.assertTrue(record["started_at"] and record["ended_at"])

    def test_run_writes_a_record_and_a_log(self):
        self.run_to_completion()
        run_id = runner.latest(self.arch_id)["id"]
        directory = self.arch_dir / "runs" / self.arch_id
        self.assertTrue((directory / f"{run_id}.json").is_file())
        self.assertTrue((directory / f"{run_id}.log").is_file())
        self.assertTrue((directory / f"{run_id}.prompt").is_file())

    def test_log_captures_the_command_output(self):
        self.run_to_completion()
        run_id = runner.latest(self.arch_id)["id"]
        text = runner.get_log(self.arch_id, run_id)["text"]
        self.assertIn("hello from the fake agent", text)

    def test_nonzero_exit_is_failed(self):
        self.set_command("echo breaking; exit 3")
        record = self.run_to_completion()
        self.assertEqual(record["status"], "failed")
        self.assertEqual(record["exit_code"], 3)

    def test_stderr_lands_in_the_same_log(self):
        self.set_command("echo to-stderr 1>&2; exit 0")
        self.run_to_completion()
        run_id = runner.latest(self.arch_id)["id"]
        self.assertIn("to-stderr", runner.get_log(self.arch_id, run_id)["text"])

    def test_run_starts_in_the_repo_directory(self):
        self.set_command("pwd; exit 0")
        self.run_to_completion()
        run_id = runner.latest(self.arch_id)["id"]
        self.assertIn(str(self.repo.resolve()), runner.get_log(self.arch_id, run_id)["text"])

    def test_records_the_phase_it_ran(self):
        self.assertEqual(self.run_to_completion()["phase"], "discover")

    def test_unknown_architecture_is_not_found(self):
        with self.assertRaises(FileNotFoundError):
            runner.start_run("no-such-architecture")

    def test_history_is_newest_first_and_capped(self):
        for _ in range(3):
            self.run_to_completion()
        rows = runner.history(self.arch_id)
        self.assertLessEqual(len(rows), runner.HISTORY_LIMIT)
        self.assertEqual([r["id"] for r in rows], sorted((r["id"] for r in rows), reverse=True))


# --------------------------------------------------------------------------
# One run at a time
# --------------------------------------------------------------------------
class TestSingleFlight(RunnerFixture):

    def test_second_start_while_running_is_refused(self):
        self.set_command("sleep 5")
        runner.start_run(self.arch_id)
        wait_for(lambda: (runner.latest(self.arch_id) or {}).get("pid"))
        with self.assertRaises(FileExistsError):
            runner.start_run(self.arch_id)
        runner.kill_run(self.arch_id)

    def test_a_new_run_is_allowed_once_the_first_finished(self):
        self.run_to_completion()
        second = self.run_to_completion()
        self.assertEqual(second["status"], "done")
        self.assertEqual(len(runner.history(self.arch_id)), 2)

    def test_stale_running_record_does_not_block_forever(self):
        """A record left running by a dead process is not an active run."""
        self.run_to_completion()
        run_id = runner.latest(self.arch_id)["id"]
        runner._update(self.arch_id, run_id, status="running", pid=999_999)
        self.assertIsNone(runner.active(self.arch_id))
        self.assertEqual(runner.latest(self.arch_id)["status"], "interrupted")


# --------------------------------------------------------------------------
# Kill
# --------------------------------------------------------------------------
class TestKill(RunnerFixture):

    def test_kill_marks_killed_and_stops_the_process(self):
        self.set_command("sleep 30")
        runner.start_run(self.arch_id)
        self.assertTrue(wait_for(lambda: (runner.latest(self.arch_id) or {}).get("pid")))
        pid = runner.latest(self.arch_id)["pid"]
        record = runner.kill_run(self.arch_id)
        self.assertEqual(record["status"], "killed")
        self.assertTrue(wait_for(lambda: not runner._alive(pid), timeout=10))

    def test_kill_takes_the_whole_process_group(self):
        """A child outliving its parent would keep editing the repo unattended."""
        self.set_command("sh -c 'sleep 30' & echo child=$!; wait")
        runner.start_run(self.arch_id)
        self.assertTrue(wait_for(lambda: (runner.latest(self.arch_id) or {}).get("pid")))
        pid = runner.latest(self.arch_id)["pid"]
        pgid = os.getpgid(pid)
        runner.kill_run(self.arch_id)
        self.assertTrue(wait_for(
            lambda: not self._group_alive(pgid), timeout=10),
            "process group survived the kill")

    @staticmethod
    def _group_alive(pgid):
        try:
            os.killpg(pgid, 0)
        except OSError:
            return False
        return True

    def test_killing_with_no_run_is_not_found(self):
        with self.assertRaises(FileNotFoundError):
            runner.kill_run(self.arch_id)

    def test_kill_status_survives_the_supervisor_finishing(self):
        self.set_command("sleep 30")
        runner.start_run(self.arch_id)
        wait_for(lambda: (runner.latest(self.arch_id) or {}).get("pid"))
        runner.kill_run(self.arch_id)
        time.sleep(0.6)                       # let the supervisor record the exit
        self.assertEqual(runner.latest(self.arch_id)["status"], "killed")


# --------------------------------------------------------------------------
# Timeout
# --------------------------------------------------------------------------
class TestTimeout(RunnerFixture):

    def test_a_run_past_its_limit_is_failed_and_stopped(self):
        os.environ["MEM0_ARCH_RUN_TIMEOUT"] = "1"
        self.set_command("sleep 30")
        runner.start_run(self.arch_id)
        self.assertTrue(wait_for(
            lambda: (runner.latest(self.arch_id) or {}).get("status") in runner.TERMINAL,
            timeout=15))
        record = runner.latest(self.arch_id)
        self.assertEqual(record["status"], "failed")
        self.assertIn("timed out", record["error"])
        self.assertTrue(wait_for(lambda: not runner._alive(record["pid"]), timeout=10))

    def test_timeout_setting_falls_back_when_nonsense(self):
        os.environ["MEM0_ARCH_RUN_TIMEOUT"] = "not-a-number"
        self.assertEqual(runner.timeout_seconds(), runner.DEFAULT_TIMEOUT)
        os.environ["MEM0_ARCH_RUN_TIMEOUT"] = "0"
        self.assertEqual(runner.timeout_seconds(), runner.DEFAULT_TIMEOUT)


# --------------------------------------------------------------------------
# Log tailing
# --------------------------------------------------------------------------
class TestLogTail(RunnerFixture):

    def test_offset_returns_only_what_is_new(self):
        self.set_command("echo one; sleep 0.4; echo two; exit 0")
        runner.start_run(self.arch_id)
        self.assertTrue(wait_for(lambda: "one" in runner.get_log(self.arch_id, self.run_id())["text"]))
        first = runner.get_log(self.arch_id, self.run_id())
        self.assertTrue(wait_for(
            lambda: runner.get_log(self.arch_id, self.run_id(), first["offset"])["text"].strip()))
        second = runner.get_log(self.arch_id, self.run_id(), first["offset"])
        self.assertNotIn("one", second["text"])
        self.assertGreaterEqual(second["offset"], first["offset"])

    def test_offset_past_the_end_restarts_from_zero(self):
        self.run_to_completion()
        tail = runner.get_log(self.arch_id, self.run_id(), 10 ** 9)
        self.assertEqual(tail["offset"], len(tail["text"].encode()))

    def test_tail_reports_the_run_status(self):
        self.run_to_completion()
        self.assertEqual(runner.get_log(self.arch_id, self.run_id())["status"], "done")

    def test_unknown_run_is_not_found(self):
        with self.assertRaises(FileNotFoundError):
            runner.get_log(self.arch_id, "run-20260101T000000-000001")

    def test_invalid_run_id_is_refused(self):
        for bad in ("../escape", "run-x", "", None):
            with self.subTest(bad=bad), self.assertRaises((ValueError, PermissionError)):
                runner.get_log(self.arch_id, bad)

    def test_invalid_architecture_id_is_refused(self):
        for bad in ("../escape", "Caps", ""):
            with self.subTest(bad=bad), self.assertRaises((ValueError, PermissionError)):
                runner.history(bad)

    def run_id(self):
        return runner.latest(self.arch_id)["id"]


# --------------------------------------------------------------------------
# Reconciliation after a restart
# --------------------------------------------------------------------------
class TestReconcile(RunnerFixture):

    def test_dead_running_record_becomes_interrupted(self):
        self.run_to_completion()
        run_id = runner.latest(self.arch_id)["id"]
        runner._update(self.arch_id, run_id, status="running", pid=999_999, ended_at=None)
        fixed = runner.reconcile_on_startup()
        self.assertIn(run_id, fixed)
        record = runner.latest(self.arch_id)
        self.assertEqual(record["status"], "interrupted")
        self.assertTrue(record["ended_at"])

    def test_finished_runs_are_left_alone(self):
        self.run_to_completion()
        before = runner.latest(self.arch_id)
        runner.reconcile_on_startup()
        self.assertEqual(runner.latest(self.arch_id), before)

    def test_a_live_run_is_left_alone(self):
        self.set_command("sleep 5")
        runner.start_run(self.arch_id)
        wait_for(lambda: (runner.latest(self.arch_id) or {}).get("pid"))
        runner.reconcile_on_startup()
        self.assertEqual(runner.latest(self.arch_id)["status"], "running")
        runner.kill_run(self.arch_id)

    def test_survives_an_empty_store(self):
        self.assertEqual(runner.reconcile_on_startup(), [])


# --------------------------------------------------------------------------
# The prompt
# --------------------------------------------------------------------------
class TestPromptBuilder(RunnerFixture):

    def prompt(self):
        arch.add_task(self.arch_id, "discover", "Reproduce the crash", priority="P0")
        arch.add_task(self.arch_id, "build", "Fix it")
        return runner.build_phase_prompt(arch.read_architecture(self.arch_id))

    def test_names_the_project_and_repo(self):
        text = self.prompt()
        self.assertIn("Runner Demo", text)
        self.assertIn(str(self.repo), text)

    def test_carries_the_reporting_contract(self):
        text = self.prompt()
        self.assertIn("/api/architecture/task", text)
        self.assertIn('"status": "doing"', text)
        self.assertIn('"status": "done"', text)
        self.assertIn("notes", text)

    def test_reporting_does_not_depend_on_curl_alone(self):
        """A real run had its `doing` report swallowed by a command guard that
        rewrites curl, so the task jumped straight to done. The contract must
        offer a client that does not go through a shell pipeline."""
        text = self.prompt()
        self.assertIn("urllib.request", text)
        self.assertIn("python3 -", text)

    def test_tells_the_agent_to_surface_a_failed_report(self):
        text = self.prompt()
        self.assertIn("do not silently carry on", text)
        self.assertIn("closing summary", text)

    def test_asks_for_doing_before_done(self):
        self.assertIn("before", self.prompt().split("Reporting contract")[1][:400])

    def test_forbids_advancing_phases(self):
        self.assertIn("Do not try to advance phases yourself", self.prompt())

    def test_explains_that_reporting_is_what_advances_the_project(self):
        # Matched on unwrapped text: the prompt is hard-wrapped, so a phrase
        # long enough to be meaningful will straddle a newline.
        text = " ".join(self.prompt().split())
        self.assertIn("moves the phase on by itself", text)
        self.assertIn("Reporting each task accurately *is* how the project advances", text)
        for phase in arch.MANUAL_PHASES:
            self.assertIn(f"`{phase}`", text)

    def test_carries_the_rabbit_hole_rule(self):
        self.assertIn("three times", self.prompt())

    def test_records_are_asked_for_in_english(self):
        """A run once wrote its whole plan in Turkish because the repo was; the
        store is searched by embedding and stops matching itself when split."""
        text = " ".join(self.prompt().split())
        self.assertIn("Write everything you send to the dashboard in **English**", text)
        self.assertIn("even when the repository", text)
        self.assertIn("keep that repository's own conventions", text)

    def test_lists_only_this_phase_s_tasks(self):
        text = self.prompt()
        self.assertIn("Reproduce the crash", text)
        self.assertNotIn("Fix it", text)          # that one belongs to `build`

    def test_carries_the_phase_playbook(self):
        text = self.prompt()
        self.assertIn("Agents:", text)
        self.assertIn("Explore", text)            # discover's real playbook

    def test_states_the_working_rules(self):
        text = self.prompt()
        for rule in ("Small diffs", "tests", "secrets", "refactor"):
            with self.subTest(rule=rule):
                self.assertIn(rule, text)

    def test_contains_no_environment_values(self):
        """The prompt is written to disk; a secret reaching it is a secret leaked."""
        os.environ["MEM0_SECRET_CANARY"] = "super-secret-value"
        try:
            self.assertNotIn("super-secret-value", self.prompt())
        finally:
            os.environ.pop("MEM0_SECRET_CANARY", None)

    def test_run_stores_the_prompt_next_to_the_log(self):
        arch.add_task(self.arch_id, "discover", "something to do")
        self.run_to_completion()
        run_id = runner.latest(self.arch_id)["id"]
        text = (self.arch_dir / "runs" / self.arch_id / f"{run_id}.prompt").read_text("utf-8")
        self.assertIn("Reporting contract", text)


# --------------------------------------------------------------------------
# Availability + isolation from the architecture store
# --------------------------------------------------------------------------
class TestRunnerEnvironment(RunnerFixture):

    def test_override_makes_the_runner_available(self):
        self.assertTrue(runner.runner_available())

    def test_fake_command_is_ignored_without_the_testing_flag(self):
        """MEM0_ARCH_CMD alone must not redirect what ▶ Run launches."""
        os.environ.pop("MEM0_ARCH_TESTING", None)
        self.assertIsNone(runner.testing_command())
        argv, label = runner._command(Path(os.devnull))
        self.assertEqual(argv[0], "claude")
        self.assertNotIn("fake agent", label)

    def test_testing_flag_must_be_exactly_one(self):
        for value in ("0", "true", "yes", ""):
            with self.subTest(value=value):
                os.environ["MEM0_ARCH_TESTING"] = value
                self.assertIsNone(runner.testing_command())

    def test_availability_without_the_flag_falls_back_to_the_real_binary(self):
        os.environ.pop("MEM0_ARCH_TESTING", None)
        import shutil as _shutil
        self.assertEqual(runner.runner_available(), _shutil.which("claude") is not None)

    def test_runs_directory_is_not_listed_as_an_architecture(self):
        self.run_to_completion()
        self.assertEqual([r["id"] for r in arch.list_architectures()["results"]],
                         [self.arch_id])

    def test_run_does_not_touch_the_architecture_document(self):
        """The agent reports through the API; the runner itself writes nothing
        into the store."""
        before = json.loads((self.arch_dir / f"{self.arch_id}.json").read_text("utf-8"))
        self.run_to_completion()
        after = json.loads((self.arch_dir / f"{self.arch_id}.json").read_text("utf-8"))
        self.assertEqual(before, after)


class TestUnattendedBoundaries(RunnerFixture):
    """An unattended run may edit files — that is the job. It may not write git
    history or delete things: those are decisions with an owner, and the owner is
    not in the room."""

    def test_every_history_writing_git_subcommand_is_denied(self):
        for sub in ("commit", "push", "merge", "rebase", "reset", "revert",
                    "cherry-pick", "am", "apply", "tag", "branch", "checkout",
                    "switch", "worktree", "update-ref", "filter-branch"):
            with self.subTest(sub=sub):
                self.assertIn(f"Bash(git {sub}:*)", runner.DENIED_TOOLS)

    def test_git_config_is_denied_because_it_can_reopen_the_others(self):
        """Aliases and hooks are configuration. Leaving it open would make the
        rest of the list a suggestion."""
        self.assertIn("Bash(git config:*)", runner.DENIED_TOOLS)

    def test_reading_history_stays_open(self):
        """Understanding the repository is the job."""
        for sub in ("log", "diff", "status", "show", "blame"):
            with self.subTest(sub=sub):
                self.assertNotIn(f"Bash(git {sub}:*)", runner.DENIED_TOOLS)

    def test_the_denials_reach_the_command_line(self):
        os.environ.pop("MEM0_ARCH_TESTING", None)
        argv, _ = runner._command(Path(os.devnull))
        self.assertIn("--disallowedTools", argv)
        passed = set(argv[argv.index("--disallowedTools") + 1:])
        self.assertTrue(set(runner.DENIED_TOOLS) <= passed)


# --------------------------------------------------------------------------
# Log rendering — the stream is JSONL; a person should never see that.
# --------------------------------------------------------------------------
class TestLogRendering(unittest.TestCase):

    def line(self, event):
        return runner.render_log_line(json.dumps(event))

    def test_assistant_text_is_the_text(self):
        self.assertEqual(self.line({"type": "assistant", "message": {"content": [
            {"type": "text", "text": "Reading the repo."}]}}), "Reading the repo.")

    def test_a_tool_call_reads_as_one_line(self):
        out = self.line({"type": "assistant", "message": {"content": [
            {"type": "tool_use", "name": "Bash", "input": {"command": "git log --oneline -5"}}]}})
        self.assertEqual(out, "· Bash: git log --oneline -5")

    def test_a_file_tool_shows_its_path(self):
        out = self.line({"type": "assistant", "message": {"content": [
            {"type": "tool_use", "name": "Read", "input": {"file_path": "/repo/a.py"}}]}})
        self.assertEqual(out, "· Read: /repo/a.py")

    def test_a_tool_without_a_recognisable_field_still_names_itself(self):
        out = self.line({"type": "assistant", "message": {"content": [
            {"type": "tool_use", "name": "Weird", "input": {"blob": {"x": 1}}}]}})
        self.assertEqual(out, "· Weird")

    def test_text_and_tools_in_one_message_both_appear(self):
        out = self.line({"type": "assistant", "message": {"content": [
            {"type": "text", "text": "Now checking."},
            {"type": "tool_use", "name": "Grep", "input": {"pattern": "TODO"}}]}})
        self.assertEqual(out, "Now checking.\n· Grep: TODO")

    def test_thinking_blocks_are_not_shown(self):
        self.assertEqual(self.line({"type": "assistant", "message": {"content": [
            {"type": "thinking", "thinking": "hmm"}]}}), "")

    def test_the_result_event_is_the_closing_summary(self):
        self.assertEqual(self.line({"type": "result", "result": "Done: 2 tasks."}),
                         "Done: 2 tasks.")

    def test_an_error_result_says_so(self):
        out = self.line({"type": "result", "result": "boom", "is_error": True})
        self.assertIn("run reported an error", out)
        self.assertIn("boom", out)

    def test_session_start_is_one_line(self):
        self.assertEqual(self.line({"type": "system", "subtype": "init"}), "· session started")

    def test_tool_results_are_dropped_but_failures_are_kept(self):
        self.assertEqual(self.line({"type": "user", "message": {"content": [
            {"type": "tool_result", "content": "x" * 5000}]}}), "")
        self.assertEqual(self.line({"type": "user", "message": {"content": [
            {"type": "tool_result", "is_error": True}]}}), "· ↳ tool call failed")

    def test_plain_text_passes_through(self):
        """Older runs and the command header are not JSON."""
        self.assertEqual(runner.render_log_line("$ claude -p…"), "$ claude -p…")

    def test_broken_json_passes_through_rather_than_vanishing(self):
        self.assertEqual(runner.render_log_line('{"type": "assist'), '{"type": "assist')

    def test_an_unknown_event_type_is_silent(self):
        self.assertEqual(self.line({"type": "something_new", "payload": 1}), "")


class TestLogChunking(unittest.TestCase):

    def test_a_partial_last_line_is_left_for_the_next_poll(self):
        whole = json.dumps({"type": "result", "result": "one"}) + "\n"
        text, consumed = runner.render_log_chunk(whole + '{"type": "resu')
        self.assertEqual(text.strip(), "one")
        self.assertEqual(consumed, len(whole.encode()))

    def test_nothing_is_consumed_without_a_line_break(self):
        self.assertEqual(runner.render_log_chunk('{"type": "resu'), ("", 0))

    def test_an_absurdly_long_unterminated_line_still_advances(self):
        blob = "x" * (runner.LOG_TAIL_MAX + 10)
        text, consumed = runner.render_log_chunk(blob)
        self.assertEqual(consumed, len(blob.encode()))

    def test_empty_input_consumes_nothing(self):
        self.assertEqual(runner.render_log_chunk(""), ("", 0))

    def test_the_offset_is_counted_in_bytes_not_in_decoded_characters(self):
        """A byte that will not decode becomes a three-byte replacement
        character. An offset taken from the decoded string would run past real
        data and the next poll would start mid-line for good."""
        chunk = b'{"type": "result", "result": "caf\xe9 done"}\n'
        text, consumed = runner.render_log_bytes(chunk)
        self.assertEqual(consumed, len(chunk))
        self.assertIn("done", text)


class TestLogTailRenders(RunnerFixture):

    def test_a_json_log_reads_as_prose_over_http_shaped_calls(self):
        self.set_command(
            """printf '%s\\n' '{"type":"system","subtype":"init"}' """
            """'{"type":"assistant","message":{"content":[{"type":"text","text":"hello"}]}}' """
            """'{"type":"result","result":"finished"}'; exit 0""")
        self.run_to_completion()
        run_id = runner.latest(self.arch_id)["id"]
        text = runner.get_log(self.arch_id, run_id)["text"]
        self.assertIn("· session started", text)
        self.assertIn("hello", text)
        self.assertIn("finished", text)
        self.assertNotIn('"type"', text)          # the JSON itself never surfaces

    def test_raw_returns_the_stream_untouched(self):
        self.set_command("""printf '%s\\n' '{"type":"result","result":"finished"}'; exit 0""")
        self.run_to_completion()
        run_id = runner.latest(self.arch_id)["id"]
        self.assertIn('"type"', runner.get_log(self.arch_id, run_id, raw=True)["text"])

    def test_the_tail_reports_when_the_run_started(self):
        self.run_to_completion()
        run_id = runner.latest(self.arch_id)["id"]
        self.assertTrue(runner.get_log(self.arch_id, run_id)["started_at"])


# --------------------------------------------------------------------------
# Plan mode — a phase with no tasks needs deciding, not doing.
# --------------------------------------------------------------------------
class TestPlanMode(RunnerFixture):

    def test_an_empty_phase_is_a_planning_run(self):
        self.assertEqual(runner.run_mode(arch.read_architecture(self.arch_id)), "plan")

    def test_a_phase_with_tasks_is_a_doing_run(self):
        arch.add_task(self.arch_id, "discover", "something")
        self.assertEqual(runner.run_mode(arch.read_architecture(self.arch_id)), "do")

    def test_the_run_record_says_which(self):
        self.assertEqual(self.run_to_completion()["mode"], "plan")
        arch.add_task(self.arch_id, "discover", "something")
        self.assertEqual(self.run_to_completion()["mode"], "do")

    def prompt(self):
        return runner.build_phase_prompt(arch.read_architecture(self.arch_id))

    def test_the_plan_prompt_asks_for_tasks_not_work(self):
        text = " ".join(self.prompt().split())
        self.assertIn("no work has been recorded", text)
        self.assertIn("decide what that work is — not to do it", text)

    def test_it_caps_the_number_of_tasks(self):
        self.assertIn(f"between 1 and {runner.PLAN_TASK_LIMIT} tasks", self.prompt())

    def test_it_demands_verifiable_titles_and_acceptance_criteria(self):
        text = " ".join(self.prompt().split())
        self.assertIn("concrete and verifiable", text)
        self.assertIn("acceptance criteria in `notes`", text)

    def test_it_forbids_executing_the_new_tasks(self):
        text = " ".join(self.prompt().split())
        self.assertIn("Do not execute the tasks you just created", text)
        self.assertIn("Do not edit code", text)

    def test_it_allows_an_empty_plan_as_an_answer(self):
        self.assertIn("An empty plan is a real answer", " ".join(self.prompt().split()))

    def test_it_shows_the_create_endpoint_not_the_patch_one(self):
        text = self.prompt()
        self.assertIn('"phase": "discover"', text)
        self.assertIn("/api/architecture/task", text)
        self.assertNotIn('"status": "done"', text)

    def test_a_planning_prompt_carries_the_phase_playbook(self):
        self.assertIn("Explore", self.prompt())

    def test_a_planning_prompt_asks_for_english_records(self):
        text = " ".join(self.prompt().split())
        self.assertIn("Write everything you send to the dashboard in **English**", text)

    def test_both_modes_carry_the_same_language_rule(self):
        plan = " ".join(self.prompt().split())
        arch.add_task(self.arch_id, "discover", "something")
        do = " ".join(self.prompt().split())
        rule = " ".join(runner.LANGUAGE_RULE.split())
        for text in (plan, do):
            self.assertIn(rule, text)

    def test_the_doing_prompt_is_unchanged(self):
        arch.add_task(self.arch_id, "discover", "something")
        text = self.prompt()
        self.assertIn("Reporting contract", text)
        self.assertNotIn("no work has been recorded", text)


# --------------------------------------------------------------------------
# Auto-chain — opt in, capped, and it says why it stopped.
# --------------------------------------------------------------------------
class ChainFixture(RunnerFixture):

    def enable(self):
        arch.set_auto_chain(self.arch_id, True)

    def record(self):
        return runner.latest(self.arch_id) or {}

    def wait_quiet(self, timeout=15):
        """Wait until nothing is running and the last run has a chain verdict."""
        wait_for(lambda: (self.record().get("status") in runner.TERMINAL
                          and self.record().get("chain_next")), timeout=timeout)
        return self.record()


class TestChainHaltReasons(ChainFixture):

    def halt(self, **record_over):
        architecture = arch.read_architecture(self.arch_id)
        return runner.chain_halt_reason(architecture, dict({"status": "done"}, **record_over),
                                        self.arch_id)

    def test_off_by_default(self):
        self.assertIn("auto-chain is off", self.halt())

    def test_a_failed_run_stops_the_chain(self):
        self.enable()
        self.assertIn("ended failed", self.halt(status="failed"))

    def test_a_killed_run_stops_the_chain(self):
        self.enable()
        self.assertIn("ended killed", self.halt(status="killed"))

    def test_a_manual_phase_stops_the_chain(self):
        self.enable()
        arch.bulk_update_workflow(self.arch_id, {"commit": {"status": "active"}})
        arch.set_auto_chain(self.arch_id, True)
        aid = arch.create_architecture("Manual", str(self.repo), "",
                                       resolve_repo=lambda v: Path(v).resolve(),
                                       template_id="full-16")["id"]
        arch.set_auto_chain(aid, True)
        arch.bulk_update_workflow(aid, {"approve": {"status": "active"}})
        reason = runner.chain_halt_reason(arch.read_architecture(aid), {"status": "done"}, aid)
        self.assertIn("manual phase", reason)

    def test_a_healthy_run_does_not_halt(self):
        self.enable()
        self.assertIsNone(self.halt())


class TestRateLimitGuard(ChainFixture):
    """A run refused for quota exits 0 and looks finished. Chaining past it would
    spend the whole cap on runs that were never going to start."""

    def test_the_marker_is_found_whatever_the_casing(self):
        for text in ("Claude usage limit reached", "RATE LIMIT exceeded",
                     "Error 429: too many requests", "your credit balance is too low"):
            with self.subTest(text=text):
                self.assertIsNotNone(runner.limit_marker(text))

    def test_ordinary_output_is_not_mistaken_for_a_refusal(self):
        for text in ("all tests pass", "rewrote the rate calculation", "", None):
            with self.subTest(text=text):
                self.assertIsNone(runner.limit_marker(text))

    def test_a_bare_429_is_not_a_refusal(self):
        """Three digits are a line count, a byte size, a port. Only the
        qualified forms mean a wall."""
        for text in ("trimmed the payload to 429 bytes", "429 tests pass",
                     "see issue 429"):
            with self.subTest(text=text):
                self.assertIsNone(runner.limit_marker(text))

    def test_a_qualified_429_still_reads_as_a_refusal(self):
        for text in ("the API returned HTTP 429", "Error 429: too many requests",
                     "status 429", "429 Too Many Requests"):
            with self.subTest(text=text):
                self.assertIsNotNone(runner.limit_marker(text))

    def test_a_refused_run_is_flagged_even_though_it_exited_zero(self):
        self.set_command("echo 'Claude usage limit reached. Try again later.'; exit 0")
        record = self.run_to_completion()
        self.assertEqual(record["status"], "done")          # the CLI said it was fine
        self.assertTrue(wait_for(lambda: self.record().get("limit_hit")))
        self.assertIn("usage limit", self.record()["limit_hit"])

    def test_a_refusal_halts_the_chain_immediately(self):
        self.enable()
        self.set_command("echo 'Claude usage limit reached'; exit 0")
        self.run_to_completion()
        self.wait_quiet()
        self.assertIn("refused", self.record()["chain_next"])
        self.assertEqual(len(runner.history(self.arch_id, limit=10)), 1,
                         "a refused run must not start another")

    def test_the_reason_says_no_work_was_done(self):
        """Otherwise a refusal reads like a run that simply had nothing to do."""
        self.enable()
        self.set_command("echo 'rate limit exceeded'; exit 0")
        self.run_to_completion()
        self.wait_quiet()
        self.assertIn("no work was done", self.record()["chain_next"])

    def test_a_healthy_run_carries_no_limit_flag(self):
        self.run_to_completion()
        self.assertTrue(wait_for(lambda: self.record().get("progressed") is not None))
        self.assertIsNone(self.record()["limit_hit"])


class TestRefusalScanScope(unittest.TestCase):
    """Where a refusal may honestly be found. Not everywhere the word appears:
    the log carries the agent's own commands, and searching for a phrase is not
    the same as being told it."""

    def test_a_tool_call_mentioning_a_limit_is_not_a_refusal(self):
        line = json.dumps({"type": "assistant", "message": {"content": [
            {"type": "tool_use", "name": "Bash",
             "input": {"command": 'grep -rn "rate limit" dashboard/'}}]}})
        self.assertIsNone(runner.limit_marker(runner.refusal_text(line)))

    def test_the_agents_own_prose_is_still_scanned(self):
        line = json.dumps({"type": "result", "result": "Claude usage limit reached"})
        self.assertIsNotNone(runner.limit_marker(runner.refusal_text(line)))

    def test_a_half_line_at_the_start_of_a_tail_is_dropped(self):
        """A tail can begin mid-line. The half does not parse, so it falls
        through as raw JSON — tool arguments and all — and would be scanned as
        if the agent had said it."""
        half = '"input": {"command": "curl -s api/x | grep \\"rate limit\\""}}]}}'
        keep = json.dumps({"type": "result", "result": "all done"})
        scanned = runner.refusal_text(half + "\n" + keep, partial_first=True)
        self.assertIsNone(runner.limit_marker(scanned))
        self.assertIn("all done", scanned)

    def test_a_whole_first_line_is_kept(self):
        line = json.dumps({"type": "result", "result": "rate limit exceeded"})
        self.assertIsNotNone(
            runner.limit_marker(runner.refusal_text(line, partial_first=False)))

    def test_a_run_that_only_grepped_for_the_phrase_keeps_its_chain(self):
        """End to end: the whole point is that this run is not halted."""
        event = json.dumps({"type": "assistant", "message": {"content": [
            {"type": "tool_use", "name": "Bash",
             "input": {"command": 'grep -rn "usage limit reached" .'}}]}})
        self.assertIsNone(runner.limit_marker(runner.refusal_text(event)))


class TestResumeAfterARefusal(ChainFixture):
    """A refused run did no work, so resuming is starting the same phase over.
    Nothing has to be restored — but somebody has to notice the quota cleared."""

    def refuse(self):
        self.set_command("echo 'Claude usage limit reached'; exit 0")
        self.run_to_completion()
        self.wait_quiet()
        return self.record()

    def test_a_refusal_books_a_time_to_try_again(self):
        self.enable()
        record = self.refuse()
        self.assertTrue(record["resume_at"])
        self.assertEqual(record["resume_attempt"], 1)
        self.assertEqual(record["resume_delay"], runner.RESUME_DELAYS[0])

    def test_the_reason_names_the_time_instead_of_asking_you_to_do_it(self):
        self.enable()
        self.refuse()
        reason = self.record()["chain_next"]
        self.assertIn("picking up again at", reason)
        self.assertIn("attempt 1 of", reason)

    def test_nothing_is_booked_when_chaining_is_off(self):
        record = self.refuse()
        self.assertIsNone(record["resume_at"])

    def test_it_waits_until_the_time_comes(self):
        self.enable()
        self.refuse()
        self.assertIsNone(runner.resume_due(self.arch_id), "resumed before its time")

    def test_it_resumes_once_the_time_has_passed(self):
        self.enable()
        self.refuse()
        later = datetime.now(timezone.utc) + timedelta(seconds=runner.RESUME_DELAYS[0] + 1)
        self.assertIsNotNone(runner.resume_due(self.arch_id, now=later))

    def test_turning_chaining_off_cancels_the_resume(self):
        self.enable()
        self.refuse()
        arch.set_auto_chain(self.arch_id, False)
        later = datetime.now(timezone.utc) + timedelta(days=1)
        self.assertIsNone(runner.resume_due(self.arch_id, now=later))

    def test_a_manual_phase_cancels_the_resume(self):
        self.enable()
        self.refuse()
        arch.bulk_update_workflow(self.arch_id, {"commit": {"status": "active"}})
        aid = arch.create_architecture("Manual resume", str(self.repo), "",
                                       resolve_repo=lambda v: Path(v).resolve(),
                                       template_id="full-16")["id"]
        arch.set_auto_chain(aid, True)
        arch.bulk_update_workflow(aid, {"approve": {"status": "active"}})
        self.assertIsNone(runner.resume_due(aid, now=datetime.now(timezone.utc)))

    def test_the_ladder_backs_off_and_then_gives_up(self):
        self.enable()
        for expected in runner.RESUME_DELAYS:
            self.refuse()
            self.assertEqual(self.record()["resume_delay"], expected)
        self.refuse()                       # one refusal past the ladder
        self.assertIsNone(self.record()["resume_at"])
        self.assertIn("needs you, not another attempt", self.record()["chain_next"])

    def test_a_refused_run_does_not_spend_the_chain_cap(self):
        self.enable()
        self.refuse()
        self.assertEqual(runner.chain_length(self.arch_id), 0)

    def test_a_sweep_restarts_what_is_due_and_leaves_the_rest(self):
        self.enable()
        self.refuse()
        self.assertEqual(runner.resume_tick(), [])          # not due yet
        record = self.record()
        past = (datetime.now(timezone.utc) - timedelta(seconds=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
        runner._update(self.arch_id, record["id"], resume_at=past)
        self.set_command("echo back to work; exit 0")
        self.assertIn(self.arch_id, runner.resume_tick())
        self.assertTrue(wait_for(
            lambda: (runner.latest(self.arch_id) or {}).get("status") in runner.TERMINAL))
        self.assertIsNone(runner.latest(self.arch_id)["limit_hit"])

    def test_a_sweep_over_an_empty_store_is_harmless(self):
        self.assertEqual(runner.resume_tick(), [])

    def test_a_run_already_in_flight_is_not_doubled(self):
        self.enable()
        self.refuse()
        record = self.record()
        past = (datetime.now(timezone.utc) - timedelta(seconds=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
        runner._update(self.arch_id, record["id"], resume_at=past)
        self.set_command("sleep 5")
        runner.start_run(self.arch_id)
        wait_for(lambda: (self.record() or {}).get("pid"))
        self.assertEqual(runner.resume_tick(), [])
        runner.kill_run(self.arch_id)


class TestProgressDetection(ChainFixture):

    def test_a_run_that_finishes_a_task_counts_as_progress(self):
        tid = arch.add_task(self.arch_id, "discover", "a")["tasks"][0]["id"]
        arch.add_task(self.arch_id, "discover", "b")
        self.set_command(f"exit 0")
        runner.start_run(self.arch_id)
        wait_for(lambda: self.record().get("status") == "running")
        arch.update_task(self.arch_id, tid, status="done")   # as the agent would
        wait_for(lambda: self.record().get("progressed") is not None, timeout=15)
        self.assertTrue(self.record()["progressed"])

    def test_a_run_that_moves_nothing_is_marked_stalled(self):
        arch.add_task(self.arch_id, "discover", "untouched")
        self.run_to_completion()
        self.assertTrue(wait_for(lambda: self.record().get("progressed") is not None))
        self.assertFalse(self.record()["progressed"])

    def test_a_plan_run_that_creates_tasks_counts_as_progress(self):
        self.set_command(
            f"""python3 -c "
import json,urllib.request
body={{'arch':'{self.arch_id}','phase':'discover','title':'planned by the fake agent'}}
" ; exit 0""")
        # The fake agent cannot reach a server here, so simulate its write.
        runner.start_run(self.arch_id)
        wait_for(lambda: self.record().get("status") == "running")
        arch.add_task(self.arch_id, "discover", "planned by the fake agent")
        wait_for(lambda: self.record().get("progressed") is not None, timeout=15)
        self.assertTrue(self.record()["progressed"])


class TestChainRunning(ChainFixture):

    def test_a_chain_stops_when_it_is_off(self):
        self.run_to_completion()
        self.assertTrue(wait_for(lambda: self.record().get("chain_next")))
        self.assertIn("auto-chain is off", self.record()["chain_next"])
        self.assertEqual(len(runner.history(self.arch_id, limit=10)), 1)

    def test_a_chain_stops_after_two_runs_that_moved_nothing(self):
        """The fake agent creates nothing and finishes nothing."""
        self.enable()
        self.run_to_completion()
        self.wait_quiet(timeout=25)
        runs = runner.history(self.arch_id, limit=10)
        self.assertLessEqual(len(runs), 3, "a run that moves nothing must not chain forever")
        self.assertIn("without moving anything", runs[0]["chain_next"] or "")

    def test_the_cap_bounds_an_unattended_chain(self):
        self.assertEqual(runner.CHAIN_CAP, 6)

    def test_a_run_a_person_started_is_not_marked_chained(self):
        self.run_to_completion()
        self.assertFalse(self.record()["chained"])

    def test_chain_length_counts_back_to_the_manual_run(self):
        self.run_to_completion()
        self.assertEqual(runner.chain_length(self.arch_id), 0)

    def test_a_failed_run_leaves_a_reason_not_a_silence(self):
        self.enable()
        self.set_command("exit 4")
        self.run_to_completion()
        self.assertTrue(wait_for(lambda: self.record().get("chain_next")))
        self.assertIn("ended failed", self.record()["chain_next"])


# --------------------------------------------------------------------------
# Memory bridge — every path must fail open.
# --------------------------------------------------------------------------
import arch_memory  # noqa: E402


class MemoryFixture(RunnerFixture):
    """Swaps the memory CLI for a script that records its arguments."""

    SEARCH_OUTPUT = (
        "  [2026-08-01 10:00] (demo/arch-runner) 0.812  canvas force layout ran out of memory at 600 nodes\n"
        "  [2026-08-02 11:30] (demo/claude-code) 0.640  the graph is read-only: the dashboard never builds it\n"
    )

    def setUp(self):
        super().setUp()
        self._mem_env = {k: os.environ.get(k)
                         for k in ("MEM0_ARCH_MEM_CMD", "MEM0_ARCH_MEMORY")}
        self.calls = Path(self._tmp.name) / "mem-calls.txt"
        self.fake = Path(self._tmp.name) / "fakemem.sh"
        self.write_fake(exit_code=0)
        os.environ["MEM0_ARCH_MEM_CMD"] = f"sh {self.fake}"
        os.environ.pop("MEM0_ARCH_MEMORY", None)   # these tests want the bridge on

    def tearDown(self):
        for key, value in self._mem_env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        super().tearDown()

    def write_fake(self, exit_code=0, search_output=None, sleep=0):
        body = f"""#!/bin/sh
printf '%s\\n' "$*" >> {self.calls}
[ "{sleep}" != "0" ] && sleep {sleep}
if [ "$1" = "search" ]; then
cat <<'OUT'
{search_output if search_output is not None else self.SEARCH_OUTPUT}
OUT
fi
exit {exit_code}
"""
        self.fake.write_text(body, encoding="utf-8")
        self.fake.chmod(0o755)

    def recorded(self):
        if not self.calls.exists():
            return []
        return [l for l in self.calls.read_text(encoding="utf-8").splitlines() if l.strip()]

    def wait_for_call(self, needle, timeout=8):
        return wait_for(lambda: any(needle in c for c in self.recorded()), timeout=timeout)


class TestMemoryRead(MemoryFixture):

    def test_learnings_reach_the_prompt(self):
        text = runner.build_phase_prompt(
            arch.read_architecture(self.arch_id),
            learnings=arch_memory.recall("demo", "build graph"))
        self.assertIn("Past learnings for this project", text)
        self.assertIn("canvas force layout ran out of memory", text)

    def test_search_is_scoped_to_the_project(self):
        arch_memory.recall("demo-scope", "build graph")
        self.assertTrue(any("--scope demo-scope" in c for c in self.recorded()))
        self.assertTrue(any(c.startswith("search") for c in self.recorded()))

    def test_no_learnings_means_no_section(self):
        text = runner.build_phase_prompt(arch.read_architecture(self.arch_id), learnings=[])
        self.assertNotIn("Past learnings", text)

    def test_no_matches_reads_as_nothing_not_as_a_hit(self):
        self.write_fake(search_output="  (no matches)")
        self.assertEqual(arch_memory.recall("demo", "anything"), [])

    def test_unparseable_output_is_ignored(self):
        self.write_fake(search_output="total gibberish\nno brackets here")
        self.assertEqual(arch_memory.recall("demo", "anything"), [])

    def test_learnings_are_framed_as_evidence_not_orders(self):
        text = runner.build_phase_prompt(
            arch.read_architecture(self.arch_id),
            learnings=arch_memory.recall("demo", "x"))
        self.assertIn("the code wins", " ".join(text.split()))

    def test_a_run_records_how_many_learnings_it_got(self):
        record = self.run_to_completion()
        self.assertEqual(record["memory_count"], 2)


class TestMemoryWrite(MemoryFixture):

    def test_a_finished_run_is_remembered(self):
        self.run_to_completion()
        self.assertTrue(self.wait_for_call("add"))
        add = [c for c in self.recorded() if c.startswith("add")][0]
        self.assertIn("--agent arch-runner", add)
        self.assertIn("status=done", add)
        self.assertIn("phase=discover", add)

    def test_the_run_record_says_the_write_happened(self):
        self.run_to_completion()
        self.assertTrue(wait_for(
            lambda: runner.latest(self.arch_id).get("memory_write") == "written"))

    def test_completed_task_titles_are_included(self):
        # Two tasks: finishing one leaves the phase open, so the run still
        # belongs to `discover`. Finishing the only task would auto-advance and
        # the run would summarise the next phase instead.
        done = arch.add_task(self.arch_id, "discover", "Reproduce the crash")["tasks"][0]["id"]
        arch.add_task(self.arch_id, "discover", "Still open")
        arch.update_task(self.arch_id, done, status="done")
        self.run_to_completion()
        self.assertTrue(self.wait_for_call("Reproduce the crash"))

    def test_a_failed_run_carries_its_last_lines(self):
        self.set_command("echo something broke here; exit 2")
        self.run_to_completion()
        self.assertTrue(self.wait_for_call("failed with"))
        add = [c for c in self.recorded() if "failed with" in c][0]
        self.assertIn("something broke here", add)

    def test_the_summary_never_carries_environment_values(self):
        os.environ["MEM0_SECRET_CANARY"] = "super-secret-value"
        try:
            self.run_to_completion()
            self.assertTrue(self.wait_for_call("add"))
            self.assertFalse(any("super-secret-value" in c for c in self.recorded()))
        finally:
            os.environ.pop("MEM0_SECRET_CANARY", None)

    def test_the_log_body_is_not_written_to_memory(self):
        """Only the tail of a *failed* run travels; a successful run's output
        may be anything at all."""
        self.set_command("echo THIS-SHOULD-NOT-BE-REMEMBERED; exit 0")
        self.run_to_completion()
        self.assertTrue(self.wait_for_call("add"))
        self.assertFalse(any("THIS-SHOULD-NOT-BE-REMEMBERED" in c for c in self.recorded()))


class TestMemoryFailsOpen(MemoryFixture):

    def test_a_broken_memory_cli_does_not_break_the_run(self):
        self.write_fake(exit_code=1)
        record = self.run_to_completion()
        self.assertEqual(record["status"], "done")
        self.assertTrue(wait_for(
            lambda: runner.latest(self.arch_id).get("memory_write") == "skipped"))

    def test_a_missing_memory_cli_does_not_break_the_run(self):
        os.environ["MEM0_ARCH_MEM_CMD"] = "/nonexistent/mem-binary"
        record = self.run_to_completion()
        self.assertEqual(record["status"], "done")
        self.assertEqual(arch_memory.recall("demo", "x"), [])
        self.assertEqual(arch_memory.remember("demo", "x"), "skipped")

    def test_a_hanging_memory_cli_is_given_up_on(self):
        original = arch_memory.TIMEOUT
        arch_memory.TIMEOUT = 1
        self.write_fake(sleep=5)
        try:
            self.assertEqual(arch_memory.recall("demo", "x"), [])
        finally:
            arch_memory.TIMEOUT = original

    def test_recall_without_a_scope_asks_nothing(self):
        self.assertEqual(arch_memory.recall("", "x"), [])
        self.assertEqual(self.recorded(), [])

    def test_remember_without_text_asks_nothing(self):
        self.assertEqual(arch_memory.remember("demo", "   "), "skipped")
        self.assertEqual(self.recorded(), [])


class TestMemoryKillSwitch(MemoryFixture):

    def test_disabled_means_no_subprocess_at_all(self):
        os.environ["MEM0_ARCH_MEMORY"] = "0"
        self.assertFalse(arch_memory.enabled())
        self.assertEqual(arch_memory.recall("demo", "x"), [])
        self.assertEqual(arch_memory.remember("demo", "something"), "off")
        arch_memory.remember_async("demo", "something")
        record = self.run_to_completion()
        self.assertEqual(record["status"], "done")
        self.assertEqual(record["memory_count"], 0)
        self.assertEqual(self.recorded(), [])

    def test_disabled_run_reports_memory_off(self):
        os.environ["MEM0_ARCH_MEMORY"] = "0"
        self.run_to_completion()
        self.assertTrue(wait_for(
            lambda: runner.latest(self.arch_id).get("memory_write") == "off"))

    def test_enabled_by_default(self):
        os.environ.pop("MEM0_ARCH_MEMORY", None)
        self.assertTrue(arch_memory.enabled())


# --------------------------------------------------------------------------
# Audit mode — a human-gate phase gets a brief, never a no-op and never a
# machine deciding it is approved.
# --------------------------------------------------------------------------
class AuditFixture(RunnerFixture):
    """A second architecture, parked on `approve`, sharing the temp store."""

    def setUp(self):
        super().setUp()
        self.gate_id = arch.create_architecture(
            "Gate Demo", str(self.repo), "", resolve_repo=lambda v: Path(v).resolve(),
            template_id="code-review")["id"]
        arch.bulk_update_workflow(self.gate_id, {"approve": {"status": "active"}})

    def arch_ids(self):
        return [self.arch_id, self.gate_id]

    def open_task(self, title="verify the diff"):
        return arch.add_task(self.gate_id, "approve", title)["tasks"][-1]["id"]

    def gate_prompt(self):
        return runner.build_phase_prompt(arch.read_architecture(self.gate_id))


class TestAuditMode(AuditFixture):

    def test_a_manual_phase_with_tasks_is_an_audit_run(self):
        self.open_task()
        self.assertEqual(runner.run_mode(arch.read_architecture(self.gate_id)), "audit")

    def test_a_manual_phase_with_nothing_written_still_plans(self):
        """Deciding what needs approving is not approving it — the planning run
        is what produced these tasks in the first place."""
        self.assertEqual(runner.run_mode(arch.read_architecture(self.gate_id)), "plan")

    def test_an_ordinary_phase_with_tasks_is_still_a_doing_run(self):
        arch.add_task(self.arch_id, "discover", "something")
        self.assertEqual(runner.run_mode(arch.read_architecture(self.arch_id)), "do")

    def test_the_run_record_says_audit(self):
        self.open_task()
        self.assertEqual(self.run_to_completion(arch_id=self.gate_id)["mode"], "audit")

    def test_the_prompt_names_the_phase_a_human_gate(self):
        self.open_task()
        text = " ".join(self.gate_prompt().split())
        self.assertIn("human-gate phase", text)
        self.assertIn("APPROVAL BRIEF", text)

    def test_the_prompt_forbids_executing_the_tasks(self):
        self.open_task()
        text = " ".join(self.gate_prompt().split())
        self.assertIn("Do NOT execute", text)

    def test_the_prompt_forbids_marking_anything_done(self):
        """The one thing an audit run must never do. If this string ever reaches
        the gate prompt, a machine can approve its own work."""
        self.open_task()
        prompt = self.gate_prompt()
        self.assertIn("never mark a task `done`", prompt)
        self.assertNotIn('"status": "done"', prompt)

    def test_the_prompt_asks_for_a_verdict_per_task(self):
        self.open_task()
        text = " ".join(self.gate_prompt().split())
        self.assertIn("pass/fail", text)
        self.assertIn("one-line reason", text)

    def test_the_prompt_wants_the_verdict_written_to_the_notes(self):
        self.open_task()
        prompt = self.gate_prompt()
        self.assertIn('"notes"', prompt)
        self.assertIn("PATCH", prompt)

    def test_the_prompt_lists_the_open_tasks_it_must_judge(self):
        tid = self.open_task("check the migration is reversible")
        prompt = self.gate_prompt()
        self.assertIn(tid, prompt)
        self.assertIn("check the migration is reversible", prompt)

    def test_a_task_already_done_is_not_asked_about_again(self):
        tid = self.open_task("already settled")
        arch.update_task(self.gate_id, tid, status="done")
        other = self.open_task("still open")
        prompt = self.gate_prompt()
        self.assertIn(other, prompt)
        self.assertNotIn(tid, prompt)

    def test_the_prompt_ends_by_handing_the_decision_back(self):
        self.open_task()
        text = " ".join(self.gate_prompt().split())
        self.assertIn("The human decides", text)

    def test_an_audit_run_leaves_every_task_untouched(self):
        """The fake agent does nothing, which is exactly what a no-op run did
        before. The difference now is that the run says it was an audit."""
        tid = self.open_task()
        record = self.run_to_completion(arch_id=self.gate_id)
        self.assertEqual(record["mode"], "audit")
        task = next(t for t in arch.read_architecture(self.gate_id)["tasks"]
                    if t["id"] == tid)
        self.assertEqual(task["status"], "todo")

    def test_an_audit_run_does_not_advance_the_phase(self):
        self.open_task()
        self.run_to_completion(arch_id=self.gate_id)
        self.assertEqual(arch.read_architecture(self.gate_id)["current_phase"], "approve")

    def test_the_chain_still_stops_at_the_gate_after_an_audit(self):
        self.open_task()
        arch.set_auto_chain(self.gate_id, True)
        record = self.run_to_completion(arch_id=self.gate_id)
        self.assertIn("manual phase", record["chain_next"] or "")


class TestRunHistoryIsDeletedWithTheArchitecture(RunnerFixture):
    """Deleting an architecture left its runs behind, while the confirmation
    said "the run history goes with it". Orphaned directories accumulated with
    no page that could ever show them again."""

    def runs_dir(self):
        return runner.runs_root() / self.arch_id

    def test_the_run_directory_goes_too(self):
        self.run_to_completion()
        self.assertTrue(self.runs_dir().is_dir())
        runner.forget_runs(self.arch_id)
        self.assertFalse(self.runs_dir().exists())

    def test_forgetting_an_architecture_that_never_ran_is_not_an_error(self):
        runner.forget_runs(self.arch_id)          # no runs yet
        runner.forget_runs(self.arch_id)          # and again

    def test_it_refuses_an_id_that_is_not_one(self):
        with self.assertRaises(ValueError):
            runner.forget_runs("../escape")

    def test_the_delete_route_forgets_the_runs(self):
        import server
        self.run_to_completion()
        self.assertTrue(self.runs_dir().is_dir())
        server.delete_architecture_with_runs(self.arch_id)
        self.assertFalse(self.runs_dir().exists())

    def test_the_architecture_itself_is_still_deleted(self):
        import server
        server.delete_architecture_with_runs(self.arch_id)
        with self.assertRaises(FileNotFoundError):
            arch.read_architecture(self.arch_id)

    def test_a_run_store_that_will_not_delete_does_not_block_the_delete(self):
        """The architecture is the record that matters; a leftover directory is
        untidy, not a reason to refuse."""
        import server
        self.run_to_completion()
        original = runner.forget_runs
        runner.forget_runs = lambda _id: (_ for _ in ()).throw(OSError("busy"))
        try:
            server.delete_architecture_with_runs(self.arch_id)
        finally:
            runner.forget_runs = original
        with self.assertRaises(FileNotFoundError):
            arch.read_architecture(self.arch_id)


class TestGatePageMarkup(unittest.TestCase):
    """The page has to offer the brief and then say it is ready. A run mode the
    UI never surfaces is a feature only the run records know about."""

    @classmethod
    def setUpClass(cls):
        from arch_page import ARCH_PAGE_HTML
        cls.html = ARCH_PAGE_HTML

    def hero(self):
        start = self.html.index("function heroFor(")
        return self.html[start:self.html.index("function spine(")]

    def test_a_manual_phase_offers_to_prepare_the_approval(self):
        self.assertIn("▶ Prepare approval", self.hero())

    def test_it_only_offers_it_when_there_is_something_to_judge(self):
        self.assertIn("if (open.length) actions.push({id:'run'", self.hero())

    def test_a_finished_audit_says_the_brief_is_ready(self):
        block = self.hero()
        self.assertIn("run.mode === 'audit' && run.status === 'done'", block)
        self.assertIn("Approval brief ready — human decision required", block)

    def test_the_banner_says_the_decision_is_still_the_human_s(self):
        self.assertIn("It marked nothing and changed nothing", self.hero())

    def test_the_gate_still_offers_approve_and_send_back(self):
        block = self.hero()
        self.assertIn("id:'advance'", block)
        self.assertIn("id:'back'", block)

    def test_the_toolbar_button_relabels_in_a_manual_phase(self):
        self.assertIn("(d.manual_phases || []).includes(d.current_phase)", self.html)


class TestDoModeUnchanged(AuditFixture):
    """Regression: audit mode must not have leaked into ordinary phases."""

    def do_prompt(self):
        arch.add_task(self.arch_id, "discover", "write the failing test")
        return runner.build_phase_prompt(arch.read_architecture(self.arch_id))

    def test_the_doing_prompt_still_asks_for_the_work(self):
        text = " ".join(self.do_prompt().split())
        self.assertIn("Reporting contract", text)
        self.assertIn("Small diffs", text)

    def test_the_doing_prompt_still_marks_tasks_done(self):
        self.assertIn('"status": "done"', self.do_prompt())

    def test_the_doing_prompt_is_not_an_audit(self):
        prompt = self.do_prompt()
        self.assertNotIn("APPROVAL BRIEF", prompt)
        self.assertNotIn("human-gate phase", prompt)

    def test_the_planning_prompt_is_still_a_plan(self):
        text = " ".join(runner.build_phase_prompt(
            arch.read_architecture(self.arch_id)).split())
        self.assertIn("decide what that work is — not to do it", text)


if __name__ == "__main__":
    unittest.main()
