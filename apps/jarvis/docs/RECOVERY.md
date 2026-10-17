# ARDA Core offline backup and isolated restore

The recovery bundle contains an SQLite Online Backup API snapshot of Core and a
manifest of SHA-256 hashes for the selected evidence/artifact roots. It rejects
symlinks and special files. Restore always writes to a new directory; this tool
does not replace the active database or artifact directory.

## Create a backup

Run on the Pi as the service owner and choose a destination on a different disk
or a mounted backup volume. Stop every process that can write Core or included
artifacts first. At minimum, stop `jarvis.service`; also stop any manually
started Control Center, bridge worker, autonomous Director, and browser/research
worker that writes to the selected artifact root. Confirm they have exited before
passing `--writers-stopped`.

```sh
systemctl --user stop jarvis.service
BACKUP_ROOT="${ARDA_BACKUP_ROOT:-/mnt/arda-backups}"
BACKUP="$BACKUP_ROOT/$(date -u +%Y%m%dT%H%M%SZ)"
python -m arda.recovery create \
  --db "${ARDA_CORE_DB:-$HOME/.arda/core.sqlite3}" \
  --artifact-root "harness=${ARDA_HARNESS_ROOT:-$HOME/.arda/harness}" \
  --output "$BACKUP" \
  --writers-stopped
python -m arda.recovery verify "$BACKUP"
systemctl --user start jarvis.service
```

The backup command refuses an existing destination. Keep the bundle on storage
with enough free space and apply the host's normal encryption and access controls;
the manifest protects against accidental corruption, not a malicious party who
can edit both the manifest and backup contents. Do not include provider secrets,
HOME directories, or unrelated application data as artifact roots.

## Rehearse restore without changing the active installation

Use a fresh destination and verify the restored database before any service is
pointed at it:

```sh
python -m arda.recovery restore "$BACKUP" \
  --destination "$HOME/arda-recovery-rehearsal"
python -m arda status --db "$HOME/arda-recovery-rehearsal/core.sqlite3" --json
```

The result contains `core.sqlite3` and `artifacts/<label>/...`. Rehearsal does
not switch service configuration, overwrite live state, or claim a production
rollback. Preserve the current database and artifacts before any separately
approved cutover. A deployment rollback is complete only after operators have
verified the restored dataset and retained the pre-cutover copy for recovery.

## Incident stop and rollback controls

1. Stop `jarvis.service` and all independently started ARDA writers.
2. Preserve a copy of the current database, its SQLite sidecars if present, and
   the active artifact roots before changing configuration.
3. Verify the candidate recovery bundle and restore it into a new directory.
4. Inspect the restored Core state and artifact hashes. Record the bundle path,
   verification output, and operator/time in the incident record.
5. Only after a separate deployment authorization, repoint the service to the
   restored data. Keep the preserved pre-cutover copy until the service passes
   the operational smoke checks.

Do not delete the current database or artifacts during recovery. If verification
fails, leave the active paths untouched and investigate using another known-good
bundle.

## Evidence boundary

Automated tests prove local bundle creation, hash/integrity rejection, safe
path handling, and isolated restore. They do not prove Pi filesystem capacity,
off-device copy durability, service quiescence, or recovery time. Run the stop,
backup, restore, and rollback rehearsal on the target Pi before the 72-hour pilot.
