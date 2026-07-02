#!/usr/bin/env python3
"""Taxonomy v2 prototype: classify attempts into primitive facts vs compound
procedures, assess strengths/weaknesses per taxon, and demo templated
strategy tips against real weak/slow attempts.

Read-only against data/feynman.db. This is the B1/B2 stress test from
docs/superpowers/plans/2026-07-01-ai-vs-code-plan-of-attack.md — the
classify() function is the candidate fact_key_v2, and STRATEGIES is the seed
of the strategy library. Every rendered tip's arithmetic is asserted against
the attempt's expected answer before it is shown.
"""
import json
import sqlite3
import statistics
from pathlib import Path

from server.taxonomy import classify, family_display, PRIMITIVE_ONSET_TARGET_MS
from server import strategies

ROOT = Path(__file__).parent.parent
DB_PATH = ROOT / "data" / "feynman.db"


# ------------------------------------------------------------- assessment

def assess(rows: list[sqlite3.Row]) -> dict:
    """Group classified attempts by family; verdict per family."""
    fams: dict[str, dict] = {}
    for r in rows:
        t = r["taxon"]
        f = fams.setdefault(t.family, {"tier": t.tier, "attempts": [], "keys": {}})
        f["attempts"].append(r)
        f["keys"].setdefault(t.key, []).append(r)

    report = {}
    for fam, f in fams.items():
        live = [r for r in f["attempts"] if not r["skipped"]]
        n = len(live)
        if n == 0:
            continue
        acc = sum(r["correct"] or 0 for r in live) / n
        lat_field = "onset_latency_ms" if f["tier"] == "primitive" else "resolution_latency_ms"
        lats = [r[lat_field] for r in live if r["correct"] and r[lat_field] is not None]
        med = int(statistics.median(lats)) if lats else None
        target = (PRIMITIVE_ONSET_TARGET_MS if f["tier"] == "primitive"
                  else (live[0]["target_latency_ms"] or 9000))
        if n < 5:
            verdict = "insufficient-n"
        elif acc >= 0.9 and med is not None and med <= target:
            verdict = "strong"
        elif acc >= 0.85:
            verdict = "slow" if (med or 0) > target else "strong"
        elif acc < 0.7:
            verdict = "weak"
        else:
            verdict = "shaky"
        # per-key outliers: any fact missed at least once with n>=2
        outliers = []
        for key, krows in sorted(f["keys"].items()):
            klive = [r for r in krows if not r["skipped"]]
            misses = sum(1 for r in klive if not r["correct"])
            if misses and len(klive) >= 1:
                outliers.append((key, len(klive), misses))
        report[fam] = {
            "tier": f["tier"], "n": n, "acc": acc, "median_ms": med,
            "target_ms": target, "verdict": verdict, "skips": len(f["attempts"]) - n,
            "outliers": sorted(outliers, key=lambda o: -o[2])[:8],
        }
    return report


# -------------------------------------------------------- strategy library
# The verified rule set now lives in server/strategies.py (asserted against the
# graded answer, dropped on any failure). This tool just feeds it real attempts.


def tips_for(row) -> list[str]:
    """Adapter: run the shared strategy library against one classified attempt."""
    return strategies.tips_for(
        row["skill_id"], row["params"], row["expected_answer"], None
    )


# ------------------------------------------------------------------- main

def load_attempts() -> list[dict]:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        """SELECT a.*, s.target_latency_ms FROM attempts a
           LEFT JOIN skills s ON s.id = a.skill_id ORDER BY a.created_at"""
    ).fetchall()
    conn.close()
    out = []
    for r in rows:
        try:
            params = json.loads(r["parameters"] or "{}")
        except ValueError:
            params = {}
        taxon = classify(r["skill_id"], params)
        if taxon:
            d = dict(r)
            d["params"], d["taxon"] = params, taxon
            out.append(d)
    return out


if __name__ == "__main__":
    rows = load_attempts()
    print(f"classified {len(rows)} attempts\n")
    report = assess(rows)

    for tier in ("primitive", "compound"):
        print(f"=== {tier.upper()} FAMILIES ===")
        fams = {k: v for k, v in report.items() if v["tier"] == tier}
        for fam, s in sorted(fams.items(), key=lambda kv: (kv[1]["acc"], -kv[1]["n"])):
            med = f"{s['median_ms']}ms" if s["median_ms"] else "—"
            line = (f"{fam:28s} ({family_display(fam)}) "
                    f"n={s['n']:<4d} acc={s['acc']:.2f} med={med:>8s} "
                    f"tgt={s['target_ms']}ms skips={s['skips']} -> {s['verdict']}")
            print(line)
            for key, kn, miss in s["outliers"]:
                print(f"    miss: {key} ({miss}/{kn} wrong)")
        print()

    print("=== STRATEGY TIPS ON REAL FAILED/SLOW ATTEMPTS ===")
    shown = 0
    for r in rows:
        if r["skipped"]:
            continue
        lat_field = ("onset_latency_ms" if r["taxon"].tier == "primitive"
                     else "resolution_latency_ms")
        target = (PRIMITIVE_ONSET_TARGET_MS if r["taxon"].tier == "primitive"
                  else (r["target_latency_ms"] or 9000))
        wrong = not r["correct"]
        slow = r["correct"] and (r[lat_field] or 0) > target * 1.5
        if not (wrong or slow):
            continue
        tips = tips_for(r)
        if not tips:
            continue
        mode = "WRONG" if wrong else f"SLOW ({r[lat_field]}ms)"
        print(f"\n[{mode}] {r['prompt_text']}  (key={r['taxon'].key})")
        for t in tips:
            print(f"   → {t}")
        shown += 1
    # coverage: how many weak/slow attempts got no tip
    weak_all = [r for r in rows if not r["skipped"] and (
        not r["correct"] or (r["correct"] and (r["resolution_latency_ms"] or 0) >
                             (r["target_latency_ms"] or 9000) * 1.5))]
    print(f"\ncoverage: {shown}/{len(weak_all)} weak/slow attempts got >=1 verified tip")
