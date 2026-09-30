"""The experiments of the study, run in parallel and appended to ``results/<name>.jsonl``.

    python -m rerouting.experiments two_road grid city     # or: all
    python -m rerouting.experiments --workers 2 two_road

A finished run is never repeated (its key is already in the results file), so an
interrupted campaign simply resumes. Demand and "experienced driver" routes are
built once per (network, demand, seed) and cached in ``build/``.
"""

from __future__ import annotations

import argparse
import itertools
import json
import os
import pickle
import time
from concurrent.futures import FIRST_COMPLETED, ProcessPoolExecutor, wait
from pathlib import Path

from rerouting import sumo as S

ROOT = Path(__file__).resolve().parents[1]
BUILD = ROOT / "build"
RESULTS = ROOT / "results"
SEEDS = {"two_road": [1, 2, 3, 4, 5], "two_road_long": [1, 2, 3, 4, 5], "grid": [1, 2, 3], "city": [1, 2, 3]}
LONG_ENTRY = 2400.0   # m: the "two_road_long" variant, whose queues stay inside the network  # grid runs are 50-100x slower
END = 7200.0        # every run stops 2 h after the start; demand lasts the first hour
DURATION = 3600.0

# --------------------------------------------------------------------------- the design
EXPERIMENTS = {
    # Two routes, calibration: everyone forced on one route, to measure its free-flow time and capacity
    "two_road_calibration": {
        "network": "two_road", "demands": [100, 300, 600, 900, 1200, 1500, 1800, 2400],
        "policies": [{"policy": "forced", "route": "short"}, {"policy": "forced", "route": "long"}],
    },
    # Two routes: demand sweep for the three kinds of drivers
    "two_road_demand": {
        "network": "two_road", "demands": [300, 450, 600, 750, 900, 1050, 1200, 1350, 1500, 1650, 1800, 2100, 2400],
        "policies": [{"policy": "no_information"}, {"policy": "experienced"},
                     {"policy": "live", "share": 1.0}, {"policy": "live", "share": 0.5}],
    },
    # Two routes: how the share of rerouting drivers matters
    "two_road_share": {
        "network": "two_road", "demands": [1200, 1800],
        "policies": [{"policy": "live", "share": p} for p in (0, .05, .1, .2, .3, .4, .5, .6, .7, .8, .9, 1.0)],
    },
    # Two routes: how fresh the information is (averaging window) and whether decisions are synchronised
    "two_road_information": {
        "network": "two_road", "demands": [1800],
        "policies": [{"policy": "live", "share": p, "window": w, "period": per, "synchronize": sync}
                     for p in (0.5, 1.0) for w in (10, 30, 60, 180, 300, 600)
                     for per, sync in ((60, False), (60, True))],
        "series": True,
    },
    # Tests of the Markov-chain predictions (see rerouting/conjectures.py), registered before running
    "two_road_queue_law": {  # fresh demands for the queue law, below capacity
        "network": "two_road", "demands": [450, 700, 750, 1000],
        "policies": [{"policy": "forced", "route": "short"}, {"policy": "forced", "route": "long"}],
    },
    "two_road_onset": {  # fine sweep around the predicted onset d* = 717 cars/h
        "network": "two_road", "demands": [650, 675, 700, 725, 750, 775, 800],
        "policies": [{"policy": "no_information"}, {"policy": "live", "share": 1.0}],
    },
    "two_road_experienced_series": {  # route split of drivers who learned the usual traffic
        "network": "two_road", "demands": [900, 1200, 1500],
        "policies": [{"policy": "experienced"}],
        "series": True,
    },
    "two_road_share_series": {  # detour share and its minute-by-minute swing, by share of rerouters
        "network": "two_road", "demands": [1200, 1500, 1800],
        "policies": [{"policy": "live", "share": p} for p in (.1, .2, .3, .4, .5, .6, .7, .8, .9, 1.0)],
        "series": True,
    },
    # Grid: demand sweep, share sweep and information at a congested level
    "grid_demand": {
        "network": "grid", "demands": [2000, 4000, 6000, 8000, 9000, 10000, 11000, 12000, 14000],
        "policies": [{"policy": "no_information"}, {"policy": "live", "share": 1.0},
                     {"policy": "live", "share": 0.5}],
    },
    "grid_share": {
        "network": "grid", "demands": [11000, 12000],
        "policies": [{"policy": "live", "share": p} for p in (0, .25, .5, .75, 1.0)],
    },
    "grid_information": {
        "network": "grid", "demands": [12000], "seeds": 2,
        "policies": [{"policy": "live", "share": 1.0, "window": w, "period": per, "synchronize": sync}
                     for w in (30, 180, 600) for per, sync in ((30, False), (120, False), (120, True))],
    },
    # A real street map: one illustrative comparison
    "city": {
        "network": "city", "demands": [1000, 2000, 3000],
        "policies": [{"policy": "no_information"}, {"policy": "live", "share": 0.5},
                     {"policy": "live", "share": 1.0}],
    },
}
GROUPS = {"two_road": ["two_road_calibration", "two_road_demand", "two_road_share", "two_road_information"],
          "tests": ["two_road_queue_law", "two_road_onset", "two_road_experienced_series", "two_road_share_series"],
          "grid": ["grid_demand", "grid_share", "grid_information"], "city": ["city"]}


# --------------------------------------------------------------------------- inputs (cached)


def network(name: str) -> Path:
    BUILD.mkdir(exist_ok=True)
    path = BUILD / f"{name}.net.xml"
    if not path.exists():
        if name == "two_road":
            S.build_two_road(path)
        elif name == "two_road_long":
            S.build_two_road(path, entry=LONG_ENTRY)
        elif name == "grid":
            S.build_grid(path)
        elif name == "city":
            S.build_city(ROOT / "data" / "larochelle.osm.gz", path)
        else:
            raise ValueError(name)
    return path


def demand(net_name: str, vph: int, seed: int, route: str = "short") -> list:
    suffix = "" if route == "short" else f"_{route}"
    cache = BUILD / "demand" / f"{net_name}_{vph}_{seed}{suffix}.pkl"
    if cache.exists():
        return pickle.loads(cache.read_bytes())
    cache.parent.mkdir(parents=True, exist_ok=True)
    if net_name.startswith("two_road"):
        vehicles = S.two_road_demand(vph, seed, DURATION, route=route)
    else:
        vehicles = S.network_demand(network(net_name), vph, seed, DURATION)
    cache.write_bytes(pickle.dumps(vehicles))
    return vehicles


def experienced(net_name: str, vph: int, seed: int) -> list:
    cache = BUILD / "experienced" / f"{net_name}_{vph}_{seed}.pkl"
    if cache.exists():
        return pickle.loads(cache.read_bytes())
    work = BUILD / "experienced" / f"{net_name}_{vph}_{seed}"
    vehicles = S.experienced_routes(network(net_name), demand(net_name, vph, seed), work, iterations=20, end=END)
    cache.write_bytes(pickle.dumps(vehicles))
    return vehicles


# --------------------------------------------------------------------------- tasks


def tasks(name: str, seeds: list[int] | None = None, net: str | None = None):
    spec = EXPERIMENTS[name]
    net = net or spec["network"]
    seeds = seeds or SEEDS[net][: spec.get("seeds", len(SEEDS[net]))]
    for vph, pol, seed in itertools.product(spec["demands"], spec["policies"], seeds):
        yield {"experiment": name, "network": net, "demand": vph, "seed": seed,
               "series": spec.get("series", False), **pol}


def key(task: dict) -> str:
    return json.dumps(task, sort_keys=True)


def run_task(task: dict) -> dict:
    net = network(task["network"])
    if task["policy"] == "experienced":
        vehicles, share = experienced(task["network"], task["demand"], task["seed"]), 0.0
    elif task["policy"] == "forced":
        vehicles, share = demand(task["network"], task["demand"], task["seed"], route=task["route"]), 0.0
    else:
        vehicles, share = demand(task["network"], task["demand"], task["seed"]), task.get("share", 0.0)
    scenario = S.Scenario(net, vehicles, share=share, period=task.get("period", 60.0),
                          window=task.get("window", 180), synchronize=task.get("synchronize", False),
                          end=END, seed=task["seed"])
    start = time.time()
    stats = S.simulate(scenario, series_bin=60.0 if task["series"] else 0.0)
    return {"key": key(task), **task, **stats, "wall_seconds": round(time.time() - start, 2)}


def _experienced(args):
    return experienced(*args) and None


def run_experiment(name: str, workers: int, seeds: list[int] | None = None, results: Path = RESULTS,
                   net: str | None = None) -> None:
    results.mkdir(parents=True, exist_ok=True)
    out = results / f"{name}.jsonl"
    done = set()
    if out.exists():
        done = {json.loads(line)["key"] for line in out.read_text().splitlines() if line.strip()}
    todo = [t for t in tasks(name, seeds, net) if key(t) not in done]
    # build shared inputs first so that workers never race on the cache: demand serially,
    # experienced drivers' routes (one assignment per demand and seed) in parallel
    for t in todo:
        demand(t["network"], t["demand"], t["seed"], route=t.get("route", "short"))
    pairs = sorted({(t["network"], t["demand"], t["seed"]) for t in todo if t["policy"] == "experienced"})
    with ProcessPoolExecutor(max_workers=workers) as pool:
        list(pool.map(_experienced, pairs))
    print(f"[{name}] {len(todo)} runs to do ({len(done)} already done)", flush=True)
    start = time.time()
    with out.open("a") as f, ProcessPoolExecutor(max_workers=workers) as pool:
        pending = {pool.submit(run_task, t) for t in todo}
        finished = 0
        while pending:
            complete, pending = wait(pending, return_when=FIRST_COMPLETED)
            for fut in complete:
                f.write(json.dumps(fut.result()) + "\n")
                finished += 1
            f.flush()
            if finished % max(1, len(todo) // 10) < len(complete):
                print(f"[{name}] {finished}/{len(todo)} after {time.time() - start:.0f} s", flush=True)
    print(f"[{name}] done in {time.time() - start:.0f} s", flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("groups", nargs="+", help=f"any of {', '.join([*GROUPS, *EXPERIMENTS])}, or 'all'")
    parser.add_argument("--workers", type=int, default=os.cpu_count())
    parser.add_argument("--seeds", type=lambda x: [int(v) for v in x.split(",")], default=None,
                        help="seeds to run instead of the defaults, e.g. 6,7,8,9,10")
    parser.add_argument("--results", type=Path, default=RESULTS, help="folder for the result files")
    parser.add_argument("--network", default=None, help="run the experiments on another network, e.g. two_road_long")
    args = parser.parse_args()
    names = []
    for g in args.groups:
        names += list(EXPERIMENTS) if g == "all" else GROUPS.get(g, [g])
    for name in names:
        run_experiment(name, args.workers, args.seeds, args.results, args.network)


if __name__ == "__main__":
    main()
