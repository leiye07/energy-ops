#!/usr/bin/env bash
# Run as root inside the VM; briefly pauses application writes for a coherent backup.
set -euo pipefail
cd /opt/energy-ops/deployment
umask 077
backup="/opt/energy-ops/backups/$(date -u +%Y%m%dT%H%M%SZ)"
mkdir -p "$backup"
# Only back up while no factory job is running.
if docker compose exec -T postgres psql -U energyops -d energyops -Atc "SELECT count(*) FROM jobs WHERE status='running'" | grep -qv '^0$'; then
 echo 'A factory job is running. Retry after it finishes.' >&2; exit 1
fi
docker compose stop model-factory runtime mlflow
trap 'docker compose start mlflow runtime model-factory >/dev/null' EXIT
docker compose exec -T postgres pg_dump -U energyops -Fc energyops > "$backup/energyops.dump"
docker compose exec -T postgres pg_dump -U energyops -Fc mlflow > "$backup/mlflow.dump"
tar --exclude=./postgres-data --exclude=./.git -czf "$backup/files.tar.gz" .
(cd "$backup"; sha256sum energyops.dump mlflow.dump files.tar.gz > SHA256SUMS)
echo "$backup"
