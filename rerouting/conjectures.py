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
HOLDOUT = RESULTS / "holdout"                 # the blind test: fresh seeds 6-10
RUNS = RESULTS                                # where the measured runs are read from
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
    "time": "within max(15 s, 25% of the predicted delay above free flow)",
    "gain": "within max(15 s, 30% of the predicted difference)",
    "gap": "within 30 s",
    "switches": "within max(2 per hour, 35% of the predicted count)",
    "share": "within 0.05",
    "swing": "within max(0.05, 30% of the predicted swing)",
    "finished": "within 0.03",
}
ACCEPT = ("a claim holds if at least 80% of its cases pass, counting only new runs when it has any "
          "(cases whose predicted delay is under 15 s are also reported separately: a constant free-flow "
          "guess would pass them too)")
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
        case("C3", f"detour minus short road time, {d} cars/h", "gap", "two_road_experienced_series",
             {"demand": d, "policy": "experienced"}, "road_gap", {"demand": d, "wardrop": True, "out": "road_gap"},
             True)
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
            case("C5", f"{d} cars/h, {p:.0%} reroute, switches per hour", "switches", "two_road_share_series", m,
                 "switches", chain, True)
            case("C6", f"{d} cars/h, {p:.0%} reroute", "time", "two_road_share_series", m, "journey", chain,
                 d == 1500)
    for p in (0.5, 1.0):
        for w in WINDOWS:
            case("C7", f"1800 cars/h, {p:.0%} reroute, {w} s average", "time", "two_road_information",
                 {"demand": 1800, "share": p, "window": w, "synchronize": False}, "journey",
                 {"demand": 1800, "share": p, "age": M.information_age(w)}, False)
        case("C7", f"1800 cars/h, {p:.0%} reroute: 600 s minus 10 s average", "gain", "two_road_information",
             {"demand": 1800, "share": p, "synchronize": False}, "contrast",
             {"demand": 1800, "share": p, "contrast": [10, 600]}, False)
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
        if spec.get("contrast"):
            old, new = (M.run(M.Chain(r1, r2, d, share=spec["share"], age=M.information_age(w)), REPLICAS)["journey"]
                        for w in spec["contrast"][::-1])
            memo[key] = {"contrast": old - new}
        elif spec.get("gain"):
            static = M.run(M.Chain(r1, r2, d), REPLICAS)["journey"]
            live = M.run(M.Chain(r1, r2, d, share=1.0), REPLICAS)["journey"]
            memo[key] = {"gain": static - live}
        elif spec.get("wardrop"):
            f = M.wardrop_split(r1, r2, d, REPLICAS)
            run = M.run(M.Chain(r1, r2, d, split=f), REPLICAS)
            memo[key] = run | {"split": f, "road_gap": run["journey_long_route"] - run["journey_short_route"]}
        else:
            memo[key] = M.run(M.Chain(r1, r2, d, share=spec.get("share", 0.0), split=spec.get("split", 0.0),
                                      age=spec.get("age", M.information_age(180))), REPLICAS)
    return memo[key][spec.get("out", metric)]


def _spec_value(args):
    spec, roads = args
    memo = {}
    chain_value(spec, "gain" if spec.get("gain") else "contrast" if spec.get("contrast") else "journey", roads, memo)
    return json.dumps(spec, sort_keys=True), memo[json.dumps(spec, sort_keys=True)]


def predict(workers: int = 1) -> dict:
    from concurrent.futures import ProcessPoolExecutor
    roads = calibrate()
    all_cases = cases()
    specs = list({json.dumps(c["chain"], sort_keys=True): c["chain"] for c in all_cases}.values())
    with ProcessPoolExecutor(workers) as pool:
        memo = dict(pool.map(_spec_value, [(sp, roads) for sp in specs]))
    registered = [dict(c, predicted=chain_value(c["chain"], c["metric"], roads, memo)) for c in all_cases]
    r1, r2 = roads
    return {"roads": {"short": {"T": r1.T, "C": r1.C}, "long": {"T": r2.T, "C": r2.C}},
            "information_age_180s": M.information_age(180), "replicas": REPLICAS,
            "claims": CLAIMS, "rules": RULES, "accept": ACCEPT, "cases": registered}


# --------------------------------------------------------------------------- tests


def measured(c: dict) -> tuple[float, int]:
    path = RUNS / f"{c['experiment']}.jsonl"
    runs = [json.loads(line) for line in path.read_text().splitlines() if line.strip()] if path.exists() else []
    rows = [r for r in runs if all(
        (abs(r.get(k, -1) - v) < 1e-9 if isinstance(v, float) else r.get(k) == v) for k, v in c["match"].items())]
    if c["metric"] in ("gain", "contrast"):
        if c["metric"] == "gain":
            a = {r["seed"]: r["journey"] for r in rows if r["policy"] == "no_information"}
            b = {r["seed"]: r["journey"] for r in rows if r["policy"] == "live"}
        else:
            lo, hi = c["chain"]["contrast"]
            a = {r["seed"]: r["journey"] for r in rows if r["window"] == hi}
            b = {r["seed"]: r["journey"] for r in rows if r["window"] == lo}
        diffs = [a[s] - b[s] for s in a if s in b]
        return (float(np.mean(diffs)) if diffs else float("nan")), len(diffs)
    seeds = [r["seed"] for r in rows]
    if len(set(seeds)) != len(seeds):
        return float("nan"), 0  # duplicated runs: refuse to score
    if c["metric"] == "swing":
        vals = [M.swing(r["long_share_series"]) for r in rows]
    elif c["metric"] == "switches":
        vals = [M.switches(r["long_share_series"]) for r in rows]
    elif c["metric"] == "road_gap":
        vals = [r["journey_long_route"] - r["journey_short_route"] for r in rows]
    else:
        vals = [r[c["metric"]] for r in rows]
    if any(v != v for v in vals):
        return float("nan"), 0  # an undefined measurement fails the case
    return (float(np.mean(vals)) if vals else float("nan")), len(vals)


def verdict(c: dict, value: float, n: int, roads) -> bool | None:
    p = c["predicted"]
    if n < SEEDS or math.isnan(value):
        return False  # a missing or incomplete case fails
    rule = c["rule"]
    if rule == "time":
        base = roads[1].T if c["base"] == "long" else roads[0].T
        return abs(value - p) <= max(15.0, 0.25 * abs(p - base))
    if rule == "gap":
        return abs(value - p) <= 30.0
    if rule == "switches":
        return abs(value - p) <= max(2.0, 0.35 * p)
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
        small = c["rule"] == "time" and abs(c["predicted"] - (roads[1].T if c["base"] == "long" else roads[0].T)) < 15
        rows.append(c | {"measured": value, "seeds": n, "pass": bool(verdict(c, value, n, roads)), "small": small})
    return rows


def fmt(v: float, rule: str) -> str:
    if math.isnan(v):
        return "missing"
    if rule == "time":
        return f"{v / 60:.1f} min"
    if rule in ("gain", "gap"):
        return f"{v:+.0f} s"
    if rule == "switches":
        return f"{v:.1f}"
    return f"{v:.0%}" if rule in ("share", "finished") else f"{v:.2f}"


def table(rows: list[dict]) -> str:
    lines = ["| claim | case | chain | SUMO | pass |", "|---|---|---|---|---|"]
    for r in rows:
        v = {True: "yes", False: "**no**"}[r["pass"]] + (" (small)" if r.get("small") else "")
        tag = "" if r["new"] else " †"
        lines.append(f"| {r['claim']} | {r['label']}{tag} | {fmt(r['predicted'], r['rule'])} | "
                     f"{fmt(r['measured'], r['rule'])} | {v} |")
    return "\n".join(lines)


def summary(rows: list[dict]) -> str:
    out = []
    for c, text in CLAIMS.items():
        cs = [r for r in rows if r["claim"] == c]
        judged = [r for r in cs if r["new"]] or cs
        rate = sum(r["pass"] for r in judged) / len(judged)
        big = [r for r in cs if not r["small"]]
        out.append(f"{c} {text}: {'HOLDS' if rate >= 0.8 else 'FAILS'} - {sum(r['pass'] for r in judged)}/"
                   f"{len(judged)} {'new ' if judged[0]['new'] else ''}cases pass; all runs {sum(r['pass'] for r in cs)}/"
                   f"{len(cs)}; cases with a predicted delay of 15 s or more {sum(r['pass'] for r in big)}/{len(big)}")
    return "\n".join(out)


def main():
    global RUNS
    if sys.argv[1:2] == ["holdout"]:
        # the blind test: predictions for seeds 6-10 are written once, before those runs exist
        path = HOLDOUT / "predictions.json"
        if sys.argv[2:] == ["predict"]:
            if path.exists():
                raise SystemExit(f"{path} exists: predictions are written once, before the test runs")
            HOLDOUT.mkdir(parents=True, exist_ok=True)
            pred = predict(workers=10)
            for c in pred["cases"]:
                c["new"] = True          # every case is a fresh run
            pred["seeds"] = [6, 7, 8, 9, 10]
            path.write_text(json.dumps(pred, indent=1))
            print(f"wrote {path}")
            return
        RUNS = HOLDOUT
        rows = evaluate(json.loads(path.read_text()))
        (HOLDOUT / "conjectures.md").write_text(table(rows) + "\n\n" + summary(rows) + "\n")
        print(summary(rows))
        return
    if sys.argv[1:] == ["dev"]:
        # development check against the runs already made (seeds 1-5): nothing is written
        global REPLICAS
        REPLICAS = 300
        rows = evaluate(predict())
        print(summary(rows))
        (RESULTS / "dev_scores.json").write_text(json.dumps(
            [{k: (bool(v) if k == "pass" else v) for k, v in r.items() if k != "chain"} for r in rows], default=float))
        for r in rows:
            if not r["pass"]:
                print("  miss:", r["claim"], r["label"], fmt(r["predicted"], r["rule"]), fmt(r["measured"], r["rule"]))
        return
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
