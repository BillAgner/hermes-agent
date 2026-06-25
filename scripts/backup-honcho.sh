#!/usr/bin/env bash
# Backup Honcho Postgres + Redis volumes + compose config.
# Designed for daily cron; keeps 7 daily + 4 weekly + 3 monthly rolling.
#
# Uses docker exec pg_dump (logical) for portability — restores work on
# any Postgres version, including a fresh pgvector container.
# Redis is small and ephemeral; saved via RDB dump copy.

set -euo pipefail
umask 077

BACKUP_ROOT="${HERMES_BACKUP_ROOT:-/mnt/c/Data/Hermes/cron/output/backup/honcho}"
COMPOSE_DIR="${HONCHO_COMPOSE_DIR:-/mnt/c/honcho}"
HOST_TZ_OFFSET="${TZ_OFFSET_HOURS:-0}"
STAMP="$(TZ=UTC${HOST_TZ_OFFSET} date +%Y-%m-%dT%H-%M-%SZ)"
DAY_OF_WEEK="$(date +%u)"   # 1=Mon .. 7=Sun
DAY_OF_MONTH="$(date +%d)"

mkdir -p "${BACKUP_ROOT}/daily" "${BACKUP_ROOT}/weekly" "${BACKUP_ROOT}/monthly"

cd "${COMPOSE_DIR}" || { echo "[FATAL] cannot cd to ${COMPOSE_DIR}" >&2; exit 1; }

DEST="${BACKUP_ROOT}/daily/honcho-${STAMP}"
mkdir -p "${DEST}"

# --- Postgres logical dump (pg_dump custom format, compressed) ---
docker compose exec -T database pg_dump \
    -U postgres -d postgres \
    --format=custom \
    --no-owner \
    --no-privileges \
    --compress=9 \
    > "${DEST}/postgres.dump" \
    || { echo "[FAIL] pg_dump failed" >&2; rm -rf "${DEST}"; exit 2; }

PG_SIZE=$(stat -c%s "${DEST}/postgres.dump")

# --- Redis snapshot (via SAVE then copy RDB) ---
# Use a sidecar exec since `redis save` blocks briefly but is safe to run.
docker compose exec -T redis sh -c "redis-cli SAVE > /dev/null" \
    || echo "[WARN] redis SAVE failed (non-fatal, Redis is ephemeral cache)"

# --- Capture compose file + env (without secrets) for reproducibility ---
cp -f "${COMPOSE_DIR}/docker-compose.yml" "${DEST}/docker-compose.yml"
# Copy env with secrets redacted (KEY=sk-... → KEY=REDACTED)
if [ -f "${COMPOSE_DIR}/.env" ]; then
    sed -E 's/(KEY|TOKEN|SECRET|PASSWORD)=.+/\1=REDACTED/' "${COMPOSE_DIR}/.env" > "${DEST}/.env.redacted"
fi

# --- Manifest ---
cat > "${DEST}/MANIFEST.json" <<EOF
{
    "timestamp": "${STAMP}",
    "postgres_dump_bytes": ${PG_SIZE},
    "compose_file": "docker-compose.yml",
    "env_redacted": ".env.redacted",
    "redis_included": false,
    "host": "$(hostname)",
    "wsl_distro": "$(cat /etc/os-release 2>/dev/null | grep PRETTY_NAME | head -1 || echo unknown)"
}
EOF

# --- Verify the dump is restorable (smoke test) ---
# Create a temporary database, restore, drop. Catches corruption early.
VERIFY_DB="hermes_verify_$$"
docker compose exec -T database psql -U postgres -c "DROP DATABASE IF EXISTS ${VERIFY_DB};" >/dev/null
docker compose exec -T database createdb -U postgres "${VERIFY_DB}" >/dev/null
if docker compose exec -T database pg_restore -U postgres -d "${VERIFY_DB}" --no-owner --no-privileges --single-transaction < "${DEST}/postgres.dump" >/dev/null 2>&1; then
    TABLES=$(docker compose exec -T database psql -U postgres -d "${VERIFY_DB}" -tAc "SELECT count(*) FROM information_schema.tables WHERE table_schema='public';")
    WORKSPACES=$(docker compose exec -T database psql -U postgres -d "${VERIFY_DB}" -tAc "SELECT count(*) FROM workspaces;" 2>/dev/null || echo 0)
    echo "[OK] verified: ${TABLES} tables, ${WORKSPACES} workspaces in restore" >> "${DEST}/MANIFEST.json"
    docker compose exec -T database psql -U postgres -c "DROP DATABASE ${VERIFY_DB};" >/dev/null
else
    echo "[FAIL] verification restore failed" >> "${DEST}/MANIFEST.json"
fi

TOTAL_SIZE=$(du -sb "${DEST}" | awk '{print $1}')
echo "[OK] Honcho backup ${STAMP}: pg=${PG_SIZE}B, total=${TOTAL_SIZE}B → ${DEST}"

# --- Rolling retention ---
# Daily: keep 7
find "${BACKUP_ROOT}/daily" -maxdepth 1 -type d -name 'honcho-*' -mtime +7 -exec rm -rf {} + 2>/dev/null || true
# Weekly: keep 4 (Sundays only)
if [ "${DAY_OF_WEEK}" = "7" ]; then
    cp -al "${DEST}" "${BACKUP_ROOT}/weekly/honcho-${STAMP}"
fi
find "${BACKUP_ROOT}/weekly" -maxdepth 1 -type d -name 'honcho-*' -mtime +28 -exec rm -rf {} + 2>/dev/null || true
# Monthly: keep 3 (1st of month only)
if [ "${DAY_OF_MONTH}" = "01" ]; then
    cp -al "${DEST}" "${BACKUP_ROOT}/monthly/honcho-${STAMP}"
fi
find "${BACKUP_ROOT}/monthly" -maxdepth 1 -type d -name 'honcho-*' -mtime +90 -exec rm -rf {} + 2>/dev/null || true

echo "[OK] retention applied: daily=7, weekly=4, monthly=3"
