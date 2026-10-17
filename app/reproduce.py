"""Re-run saved evaluation arrays inside a guest container, without fetching data."""

import json, sys
import numpy as np
from sklearn.ensemble import HistGradientBoostingRegressor
from .db import DATA
from .modeling import metrics

sha = sys.argv[1]
if len(sha) != 64 or any(c not in "0123456789abcdef" for c in sha):
    raise SystemExit("Invalid dataset SHA")
a = np.load(DATA / "datasets" / (sha + ".npz"))
X, y, b, t, h = [a[k] for k in ("X", "y", "b", "t", "h")]
expected = json.loads((DATA / "datasets" / (sha + "-evaluation.json")).read_text())
for ago, fold in zip([28, 14, 0], expected):
    end = float(t.max()) - ago * 86400
    tr = t <= end - 30 * 86400
    test = (t > end - 14 * 86400) & (t <= end)
    m = HistGradientBoostingRegressor(
        max_iter=120,
        max_leaf_nodes=15,
        l2_regularization=2,
        random_state=41,
        early_stopping=False,
    ).fit(X[tr], y[tr])
    got = metrics(y[test], m.predict(X[test]), h[test])
    assert all(
        np.isclose(got[k], fold["candidate"][k], rtol=1e-9, atol=1e-8) for k in got
    ), (got, fold["candidate"])
print("PASS: all three chronological evaluation folds reproduced from saved inputs")
