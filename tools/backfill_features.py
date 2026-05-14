#!/usr/bin/env python3
"""One-time script: backfill features into existing attempt parameters."""
import json, sqlite3
from pathlib import Path

ROOT = Path(__file__).parent.parent
DB_PATH = ROOT / "data" / "feynman.db"


def _ab_features(params: dict) -> dict:
    a, b = params.get("a"), params.get("b")
    if a is None or b is None:
        return {}
    a, b = int(a), int(b)
    f = {"abs_diff": abs(a - b), "min_operand": min(a, b), "max_operand": max(a, b)}
    if "borrow" in params:
        f["has_borrow"] = bool(params["borrow"])
    if "carry" in params:
        f["has_carry"] = bool(params["carry"])
    return f


def _weather_features(params: dict) -> dict:
    op = params.get("operation", "")
    pairs = {
        "wind_delta": ("stronger", "calmer"),
        "temp_delta": ("warmer", "cooler"),
        "daily_range": ("high", "low"),
    }
    if op not in pairs:
        return {}
    ka, kb = pairs[op]
    a, b = params.get(ka), params.get(kb)
    if a is None or b is None:
        return {}
    a, b = int(a), int(b)
    return {"abs_diff": abs(a - b), "min_operand": min(a, b), "max_operand": max(a, b), "operation": op}


conn = sqlite3.connect(DB_PATH)
conn.row_factory = sqlite3.Row
rows = conn.execute("SELECT id, skill_id, parameters FROM attempts WHERE parameters IS NOT NULL").fetchall()
updated = 0
for row in rows:
    try:
        params = json.loads(row["parameters"])
    except (TypeError, ValueError):
        continue
    if "features" in params:
        continue
    features = _weather_features(params) if row["skill_id"] == "weather_math" else _ab_features(params)
    if not features:
        continue
    params["features"] = features
    conn.execute("UPDATE attempts SET parameters = ? WHERE id = ?", (json.dumps(params), row["id"]))
    updated += 1
conn.commit()
conn.close()
print(f"Backfilled {updated} attempts.")
