#!/usr/bin/env bash
# ==============================================================================
# 🛡️ Jarvis / ARDA Core — Recovery Rehearsal Runner (WP-6)
# ==============================================================================
# Exercises backup creation, manifest verification, and isolated restore
# using arda.recovery without modifying active databases or live service state.
# ==============================================================================
set -euo pipefail

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
ROOT_DIR=$(CDPATH= cd -- "$SCRIPT_DIR/.." && pwd)
export PYTHONPATH="$ROOT_DIR${PYTHONPATH:+:$PYTHONPATH}"

PY="$ROOT_DIR/.venv/bin/python"
if [ ! -x "$PY" ]; then
  PY=$(command -v python3 || true)
fi
if [ -z "$PY" ] || ! "$PY" -c "import arda.recovery" 2>/dev/null; then
  echo "❌ arda.recovery Python modülü bulunamadı. Önce sanal ortamı kurun." >&2
  exit 1
fi

MODE="live"
if [ "${1:-}" = "--exercise-sandbox" ] || [ "$(uname -s)" != "Linux" ]; then
  MODE="sandbox"
fi

if [ "${1:-}" = "--help" ] || [ "${1:-}" = "-h" ]; then
  echo "Kullanım: $0 [--exercise-sandbox]"
  echo "  --exercise-sandbox: Canlı veritabanı yerine geçici izole sandbox verisiyle provayı çalıştırır."
  exit 0
fi

echo "==> ARDA Core Kurtarma Provası (Mod: $MODE)"

TMP_DIR=$(mktemp -d "/tmp/arda-recovery-rehearsal.XXXXXX")
trap 'rm -rf "$TMP_DIR"' EXIT

if [ "$MODE" = "sandbox" ]; then
  echo "  [1/4] İzole test veritabanı ve kanıt dosyaları hazırlanıyor..."
  SANDBOX_DB="$TMP_DIR/sandbox.sqlite3"
  SANDBOX_ARTIFACTS="$TMP_DIR/artifacts"
  mkdir -p "$SANDBOX_ARTIFACTS/evidence"
  echo '{"test_run": true, "timestamp": "rehearsal"}' > "$SANDBOX_ARTIFACTS/evidence/log.json"

  "$PY" - <<PYEOF
import sqlite3
from arda.core import bootstrap_runtime_database
db = bootstrap_runtime_database("$SANDBOX_DB")
db.execute("INSERT INTO projects(id, name, created_at, updated_at) VALUES ('p-rehearsal', 'Rehearsal Sandbox', 'now', 'now')")
db.close()
PYEOF

  SOURCE_DB="$SANDBOX_DB"
  ARTIFACT_ARGS=(--artifact-root "sandbox=$SANDBOX_ARTIFACTS")
else
  SOURCE_DB="${ARDA_CORE_DB:-$HOME/.arda/core.sqlite3}"
  if [ ! -f "$SOURCE_DB" ]; then
    echo "❌ Canlı veritabanı bulunamadı: $SOURCE_DB" >&2
    echo "   Test amaçlı çalıştırmak için: $0 --exercise-sandbox" >&2
    exit 1
  fi

  if command -v systemctl >/dev/null 2>&1 && systemctl --user is-active --quiet jarvis.service 2>/dev/null; then
    echo "⚠️  jarvis.service aktif durumda! Yedek alma öncesi yazıcılar durdurulmalıdır." >&2
    echo "   Durdurmak için: systemctl --user stop jarvis.service" >&2
    exit 1
  fi

  HARNESS_DIR="${ARDA_HARNESS_ROOT:-$HOME/.arda/harness}"
  ARTIFACT_ARGS=()
  if [ -d "$HARNESS_DIR" ]; then
    ARTIFACT_ARGS=(--artifact-root "harness=$HARNESS_DIR")
  fi
fi

BACKUP_DEST="$TMP_DIR/backup-bundle"
RESTORE_DEST="$TMP_DIR/restored"

echo "  [2/4] Yedek oluşturuluyor (--writers-stopped)..."
"$PY" -m arda.recovery create \
  --db "$SOURCE_DB" \
  "${ARTIFACT_ARGS[@]}" \
  --output "$BACKUP_DEST" \
  --writers-stopped

echo "  [3/4] Yedek hash'leri ve SQLite bütünlüğü doğrulanıyor..."
"$PY" -m arda.recovery verify "$BACKUP_DEST"

echo "  [4/4] İzole dizine geri yükleme provası yapılıyor..."
"$PY" -m arda.recovery restore "$BACKUP_DEST" --destination "$RESTORE_DEST"

# SQLite bütünlük sorgusu
RESTORED_DB="$RESTORE_DEST/core.sqlite3"
INTEGRITY=$("$PY" -c "
import sqlite3
with sqlite3.connect('$RESTORED_DB') as db:
    print(db.execute('PRAGMA integrity_check').fetchone()[0])
")

if [ "$INTEGRITY" != "ok" ]; then
  echo "❌ Geri yüklenen veritabanı bütünlük denetiminden geçemedi: $INTEGRITY" >&2
  exit 1
fi

echo "✅ Kurtarma provası başarıyla tamamlandı (SQLite integrity: $INTEGRITY)."
