import hashlib, json, uuid
from datetime import datetime, timedelta, timezone
import joblib, numpy as np, requests
from sklearn.ensemble import HistGradientBoostingRegressor
from psycopg.types.json import Jsonb
from .db import DATA, connect, setting, set_setting, audit

UTC = timezone.utc
HOUR = timedelta(hours=1)


def series():
    authority = setting("authority")
    with connect() as c:
        rows = c.execute(
            "SELECT period,value,snapshot_sha FROM observations WHERE authority=%s ORDER BY period",
            (authority,),
        ).fetchall()
    values = {r["period"]: r["value"] for r in rows}
    sha = hashlib.sha256(
        json.dumps(
            [(r["period"].isoformat(), r["value"], r["snapshot_sha"]) for r in rows],
            separators=(",", ":"),
        ).encode()
    ).hexdigest()
    return authority, values, sha


def feature(values, issue, h):
    target = issue + h * HOUR
    cutoff = issue - 24 * HOUR
    # Every lag is strictly at or before the reporting-delay cutoff.
    times = [
        target - 168 * HOUR,
        target - 336 * HOUR,
        cutoff,
        cutoff - 24 * HOUR,
        cutoff - 168 * HOUR,
    ]
    if any(t > cutoff or t not in values for t in times):
        return None
    return [values[t] for t in times] + [target.hour, target.weekday(), target.month, h]


def samples(values):
    first = min(values) + timedelta(days=21)
    last = max(values) - 48 * HOUR
    issue = first.replace(hour=0)
    X = []
    y = []
    base = []
    issues = []
    horizons = []
    while issue <= last:
        for h in range(1, 49):
            f = feature(values, issue, h)
            target = issue + h * HOUR
            if f is not None and target in values:
                X.append(f)
                y.append(values[target])
                base.append(f[0])
                issues.append(issue.timestamp())
                horizons.append(h)
        issue += timedelta(days=1)
    return (
        np.array(X),
        np.array(y),
        np.array(base),
        np.array(issues),
        np.array(horizons),
    )


def metrics(y, p, h):
    return {
        "mae": float(np.mean(np.abs(y - p))),
        "wape": float(np.abs(y - p).sum() / np.abs(y).sum()),
        "mae_1_24": float(np.mean(np.abs(y[h <= 24] - p[h <= 24]))),
        "mae_25_48": float(np.mean(np.abs(y[h > 24] - p[h > 24]))),
    }


def interval(y, p, h):
    return {
        group: float(np.quantile(np.abs(y[mask] - p[mask]), 0.9, method="higher"))
        for group, mask in [("1_24", h <= 24), ("25_48", h > 24)]
    }


def track(model_id, meta):
    uri = "http://mlflow:5000/api/2.0/mlflow"
    r = requests.get(
        uri + "/experiments/get-by-name",
        params={"experiment_name": "hourly-demand"},
        timeout=10,
    )
    if r.ok:
        experiment = r.json()["experiment"]["experiment_id"]
    else:
        r = requests.post(
            uri + "/experiments/create", json={"name": "hourly-demand"}, timeout=10
        )
        r.raise_for_status()
        experiment = r.json()["experiment_id"]
    r = requests.post(
        uri + "/runs/create",
        json={
            "experiment_id": experiment,
            "start_time": int(datetime.now(UTC).timestamp() * 1000),
            "tags": [
                {"key": "model_id", "value": model_id},
                {"key": "dataset_sha", "value": meta["dataset_sha"]},
                {"key": "evaluation_mode", "value": "retrospective_revised_data"},
            ],
        },
        timeout=15,
    )
    r.raise_for_status()
    info = r.json()["run"]["info"]
    run = info["run_id"]
    artifact = info["artifact_uri"].replace("mlflow-artifacts:/", "", 1).lstrip("/")
    folder = DATA / "models"
    for name, content in [
        ("model/model.joblib", (folder / (model_id + ".joblib")).read_bytes()),
        ("model/evaluation.json", json.dumps(meta, indent=2).encode()),
    ]:
        requests.put(
            "http://mlflow:5000/api/2.0/mlflow-artifacts/artifacts/"
            + artifact
            + "/"
            + name,
            data=content,
            timeout=30,
        ).raise_for_status()
    for k, v in meta["holdout"]["candidate"].items():
        requests.post(
            uri + "/runs/log-metric",
            json={
                "run_id": run,
                "key": k,
                "value": v,
                "timestamp": int(datetime.now(UTC).timestamp() * 1000),
                "step": 0,
            },
            timeout=10,
        ).raise_for_status()
    requests.post(
        uri + "/runs/update",
        json={
            "run_id": run,
            "status": "FINISHED",
            "end_time": int(datetime.now(UTC).timestamp() * 1000),
        },
        timeout=10,
    ).raise_for_status()
    requests.post(
        uri + "/registered-models/create", json={"name": "energy-demand"}, timeout=10
    )
    registered = requests.post(
        uri + "/model-versions/create",
        json={
            "name": "energy-demand",
            "source": info["artifact_uri"] + "/model",
            "run_id": run,
            "description": "Custom joblib candidate; inference is managed by Energy Operations Runtime. No promotion approved.",
        },
        timeout=15,
    )
    registered.raise_for_status()
    return run


def train():
    authority, values, sha = series()
    if len(values) < 24 * 150:
        raise RuntimeError("At least 150 days of valid demand history are required")
    X, y, b, t, h = samples(values)
    if len(y) < 5000:
        raise RuntimeError("Too few complete lagged training samples")
    last = float(t.max())
    day = 86400
    folds = []
    for ago in [28, 14, 0]:
        end = last - ago * day
        test = (t > end - 14 * day) & (t <= end)
        cal = (t > end - 28 * day) & (t <= end - 16 * day)
        # Purge the 48-hour target window at each temporal boundary.
        tr = t <= end - 30 * day
        if min(tr.sum(), cal.sum(), test.sum()) < 48:
            raise RuntimeError("Insufficient chronological fold coverage")
        model = HistGradientBoostingRegressor(
            max_iter=120,
            max_leaf_nodes=15,
            l2_regularization=2,
            random_state=41,
            early_stopping=False,
        )
        model.fit(X[tr], y[tr])
        pred = model.predict(X[test])
        cp = model.predict(X[cal])
        baseline = metrics(y[test], b[test], h[test])
        candidate = metrics(y[test], pred, h[test])
        folds.append(
            {
                "test_start": datetime.fromtimestamp(t[test].min(), UTC).isoformat(),
                "test_end": datetime.fromtimestamp(t[test].max(), UTC).isoformat(),
                "baseline": baseline,
                "candidate": candidate,
                "train_samples": int(tr.sum()),
                "test_samples": int(test.sum()),
            }
        )
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S")
    code_sha = hashlib.sha256(
        b"".join(
            p.read_bytes()
            for p in sorted(__import__("pathlib").Path(__file__).parent.glob("*.py"))
        )
    ).hexdigest()
    common = {
        "code_sha": code_sha,
        "authority": authority,
        "dataset_sha": sha,
        "created_at": datetime.now(UTC).isoformat(),
        "source_latest_period": max(values).isoformat(),
        "units": "MW equivalent hourly demand (source MWh per hour)",
        "reporting_delay_hours": 24,
        "evaluation_mode": "retrospective_revised_data",
        "historical_publication_times_available": False,
        "folds": folds,
        "holdout": folds[-1],
        "promotion_requires_human": True,
        "live_validated": False,
    }
    baseline_id = "seasonal-week-" + stamp
    baseline_meta = {
        **common,
        "kind": "seasonal-week",
        "intervals": interval(y[cal], b[cal], h[cal]),
    }
    tree_id = "tree-" + stamp
    improvement = 1 - folds[-1]["candidate"]["mae"] / folds[-1]["baseline"]["mae"]
    regressions = any(
        folds[-1]["candidate"][k] > 1.05 * folds[-1]["baseline"][k]
        for k in ["mae_1_24", "mae_25_48"]
    )
    meta = {
        **common,
        "kind": "hist-gradient-boosting",
        "intervals": interval(y[cal], cp, h[cal]),
        "relative_mae_improvement": improvement,
        "proposed_metric_gate_passed": improvement >= 0.05 and not regressions,
        "operational_gate_passed": False,
        "operational_gate_reason": "Historical publication vintages and live validation are not established.",
    }
    folder = DATA / "models"
    folder.mkdir(exist_ok=True)
    joblib.dump(model, folder / (tree_id + ".joblib"))
    for mid, obj in [(baseline_id, baseline_meta), (tree_id, meta)]:
        (folder / (mid + ".json")).write_text(json.dumps(obj, indent=2))
    # Save an immutable evaluation input so it can be reproduced without later revisions.
    dataset = DATA / "datasets"
    dataset.mkdir(exist_ok=True)
    np.savez_compressed(dataset / (sha + ".npz"), X=X, y=y, b=b, t=t, h=h)
    (dataset / (sha + "-evaluation.json")).write_text(json.dumps(folds, indent=2))
    (dataset / (sha + ".json")).write_text(
        json.dumps(
            {
                "authority": authority,
                "series": [(k.isoformat(), v) for k, v in values.items()],
                "dataset_sha": sha,
            },
            separators=(",", ":"),
        )
    )
    meta["mlflow_run_id"] = track(tree_id, meta)
    with connect() as c:
        for mid, kind, obj in [
            (baseline_id, "seasonal-week", baseline_meta),
            (tree_id, "hist-gradient-boosting", meta),
        ]:
            c.execute(
                "INSERT INTO models(id,kind,dataset_sha,metadata) VALUES (%s,%s,%s,%s)",
                (mid, kind, sha, Jsonb(obj)),
            )
        set_setting(c, "reference_model", baseline_id)
        set_setting(c, "latest_candidate", tree_id)
        audit(
            c,
            "candidate_registered",
            {
                "model_id": tree_id,
                "baseline_id": baseline_id,
                "dataset_sha": sha,
                "proposed_metric_gate_passed": meta["proposed_metric_gate_passed"],
                "operational_gate_passed": False,
            },
        )
    return {
        "baseline_id": baseline_id,
        "candidate_id": tree_id,
        "holdout": folds[-1],
        "relative_mae_improvement": improvement,
        "operational_gate_passed": False,
    }


def forecast(model_id, reference=False):
    authority, values, sha = series()
    with connect() as c:
        row = c.execute("SELECT * FROM models WHERE id=%s", (model_id,)).fetchone()
    if not row:
        raise ValueError("Model not found")
    meta = row["metadata"]
    issue = datetime.now(UTC).replace(minute=0, second=0, microsecond=0)
    if max(values) < issue - 72 * HOUR:
        raise ValueError("Source data is too stale to issue a forecast")
    fs = [feature(values, issue, h) for h in range(1, 49)]
    if any(f is None for f in fs):
        raise ValueError("Missing required hourly lag observations")
    pred = (
        np.array([f[0] for f in fs])
        if row["kind"] == "seasonal-week"
        else joblib.load(DATA / "models" / (model_id + ".joblib")).predict(fs)
    )
    points = []
    for h, p in enumerate(pred, 1):
        q = meta["intervals"]["1_24" if h <= 24 else "25_48"]
        points.append(
            {
                "horizon_hours": h,
                "period": (issue + h * HOUR).isoformat(),
                "demand_mw": max(0, float(p)),
                "lower_90": max(0, float(p - q)),
                "upper_90": float(p + q),
            }
        )
    result = {
        "forecast_id": str(uuid.uuid4()),
        "authority": authority,
        "model_id": model_id,
        "model_training_dataset_sha": row["dataset_sha"],
        "forecast_input_dataset_sha": sha,
        "issue_time": issue.isoformat(),
        "latest_observation": max(values).isoformat(),
        "reference_only": reference,
        "interval_note": "90% residual intervals calibrated on retrospective revised data; live coverage unverified.",
        "points": points,
    }
    with connect() as c:
        c.execute(
            "INSERT INTO forecast_runs(id,model_id,issue_time,dataset_sha,payload) VALUES (%s,%s,%s,%s,%s)",
            (result["forecast_id"], model_id, issue, sha, Jsonb(result)),
        )
    return result
