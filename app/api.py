import hmac, uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal
from fastapi import FastAPI, Depends, HTTPException
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from pydantic import BaseModel
from psycopg.types.json import Jsonb
from .db import init, connect, setting, set_setting, audit
from .modeling import forecast


@asynccontextmanager
async def lifespan(app):
    init()
    yield


app = FastAPI(
    title="Energy Operations Runtime",
    version="0.1.0",
    lifespan=lifespan,
    description="Authenticated demand forecasts, source status, model review and job control. Model Factory executes jobs inside this VM.",
)
bearer = HTTPBearer(auto_error=False)


def auth(credentials: HTTPAuthorizationCredentials = Depends(bearer)):
    token = Path("/run/secrets/api_token").read_text().strip()
    if not credentials or not hmac.compare_digest(credentials.credentials, token):
        raise HTTPException(
            401, "Bearer token required", headers={"WWW-Authenticate": "Bearer"}
        )
    return "operator"


@app.get("/healthz")
def health():
    return {"status": "ok", "service": "runtime"}


@app.get("/readyz")
def ready():
    try:
        with connect() as c:
            c.execute("SELECT 1")
    except Exception:
        raise HTTPException(503, "Database unavailable")
    return {"status": "ready"}


@app.get("/v1/status", dependencies=[Depends(auth)])
def status():
    with connect() as c:
        counts = c.execute(
            "SELECT authority,count(*) hours,min(period) first_period,max(period) last_period FROM observations GROUP BY authority ORDER BY authority"
        ).fetchall()
        jobs = c.execute(
            "SELECT * FROM jobs ORDER BY created_at DESC LIMIT 10"
        ).fetchall()
    return {
        "authority": setting("authority"),
        "coverage_selection": setting("coverage_selection"),
        "last_ingest": setting("last_ingest"),
        "observations": counts,
        "jobs": jobs,
        "reference_model": setting("reference_model"),
        "champion": setting("champion"),
        "latest_candidate": setting("latest_candidate"),
        "promotion_policy": "Human approval required; historical revised-data scores are not live validation.",
    }


@app.get("/v1/models", dependencies=[Depends(auth)])
def models():
    with connect() as c:
        return c.execute(
            "SELECT * FROM models ORDER BY created_at DESC LIMIT 30"
        ).fetchall()


class Job(BaseModel):
    kind: Literal["ingest", "train", "ingest_train"]


@app.post("/v1/jobs", dependencies=[Depends(auth)], status_code=202)
def submit(job: Job):
    mid = str(uuid.uuid4())
    with connect() as c:
        c.execute("SELECT pg_advisory_xact_lock(30302)")
        if c.execute(
            "SELECT 1 FROM jobs WHERE status IN ('queued','running') LIMIT 1"
        ).fetchone():
            raise HTTPException(409, "A job is already queued or running")
        c.execute(
            "INSERT INTO jobs(id,kind,status) VALUES (%s,%s,'queued')", (mid, job.kind)
        )
        audit(c, "job_requested", {"id": mid, "kind": job.kind}, "operator")
    return {"id": mid, "status": "queued"}


@app.get("/v1/forecasts/reference", dependencies=[Depends(auth)])
def reference():
    mid = setting("reference_model")
    if not mid:
        raise HTTPException(409, "Reference baseline is not ready; check jobs")
    try:
        return forecast(mid, reference=True)
    except ValueError as e:
        raise HTTPException(409, str(e))


@app.get("/v1/forecasts", dependencies=[Depends(auth)])
def deployed():
    mid = setting("champion")
    if not mid:
        raise HTTPException(
            409, "No model has been approved; use the reference forecast for review"
        )
    try:
        return forecast(mid)
    except ValueError as e:
        raise HTTPException(409, str(e))


@app.get("/v1/observations", dependencies=[Depends(auth)])
def observations():
    with connect() as c:
        return c.execute(
            "SELECT period,value FROM observations WHERE authority=%s ORDER BY period DESC LIMIT 168",
            (setting("authority"),),
        ).fetchall()


@app.get("/v1/audit", dependencies=[Depends(auth)])
def history():
    with connect() as c:
        return c.execute("SELECT * FROM audit ORDER BY id DESC LIMIT 100").fetchall()


class Approval(BaseModel):
    acknowledge_revised_history: bool = False


@app.post("/v1/models/{model_id}/promote", dependencies=[Depends(auth)])
def promote(model_id: str, approval: Approval):
    if not approval.acknowledge_revised_history:
        raise HTTPException(
            409,
            "Explicit acknowledgement of revised-history evaluation limitations is required",
        )
    with connect() as c:
        c.execute("SELECT pg_advisory_xact_lock(30303)")
        row = c.execute("SELECT * FROM models WHERE id=%s", (model_id,)).fetchone()
        if not row:
            raise HTTPException(404, "Unknown model")
        if row["kind"] != "seasonal-week":
            raise HTTPException(
                409,
                "Tree promotion remains blocked until point-in-time/live validation is established",
            )
        try:
            forecast(model_id, reference=True)
        except ValueError as e:
            raise HTTPException(409, str(e))
        previous = setting("champion")
        if previous:
            set_setting(c, "previous_champion", previous)
        set_setting(c, "champion", model_id)
        audit(
            c,
            "model_promoted",
            {
                "model_id": model_id,
                "previous": previous,
                "revised_history_acknowledged": True,
            },
            "operator",
        )
    return {"champion": model_id, "previous": previous}


@app.post("/v1/rollback", dependencies=[Depends(auth)])
def rollback():
    with connect() as c:
        c.execute("SELECT pg_advisory_xact_lock(30303)")
        previous = setting("previous_champion")
        if not previous:
            raise HTTPException(409, "No previous champion is available")
        current = setting("champion")
        set_setting(c, "champion", previous)
        set_setting(c, "previous_champion", current)
        audit(c, "rollback", {"champion": previous, "previous": current}, "operator")
    return {"champion": previous}


@app.get("/v1/anomalies", dependencies=[Depends(auth)])
def anomalies():
    from datetime import timedelta

    mid = setting("reference_model")
    if not mid:
        raise HTTPException(409, "Reference model is not ready")
    with connect() as c:
        model = c.execute("SELECT metadata FROM models WHERE id=%s", (mid,)).fetchone()[
            "metadata"
        ]
        rows = c.execute(
            "SELECT period,value FROM observations WHERE authority=%s ORDER BY period DESC LIMIT 216",
            (setting("authority"),),
        ).fetchall()
    values = {r["period"]: r["value"] for r in rows}
    q = model["intervals"]["25_48"]
    result = []
    for r in rows[:48]:
        prior = values.get(r["period"] - timedelta(hours=168))
        if prior is not None and abs(r["value"] - prior) > q:
            result.append(
                {
                    "period": r["period"],
                    "actual": r["value"],
                    "weekly_reference": prior,
                    "absolute_residual": abs(r["value"] - prior),
                    "threshold": q,
                }
            )
    return {
        "flags": result,
        "model_id": mid,
        "note": "Residual flags for operator review; not verified outage or fault diagnoses.",
    }
