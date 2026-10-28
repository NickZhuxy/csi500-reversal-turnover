"""Compare two runs' summary.json scalar fields (reproduction check)."""

from __future__ import annotations

import json
import math
from pathlib import Path

METADATA = {"run_id", "created_at", "note"}


def flatten(d, prefix=""):
    if isinstance(d, dict):
        for k, v in d.items():
            yield from flatten(v, f"{prefix}{k}.")
    elif isinstance(d, list):
        for i, v in enumerate(d):
            yield from flatten(v, f"{prefix}{i}.")
    else:
        yield prefix[:-1], d


def compare_runs(old: Path, new: Path, rel_tol: float = 1e-9) -> dict:
    a = dict(flatten(json.loads((Path(old) / "summary.json").read_text())))
    b = dict(flatten(json.loads((Path(new) / "summary.json").read_text())))
    mism, n = [], 0
    for k in sorted(set(a) | set(b)):
        if k.split(".")[0] in METADATA:
            continue
        n += 1
        x, y = a.get(k), b.get(k)
        if isinstance(x, float) and isinstance(y, float):
            if math.isnan(x) and math.isnan(y):
                continue
            if math.isclose(x, y, rel_tol=rel_tol, abs_tol=1e-12):
                continue
        elif x == y:
            continue
        mism.append(f"{k}: {x!r} -> {y!r}")
    return {"n_compared": n, "mismatches": mism}
