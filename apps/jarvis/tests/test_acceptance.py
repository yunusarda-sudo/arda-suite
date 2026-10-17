from __future__ import annotations

import os
import subprocess
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from arda.awareness import ProjectRegistry, TaskStore
from arda.core import JobQueue, StaleLeaseError, _iso, bootstrap_runtime_database
from arda.acceptance import AcceptanceConflict, AcceptanceCoordinator


def crash_child(script):
    env = {**os.environ, "PYTHONPATH": str(Path(__file__).parents[1])}
    return subprocess.run([sys.executable, "-c", script], cwd=env["PYTHONPATH"], env=env,
                          capture_output=True, text=True, check=False)


def make_repo(tmp_path, name="repo"):
    repo = tmp_path / name
    repo.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "test@example.invalid"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=repo, check=True)
    (repo / "source.txt").write_text("baseline\n", encoding="utf-8")
    subprocess.run(["git", "add", "source.txt"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-qm", "baseline"], cwd=repo, check=True)
    old = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=repo, text=True).strip()
    (repo / "source.txt").write_text("candidate\n", encoding="utf-8")
    subprocess.run(["git", "commit", "-qam", "candidate"], cwd=repo, check=True)
    candidate = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=repo, text=True).strip()
    subprocess.run(["git", "update-ref", "refs/heads/accepted", old], cwd=repo, check=True)
    return repo, old, candidate


def lease(db_path, *, task_id="TASK-1", objective="accept candidate"):
    db = bootstrap_runtime_database(db_path)
    projects = ProjectRegistry(db)
    if not db.execute("SELECT 1 FROM projects WHERE id='project'").fetchone():
        projects.create("project", "Project")
    task = TaskStore(db).create("project", objective, status="ready", task_id=task_id)
    queue = JobQueue(db, lease_seconds=60)
    queued = queue.enqueue("autonomous-coding", objective, idempotency_key=f"key-{task_id}",
                           project_id="project")
    claimed = queue.claim("worker")
    queue.start(claimed.id, claimed.lease_token)
    TaskStore(db).update_status(task_id, "in_progress", expected_version=task["version"])
    token = claimed.lease_token
    generation = claimed.attempt_count
    db.close()
    return claimed.id, token, generation, task["version"]


def coordinator(db_path, fault_hook=None):
    return AcceptanceCoordinator(bootstrap_runtime_database(db_path), fault_hook=fault_hook)


def prepare(coordinator, *, job_id, token, generation, repo, old, candidate,
            task_id="TASK-1", ref_name="refs/heads/accepted"):
    evidence = b'{"verification":"PASS"}'
    JobQueue(coordinator.db).record_artifact(
        job_id, "candidate-evidence", f"fixture://{job_id}/{generation}",
        __import__("hashlib").sha256(evidence).hexdigest(), content=evidence,
        lease_token=token,
    )
    return coordinator.prepare(
        job_id=job_id, lease_token=token, lease_generation=generation,
        task_id=task_id, repo_path=str(repo), ref_name=ref_name,
        expected_old_sha=old, candidate_sha=candidate,
        evidence_sha256=__import__("hashlib").sha256(evidence).hexdigest(),
        result_payload={"result": "PASS", "candidate_sha": candidate, "accepted_sha": None,
                        "evidence": {"tests": "PASS"}},
    )


def test_expired_lease_cannot_create_or_promote_acceptance(tmp_path):
    db_path = tmp_path / "core.sqlite3"
    repo, old, candidate = make_repo(tmp_path)
    job_id, token, generation, _ = lease(db_path)
    db = bootstrap_runtime_database(db_path)
    db.execute("UPDATE jobs SET lease_expires_at=? WHERE id=?",
               (_iso(datetime.now(timezone.utc) - timedelta(seconds=1)), job_id))
    db.close()

    with pytest.raises(StaleLeaseError):
        prepare(coordinator(db_path), job_id=job_id, token=token, generation=generation,
                repo=repo, old=old, candidate=candidate)

    assert subprocess.check_output(["git", "rev-parse", "refs/heads/accepted"], cwd=repo, text=True).strip() == old


def test_lease_expiry_after_durable_intent_still_blocks_git_cas(tmp_path):
    db_path = tmp_path / "core.sqlite3"
    repo, old, candidate = make_repo(tmp_path)
    job_id, token, generation, _ = lease(db_path)
    intent_id = prepare(coordinator(db_path), job_id=job_id, token=token, generation=generation,
                        repo=repo, old=old, candidate=candidate)
    db = bootstrap_runtime_database(db_path)
    db.execute("UPDATE jobs SET lease_expires_at=? WHERE id=?",
               (_iso(datetime.now(timezone.utc) - timedelta(seconds=1)), job_id))
    db.close()

    with pytest.raises(StaleLeaseError):
        coordinator(db_path).promote(intent_id, lease_token=token)

    assert subprocess.check_output(["git", "rev-parse", "refs/heads/accepted"], cwd=repo, text=True).strip() == old


def test_superseded_lease_generation_cannot_promote_or_mutate_new_task_state(tmp_path):
    db_path = tmp_path / "core.sqlite3"
    repo, old, candidate = make_repo(tmp_path)
    job_id, stale_token, generation, _ = lease(db_path)
    intent_id = prepare(coordinator(db_path), job_id=job_id, token=stale_token, generation=generation,
                        repo=repo, old=old, candidate=candidate)
    db = bootstrap_runtime_database(db_path)
    db.execute("UPDATE jobs SET lease_expires_at=? WHERE id=?",
               (_iso(datetime.now(timezone.utc) - timedelta(seconds=1)), job_id))
    queue = JobQueue(db, lease_seconds=60)
    current = queue.claim("worker-2")
    queue.start(current.id, current.lease_token)
    db.close()

    with pytest.raises(StaleLeaseError):
        coordinator(db_path).promote(intent_id, lease_token=stale_token)
    assert coordinator(db_path).reconcile() == 1

    db = bootstrap_runtime_database(db_path)
    try:
        row = db.execute("SELECT status, lease_token, attempt_count FROM jobs WHERE id=?", (job_id,)).fetchone()
        assert row["status"] == "running"
        assert row["lease_token"] == current.lease_token
        assert row["attempt_count"] == generation + 1
        assert db.execute("SELECT status FROM tasks WHERE id='TASK-1'").fetchone()[0] == "in_progress"
        assert db.execute("SELECT status FROM acceptance_intents WHERE intent_id=?", (intent_id,)).fetchone()[0] == "aborted"
    finally:
        db.close()
    assert subprocess.check_output(["git", "rev-parse", "refs/heads/accepted"], cwd=repo, text=True).strip() == old


def test_cancellation_fences_acceptance_before_intent_and_before_git_cas(tmp_path):
    # A request already present before intent creation cannot create an intent.
    db_path = tmp_path / "before-intent.sqlite3"
    repo, old, candidate = make_repo(tmp_path, "before-intent-repo")
    job_id, token, generation, _ = lease(db_path)
    db = bootstrap_runtime_database(db_path)
    JobQueue(db).request_cancel(job_id, requested_by="test-operator")
    db.close()
    with pytest.raises(AcceptanceConflict, match="cancellation"):
        prepare(coordinator(db_path), job_id=job_id, token=token, generation=generation,
                repo=repo, old=old, candidate=candidate)
    db = bootstrap_runtime_database(db_path)
    try:
        assert db.execute("SELECT COUNT(*) FROM acceptance_intents").fetchone()[0] == 0
    finally:
        db.close()

    # A request arriving after intent persistence still prevents the Git CAS.
    db_path = tmp_path / "before-cas.sqlite3"
    repo, old, candidate = make_repo(tmp_path, "before-cas-repo")
    job_id, token, generation, _ = lease(db_path)
    core = coordinator(db_path)
    intent_id = prepare(core, job_id=job_id, token=token, generation=generation,
                        repo=repo, old=old, candidate=candidate)
    JobQueue(core.db).request_cancel(job_id, requested_by="test-operator")
    with pytest.raises(AcceptanceConflict, match="cancellation"):
        core.promote(intent_id, lease_token=token)
    assert subprocess.check_output(["git", "rev-parse", "refs/heads/accepted"], cwd=repo, text=True).strip() == old


def test_executor_supplied_accepted_sha_is_rejected(tmp_path):
    db_path = tmp_path / "core.sqlite3"
    repo, old, candidate = make_repo(tmp_path)
    job_id, token, generation, _ = lease(db_path)
    core = coordinator(db_path)
    evidence = b'{"verification":"PASS"}'
    digest = __import__("hashlib").sha256(evidence).hexdigest()
    JobQueue(core.db).record_artifact(job_id, "candidate-evidence", f"fixture://{job_id}/{generation}",
                                      digest, content=evidence, lease_token=token)

    with pytest.raises(AcceptanceConflict, match="executor cannot assert"):
        core.prepare(job_id=job_id, lease_token=token, lease_generation=generation,
                     task_id="TASK-1", repo_path=str(repo), ref_name="refs/heads/accepted",
                     expected_old_sha=old, candidate_sha=candidate, evidence_sha256=digest,
                     result_payload={"result": "PASS", "candidate_sha": candidate,
                                     "accepted_sha": candidate})

    assert subprocess.check_output(["git", "rev-parse", "refs/heads/accepted"], cwd=repo, text=True).strip() == old
    assert core.db.execute("SELECT COUNT(*) FROM acceptance_intents").fetchone()[0] == 0


def test_candidate_commit_must_be_direct_child_of_expected_baseline(tmp_path):
    db_path = tmp_path / "core.sqlite3"
    repo, old, _candidate = make_repo(tmp_path)
    subprocess.run(["git", "switch", "--orphan", "unrelated"], cwd=repo, check=True,
                   capture_output=True, text=True)
    (repo / "unrelated.txt").write_text("not based on accepted baseline\n", encoding="utf-8")
    subprocess.run(["git", "add", "--all"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-qm", "unrelated candidate"], cwd=repo, check=True)
    unrelated = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=repo, text=True).strip()
    job_id, token, generation, _ = lease(db_path)
    core = coordinator(db_path)

    with pytest.raises(AcceptanceConflict, match="direct child of expected baseline"):
        prepare(core, job_id=job_id, token=token, generation=generation,
                repo=repo, old=old, candidate=unrelated)

    assert subprocess.check_output(["git", "rev-parse", "refs/heads/accepted"], cwd=repo, text=True).strip() == old
    assert core.db.execute("SELECT COUNT(*) FROM acceptance_intents").fetchone()[0] == 0


def test_tampered_immutable_evidence_blocks_promotion(tmp_path):
    db_path = tmp_path / "core.sqlite3"
    repo, old, candidate = make_repo(tmp_path)
    job_id, token, generation, _ = lease(db_path)
    core = coordinator(db_path)
    intent_id = prepare(core, job_id=job_id, token=token, generation=generation,
                        repo=repo, old=old, candidate=candidate)
    db = bootstrap_runtime_database(db_path)
    artifact = db.execute("SELECT id FROM artifacts WHERE job_id=?", (job_id,)).fetchone()[0]
    db.execute("DROP TRIGGER artifacts_no_update")
    db.execute("UPDATE artifacts SET content=? WHERE id=?", (b"tampered evidence", artifact))
    db.close()

    with pytest.raises(AcceptanceConflict, match="evidence"):
        coordinator(db_path).promote(intent_id, lease_token=token)

    assert subprocess.check_output(["git", "rev-parse", "refs/heads/accepted"], cwd=repo, text=True).strip() == old
    db = bootstrap_runtime_database(db_path)
    try:
        assert db.execute("SELECT status FROM acceptance_intents WHERE intent_id=?", (intent_id,)).fetchone()[0] == "conflict"
        assert db.execute("SELECT status FROM jobs WHERE id=?", (job_id,)).fetchone()[0] == "failed"
    finally:
        db.close()


def test_promotion_does_not_run_repository_reference_transaction_hook(tmp_path):
    db_path = tmp_path / "core.sqlite3"
    repo, old, candidate = make_repo(tmp_path)
    job_id, token, generation, _ = lease(db_path)
    intent_id = prepare(coordinator(db_path), job_id=job_id, token=token, generation=generation,
                        repo=repo, old=old, candidate=candidate)
    marker = tmp_path / "hook-ran"
    hook = repo / ".git" / "hooks" / "reference-transaction"
    hook.write_text(f"#!/bin/sh\nprintf called > {marker}\n", encoding="utf-8")
    hook.chmod(0o755)

    coordinator(db_path).promote(intent_id, lease_token=token)

    assert not marker.exists()
    assert subprocess.check_output(["git", "rev-parse", "refs/heads/accepted"], cwd=repo, text=True).strip() == candidate


def test_short_branch_name_is_normalized_to_full_heads_ref(tmp_path):
    db_path = tmp_path / "core.sqlite3"
    repo, old, candidate = make_repo(tmp_path)
    job_id, token, generation, _ = lease(db_path)
    core = coordinator(db_path)
    intent_id = prepare(core, job_id=job_id, token=token, generation=generation,
                        repo=repo, old=old, candidate=candidate, ref_name="accepted")
    intent = core.db.execute("SELECT ref_name FROM acceptance_intents WHERE intent_id=?", (intent_id,)).fetchone()
    assert intent[0] == "refs/heads/accepted"
    assert core.promote(intent_id, lease_token=token) == candidate


def test_old_conflicted_intent_cannot_overwrite_newer_accepted_job_state(tmp_path):
    db_path = tmp_path / "core.sqlite3"
    repo, old, candidate_one = make_repo(tmp_path)
    subprocess.run(["git", "checkout", "--detach", old], cwd=repo, check=True,
                   capture_output=True, text=True)
    (repo / "third.txt").write_text("candidate two\n", encoding="utf-8")
    subprocess.run(["git", "add", "third.txt"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-qm", "candidate two"], cwd=repo, check=True)
    candidate_two = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=repo, text=True).strip()
    job_id, token_one, gen_one, _ = lease(db_path)
    core = coordinator(db_path)
    stale_intent = prepare(core, job_id=job_id, token=token_one, generation=gen_one,
                           repo=repo, old=old, candidate=candidate_one)
    db = bootstrap_runtime_database(db_path)
    db.execute("UPDATE jobs SET lease_expires_at=? WHERE id=?",
               (_iso(datetime.now(timezone.utc) - timedelta(seconds=1)), job_id))
    queue = JobQueue(db, lease_seconds=60)
    current = queue.claim("worker-2")
    queue.start(current.id, current.lease_token)
    db.close()
    new_intent = prepare(coordinator(db_path), job_id=job_id, token=current.lease_token,
                         generation=current.attempt_count, repo=repo, old=old,
                         candidate=candidate_two)
    coordinator(db_path).promote(new_intent, lease_token=current.lease_token)

    assert coordinator(db_path).reconcile() == 1

    db = bootstrap_runtime_database(db_path)
    try:
        assert db.execute("SELECT status FROM acceptance_intents WHERE intent_id=?", (stale_intent,)).fetchone()[0] == "conflict"
        assert db.execute("SELECT status FROM acceptance_intents WHERE intent_id=?", (new_intent,)).fetchone()[0] == "accepted"
        assert db.execute("SELECT status FROM jobs WHERE id=?", (job_id,)).fetchone()[0] == "succeeded"
        assert db.execute("SELECT status FROM tasks WHERE id='TASK-1'").fetchone()[0] == "done"
    finally:
        db.close()
    assert subprocess.check_output(["git", "rev-parse", "refs/heads/accepted"], cwd=repo, text=True).strip() == candidate_two


def test_crash_before_intent_commit_leaves_no_prepared_record(tmp_path):
    db_path = tmp_path / "core.sqlite3"
    repo, old, candidate = make_repo(tmp_path)
    job_id, token, generation, _ = lease(db_path)
    core = coordinator(db_path)
    evidence = b'{"verification":"PASS"}'
    JobQueue(core.db).record_artifact(job_id, "candidate-evidence", f"fixture://{job_id}/{generation}",
                                      __import__("hashlib").sha256(evidence).hexdigest(),
                                      content=evidence, lease_token=token)
    with pytest.raises(RuntimeError, match="before acceptance intent"):
        core.prepare(job_id=job_id, lease_token=token, lease_generation=generation,
                     task_id="TASK-1", repo_path=str(repo), ref_name="refs/heads/accepted",
                     expected_old_sha=old, candidate_sha=candidate,
                     evidence_sha256=__import__("hashlib").sha256(evidence).hexdigest(),
                     result_payload={"result": "PASS", "candidate_sha": candidate, "evidence": {}},
                     crash_at="before_intent_commit")
    assert core.db.execute("SELECT COUNT(*) FROM acceptance_intents").fetchone()[0] == 0


def test_process_crash_before_intent_commit_rolls_back_prepared_row(tmp_path):
    db_path = tmp_path / "core.sqlite3"
    repo, old, candidate = make_repo(tmp_path)
    job_id, token, generation, _ = lease(db_path)
    core = coordinator(db_path)
    evidence = b'{"verification":"PASS"}'
    digest = __import__("hashlib").sha256(evidence).hexdigest()
    JobQueue(core.db).record_artifact(job_id, "candidate-evidence", f"fixture://{job_id}/{generation}",
                                      digest, content=evidence, lease_token=token)
    script = f'''
import os
from arda.core import bootstrap_runtime_database
from arda.acceptance import AcceptanceCoordinator
db = bootstrap_runtime_database({str(db_path)!r})
def crash(stage):
    if stage == "before_intent_commit": os._exit(71)
AcceptanceCoordinator(db, fault_hook=crash).prepare(
    job_id={job_id!r}, lease_token={token!r}, lease_generation={generation},
    task_id="TASK-1", repo_path={str(repo)!r}, ref_name="refs/heads/accepted",
    expected_old_sha={old!r}, candidate_sha={candidate!r}, evidence_sha256={digest!r},
    result_payload={{"result":"PASS", "candidate_sha":{candidate!r}}})
'''
    child = crash_child(script)

    assert child.returncode == 71
    assert core.db.execute("SELECT COUNT(*) FROM acceptance_intents").fetchone()[0] == 0
    assert subprocess.check_output(["git", "rev-parse", "refs/heads/accepted"], cwd=repo, text=True).strip() == old


def test_process_crash_between_intent_and_git_cas_requeues_for_reevaluation(tmp_path):
    db_path = tmp_path / "core.sqlite3"
    repo, old, candidate = make_repo(tmp_path)
    job_id, token, generation, _ = lease(db_path)
    intent_id = prepare(coordinator(db_path), job_id=job_id, token=token, generation=generation,
                        repo=repo, old=old, candidate=candidate)
    script = f'''
import os
from arda.core import bootstrap_runtime_database
from arda.acceptance import AcceptanceCoordinator
db = bootstrap_runtime_database({str(db_path)!r})
def crash(stage):
    if stage == "before_git_cas": os._exit(72)
AcceptanceCoordinator(db, fault_hook=crash).promote({intent_id!r}, lease_token={token!r})
'''
    child = crash_child(script)

    assert child.returncode == 72
    assert coordinator(db_path).reconcile() == 1
    db = bootstrap_runtime_database(db_path)
    try:
        assert db.execute("SELECT status FROM acceptance_intents WHERE intent_id=?", (intent_id,)).fetchone()[0] == "aborted"
        assert db.execute("SELECT status FROM jobs WHERE id=?", (job_id,)).fetchone()[0] == "retry_wait"
        assert db.execute("SELECT status FROM tasks WHERE id='TASK-1'").fetchone()[0] == "ready"
    finally:
        db.close()
    assert subprocess.check_output(["git", "rev-parse", "refs/heads/accepted"], cwd=repo, text=True).strip() == old


def test_process_crash_after_git_subprocess_before_sqlite_commit_recovers(tmp_path):
    db_path = tmp_path / "core.sqlite3"
    repo, old, candidate = make_repo(tmp_path)
    job_id, token, generation, _ = lease(db_path)
    intent_id = prepare(coordinator(db_path), job_id=job_id, token=token, generation=generation,
                        repo=repo, old=old, candidate=candidate)
    script = f'''
import os
from arda.core import bootstrap_runtime_database
from arda.acceptance import AcceptanceCoordinator
db = bootstrap_runtime_database({str(db_path)!r})
def crash(stage):
    if stage == "after_git_cas": os._exit(73)
AcceptanceCoordinator(db, fault_hook=crash).promote({intent_id!r}, lease_token={token!r})
'''
    child = crash_child(script)

    assert child.returncode == 73
    assert subprocess.check_output(["git", "rev-parse", "refs/heads/accepted"], cwd=repo, text=True).strip() == candidate
    db = bootstrap_runtime_database(db_path)
    try:
        assert db.execute("SELECT status FROM acceptance_intents WHERE intent_id=?", (intent_id,)).fetchone()[0] == "prepared"
    finally:
        db.close()
    recovery = coordinator(db_path)
    assert recovery.reconcile() == 1
    assert recovery.reconcile() == 0
    db = bootstrap_runtime_database(db_path)
    try:
        assert db.execute("SELECT status FROM acceptance_intents WHERE intent_id=?", (intent_id,)).fetchone()[0] == "accepted"
        assert db.execute("SELECT status FROM jobs WHERE id=?", (job_id,)).fetchone()[0] == "succeeded"
        assert db.execute("SELECT status FROM tasks WHERE id='TASK-1'").fetchone()[0] == "done"
    finally:
        db.close()


def test_intent_records_generation_and_reconciles_crash_after_git_cas(tmp_path):
    db_path = tmp_path / "core.sqlite3"
    repo, old, candidate = make_repo(tmp_path)
    job_id, token, generation, _ = lease(db_path)
    intent_id = prepare(coordinator(db_path), job_id=job_id, token=token, generation=generation,
                        repo=repo, old=old, candidate=candidate)

    def crash(stage):
        if stage == "after_git_cas":
            raise RuntimeError("injected process crash")

    with pytest.raises(RuntimeError, match="injected"):
        coordinator(db_path, crash).promote(intent_id, lease_token=token)

    db = bootstrap_runtime_database(db_path)
    try:
        row = db.execute("SELECT * FROM acceptance_intents WHERE intent_id=?", (intent_id,)).fetchone()
        assert row["status"] == "prepared"
        assert row["lease_generation"] == generation
        assert row["expected_old_sha"] == old
        assert row["candidate_sha"] == candidate
        assert len(row["evidence_sha256"]) == 64
    finally:
        db.close()

    recovery = coordinator(db_path)
    assert recovery.reconcile() == 1
    assert recovery.reconcile() == 0
    assert subprocess.check_output(["git", "rev-parse", "refs/heads/accepted"], cwd=repo, text=True).strip() == candidate
    db = bootstrap_runtime_database(db_path)
    try:
        assert db.execute("SELECT status FROM acceptance_intents WHERE intent_id=?", (intent_id,)).fetchone()[0] == "accepted"
        assert db.execute("SELECT status FROM jobs WHERE id=?", (job_id,)).fetchone()[0] == "succeeded"
        assert db.execute("SELECT status FROM tasks WHERE id='TASK-1'").fetchone()[0] == "done"
    finally:
        db.close()


def test_crash_before_git_cas_aborts_intent_and_never_claims_success(tmp_path):
    db_path = tmp_path / "core.sqlite3"
    repo, old, candidate = make_repo(tmp_path)
    job_id, token, generation, _ = lease(db_path)
    intent_id = prepare(coordinator(db_path), job_id=job_id, token=token, generation=generation,
                        repo=repo, old=old, candidate=candidate)

    def crash(stage):
        if stage == "before_git_cas":
            raise RuntimeError("injected process crash")

    with pytest.raises(RuntimeError, match="injected"):
        coordinator(db_path, crash).promote(intent_id, lease_token=token)
    recovery = coordinator(db_path)
    assert recovery.reconcile() == 1
    db = bootstrap_runtime_database(db_path)
    try:
        assert db.execute("SELECT status FROM acceptance_intents WHERE intent_id=?", (intent_id,)).fetchone()[0] == "aborted"
        assert db.execute("SELECT status FROM jobs WHERE id=?", (job_id,)).fetchone()[0] != "succeeded"
        assert db.execute("SELECT status FROM tasks WHERE id='TASK-1'").fetchone()[0] != "done"
    finally:
        db.close()
    assert subprocess.check_output(["git", "rev-parse", "refs/heads/accepted"], cwd=repo, text=True).strip() == old


def test_two_candidates_from_same_baseline_never_force_overwrite(tmp_path):
    db_path = tmp_path / "core.sqlite3"
    repo, old, candidate = make_repo(tmp_path)
    subprocess.run(["git", "checkout", "--detach", old], cwd=repo, check=True,
                   capture_output=True, text=True)
    (repo / "other.txt").write_text("second candidate\n", encoding="utf-8")
    subprocess.run(["git", "add", "other.txt"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-qm", "other candidate"], cwd=repo, check=True)
    candidate_two = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=repo, text=True).strip()
    job1, token1, gen1, _ = lease(db_path, task_id="TASK-1")
    job2, token2, gen2, _ = lease(db_path, task_id="TASK-2")
    core = coordinator(db_path)
    first = prepare(core, job_id=job1, token=token1, generation=gen1, repo=repo, old=old, candidate=candidate)
    second = prepare(core, job_id=job2, token=token2, generation=gen2, repo=repo, old=old, candidate=candidate_two,
                     task_id="TASK-2")
    core.promote(first, lease_token=token1)

    with pytest.raises(AcceptanceConflict):
        core.promote(second, lease_token=token2)

    assert subprocess.check_output(["git", "rev-parse", "refs/heads/accepted"], cwd=repo, text=True).strip() == candidate
    db = bootstrap_runtime_database(db_path)
    try:
        assert db.execute("SELECT status FROM acceptance_intents WHERE intent_id=?", (second,)).fetchone()[0] == "conflict"
        assert db.execute("SELECT status FROM tasks WHERE id='TASK-2'").fetchone()[0] == "blocked"
    finally:
        db.close()


def test_changed_baseline_is_recorded_as_conflict_before_any_promotion(tmp_path):
    db_path = tmp_path / "core.sqlite3"
    repo, old, candidate_one = make_repo(tmp_path)
    (repo / "second.txt").write_text("second candidate\n", encoding="utf-8")
    subprocess.run(["git", "add", "second.txt"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-qm", "second candidate"], cwd=repo, check=True)
    candidate_two = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=repo, text=True).strip()
    job1, token1, gen1, _ = lease(db_path, task_id="TASK-1")
    job2, token2, gen2, _ = lease(db_path, task_id="TASK-2")
    core = coordinator(db_path)
    intent1 = prepare(core, job_id=job1, token=token1, generation=gen1,
                      repo=repo, old=old, candidate=candidate_one)
    core.promote(intent1, lease_token=token1)

    intent2 = prepare(core, job_id=job2, token=token2, generation=gen2,
                      repo=repo, old=old, candidate=candidate_two, task_id="TASK-2")

    db = bootstrap_runtime_database(db_path)
    try:
        assert db.execute("SELECT status FROM acceptance_intents WHERE intent_id=?", (intent2,)).fetchone()[0] == "conflict"
        assert db.execute("SELECT status FROM jobs WHERE id=?", (job2,)).fetchone()[0] == "failed"
        assert db.execute("SELECT status FROM tasks WHERE id='TASK-2'").fetchone()[0] == "blocked"
    finally:
        db.close()
    assert subprocess.check_output(["git", "rev-parse", "refs/heads/accepted"], cwd=repo, text=True).strip() == candidate_one


def test_concurrent_same_baseline_promotions_have_one_winner(tmp_path):
    db_path = tmp_path / "core.sqlite3"
    repo, old, candidate = make_repo(tmp_path)
    subprocess.run(["git", "checkout", "--detach", old], cwd=repo, check=True,
                   capture_output=True, text=True)
    (repo / "other.txt").write_text("second candidate\n", encoding="utf-8")
    subprocess.run(["git", "add", "other.txt"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-qm", "other candidate"], cwd=repo, check=True)
    candidate_two = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=repo, text=True).strip()
    job1, token1, gen1, _ = lease(db_path, task_id="TASK-1")
    job2, token2, gen2, _ = lease(db_path, task_id="TASK-2")
    core = coordinator(db_path)
    intents = [
        prepare(core, job_id=job1, token=token1, generation=gen1, repo=repo, old=old, candidate=candidate),
        prepare(core, job_id=job2, token=token2, generation=gen2, repo=repo, old=old, candidate=candidate_two, task_id="TASK-2"),
    ]
    barrier = threading.Barrier(2)

    def promote(args):
        intent_id, token = args
        local = coordinator(db_path)
        barrier.wait()
        try:
            local.promote(intent_id, lease_token=token)
            return "accepted"
        except AcceptanceConflict:
            return "conflict"
        finally:
            local.db.close()

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(promote, [(intents[0], token1), (intents[1], token2)]))
    assert sorted(results) == ["accepted", "conflict"]


def test_intent_is_durable_before_git_and_repeated_reconcile_is_idempotent(tmp_path):
    db_path = tmp_path / "core.sqlite3"
    repo, old, candidate = make_repo(tmp_path)
    job_id, token, generation, _ = lease(db_path)
    intent_id = prepare(coordinator(db_path), job_id=job_id, token=token, generation=generation,
                        repo=repo, old=old, candidate=candidate)
    assert subprocess.check_output(["git", "rev-parse", "refs/heads/accepted"], cwd=repo, text=True).strip() == old
    core = coordinator(db_path)
    with pytest.raises(RuntimeError, match="simulated"):
        core.promote(intent_id, lease_token=token, crash_at="before_git_cas")
    assert core.reconcile() == 1
    assert core.reconcile() == 0


def test_unexpected_ref_is_conflict_and_never_force_reset(tmp_path):
    db_path = tmp_path / "core.sqlite3"
    repo, old, candidate = make_repo(tmp_path)
    job_id, token, generation, _ = lease(db_path)
    intent_id = prepare(coordinator(db_path), job_id=job_id, token=token, generation=generation,
                        repo=repo, old=old, candidate=candidate)
    (repo / "unexpected.txt").write_text("external winner\n", encoding="utf-8")
    subprocess.run(["git", "add", "unexpected.txt"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-qm", "external winner"], cwd=repo, check=True)
    unexpected = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=repo, text=True).strip()
    subprocess.run(["git", "update-ref", "refs/heads/accepted", unexpected, old], cwd=repo, check=True)

    assert coordinator(db_path).reconcile() == 1

    assert subprocess.check_output(["git", "rev-parse", "refs/heads/accepted"], cwd=repo, text=True).strip() == unexpected
    db = bootstrap_runtime_database(db_path)
    try:
        assert db.execute("SELECT status FROM acceptance_intents WHERE intent_id=?", (intent_id,)).fetchone()[0] == "conflict"
    finally:
        db.close()
