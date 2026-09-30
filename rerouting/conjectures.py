"""Predictions of the Markov chain (rerouting/markov.py) and their test against SUMO.

    python -m rerouting.conjectures predict   # write results/predictions.json (never overwritten)
    python -m rerouting.conjectures           # score every prediction and print the table

The chain's only inputs are the two roads' free-flow time T and capacity C, measured in SUMO
at the extremes (100 cars per hour, and the highest exit rate once saturated), and the
information age fixed by the network. The pass rules below were written, and the predictions
saved, before the runs marked "new" were made.
"""

from __future__ import annotations

import json
import math
import sys
from pathlib import Path

import numpy as np

from rerouting import markov as M
from rerouting import theory as T

ROOT = Path(__file__).resolve().parents[1]
RESULTS = ROOT / "results"
PREDICTIONS = RESULTS / "predictions.json"
SEEDS = 5
REPLICAS = 1000

CLAIMS = {
    "C1": "a road is a drive plus a queue",
    "C2": "rerouting helps only once the short road's queue costs more than the detour",
    "C3": "drivers who know the usual traffic settle where both roads take the same time",
    "C4": "rerouters fill the detour only up to the share needed",
    "C5": "above that share, rerouters swing together between the roads",
    "C6": "trip time against the share of rerouters",
    "C7": "older information makes the swing costlier",
    "C8": "trip time and trips finished against traffic",
}
RULES = {
    "time": "within max(15 s, 25% of the predicted delay above free flow); unscored if that delay is under 15 s",
    "gain": "within max(15 s, 30% of the predicted gain)",
    "share": "within 0.05",
    "swing": "within max(0.05, 30% of the predicted swing)",
    "finished": "within 0.03",
}
SHARES, SHARE_DEMANDS = [.1, .2, .3, .4, .5, .6, .7, .8, .9, 1.0], [1200, 1500, 1800]
WINDOWS = [10, 30, 60, 180, 300, 600]
DEMANDS = [300, 450, 600, 750, 900, 1050, 1200, 1350, 1500, 1650, 1800, 2100, 2400]


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


def cases() -> list[dict]:
    """Every registered comparison: where SUMO measured it, and which chain predicts it."""
    out = []

    def case(claim, label, rule, experiment, match, metric, chain, new, base="short"):
        out.append({"claim": claim, "label": label, "rule": rule, "experiment": experiment, "match": match,
                    "metric": metric, "chain": chain, "new": new, "base": base})

    for route, demands in (("short", [300, 450, 600, 700]), ("long", [300, 450, 600, 750, 900, 1000])):
        for d in demands:
            earlier = d in (300, 600, 900)
            case("C1", f"everyone on the {route} road, {d} cars/h", "time",
                 "two_road_calibration" if earlier else "two_road_queue_law",
                 {"demand": d, "route": route}, "journey", {"demand": d, "split": float(route == "long")},
                 not earlier, base=route)
    for d in [650, 675, 700, 725, 750, 775, 800]:
        case("C2", f"{d} cars/h", "gain", "two_road_onset", {"demand": d}, "gain",
             {"demand": d, "gain": True}, True)
    for d in [900, 1200, 1500]:
        case("C3", f"share on the detour, {d} cars/h", "share", "two_road_experienced_series",
             {"demand": d, "policy": "experienced"}, "long_share", {"demand": d, "wardrop": True, "out": "split"}, True)
    for d in [750, 900, 1050, 1200, 1350]:
        case("C3", f"trip time, {d} cars/h", "time", "two_road_demand", {"demand": d, "policy": "experienced"},
             "journey", {"demand": d, "wardrop": True}, False)
    for d in SHARE_DEMANDS:
        for p in SHARES:
            m = {"demand": d, "share": p}
            chain = {"demand": d, "share": p}
            case("C4", f"{d} cars/h, {p:.0%} reroute", "share", "two_road_share_series", m,
                 "long_share_rerouters", chain, True)
            case("C5", f"{d} cars/h, {p:.0%} reroute", "swing", "two_road_share_series", m, "swing", chain, True)
            case("C6", f"{d} cars/h, {p:.0%} reroute", "time", "two_road_share_series", m, "journey", chain,
                 d == 1500)
    for p in (0.5, 1.0):
        for w in WINDOWS:
            case("C7", f"1800 cars/h, {p:.0%} reroute, {w} s average", "time", "two_road_information",
                 {"demand": 1800, "share": p, "window": w, "synchronize": False}, "journey",
                 {"demand": 1800, "share": p, "age": M.information_age(w)}, False)
    for pol, share in (("no_information", 0.0), ("live", 0.5), ("live", 1.0)):
        name = "no information" if pol != "live" else f"{share:.0%} reroute"
        for d in DEMANDS:
            m = {"demand": d, "policy": pol} | ({"share": share} if pol == "live" else {})
            case("C8", f"{name}, {d} cars/h", "time", "two_road_demand", m, "journey",
                 {"demand": d, "share": share}, False)
            if d >= 1500:
                case("C8", f"{name}, {d} cars/h, finished", "finished", "two_road_demand", m, "completed",
                     {"demand": d, "share": share}, False)
    return out


# --------------------------------------------------------------------------- predictions


def chain_value(spec: dict, metric: str, roads, memo: dict) -> float:
    r1, r2 = roads
    key = json.dumps(spec, sort_keys=True)
    if key not in memo:
        d = spec["demand"]
        if spec.get("gain"):
            static = M.run(M.Chain(r1, r2, d), REPLICAS)["journey"]
            live = M.run(M.Chain(r1, r2, d, share=1.0), REPLICAS)["journey"]
            memo[key] = {"gain": static - live}
        elif spec.get("wardrop"):
            f = M.wardrop_split(r1, r2, d, REPLICAS)
            memo[key] = M.run(M.Chain(r1, r2, d, split=f), REPLICAS) | {"split": f}
        else:
            memo[key] = M.run(M.Chain(r1, r2, d, share=spec.get("share", 0.0), split=spec.get("split", 0.0),
                                      age=spec.get("age", M.information_age(180))), REPLICAS)
    return memo[key][spec.get("out", metric)]


def predict() -> dict:
    roads = calibrate()
    memo = {}
    registered = []
    for c in cases():
        c = dict(c, predicted=chain_value(c["chain"], c["metric"], roads, memo))
        registered.append(c)
    r1, r2 = roads
    return {"roads": {"short": {"T": r1.T, "C": r1.C}, "long": {"T": r2.T, "C": r2.C}},
            "information_age_180s": M.information_age(180), "replicas": REPLICAS,
            "claims": CLAIMS, "rules": RULES, "cases": registered}


# --------------------------------------------------------------------------- tests


def measured(c: dict) -> tuple[float, int]:
    rows = [r for r in load(c["experiment"]) if all(
        (abs(r.get(k, -1) - v) < 1e-9 if isinstance(v, float) else r.get(k) == v) for k, v in c["match"].items())]
    if c["metric"] == "gain":
        static = {r["seed"]: r["journey"] for r in rows if r["policy"] == "no_information"}
        live = {r["seed"]: r["journey"] for r in rows if r["policy"] == "live"}
        gains = [static[s] - live[s] for s in static if s in live]
        return (float(np.mean(gains)) if gains else float("nan")), len(gains)
    if c["metric"] == "swing":
        vals = [M.swing(r["long_share_series"]) for r in rows]
    else:
        vals = [r[c["metric"]] for r in rows]
    vals = [v for v in vals if v == v]
    return (float(np.mean(vals)) if vals else float("nan")), len(vals)


def verdict(c: dict, value: float, n: int, roads) -> bool | None:
    p = c["predicted"]
    if n < SEEDS or math.isnan(value):
        return False  # a missing or incomplete case fails
    rule = c["rule"]
    if rule == "time":
        base = roads[1].T if c["base"] == "long" else roads[0].T
        delay = abs(p - base)
        if delay < 15:
            return None
        return abs(value - p) <= max(15.0, 0.25 * delay)
    if rule == "gain":
        return abs(value - p) <= max(15.0, 0.30 * abs(p))
    if rule == "share":
        return abs(value - p) <= 0.05
    if rule == "swing":
        return abs(value - p) <= max(0.05, 0.30 * p)
    return abs(value - p) <= 0.03


def evaluate(pred: dict) -> list[dict]:
    roads = (T.Road(**pred["roads"]["short"]), T.Road(**pred["roads"]["long"]))
    rows = []
    for c in pred["cases"]:
        value, n = measured(c)
        rows.append(c | {"measured": value, "seeds": n, "pass": verdict(c, value, n, roads)})
    return rows


def fmt(v: float, rule: str) -> str:
    if math.isnan(v):
        return "missing"
    if rule == "time":
        return f"{v / 60:.1f} min"
    if rule == "gain":
        return f"{v:+.0f} s"
    return f"{v:.0%}" if rule in ("share", "finished") else f"{v:.2f}"


def table(rows: list[dict]) -> str:
    lines = ["| claim | case | chain | SUMO | pass |", "|---|---|---|---|---|"]
    for r in rows:
        v = {True: "yes", False: "**no**", None: "(too small)"}[r["pass"]]
        tag = "" if r["new"] else " †"
        lines.append(f"| {r['claim']} | {r['label']}{tag} | {fmt(r['predicted'], r['rule'])} | "
                     f"{fmt(r['measured'], r['rule'])} | {v} |")
    return "\n".join(lines)


def summary(rows: list[dict]) -> str:
    out = []
    for c, text in CLAIMS.items():
        scored = [r for r in rows if r["claim"] == c and r["pass"] is not None]
        new = [r for r in scored if r["new"]]
        out.append(f"{c} {text}: {sum(r['pass'] for r in scored)}/{len(scored)} pass "
                   f"({sum(r['pass'] for r in new)}/{len(new)} on new runs)")
    return "\n".join(out)


def main():
    if sys.argv[1:] == ["predict"]:
        if PREDICTIONS.exists():
            raise SystemExit(f"{PREDICTIONS} exists: predictions are written once, before the test runs")
        PREDICTIONS.write_text(json.dumps(predict(), indent=1))
        print(f"wrote {PREDICTIONS}")
        return
    rows = evaluate(json.loads(PREDICTIONS.read_text()))
    print(table(rows))
    print()
    print(summary(rows))


if __name__ == "__main__":
    main()
