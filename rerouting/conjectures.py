"""Six predictions of the Markov-chain theory, and their test against the SUMO runs.

    python -m rerouting.conjectures predict   # write results/predictions.json (never overwritten)
    python -m rerouting.conjectures           # score every prediction and print the table

The only inputs taken from SUMO are the two road parameters, measured at the extremes:
the free-flow time ``T`` (100 cars per hour) and the capacity ``C`` (highest exit rate once
the road is saturated). Everything else is predicted, and the pass rules below were fixed
before the test runs (two_road_queue_law, two_road_onset, two_road_share_series).
"""

from __future__ import annotations

import json
import math
import sys
from pathlib import Path

import numpy as np

from rerouting import theory as T

ROOT = Path(__file__).resolve().parents[1]
RESULTS = ROOT / "results"
PREDICTIONS = RESULTS / "predictions.json"

QUEUE_LAW = {"short": [300, 450, 600, 700], "long": [300, 450, 600, 750, 900, 1000]}
ONSET = [650, 675, 700, 725, 750, 775, 800]
EQUILIBRIUM = [750, 900, 1050, 1200, 1350]
SHARES, SHARE_DEMANDS = [.1, .2, .3, .4, .5, .6, .7, .8, .9, 1.0], [1200, 1500, 1800]
FINISHED = [1500, 1650, 1800, 2100, 2400]


def load(name: str) -> list[dict]:
    path = RESULTS / f"{name}.jsonl"
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()] if path.exists() else []


def calibrate() -> tuple[T.Road, T.Road]:
    """Free-flow time (s) and capacity (cars/h) of each route, from runs with everyone forced on it."""
    rows = load("two_road_calibration")
    roads = []
    for route in ("short", "long"):
        rs = [r for r in rows if r["route"] == route]
        low = min(r["demand"] for r in rs)
        free_flow = np.mean([r["journey"] for r in rs if r["demand"] == low])
        capacity = max(np.mean([r["throughput"] for r in rs if r["demand"] == d]) for d in {r["demand"] for r in rs})
        roads.append(T.Road(float(free_flow), float(capacity)))
    return roads[0], roads[1]


def mean_of(rows, metric, **match):
    vals = [r[metric] for r in rows if all(r.get(k) == v for k, v in match.items())]
    return (float(np.mean(vals)), vals) if vals else (float("nan"), [])


# --------------------------------------------------------------------------- predictions


def predict() -> dict:
    r1, r2 = calibrate()
    d_star = T.demand_threshold(r1, r2)
    out = {
        "roads": {"short": {"T": r1.T, "C": r1.C}, "long": {"T": r2.T, "C": r2.C}},
        "C1_queue_law": {route: {str(x): road.time(x) for x in QUEUE_LAW[route]}
                         for route, road in (("short", r1), ("long", r2))},
        "C2_onset": {"d_star": d_star},
        "C3_equilibrium": {str(d): r1.time(T.equilibrium_flow(d, r1, r2)) for d in EQUILIBRIUM},
        "C4_detour_share": {str(d): {"p_star": T.share_threshold(d, r1, r2),
                                     **{str(p): min(p, T.share_threshold(d, r1, r2)) for p in SHARES}}
                            for d in SHARE_DEMANDS},
        "C5_herding_swing": {str(d): {str(p): T.herding_swing(p, T.share_threshold(d, r1, r2)) for p in SHARES}
                             for d in SHARE_DEMANDS},
        "C6_finished": {"no_information": {str(d): T.finished_share(d, r1.C, r1.T) for d in FINISHED},
                        "live": {str(d): T.finished_share(d, r1.C + r2.C, r1.T) for d in FINISHED}},
        "rules": {
            "C1": "SUMO within 10% of the prediction when load x/C <= 0.8, within 25% when 0.8 < x/C <= 0.95",
            "C2": "gain (no information minus live) under 10 s for d <= 700; positive in every seed for d >= 750",
            "C3": "experienced drivers within 10% of the equilibrium time for 750 <= d <= 1350",
            "C4": "detour share (finished trips, minutes 10-60) within 0.05 of min(p, p*)",
            "C5": "swing under 0.1 for p <= p* - 0.1; for p >= p* + 0.2 between 0.5x and 1.0x the prediction",
            "C6": "share of trips finished by the 2 h cut-off within 0.03 of the prediction",
        },
    }
    return out


def swing(series: list) -> float:
    """Spread of the per-minute detour share over minutes 10-60, with the sampling noise of a
    minute (a binomial draw of ~20-30 cars) removed: sqrt(var(q_k) - mean(q_k (1-q_k) / (n_k-1)))."""
    pts = [(q, n) for t, q, n in series if 600 <= t < 3600 and n > 1]
    q = np.array([a for a, _ in pts])
    noise = np.mean([a * (1 - a) / (n - 1) for a, n in pts])
    return math.sqrt(max(0.0, q.var() - noise))


# --------------------------------------------------------------------------- tests


def evaluate(pred: dict) -> list[dict]:
    rows = []

    def add(conj, case, predicted, measured, ok, unit=""):
        rows.append({"conjecture": conj, "case": case, "predicted": predicted, "measured": measured,
                     "pass": bool(ok), "unit": unit})

    # C1 queue law, forced routes below capacity
    forced = load("two_road_calibration") + load("two_road_queue_law")
    for route, road in zip(("short", "long"), calibrate()):
        for x in QUEUE_LAW[route]:
            p = pred["C1_queue_law"][route][str(x)]
            m, vals = mean_of(forced, "journey", route=route, demand=x)
            if not vals:
                continue
            tol = 0.10 if x / road.C <= 0.8 else 0.25
            add("C1", f"{route} road, {x} cars/h (load {x / road.C:.2f})", p, m, abs(m - p) <= tol * p, "s")

    # C2 onset of the gain
    onset = load("two_road_onset")
    d_star = pred["C2_onset"]["d_star"]
    for d in ONSET:
        _, static = mean_of(onset, "journey", demand=d, policy="no_information")
        live = {r["seed"]: r["journey"] for r in onset if r["demand"] == d and r["policy"] == "live"}
        seeds = {r["seed"]: r["journey"] for r in onset if r["demand"] == d and r["policy"] == "no_information"}
        gains = [seeds[s] - live[s] for s in seeds if s in live]
        if not gains:
            continue
        g = float(np.mean(gains))
        ok = abs(g) < 10 if d <= 700 else (min(gains) > 0 if d >= 750 else True)
        add("C2", f"{d} cars/h ({'below' if d < d_star else 'above'} d* = {d_star:.0f})",
            0.0 if d < d_star else float("nan"), g, ok, "s gain")

    # C3 experienced drivers reach the equilibrium time
    demand = load("two_road_demand")
    for d in EQUILIBRIUM:
        m, vals = mean_of(demand, "journey", demand=d, policy="experienced")
        if vals:
            p = pred["C3_equilibrium"][str(d)]
            add("C3", f"{d} cars/h", p, m, abs(m - p) <= 0.10 * p, "s")

    # C4 detour share and C5 swing
    series = load("two_road_share_series")
    for d in SHARE_DEMANDS:
        p_star = pred["C4_detour_share"][str(d)]["p_star"]
        for p in SHARES:
            rs = [r for r in series if r["demand"] == d and abs(r["share"] - p) < 1e-9]
            if not rs:
                continue
            share = float(np.mean([r["long_share"] for r in rs]))
            add("C4", f"{d} cars/h, p = {p:.0%} (p* = {p_star:.0%})", pred["C4_detour_share"][str(d)][str(p)],
                share, abs(share - min(p, p_star)) <= 0.05)
            s = float(np.mean([swing(r["long_share_series"]) for r in rs]))
            ps = pred["C5_herding_swing"][str(d)][str(p)]
            if p <= p_star - 0.1:
                ok = s < 0.1
            elif p >= p_star + 0.2:
                ok = 0.5 * ps <= s <= 1.0 * ps
            else:
                ok = None  # too close to p* to call; reported, not scored
            add("C5", f"{d} cars/h, p = {p:.0%} (p* = {p_star:.0%})", ps, s, ok if ok is not None else True)
            if ok is None:
                rows[-1]["pass"] = None

    # C6 trips finished by the cut-off (queue chain without a steady state)
    for policy, key in (("no_information", "no_information"), ("live", "live")):
        for d in FINISHED:
            match = {"demand": d, "policy": policy} | ({"share": 1.0} if policy == "live" else {})
            m, vals = mean_of(demand, "completed", **match)
            if vals:
                p = pred["C6_finished"][key][str(d)]
                add("C6", f"{'no information' if policy != 'live' else 'every driver rerouting'}, {d} cars/h",
                    p, m, abs(m - p) <= 0.03)
    return rows


def table(rows: list[dict]) -> str:
    def fmt(v, unit):
        if isinstance(v, float) and math.isnan(v):
            return "> 0"
        if unit == "s":
            return f"{v / 60:.2f} min"
        if unit == "s gain":
            return f"{v:+.0f} s"
        return f"{v:.2f}"
    lines = ["| | case | predicted | SUMO | pass |", "|---|---|---|---|---|"]
    for r in rows:
        verdict = {True: "yes", False: "**no**", None: "(near p*)"}[r["pass"]]
        lines.append(f"| {r['conjecture']} | {r['case']} | {fmt(r['predicted'], r['unit'])} | "
                     f"{fmt(r['measured'], r['unit'])} | {verdict} |")
    return "\n".join(lines)


def main():
    if sys.argv[1:] == ["predict"]:
        if PREDICTIONS.exists():
            raise SystemExit(f"{PREDICTIONS} exists: predictions are written once, before the test runs")
        PREDICTIONS.write_text(json.dumps(predict(), indent=1))
        print(f"wrote {PREDICTIONS}")
        return
    pred = json.loads(PREDICTIONS.read_text())
    rows = evaluate(pred)
    print(table(rows))
    for c in sorted({r["conjecture"] for r in rows}):
        scored = [r["pass"] for r in rows if r["conjecture"] == c and r["pass"] is not None]
        print(f"{c}: {sum(scored)}/{len(scored)} pass")


if __name__ == "__main__":
    main()
