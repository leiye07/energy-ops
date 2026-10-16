# Energy Operations Forecasting Hub — initial service deployment

All runtime, ingestion, evaluation and training execute inside the Ubuntu VM. Source files can be edited on the laptop, but application commands and tests run in guest containers.

Services: Runtime API, one Model Factory worker, private PostgreSQL and private MLflow. API: host `http://127.0.0.1:8000`; interactive API documentation: `/docs`. Protected endpoints use the Bearer token in `/opt/energy-ops/deployment/secrets/api_token` inside the VM. Do not paste this token into chat. Database and MLflow have no published ports. The API is bound only to the guest NAT address; host VirtualBox forwards it only on loopback.

Model Factory checks EIA hourly demand coverage for CISO, ERCO and PJM over a common 90-day UTC window, selects the best authority only if it has at least 98% coverage, and downloads about 400 days for evaluation. Invalid rows are quarantined, raw responses are saved by SHA256 with credential-bearing request echoes removed, and changed observations receive revision records. Incremental hourly downloads revisit seven days for revisions. Full-history refresh can be performed by a controlled fresh ingestion; revisions older than seven days are not automatically revisited.

A seasonal weekly baseline and CPU gradient-boosted tree are evaluated with expanding chronological folds, a reporting-delay cutoff, purged boundaries and separate residual calibration. Evaluation uses revised historical EIA observations, whose original publication times are unknown. Scores are retrospective, not evidence of historical live availability. Tree promotion is blocked pending point-in-time/live validation. No model is automatically promoted. `/v1/forecasts/reference` gives an explicitly labeled baseline forecast for review; `/v1/forecasts` requires an operator-approved model. Model/data versions and the issue time are stored for each forecast. Residual intervals have not been validated for live coverage.

`POST /v1/jobs` accepts `ingest`, `train`, or `ingest_train`. A single database advisory lock excludes duplicate workers and each worker runs one job at a time. CPU/memory are bounded in Compose. The initial job collects data and trains; subsequent hourly jobs collect revisions. Retraining runs weekly and can also be operator-triggered. Arbitrary shell tasks are not exposed by the API.

`POST /v1/models/{id}/promote` allows explicit approval of a baseline only, with `{"acknowledge_revised_history":true}`. Tree approval remains blocked. Each promotion saves the previous champion; `POST /v1/rollback` restores it. Review candidate metrics and source freshness before approving anything.

Backup and restoration commands are in `scripts/`. Source-outage retries are bounded and failures are recorded without credential-bearing exception strings. No email/Slack alert channel is configured. A local authenticated dashboard is available at host http://127.0.0.1:8080. Sign in as energyops using the Ubuntu VM password as it was at dashboard deployment. Only its salted password hash is mounted into the dashboard; plaintext VM passwords are not stored there. If you change the Ubuntu password, refresh the dashboard hash and restart its container. External access, a capacity guarantee, and measured live forecasting quality are not delivered in this stage.

## Dashboard

The dashboard shows demand history and reference uncertainty, retained coverage, candidate metrics, model/data provenance, jobs and audit history. Collection and training buttons enqueue controlled jobs. Baseline approval requires an explicit confirmation acknowledging the revised-history limitation; tree approval remains blocked. Sign-in uses an eight-hour in-memory session cookie, HTTP-only and same-site strict. Restarting the dashboard invalidates sessions. All modifying requests require a matching Origin. This HTTP configuration is limited to laptop loopback; authenticated LAN access with TLS has not been configured.

## Architecture and dataflow

- [Architecture](docs/architecture.md): VM and container topology, training/services boundaries, storage, credentials, scheduling, recovery and verification status.
- [Dataflow](docs/dataflow.md): EIA collection, validation, revision tracking, feature availability, chronological evaluation, model approval and forecast lineage.
