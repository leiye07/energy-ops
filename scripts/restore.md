# Restore inside an Ubuntu VM

The backup contains API/EIA/database credentials. Keep its directory root-only. Copy backups off the VM to an approved destination; no off-VM backup destination is configured yet.

1. Verify `sha256sum -c SHA256SUMS` in the selected backup directory.
2. Preserve any current deployment before replacement. Stop its Compose services. Extract `files.tar.gz` into `/opt/energy-ops/deployment` on a recovery VM with Docker/Compose. Use root so saved ownership is preserved. Do not overlay a running deployment.
3. Run `docker compose up -d postgres` from that directory and wait for database health.
4. Restore the databases, with application services stopped:

   ```sh
   docker compose exec -T postgres pg_restore -U energyops -d energyops --clean --if-exists --no-owner < /path/to/backup/energyops.dump
   docker compose exec -T postgres pg_restore -U energyops -d mlflow --clean --if-exists --no-owner < /path/to/backup/mlflow.dump
   docker compose up -d
   ```

5. Check service health, authenticated `/v1/status`, model metadata, forecast generation, and the source snapshot/artifact hashes. API documentation is at `http://127.0.0.1:8000/docs` through the host NAT forward.

A backup is not a substitute for a tested restore. The deployment report records the verification actually completed.
