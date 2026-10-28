# Tracked jobs API

Operational work belongs in Arda Core's append-only `events` registry. These
routes project those events into reusable job and run records; they do not use
Mem0/Qdrant or create another database.

The Control Plane exposes these routes on loopback at `127.0.0.1:8080`:

- `POST /api/tracked-jobs/events` records a lifecycle event.
- `GET /api/tracked-jobs?view=active|history|scheduled|all` lists logical jobs.
- `GET /api/tracked-jobs/{job_id}` returns the job and its recent runs.
- `GET /api/tracked-jobs/{job_id}/runs/{run_id}` returns one run and its
  bounded logs.

Each run starts with a `queued` event. Keep the same `job_id` for the logical
job, mint a new `run_id` for each execution, and use a stable unique
`idempotency_key` per lifecycle event. Reposting an event with the same key
returns the original record. The accepted statuses are `queued`, `running`,
`paused`, `retrying`, `completed`, `failed`, and `cancelled`; terminal runs are
preserved when a later run starts.

```json
{
  "job_id": "pickable-problem-intelligence",
  "run_id": "processor-2026-10-15T12:00:00Z",
  "status": "running",
  "idempotency_key": "processor:2026-10-15T12:00:00Z:running",
  "name": "Problem Intelligence",
  "project": "Pickable",
  "kind": "continuous",
  "metrics": {"processed": 824, "pending": 376},
  "log": "Batch 824 completed"
}
```

Event payloads are size-bounded. A log entry is truncated to its last 4,000
characters; run detail returns at most 200 recent log entries. Running jobs
without a heartbeat for five minutes are marked failed as stale. Send a
`running` lifecycle update at least once per five minutes; the Pi observer
reports service and scraper heartbeats automatically. A scheduled run uses
`kind: "scheduled"` and may include `scheduled_at`.

The API accepts unauthenticated calls only over loopback, matching the existing
Control Plane policy. Remote clients must use an authorized local bridge rather
than opening the Pi API to the LAN.
