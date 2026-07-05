"""Weather-grounded math problems backed by Open-Meteo.

Numbers are pre-rounded to integers by the time they appear in a prompt;
graded answers are exact (the scheduler maps weather_math to tolerance:exact).
"""

from __future__ import annotations

import csv
import json
import logging
import random
import time
import urllib.parse
import urllib.request
from datetime import datetime, timedelta
from pathlib import Path

from server import bones, forge_ops, forge_pool, suppressions

logger = logging.getLogger(__name__)


ROOT = Path(__file__).parent.parent
DEFAULT_LOCATIONS = ROOT / "data" / "locations.csv"
CACHE_PATH = ROOT / "data" / "weather_cache.json"
CACHE_TTL_SECONDS = 6 * 3600

OPEN_METEO_URL = "https://api.open-meteo.com/v1/forecast"
OPEN_METEO_PARAMS = (
    "daily=temperature_2m_max,temperature_2m_min,wind_speed_10m_max"
    "&temperature_unit=fahrenheit"
    "&wind_speed_unit=mph"
    "&timezone=auto"
    "&forecast_days=7"
)


def load_locations(path: Path = DEFAULT_LOCATIONS) -> list[dict]:
    if not path.exists():
        return [{"name": "NYC", "lat": 40.7128, "lon": -74.0060}]
    out = []
    with path.open(newline="") as f:
        for r in csv.DictReader(f):
            try:
                out.append({
                    "name": (r.get("name") or "").strip(),
                    "lat": float(r["lat"]),
                    "lon": float(r["lon"]),
                })
            except (KeyError, TypeError, ValueError):
                continue
    return out or [{"name": "NYC", "lat": 40.7128, "lon": -74.0060}]


def _forge_validator(skill_id: str):
    """Build a forge_pool validator: entry passes when its bones features are
    mappable AND don't match an active suppression rule for ``skill_id``.

    Active rules are loaded once per call, not once per candidate.
    """
    active = suppressions.load_active()

    def _valid(entry: dict) -> bool:
        features = forge_ops.features_for_entry(entry)
        return features is not None and not suppressions.matches(
            skill_id, {"features": features}, active,
        )

    return _valid


def load_forecast(location: dict) -> dict:
    """Fetch + cache 7-day daily summary for a single location.

    Returns a dict with parallel lists `dates`, `t_max`, `t_min`,
    `wind_max`. Falls back to a stub forecast if the API is unreachable.
    """
    cache = _read_cache()
    key = _location_key(location)
    cached = cache.get(key)
    now = time.time()
    if cached and (now - cached.get("fetched_at", 0)) < CACHE_TTL_SECONDS:
        return cached["data"]

    try:
        forecast = _fetch_open_meteo(location)
    except Exception:
        forecast = _fallback_forecast()
        # Don't cache a fallback so we retry next call.
        return forecast

    cache[key] = {"fetched_at": now, "data": forecast}
    _write_cache(cache)
    return forecast


def generate_problem(target: dict | None = None) -> dict:
    op = (target or {}).get("operation") or random.choice(
        ["temp_delta", "daily_range", "f_to_c_approx", "wind_delta"]
    )
    # Try the forge pool first — LLM-generated prompts grounded on real data.
    # Skip when target carries pinned keys the pool can't honor.
    entry = (
        forge_pool.take("weather_math", op, validator=_forge_validator("weather_math"))
        if forge_pool.eligible(target) else None
    )
    if entry is not None:
        try:
            expected = forge_ops.OPS[entry["op"]](*entry["args"])
            return {
                "prompt": entry["prompt"],
                "expected": float(expected),
                "parameters": {
                    "operation": entry["operation"],
                    "source": "forge",
                    "forge_id": entry["id"],
                    "level": (target or {}).get("level"),
                    "features": forge_ops.features_for_entry(entry),
                },
            }
        except Exception:
            logger.warning(
                "forge: malformed pool entry %s skipped (op=%r args=%r)",
                entry.get("id"), entry.get("op"), entry.get("args"),
            )
    locations = load_locations()
    location = random.choice(locations)
    forecast = load_forecast(location)
    if op == "temp_delta":
        return _temp_delta(location, forecast)
    if op == "daily_range":
        return _daily_range(location, forecast)
    if op == "f_to_c_approx":
        return _f_to_c_approx(location, forecast)
    if op == "wind_delta":
        return _wind_delta(location, forecast)
    return _temp_delta(location, forecast)


# ---- problem generators ---------------------------------------------------


def _temp_delta(location: dict, forecast: dict) -> dict:
    pick = _pick_distinct_pair(forecast, "t_max")
    if pick is None:
        # Forecast is flat enough that no pair has a non-zero whole-degree
        # difference. Fall back to a daily range question.
        return _daily_range(location, forecast)
    i, j, a, b = pick
    label_a = _day_label(forecast["dates"][i])
    label_b = _day_label(forecast["dates"][j])
    diff = abs(a - b)
    warmer_label, warmer, cooler_label, cooler = (
        (label_a, a, label_b, b) if a >= b else (label_b, b, label_a, a)
    )
    return {
        "prompt": (
            f"{warmer_label}'s high in {location['name']} is {warmer}. "
            f"{cooler_label}'s high is {cooler}. How much warmer is {warmer_label}?"
        ),
        "expected": float(diff),
        "parameters": {
            "operation": "temp_delta",
            "source": "open-meteo",
            "location": location["name"],
            "warmer": warmer,
            "cooler": cooler,
            "features": bones.compute_features(
                "-", (warmer, cooler), endpoints=(warmer, cooler),
                extra={"operation": "temp_delta"},
            ),
        },
    }


def _daily_range(location: dict, forecast: dict) -> dict:
    i = _pick_crossing_day(forecast)
    if i is None:
        i = _pick_index(forecast)
    hi = int(round(forecast["t_max"][i]))
    lo = int(round(forecast["t_min"][i]))
    if lo > hi:
        hi, lo = lo, hi
    label = _day_label(forecast["dates"][i])
    return {
        "prompt": (
            f"{label} in {location['name']}: high {hi}, low {lo}. What's the daily range?"
        ),
        "expected": float(hi - lo),
        "parameters": {
            "operation": "daily_range",
            "source": "open-meteo",
            "location": location["name"],
            "high": hi,
            "low": lo,
            "features": bones.compute_features(
                "-", (hi, lo), endpoints=(hi, lo),
                extra={"operation": "daily_range"},
            ),
        },
    }


def _f_to_c_approx(location: dict, forecast: dict) -> dict:
    i = _pick_index(forecast)
    raw = int(round(forecast["t_max"][i]))
    # Make sure (f - 30) / 2 lands on an integer for a deterministic answer.
    f = raw if (raw - 30) % 2 == 0 else raw + 1
    c = (f - 30) // 2
    label = _day_label(forecast["dates"][i])
    return {
        "prompt": (
            f"{label}'s high in {location['name']} is {f}°F. "
            "Use the rough rule: subtract 30 and halve. About what's that in Celsius?"
        ),
        "expected": float(c),
        "parameters": {
            "operation": "f_to_c_approx",
            "source": "open-meteo",
            "location": location["name"],
            "fahrenheit": f,
            "features": bones.compute_features(
                "-", (f, 30), extra={"operation": "f_to_c_approx"},
            ),
        },
    }


def _wind_delta(location: dict, forecast: dict) -> dict:
    pick = _pick_distinct_pair(forecast, "wind_max")
    if pick is None:
        return _daily_range(location, forecast)
    i, j, a, b = pick
    label_a = _day_label(forecast["dates"][i])
    label_b = _day_label(forecast["dates"][j])
    diff = abs(a - b)
    stronger_label, stronger, calmer_label, calmer = (
        (label_a, a, label_b, b) if a >= b else (label_b, b, label_a, a)
    )
    return {
        "prompt": (
            f"{stronger_label}'s wind in {location['name']} is {stronger} mph. "
            f"{calmer_label}'s is {calmer} mph. How much stronger is {stronger_label}?"
        ),
        "expected": float(diff),
        "parameters": {
            "operation": "wind_delta",
            "source": "open-meteo",
            "location": location["name"],
            "stronger": stronger,
            "calmer": calmer,
            "features": bones.compute_features(
                "-", (stronger, calmer), endpoints=(stronger, calmer),
                extra={"operation": "wind_delta"},
            ),
        },
    }


# ---- helpers --------------------------------------------------------------


def _location_key(location: dict) -> str:
    return f"{location['lat']:.4f},{location['lon']:.4f}"


def _read_cache() -> dict:
    if not CACHE_PATH.exists():
        return {}
    try:
        return json.loads(CACHE_PATH.read_text())
    except (json.JSONDecodeError, OSError):
        return {}


def _write_cache(cache: dict) -> None:
    try:
        CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
        CACHE_PATH.write_text(json.dumps(cache))
    except OSError:
        pass


def _fetch_open_meteo(location: dict) -> dict:
    qs = (
        f"latitude={location['lat']}&longitude={location['lon']}&"
        f"{OPEN_METEO_PARAMS}"
    )
    url = f"{OPEN_METEO_URL}?{qs}"
    with urllib.request.urlopen(url, timeout=8) as resp:
        payload = json.loads(resp.read().decode("utf-8"))
    daily = payload.get("daily") or {}
    return {
        "dates": list(daily.get("time") or []),
        "t_max": list(daily.get("temperature_2m_max") or []),
        "t_min": list(daily.get("temperature_2m_min") or []),
        "wind_max": list(daily.get("wind_speed_10m_max") or []),
    }


def _fallback_forecast() -> dict:
    today = datetime.now().date()
    dates = [(today + timedelta(days=i)).isoformat() for i in range(7)]
    return {
        "dates": dates,
        "t_max": [62, 68, 71, 65, 58, 60, 66],
        "t_min": [49, 52, 55, 51, 44, 47, 53],
        "wind_max": [9, 12, 15, 11, 7, 14, 10],
    }


def _pick_index(forecast: dict) -> int:
    n = len(forecast.get("dates") or [])
    if n == 0:
        return 0
    return random.randrange(n)


def _two_distinct_indices(forecast: dict) -> tuple[int, int]:
    n = len(forecast.get("dates") or [])
    if n < 2:
        return 0, 0
    i = random.randrange(n)
    j = random.randrange(n - 1)
    if j >= i:
        j += 1
    return i, j


def _pick_distinct_pair(forecast: dict, field: str) -> tuple[int, int, int, int] | None:
    """Pick two indices whose rounded `field` values differ AND cross a ten.

    Returns (i, j, a_int, b_int) or None if no pair both rounds to distinct
    integers and crosses a multiple of ten (a flat-ish forecast). Caller
    falls back to a different op when None — this pre-filter exists so the
    generate() suppression gate's retry budget isn't burned on candidates
    that would just be rejected anyway.
    """
    values = forecast.get(field) or []
    n = len(values)
    if n < 2:
        return None
    rounded = [int(round(v)) for v in values]
    distinct_indices = list(range(n))
    random.shuffle(distinct_indices)
    for idx, i in enumerate(distinct_indices):
        for j in distinct_indices[idx + 1:]:
            if rounded[i] != rounded[j] and bones.crosses_ten(rounded[i], rounded[j]):
                # Random which gets named "first" so we don't always anchor
                # on the same calendar day.
                if random.random() < 0.5:
                    i, j = j, i
                return i, j, rounded[i], rounded[j]
    return None


def _pick_crossing_day(forecast: dict) -> int | None:
    """Shuffle day indices and return the first whose hi/lo crosses a ten.

    Returns None when no day qualifies; caller falls back to a random pick
    (today's behavior) so the generate() gate's retry budget isn't burned.
    """
    n = len(forecast.get("dates") or [])
    if n == 0:
        return None
    indices = list(range(n))
    random.shuffle(indices)
    for i in indices:
        hi = int(round(forecast["t_max"][i]))
        lo = int(round(forecast["t_min"][i]))
        if bones.crosses_ten(hi, lo):
            return i
    return None


def _day_label(date_str: str) -> str:
    """Turn an ISO date into 'Today', 'Tomorrow', or 'Friday'-style."""
    try:
        d = datetime.strptime(date_str, "%Y-%m-%d").date()
    except ValueError:
        return "That day"
    today = datetime.now().date()
    delta = (d - today).days
    if delta == 0:
        return "Today"
    if delta == 1:
        return "Tomorrow"
    if 2 <= delta <= 6:
        return d.strftime("%A")
    return d.strftime("%b %d")
