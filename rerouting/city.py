"""La Rochelle as one Markov chain: the lab's three rules on every street, and the theory they average to.

    python -m rerouting.city streets     # the streets, their lights and priorities (build/city/streets.npz)   [CPU]
    python -m rerouting.city demand      # trips and empty-city routes for every traffic level and seed      [CPU, SUMO]
    python -m rerouting.city freeflow    # each street's free-flow time, measured in SUMO on a nearly empty city [CPU, SUMO]
    python -m rerouting.city theory      # d* and p* for the whole city (results/city/theory.json)          [GPU]
    python -m rerouting.city chain       # the city chain on the test grid (results/city/chain.jsonl)        [GPU]
    python -m rerouting.city predict     # freeze every prediction (results/city/predictions.json)
    python -m rerouting.city sumo        # the blind SUMO runs (results/city/sumo.jsonl)                     [CPU, SUMO]
    python -m rerouting.city score       # how each prediction fared (results/city/verdicts.md)

The same three rules as the two-road lab (rerouting/markov.py), applied to all 12,524 streets:

1. **Every car ahead of you costs you time.** Entering a street, a car drives its free-flow time T plus
   7.5 m / speed limit for every car driving ahead of it in its lane, then joins the queue at the street's
   end. Every car queued ahead costs 3600 / C s: the street's end lets C cars per hour go, 1806 per lane
   per hour of green at a light, which follows its programme from the network (the lab's traffic light,
   measured in SUMO), and elsewhere one car per lane
   every 1 s + 7.5 m / speed limit (SUMO's reaction time and the lab's 7.5 m); an idle end lets the next
   car go at once.
   A street that must give way (a minor road, a roundabout entrance) lets a car go only in a second
   when no car from a main street went through the same junction.
2. **Cars enter one at a time.** A street admits cars until it is full (length x lanes / 7.5 m cars, at least
   one per lane; a street shorter than a car is part of its junction and never fills); when
   several cars want the same street, they go in random order. A car that cannot enter holds up the
   cars behind it (on a street with several lanes, only those heading the same way). A car stuck
   300 s moves on regardless (SUMO's teleport rule).
3. **The app shows every street's time averaged over the last 180 s, seen from inside** (free-flow time +
   cars driving + cars queued, as in rule 1). Every 60 s the app recomputes the fastest way from every
   street to every destination; app users take the next street on it at every junction. The others keep
   the fastest route through the empty city (the lab's drivers without information).

Averaging the chain gives the theory: every street follows the lab's road law, and two numbers answer
the question for the whole city,

* ``d*``: the traffic at which the app starts to save time (at least 15 s per trip, the lab's resolution);
* ``p*``: the share of app users after which more of them save less than 15 s per trip.

Heavy steps run on Jean Zay: jz/city_gpu.slurm (theory, chain, on one V100) and jz/python.slurm (SUMO).
The GPU steps only need numpy and torch.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import time
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
BUILD = ROOT / "build" / "city"
RESULTS = ROOT / "results" / "city"
PREDICTIONS = RESULTS / "predictions.json"

START, COHORT, END = int(6.5 * 3600), (7 * 3600, 9 * 3600), 11 * 3600   # as in rerouting/larochelle.py
PEAK = (7.5 * 3600, 8.5 * 3600)       # the busiest hour: the theory's steady traffic
SATURATION = 1806.0                   # cars per lane per hour of green: the lab's light, measured in SUMO
CAR_SPACE = 7.5                       # metres a car takes with its gap
REACTION = 1.0                        # seconds: SUMO's driver reaction time (tau), the gap a car keeps
STUCK = 300.0                         # seconds before a stuck car moves on (SUMO's teleport time)
WINDOW, PERIOD = 180, 60              # the app's averaging window and how often it recomputes routes (SUMO)
RESOLUTION = 15.0                     # seconds: the smallest gain the lab's rules count as real
LAMBDAS = [0.25, 0.5, 0.75, 1.0, 1.25, 1.5, 1.75, 2.0, 2.5, 3.0]   # traffic, x the calibrated morning
SHARES = [0.0, 0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 1.0]
TEST_SEEDS = [4, 5, 6]                # fresh trip draws, never used before
FREEFLOW = (0.1, [97, 98, 99])        # nearly empty city, for the free-flow times


# --------------------------------------------------------------------------- the streets (CPU)


def streets() -> Path:
    """Every street of the network: length, lanes, speed limit, green share, give-way, junction, and the
    turns allowed from it (build/city/streets.npz)."""
    import xml.etree.ElementTree as ET
    from rerouting import larochelle as L
    edges, lights, conns, offsets, car_lanes = {}, {}, [], {}, set()

    def for_cars(lane):
        allow, disallow = lane.get("allow"), lane.get("disallow")
        return ("passenger" in allow.split()) if allow else not (disallow and "passenger" in disallow.split())

    for _, el in ET.iterparse(L.network()):
        if el.tag == "edge" and el.get("function") != "internal":
            lanes = el.findall("lane")
            cars = [ln for ln in lanes if for_cars(ln)]
            for ln in cars:
                car_lanes.add(ln.get("id"))
            edges[el.get("id")] = (float(lanes[0].get("length")), max(1, len(cars)),
                                   max(float(ln.get("speed")) for ln in (cars or lanes)), el.get("to"))
        elif el.tag == "tlLogic":
            phases = [(float(p.get("duration")), p.get("state")) for p in el.findall("phase")]
            lights[el.get("id")] = phases
            offsets[el.get("id")] = float(el.get("offset", 0))
        elif el.tag == "connection" and not el.get("from").startswith(":"):
            conns.append((el.get("from"), el.get("to"), int(el.get("fromLane")), el.get("tl"),
                          int(el.get("linkIndex", -1)), el.get("state"), el.get("via"), int(el.get("toLane"))))
        if el.tag in ("edge", "tlLogic", "junction"):
            el.clear()
    ids = sorted(edges)
    index = {e: i for i, e in enumerate(ids)}
    nodes = sorted({v[3] for v in edges.values()})
    node_index = {n: i for i, n in enumerate(nodes)}

    def green(tl, k):
        phases = lights[tl]
        return sum(d for d, s in phases if s[k] in "GgOo") / sum(d for d, _ in phases)   # O, o: light off

    lane_green, minor_votes, arcs, via, conns_by, lane_links = {}, {}, set(), {}, {}, {}
    for a, b, lane, tl, k, state, v, to_lane in conns:
        if a not in index or b not in index or f"{a}_{lane}" not in car_lanes or f"{b}_{to_lane}" not in car_lanes:
            continue                                  # only turns cars may take (not bus or bike lanes)
        arcs.add((index[a], index[b]))
        conns_by.setdefault(a, []).append((a, b, lane, tl))
        if tl:
            lane_links.setdefault((a, lane), []).append((tl, k))
        g = green(tl, k) if tl else 1.0
        lane_green[(a, lane)] = max(lane_green.get((a, lane), 0.0), g)
        minor_votes.setdefault(a, []).append(0 if tl else int(state in "msw"))
        if v:
            via[v.rsplit("_", 1)[0]] = a          # internal street -> the street it leaves from
    n = len(ids)
    length = np.array([edges[e][0] for e in ids])
    lanes = np.array([edges[e][1] for e in ids])
    speed = np.array([edges[e][2] for e in ids])
    node = np.array([node_index[edges[e][3]] for e in ids])
    g = np.ones(n)
    for i, e in enumerate(ids):
        gs = [lane_green[(e, k)] for k in range(16) if (e, k) in lane_green]
        g[i] = float(np.mean(gs)) if gs else 1.0
    minor = np.array([np.mean(minor_votes.get(e, [0])) > 0.5 for e in ids])
    arcs = np.array(sorted(arcs), dtype=np.int64).reshape(-1, 2)
    BUILD.mkdir(parents=True, exist_ok=True)
    out = BUILD / "streets.npz"
    light = np.array([any(tl for a, _, _, tl, *_ in conns_by.get(e, [])) for e in ids])
    # every light street's programme: phase durations and the share of its lanes that may go in each phase
    schedule_rows = []
    for i, e in enumerate(ids):
        if not light[i]:
            continue
        tls = {tl for k in range(16) for tl, _ in lane_links.get((e, k), [])}
        tl = sorted(tls)[0]
        lanes_here = [k for k in range(16) if (e, k) in lane_links]
        per_phase = [sum(any(tl2 == tl and st[j] in "GgOo" for tl2, j in lane_links[(e, k)]) for k in lanes_here)
                     / len(lanes_here) for _, st in lights[tl]]
        schedule_rows.append((i, offsets.get(tl, 0.0), [d for d, _ in lights[tl]], per_phase))
    width = max(len(r[2]) for r in schedule_rows)
    light_street = np.array([r[0] for r in schedule_rows])
    light_offset = np.array([r[1] for r in schedule_rows])
    light_durations = np.array([r[2] + [0.0] * (width - len(r[2])) for r in schedule_rows])
    light_open = np.array([r[3] + [0.0] * (width - len(r[3])) for r in schedule_rows])
    np.savez_compressed(out, ids=np.array(ids), length=length, lanes=lanes, speed=speed, green=g, minor=minor, light=light,
                        light_street=light_street, light_offset=light_offset, light_durations=light_durations,
                        light_open=light_open,
                        node=node, arc_from=arcs[:, 0], arc_to=arcs[:, 1])
    (BUILD / "internal.json").write_text(json.dumps(via))
    print(f"{n} streets, {len(arcs)} turns, {int(minor.sum())} give-way, {int((g < 1).sum())} with a light")
    return out


# --------------------------------------------------------------------------- demand and SUMO runs (CPU)


def demand_path(lam: float, seed: int) -> Path:
    return BUILD / f"demand_l{lam:g}_s{seed}.json"


def _demand_case(args):
    lam, seed = args
    from rerouting import larochelle as L
    from rerouting import sumo as S
    path = demand_path(lam, seed)
    if path.exists():
        return str(path)
    commute, crossing = L.chosen_volume()
    net = L.load_net()
    trip_list = L.trips(L.zones(net), net, commute * lam, crossing * lam, seed)
    veh = L.free_routes(L.network(), trip_list, BUILD / "work" / f"l{lam:g}_s{seed}")
    path.write_text(json.dumps([[v.id, round(v.depart, 1), v.route, S.rerouting_rank(v.id, seed)] for v in veh]))
    return str(path)


def demand(workers: int) -> None:
    """Trips at every traffic level for the test seeds (and the nearly empty city), with their fastest
    route through the empty city (SUMO's duarouter)."""
    from concurrent.futures import ProcessPoolExecutor
    BUILD.mkdir(parents=True, exist_ok=True)
    cases = [(lam, s) for lam in LAMBDAS for s in TEST_SEEDS] + [(FREEFLOW[0], s) for s in FREEFLOW[1]]
    with ProcessPoolExecutor(workers) as pool:
        for p in pool.map(_demand_case, cases):
            print(p, flush=True)


def load_vehicles(lam: float, seed: int):
    from rerouting import sumo as S
    return [S.Vehicle(r[0], r[1], r[2]) for r in json.loads(demand_path(lam, seed).read_text())]


def _freeflow_case(seed):
    from rerouting import larochelle as L
    from rerouting import sumo as S
    work = BUILD / "freeflow" / f"s{seed}"
    work.mkdir(parents=True, exist_ok=True)
    (work / "measure.add.xml").write_text(
        f'<additional><edgeData id="m" file="{work / "edges.xml"}" begin="{START}" end="{END}" '
        f'withInternal="true" excludeEmpty="true"/></additional>')
    stats = S.simulate(S.Scenario(L.network(), load_vehicles(FREEFLOW[0], seed), share=0.0, end=END, seed=seed,
                                  teleport=STUCK, additional=(work / "measure.add.xml",), measure=COHORT))
    return seed, stats["journey"]


def freeflow(workers: int) -> None:
    """Every street's free-flow time, measured as the lab did (SUMO at very low traffic): the time to drive
    it plus the junction behind it. Streets no car used get length / speed x the median measured ratio."""
    import xml.etree.ElementTree as ET
    from concurrent.futures import ProcessPoolExecutor
    todo = [s for s in FREEFLOW[1] if not (BUILD / "freeflow" / f"s{s}" / "edges.xml").exists()]
    with ProcessPoolExecutor(workers) as pool:
        for r in pool.map(_freeflow_case, todo):
            print("free-flow run", r, flush=True)
    st = np.load(BUILD / "streets.npz")
    ids = list(st["ids"])
    index = {e: i for i, e in enumerate(ids)}
    via = json.loads((BUILD / "internal.json").read_text())
    time_sum, wait_sum, entered = np.zeros(len(ids)), np.zeros(len(ids)), np.zeros(len(ids))
    inner_sum, inner_n = np.zeros(len(ids)), np.zeros(len(ids))
    for seed in FREEFLOW[1]:
        for edge in ET.parse(BUILD / "freeflow" / f"s{seed}" / "edges.xml").getroot().iter("edge"):
            e, n, tt = edge.get("id"), float(edge.get("entered", 0)), edge.get("traveltime")
            if tt is None or n <= 0:
                continue
            if e in index:
                time_sum[index[e]] += float(tt) * n
                wait_sum[index[e]] += float(edge.get("waitingTime", 0))
                entered[index[e]] += n
            elif e in via and via[e] in index:
                inner_sum[index[via[e]]] += float(tt) * n
                inner_n[index[via[e]]] += n
    free = st["length"] / st["speed"]
    seen = entered > 0
    # the usual wait at the street's end (standing at a red light or a give-way line), and the rest: driving
    wait = np.where(seen, wait_sum / np.maximum(entered, 1), 0.0)
    T = np.where(seen, time_sum / np.maximum(entered, 1), free) - wait
    T = np.maximum(T + np.where(inner_n > 0, inner_sum / np.maximum(inner_n, 1), 0.0), 0.1)
    ratio = float(np.median(T[seen] / free[seen]))
    T = np.where(seen, T, free * ratio)
    RESULTS.mkdir(parents=True, exist_ok=True)
    np.save(BUILD / "free_time.npy", T)
    np.save(BUILD / "free_wait.npy", wait)
    (RESULTS / "free_flow.json").write_text(json.dumps(
        {"streets_measured": int(seen.sum()), "streets": len(ids), "median_driving_ratio_to_length_over_speed": ratio,
         "streets_with_a_usual_wait": int((wait > 0.5).sum())}))
    print(f"measured {seen.sum()} of {len(ids)} streets; median driving time / (length / speed) = {ratio:.2f}; "
          f"{(wait > 0.5).sum()} streets with a usual wait at their end")


def _sumo_case(args):
    lam, share, seed = args
    from rerouting import larochelle as L
    from rerouting import sumo as S
    t0 = time.time()
    stats = S.simulate(S.Scenario(L.network(), load_vehicles(lam, seed), share=share, end=END, seed=seed,
                                  teleport=STUCK, measure=COHORT))
    return {"lam": lam, "share": share, "seed": seed, **stats, "minutes": round((time.time() - t0) / 60, 1)}


def sumo_runs(workers: int) -> None:
    """The blind test: every case frozen in results/city/predictions.json, run in SUMO (results/city/sumo.jsonl)."""
    from concurrent.futures import ProcessPoolExecutor
    grid = json.loads(PREDICTIONS.read_text())["grid"]
    cases, tag = part_cases([(c["lam"], c["share"], s) for c in grid for s in TEST_SEEDS])
    done = done_in("sumo*.jsonl", ("lam", "share", "seed"))
    out = RESULTS / f"sumo{tag}.jsonl"
    with ProcessPoolExecutor(workers) as pool, out.open("a") as f:
        for r in pool.map(_sumo_case, [c for c in cases if c not in done]):
            f.write(json.dumps(r) + "\n")
            f.flush()
            print(r, flush=True)


# --------------------------------------------------------------------------- the city on the GPU


class City:
    """The streets as tensors, with the lab's constants turned into per-street numbers."""

    def __init__(self, device: str = "cuda"):
        import torch
        self.torch, self.device = torch, device
        st = np.load(BUILD / "streets.npz")
        self.ids = list(st["ids"])
        self.index = {e: i for i, e in enumerate(self.ids)}
        f = lambda a, dt=torch.float32: torch.as_tensor(np.asarray(a), dtype=dt, device=device)
        self.E = len(self.ids)
        self.T = f(np.load(BUILD / "free_time.npy"))                     # free-flow driving time (s)
        self.wait = f(np.load(BUILD / "free_wait.npy"))                  # usual wait at the end (red light, give way)
        self.lanes = f(st["lanes"])
        self.per_car = f(CAR_SPACE / st["speed"] / st["lanes"])           # rule 1: s per car driving ahead in the lane
        # rule 2: a street holds length x lanes / 7.5 m cars, at least one per lane; one shorter than a car is part
        # of its junction and never fills (a car there already reaches beyond it)
        holds = np.maximum(st["lanes"], np.floor(st["length"] * st["lanes"] / CAR_SPACE))
        self.holds = f(np.where(st["length"] < CAR_SPACE, 1e9, holds))
        # cars per hour at the street's end: the lab's light (1806 per lane per hour of green), or, without a
        # light, one car per SUMO reaction time (1 s) plus the time to drive 7.5 m
        free_lane = 3600.0 / (REACTION + CAR_SPACE / st["speed"])
        self.C = f(st["lanes"] * np.where(st["light"], SATURATION * st["green"], free_lane))
        self.minor = f(st["minor"], torch.bool)
        # lights: the share of each light street's lanes that may go, second by second over its cycle
        self.light_street = f(st["light_street"], torch.long)
        rows = []
        for off, durs, opens in zip(st["light_offset"], st["light_durations"], st["light_open"]):
            per_s = np.concatenate([np.full(int(round(d)), o) for d, o in zip(durs, opens) if d > 0])
            rows.append(np.roll(per_s, int(round(off))))
        self.cycle = f([len(r) for r in rows], torch.long)
        width = max(len(r) for r in rows)
        self.light_open = f(np.stack([np.pad(r, (0, width - len(r))) for r in rows]))
        self.is_light = torch.zeros(self.E, dtype=torch.bool, device=device)
        self.is_light[self.light_street] = True
        self.light_rate = f(SATURATION / 3600.0 * st["lanes"][st["light_street"]])   # cars/s while all lanes go
        self.node = f(st["node"], torch.long)
        self.nodes = int(st["node"].max()) + 1
        self.arc_from = f(st["arc_from"], torch.long)
        self.arc_to = f(st["arc_to"], torch.long)

    # ---- fastest ways to a set of destinations (Bellman-Ford over the streets and their allowed turns)

    def tables(self, cost, dests, chunk: int = 512):
        """``cost``: [R, E] seconds per street; ``dests``: [D] street ids. Returns ``dist`` [R, D, E] (time from
        entering a street to the end of the destination) and ``nxt`` [R, D, E] (the next street, -1 at the
        destination or where it cannot be reached)."""
        torch = self.torch
        R, E, D = cost.shape[0], self.E, len(dests)
        dist = torch.full((R, D, E), math.inf, device=self.device)
        nxt = torch.full((R, D, E), -1, dtype=torch.long, device=self.device)
        for a in range(0, D, chunk):
            b = min(D, a + chunk)
            d = dests[a:b]
            cur = torch.full((R, b - a, E), math.inf, device=self.device)
            rows = torch.arange(b - a, device=self.device)
            cur[:, rows, d] = cost[:, d]
            base = cost[:, None, self.arc_from]
            for _ in range(E):
                cand = base + cur[:, :, self.arc_to]
                new = cur.clone().index_reduce_(2, self.arc_from, cand, "amin", include_self=True)
                if torch.equal(new, cur):
                    break
                cur = new
            cand = base + cur[:, :, self.arc_to]
            best = cur[:, :, self.arc_from]
            arc_ids = torch.arange(len(self.arc_from), device=self.device).expand_as(cand)
            pick = torch.where(torch.isfinite(cand) & (cand <= best * (1 + 1e-6) + 1e-4), arc_ids,
                               torch.full_like(arc_ids, len(self.arc_from)))
            first = torch.full((R, b - a, E), len(self.arc_from), dtype=torch.long, device=self.device)
            first.index_reduce_(2, self.arc_from, pick, "amin", include_self=True)
            ok = first < len(self.arc_from)
            step = torch.where(ok, self.arc_to[first.clamp(max=len(self.arc_from) - 1)], -1)
            step[:, rows, d] = -1
            dist[:, a:b], nxt[:, a:b] = cur, step
        return dist, nxt


# --------------------------------------------------------------------------- the theory (the chain averaged)


def road_time(city: City, x, C):
    """The lab's road law for every street over a one-hour peak, in seconds, at x cars per hour:
    T (1 + drive delay) + the queue. Below capacity the queue is the lab's 1800 x / (C (C - x)); above it
    the queue grows by x - C cars per hour (both limits of the same formula, Akcelik's with k = 1/2)."""
    torch = city.torch
    r = x / C
    queue = 900.0 * ((r - 1) + torch.sqrt((r - 1) ** 2 + 4 * r / C))
    return city.T + city.T * x / 3600.0 * city.per_car + city.wait + queue


def give_way(city: City, x):
    """Capacity of every street's end: a give-way street only gets the seconds in which no car from a main
    street passes its junction, a share exp(-q / 3600) at q main-street cars per hour."""
    torch = city.torch
    main = torch.zeros(city.nodes, device=city.device).index_add_(0, city.node, torch.where(city.minor, 0.0, x))
    return torch.where(city.minor, city.C * torch.exp(-main[city.node] / 3600.0), city.C).clamp(min=1.0)


class Trips:
    """Peak-hour trips of one traffic level and seed: origin, destination and the empty-city route."""

    def __init__(self, city: City, lam: float, seed: int, window=PEAK):
        torch = city.torch
        rows = [r for r in json.loads(demand_path(lam, seed).read_text()) if window[0] <= r[1] < window[1]]
        hours = (window[1] - window[0]) / 3600.0
        self.n = len(rows)
        self.weight = 1.0 / hours                                   # each trip is one car per hour of the window
        routes = [[city.index[e] for e in r[2].split()] for r in rows]
        self.orig = torch.tensor([r[0] for r in routes], device=city.device)
        dest = [r[-1] for r in routes]
        self.dests = torch.tensor(sorted(set(dest)), device=city.device)
        slot = {int(d): i for i, d in enumerate(self.dests.tolist())}
        self.slot = torch.tensor([slot[d] for d in dest], device=city.device)
        i = np.repeat(np.arange(len(routes)), [len(r) for r in routes])
        j = np.concatenate(routes)
        self.route = torch.sparse_coo_tensor(torch.as_tensor(np.stack([i, j])), torch.ones(len(j)),
                                             (self.n, city.E)).coalesce().to(city.device)


def load_by_tree(city: City, trips: Trips, nxt, amount):
    """Street flows (cars/h) when every trip with ``amount`` [n] follows the fastest-way tree ``nxt`` [D, E]."""
    torch = city.torch
    D, E = nxt.shape
    f = torch.zeros(D * E, device=city.device).index_add_(0, trips.slot * E + trips.orig, amount)
    flat = nxt.reshape(-1)
    base = torch.arange(D, device=city.device).repeat_interleave(E) * E
    load = torch.zeros(E, device=city.device)
    for _ in range(E):
        if f.sum() <= 1e-9:
            break
        load += f.view(D, E).sum(0)
        go = flat >= 0
        f = torch.zeros_like(f).index_add_(0, (base + flat.clamp(min=0))[go], f[go])
    return load


def assignment(city: City, trips: Trips, share: float, iterations: int = 100, gap: float = 1e-3) -> dict:
    """Mean trip time when a share ``share`` of every trip's drivers take the fastest way on the current
    traffic and the rest keep the empty-city route: the steady state of the chain averaged (Frank-Wolfe on
    the road law; the give-way capacities are updated at every step)."""
    torch = city.torch
    w = trips.weight
    fixed = torch.sparse.mm(trips.route.t(), torch.full((trips.n, 1), w * (1 - share), device=city.device)).squeeze(1)
    free_part = torch.sparse.mm(trips.route.t(), torch.full((trips.n, 1), w * share, device=city.device)).squeeze(1)
    x = fixed + free_part                                            # everyone on the empty-city route
    amount = torch.full((trips.n,), w * share, device=city.device)
    rel = float("nan")
    for k in range(iterations if share > 0 else 0):
        C = give_way(city, x)
        t = road_time(city, x, C)
        _, nxt = city.tables(t[None], trips.dests)
        y = fixed + load_by_tree(city, trips, nxt[0], amount)
        rel = float(((x - y) * t).sum() / (x * t).sum())
        if rel < gap:
            break
        lo, hi = 0.0, 1.0                                             # step: where the cost along y - x stops falling
        for _ in range(30):
            a = (lo + hi) / 2
            z = x + a * (y - x)
            lo, hi = (a, hi) if float(((y - x) * road_time(city, z, give_way(city, z))).sum()) < 0 else (lo, a)
        x = x + (lo + hi) / 2 * (y - x)
    t = road_time(city, x, give_way(city, x))
    # every trip's own time: its empty-city route for the drivers who keep it, the fastest way for the others
    kept = torch.sparse.mm(trips.route, t[:, None]).squeeze(1)
    dist, _ = city.tables(t[None], trips.dests)
    best = dist[0, trips.slot, trips.orig]
    reach = torch.isfinite(best)
    per_trip = (1 - share) * kept + share * torch.where(reach, best, kept)
    return {"mean_trip_s": float(per_trip.mean()), "mean_trip_flows_s": float((x * t).sum() / (w * trips.n)),
            "empty_city_s": float(torch.sparse.mm(trips.route, (city.T + city.wait)[:, None]).mean()),
            "unreachable": float(1 - reach.float().mean()), "gap": rel, "iterations": k + 1 if share > 0 else 0,
            "over_capacity_streets": int((x > give_way(city, x)).sum())}


# --------------------------------------------------------------------------- the city chain


def chain(lam: float, seed: int, share: float, replicas: int = 2, city: City | None = None, probe=(),
          per_street: bool = False) -> dict:
    """Runs ``replicas`` copies of the city chain side by side, 6:30-11:00 in one-second steps, on the trips of
    one traffic level and seed. Returns the same statistics as SUMO on the 7:00-9:00 cohort."""
    import torch
    city = city or City()
    dev = city.device
    gen = torch.Generator(device=dev).manual_seed(int(lam * 1000) * 100 + seed * 10 + int(share * 10))
    rows = json.loads(demand_path(lam, seed).read_text())
    N, R, E = len(rows), replicas, city.E
    routes = [[city.index[e] for e in r[2].split()] for r in rows]
    L = max(len(r) for r in routes)
    padded = np.full((N, L), -1, dtype=np.int64)
    for i, r in enumerate(routes):
        padded[i, :len(r)] = r
    route = torch.as_tensor(padded, device=dev)
    n_edges = torch.tensor([len(r) for r in routes], device=dev)
    free_trip = torch.where(route >= 0, (city.T + city.wait)[route.clamp(min=0)], 0.0).sum(1)   # empty-city trip time
    # every car of every replica: c = replica * N + trip
    car = torch.arange(N, device=dev).repeat(R)
    rep = torch.arange(R, device=dev).repeat_interleave(N)
    depart = torch.tensor([r[1] for r in rows], device=dev)[car]
    app = torch.tensor([r[3] < share for r in rows], device=dev)[car]
    last = (n_edges - 1)[car]
    dest = route[car, last]
    status = torch.zeros(R * N, dtype=torch.int8, device=dev)   # 0 not due, 1 waiting to enter, 2 driving, 3 queued, 4 arrived
    cur = route[car, 0].clone()
    pos = torch.zeros(R * N, dtype=torch.long, device=dev)
    ready = torch.zeros(R * N, device=dev)
    ticket = torch.zeros(R * N, dtype=torch.long, device=dev)
    joined = torch.full((R * N,), -1, dtype=torch.long, device=dev)     # second the car joined its queue
    arrive = torch.full((R * N,), math.nan, device=dev)
    # every street of every replica: s = replica * E + street
    T, per_car, holds, usual = city.T.repeat(R), city.per_car.repeat(R), city.holds.repeat(R), city.wait.repeat(R)
    lights = (city.light_street[None] + E * torch.arange(R, device=dev)[:, None]).reshape(-1)
    light_rate, cycle = city.light_rate.repeat(R), city.cycle.repeat(R)
    light_rows = torch.arange(len(city.light_street), device=dev).repeat(R)
    rate, minor, one_lane = (city.C / 3600.0).repeat(R), city.minor.repeat(R), (city.lanes == 1).repeat(R)
    node = (city.node[None] + city.nodes * torch.arange(R, device=dev)[:, None]).reshape(-1)
    m = torch.zeros(R * E, device=dev)                  # cars driving
    q = torch.zeros(R * E, device=dev)                  # cars queued at the end
    issued = torch.zeros(R * E, dtype=torch.long, device=dev)
    credit = torch.zeros(R * E, device=dev)
    stuck_since = torch.full((R * E,), math.inf, device=dev)
    teleports = torch.zeros(R, device=dev)
    waited = torch.zeros(R * E, device=dev)            # seconds cars spent queued at each street's end
    entered = torch.zeros(R * E, device=dev)           # cars that entered each street
    big = torch.iinfo(torch.long).max
    # the app: every street's time seen from inside, averaged over the last 180 s, and its fastest ways
    use_app = bool(app.any())
    ring = torch.zeros((WINDOW, R, E), device=dev)
    shown = torch.zeros((R, E), device=dev)
    slot = torch.full((E,), -1, dtype=torch.long, device=dev)
    ways = None
    if use_app:
        every_dest = torch.unique(dest[app])
        _, empty_ways = city.tables((city.T + city.wait)[None], every_dest)          # fallback: the empty city's ways
        empty_ways, empty_slot = empty_ways[0], torch.full((E,), -1, dtype=torch.long, device=dev)
        empty_slot[every_dest] = torch.arange(len(every_dest), device=dev)

    def rank_within(keys):
        """A random order among entries that share a key: 0, 1, 2 ... within every key."""
        n = keys.numel()
        if n == 0:
            return keys
        perm = torch.randperm(n, device=dev, generator=gen)
        perm = perm[torch.argsort(keys[perm], stable=True)]
        sk = keys[perm]
        at = torch.arange(n, device=dev)
        start = torch.ones(n, dtype=torch.bool, device=dev)
        start[1:] = sk[1:] != sk[:-1]
        first = torch.where(start, at, 0).cummax(0).values
        out = torch.empty_like(at)
        out[perm] = at - first
        return out

    def next_street(c, street=None, k=None):
        """The street each car of ``c`` wants after ``street`` (its current one), the ``k``-th of its route:
        the route's next street, or the app's fastest way."""
        street = cur[c] if street is None else street
        k = pos[c] if k is None else k
        fixed = route[car[c], (k + 1).clamp(max=L - 1)]
        if ways is None:
            return fixed
        sl = slot[dest[c]]
        live = torch.where(sl >= 0, ways[rep[c], sl.clamp(min=0), street], -1)
        live = torch.where(live >= 0, live, empty_ways[empty_slot[dest[c]].clamp(min=0), street])
        return torch.where(app[c] & (live >= 0), live, fixed)

    def step_out(c, t, force):
        """Cars ``c`` (queue heads, or cars waiting to enter their first street) try to enter the street they
        want; returns the streets they left (for queued cars)."""
        waiting = status[c] == 1
        target = torch.where(waiting, cur[c], next_street(c))
        skipped = torch.zeros(len(c), dtype=torch.long, device=dev)
        extra = torch.zeros(len(c), device=dev)
        if bool(force.any()):
            # SUMO's teleport: a stuck car reappears on the first street of its way that has room
            f = force.nonzero().squeeze(1)
            tg, k = target[f], pos[c[f]] + 1
            for _ in range(50):
                full = (holds - m - q)[rep[c[f]] * E + tg] < 1
                there = torch.where(app[c[f]], tg == dest[c[f]], k >= last[c[f]])
                on = full & ~there
                if not bool(on.any()):
                    break
                extra[f] += torch.where(on, city.T[tg] + city.wait[tg], 0.0)
                tg = torch.where(on, next_street(c[f], tg, k), tg)
                k = k + on.long()
            target[f] = tg
            skipped[f] = k - pos[c[f]] - 1
        ts = rep[c] * E + target
        ok = (rank_within(ts) < (holds - m - q)[ts]) | force
        src = rep[c] * E + cur[c]
        blocked = ~ok & ~waiting
        if bool(blocked.any()):
            # a car that cannot enter holds up the cars behind it: all of them on a one-lane street, those
            # heading the same way on a wider one
            lane = torch.where(one_lane[src], src * (E + 1), src * (E + 1) + 1 + target)
            keys, inv = torch.unique(lane[blocked], return_inverse=True)
            low = torch.full((len(keys),), big, dtype=torch.long, device=dev).scatter_reduce_(0, inv, ticket[c][blocked], "amin")
            at = torch.searchsorted(keys, lane).clamp(max=len(keys) - 1)
            ok &= ~((keys[at] == lane) & (ticket[c] > low[at]) & ~waiting)
        go, tgt, skipped, extra = c[ok], target[ok], skipped[ok], extra[ok]
        gs = rep[go] * E + tgt
        ahead = m[gs] + rank_within(gs).float()
        was_queued = status[go] == 3
        left = (rep[go] * E + cur[go])[was_queued]
        q.index_add_(0, left, torch.full((len(left),), -1.0, device=dev))
        credit.index_add_(0, left, torch.full((len(left),), -1.0, device=dev))
        m.index_add_(0, gs, torch.ones(len(gs), device=dev))
        entered.index_add_(0, gs, torch.ones(len(gs), device=dev))
        src_go = rep[go] * E + cur[go]
        start = torch.where(was_queued & (ready[go] > t - 1), ready[go], float(t))
        waited.index_add_(0, src_go[was_queued], (start - ready[go])[was_queued])
        ready[go] = start + extra + T[gs] + per_car[gs] * ahead
        pos[go] += was_queued.long() + skipped
        cur[go] = tgt
        status[go] = 2
        return left

    def heads(t, streets, with_waiting):
        """Queued cars at the front of the streets in ``streets`` that have credit try to leave; the cars waiting
        to enter the city compete with them for room."""
        qd = (status == 3).nonzero().squeeze(1)
        s = rep[qd] * E + cur[qd]
        low = torch.full((R * E,), big, dtype=torch.long, device=dev).scatter_reduce_(0, s, ticket[qd], "amin")
        c = qd[streets[s] & (ticket[qd] < low[s] + torch.floor(credit[s]).long())]
        s = rep[c] * E + cur[c]
        force = (ticket[c] == low[s]) & (t - stuck_since[s] >= STUCK)
        teleports.index_add_(0, rep[c][force], torch.ones(int(force.sum()), device=dev))
        if with_waiting:
            w = (status == 1).nonzero().squeeze(1)
            c, force = torch.cat([c, w]), torch.cat([force, torch.zeros(len(w), dtype=torch.bool, device=dev)])
        return step_out(c, t, force) if len(c) else c

    t, snaps = START, []
    for t in range(START, END):
        status[(status == 0) & (depart <= t)] = 1
        done = ((status == 2) & (ready <= t)).nonzero().squeeze(1)
        if len(done):
            s = rep[done] * E + cur[done]
            m.index_add_(0, s, torch.full((len(done),), -1.0, device=dev))
            end = torch.where(app[done], cur[done] == dest[done], pos[done] == last[done])
            status[done[end]] = 4
            arrive[done[end]] = ready[done[end]]
            qd, sq = done[~end], s[~end]
            ticket[qd] = issued[sq] + rank_within(sq)
            issued.index_add_(0, sq, torch.ones(len(sq), dtype=torch.long, device=dev))
            q.index_add_(0, sq, torch.ones(len(sq), device=dev))
            status[qd] = 3
            joined[qd] = t
        # every street's end lets cars go at its pace; an idle end lets the next car go at once (the lab's
        # detour gate): the measured free-flow time already holds the time to cross the junction
        # lights follow their programme: the street's end passes cars only while its lanes are green
        rate_now = rate.clone()
        green = city.light_open[light_rows, t % cycle]
        rate_now[lights] = light_rate * green
        open_now = rate_now > 0
        grow = torch.minimum(credit + rate_now, rate_now + 1.0)
        credit.copy_(torch.where(minor, credit, torch.where(open_now, grow, credit.clamp(max=1.0))))
        left_main = heads(t, ~minor & open_now, True)
        # give-way streets: only in a second when no car from a main street passed their junction
        passed = torch.zeros(R * city.nodes, dtype=torch.bool, device=dev)
        passed[node[left_main]] = True
        free = minor & ~passed[node]
        grow = torch.minimum(credit + rate_now, rate_now + 1.0)
        credit.copy_(torch.where(minor & free, grow, credit))
        left_minor = heads(t, free & open_now, False)
        # a street whose front car has credit but cannot leave starts its stuck clock (again, if a car just left)
        stuck = q > 0          # SUMO's clock: cars queued and none has left since
        stuck_since.copy_(torch.where(stuck, torch.clamp(stuck_since, max=float(t)), math.inf))
        moved = torch.cat([left_main, left_minor])
        stuck_since[moved] = torch.where(stuck[moved], float(t), math.inf)
        if use_app:
            now = (T + usual + per_car * m + q / rate).view(R, E)
            shown += (now - ring[t % WINDOW]) / WINDOW
            ring[t % WINDOW] = now
            if (t - START) % PERIOD == 0:
                on_road = app & (status >= 1) & (status <= 3)
                if bool(on_road.any()):
                    active = torch.unique(dest[on_road])
                    slot.fill_(-1)
                    slot[active] = torch.arange(len(active), device=dev)
                    warm = min(t - START + 1, WINDOW)
                    _, ways = city.tables(shown * WINDOW / warm, active)
        if t in probe:
            top = torch.topk(q[:E], 15)
            snaps.append({"t": t, "queued": float(q[:E].sum()), "driving": float(m[:E].sum()),
                          "stuck_streets": int(((q[:E] > 0) & (t - stuck_since[:E] > 60)).sum()),
                          "stuck_minor": int(((q[:E] > 0) & (t - stuck_since[:E] > 60) & city.minor).sum()),
                          "full_streets": int((m[:E] + q[:E] >= holds[:E]).sum()),
                          "top": [{"id": city.ids[i], "q": float(q[i]), "holds": float(holds[i]),
                                   "C": float(city.C[i]), "minor": bool(city.minor[i]), "lanes": float(city.lanes[i]),
                                   "credit": float(credit[i]), "stuck_s": float(t - stuck_since[i])}
                                  for i in top.indices.tolist()]})
        if t % 300 == 0 and t > COHORT[1] and bool((status == 4).all()):
            break
    cohort = (depart >= COHORT[0]) & (depart < COHORT[1])
    trip = torch.where(torch.isnan(arrive), END - depart, arrive - depart)
    count = torch.zeros(R, device=dev).index_add_(0, rep[cohort], torch.ones(int(cohort.sum()), device=dev))
    per_rep = torch.zeros(R, device=dev).index_add_(0, rep[cohort], trip[cohort]) / count
    mean = lambda mask: float(trip[mask].mean()) if bool(mask.any()) else math.nan
    return {"lam": lam, "seed": seed, "share": share, "replicas": R, "journey": float(per_rep.mean()),
            "journey_by_replica": per_rep.tolist(), "free_flow": float(free_trip[car[cohort]].mean()),
            "completed": float((~torch.isnan(arrive[cohort])).float().mean()),
            "journey_app": mean(cohort & app), "journey_others": mean(cohort & ~app),
            "teleports": float(teleports.mean()), "trips_in_cohort": int(cohort.sum()) // R,
            "finished_at_s": t, **({"probe": snaps} if probe else {}),
            **({"per_street": {"waited": waited.view(R, E).mean(0).tolist(),
                               "entered": entered.view(R, E).mean(0).tolist()}} if per_street else {}),
            "waited_by_kind": {k: float(waited.view(R, E)[:, mask].sum() / R / max(1, int(cohort.sum()) // R))
                               for k, mask in (("light", city.is_light), ("give_way", city.minor & ~city.is_light),
                                               ("other", ~city.minor & ~city.is_light))},
            "waited_top": [{"id": city.ids[i], "s_per_replica": float(waited.view(R, E)[:, i].mean()),
                            "C": float(city.C[i]), "minor": bool(city.minor[i]), "holds": float(city.holds[i]),
                            "lanes": float(city.lanes[i])}
                           for i in torch.topk(waited.view(R, E).mean(0), 12).indices.tolist()]}


# --------------------------------------------------------------------------- the pre-registered test

# every case run in SUMO: the traffic sweep with nobody / everybody on the app, and the share sweep at
# today's traffic and at twice it
GRID = [{"lam": lam, "share": p} for lam in (0.25, 0.5, 0.75, 1.0, 1.25, 1.5, 1.75, 2.0) for p in (0.0, 1.0)] + \
       [{"lam": lam, "share": p} for lam in (1.0, 2.0) for p in (0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9)]
RULES = {"time": "within max(15 s, 25% of the predicted delay above free flow)",
         "gain": "within max(15 s, 30% of the predicted gain)",
         "d_star": "SUMO's d* (where its gain reaches 15 s, interpolated) within 25% of the predicted d*",
         "p_star": "SUMO's p* (smallest share after which it stays within 15 s of everyone on the app) within 0.1"}
CLAIMS = {"K1": "chain: trip time against traffic, nobody and everybody on the app",
          "K2": "chain: the app's gain against traffic",
          "K3": "chain: trip time against the share of app users",
          "K4": "theory: the app's gain against traffic",
          "K5": "theory: d*, the traffic at which the app starts to help",
          "K6": "theory: p*, the share of app users after which more of them add little"}
ACCEPT = "a statement holds if at least 80% of its cases pass"


def part_cases(cases: list) -> tuple[list, str]:
    """PART=k/n keeps every n-th case from the k-th (one GPU or SUMO job each)."""
    part = os.environ.get("PART")
    if not part:
        return cases, ""
    k, n = map(int, part.split("/"))
    return cases[k::n], f"_part{k}"


def done_in(pattern: str, keys) -> set:
    out = set()
    for path in RESULTS.glob(pattern):
        for line in path.read_text().splitlines():
            r = json.loads(line)
            out.add(tuple(r[k] for k in keys))
    return out


def theory_runs() -> None:
    """The road-law assignment for every traffic level, seed and share (results/city/theory_cases*.jsonl)."""
    city = City()
    cases, tag = part_cases([(lam, s) for lam in LAMBDAS for s in TEST_SEEDS])
    done = done_in("theory_cases*.jsonl", ("lam", "seed", "share"))
    RESULTS.mkdir(parents=True, exist_ok=True)
    with (RESULTS / f"theory_cases{tag}.jsonl").open("a") as f:
        for lam, seed in cases:
            trips = Trips(city, lam, seed)
            for p in SHARES:
                if (lam, seed, p) in done:
                    continue
                t0 = time.time()
                r = {"lam": lam, "seed": seed, "share": p, "trips_per_hour": trips.n * trips.weight,
                     **assignment(city, trips, p), "seconds": round(time.time() - t0, 1)}
                f.write(json.dumps(r) + "\n")
                f.flush()
                print(r, flush=True)


def chain_runs() -> None:
    """The city chain on every test case and seed (results/city/chain_cases*.jsonl)."""
    city = City()
    cases, tag = part_cases([(c["lam"], c["share"], s) for c in GRID for s in TEST_SEEDS])
    done = done_in("chain_cases*.jsonl", ("lam", "share", "seed"))
    RESULTS.mkdir(parents=True, exist_ok=True)
    with (RESULTS / f"chain_cases{tag}.jsonl").open("a") as f:
        for lam, p, seed in cases:
            if (lam, p, seed) in done:
                continue
            t0 = time.time()
            r = {**chain(lam, seed, p, city=city), "seconds": round(time.time() - t0, 1)}
            f.write(json.dumps(r) + "\n")
            f.flush()
            print(r, flush=True)


def rows(pattern: str) -> list[dict]:
    return [json.loads(line) for path in sorted(RESULTS.glob(pattern)) for line in path.read_text().splitlines()]


def by_case(rs: list[dict], key: str = "journey") -> dict:
    """Mean over seeds of ``key`` for every (traffic, share)."""
    out: dict = {}
    for r in rs:
        out.setdefault((r["lam"], r["share"]), []).append(r[key])
    return {k: float(np.mean(v)) for k, v in out.items()}


def crossing(gains: dict) -> float | None:
    """Traffic at which a gain first reaches 15 s, interpolated between grid levels."""
    lams = sorted(gains)
    for a, b in zip(lams, lams[1:]):
        if gains[a] < RESOLUTION <= gains[b]:
            return a + (RESOLUTION - gains[a]) / (gains[b] - gains[a]) * (b - a)
    return None


def share_star(times: dict, shares) -> float:
    """Smallest share after which the trip time stays within 15 s of everyone on the app."""
    full = times[1.0]
    return next(p for p in shares if all(times[q] - full < RESOLUTION for q in shares if q >= p))


def predict() -> None:
    """Freezes every prediction before any SUMO test run (results/city/predictions.json, never overwritten)."""
    if PREDICTIONS.exists():
        raise SystemExit(f"{PREDICTIONS} exists: predictions are frozen")
    th, ch = rows("theory_cases*.jsonl"), rows("chain_cases*.jsonl")
    theory_t, chain_t = by_case(th, "mean_trip_s"), by_case(ch)
    chain_free = by_case(ch, "free_flow")
    lams = sorted({lam for lam, _ in theory_t})
    theory_gain = {lam: theory_t[(lam, 0.0)] - theory_t[(lam, 1.0)] for lam in lams}
    missing = [c for c in GRID if (c["lam"], c["share"]) not in chain_t]
    if missing:
        raise SystemExit(f"chain cases missing: {missing}")
    p_star = {lam: share_star({p: theory_t[(lam, p)] for p in SHARES}, SHARES) for lam in lams}
    rate = {lam: float(np.mean([r["trips_per_hour"] for r in th if r["lam"] == lam])) for lam in lams}
    d = crossing(theory_gain)
    out = {"made": time.strftime("%Y-%m-%d %H:%M:%S"), "seeds": TEST_SEEDS, "grid": GRID, "rules": RULES,
           "claims": CLAIMS, "accept": ACCEPT,
           "theory": {"gain_s": {str(k): v for k, v in theory_gain.items()},
                      "mean_trip_s": {f"{k[0]}|{k[1]}": v for k, v in theory_t.items()},
                      "trips_per_hour": {str(k): v for k, v in rate.items()},
                      "d_star_lambda": d,
                      "d_star_trips_per_hour": float(np.interp(d, lams, [rate[x] for x in lams])) if d else None,
                      "p_star": {str(k): v for k, v in p_star.items()}},
           "chain": {f"{c['lam']}|{c['share']}": {"journey": chain_t[(c["lam"], c["share"])],
                                                   "free_flow": chain_free[(c["lam"], c["share"])]} for c in GRID}}
    PREDICTIONS.write_text(json.dumps(out, indent=1))
    print(json.dumps(out["theory"], indent=1))


def score() -> None:
    """Every frozen prediction against the blind SUMO runs (results/city/verdicts.md and scores.json)."""
    pred = json.loads(PREDICTIONS.read_text())
    sumo = by_case(rows("sumo*.jsonl"))
    chain = {tuple(map(float, k.split("|"))): v for k, v in pred["chain"].items()}
    cases = []

    def case(claim, label, rule, predicted, measured, scale=None):
        if rule == "time":
            tol = max(RESOLUTION, 0.25 * max(0.0, predicted - scale))
        elif rule == "gain":
            tol = max(RESOLUTION, 0.3 * abs(predicted))
        elif rule == "d_star":
            tol = 0.25 * predicted
        else:
            tol = 0.1 + 1e-9
        ok = measured is not None and predicted is not None and abs(measured - predicted) <= tol
        cases.append({"claim": claim, "label": label, "rule": rule, "predicted": predicted, "measured": measured,
                      "tolerance": tol, "pass": bool(ok)})

    lams = sorted({lam for lam, p in chain if p in (0.0, 1.0) and (lam, 1.0 - p) in chain})
    for lam in lams:
        for p in (0.0, 1.0):
            case("K1", f"traffic x{lam:g}, {p:.0%} on the app", "time", chain[(lam, p)]["journey"],
                 sumo.get((lam, p)), chain[(lam, p)]["free_flow"])
        g_sumo = sumo[(lam, 0.0)] - sumo[(lam, 1.0)] if (lam, 0.0) in sumo and (lam, 1.0) in sumo else None
        case("K2", f"traffic x{lam:g}", "gain", chain[(lam, 0.0)]["journey"] - chain[(lam, 1.0)]["journey"], g_sumo)
        case("K4", f"traffic x{lam:g}", "gain", pred["theory"]["gain_s"][str(lam)], g_sumo)
    for lam in sorted({lam for lam, p in chain if 0 < p < 1}):
        for p in [0.0] + [q for (l2, q) in sorted(chain) if l2 == lam and 0 < q < 1] + [1.0]:
            case("K3", f"traffic x{lam:g}, {p:.0%} on the app", "time", chain[(lam, p)]["journey"],
                 sumo.get((lam, p)), chain[(lam, p)]["free_flow"])
        shares = sorted(q for (l2, q) in sumo if l2 == lam)
        measured = share_star({q: sumo[(lam, q)] for q in shares}, shares) if 1.0 in shares else None
        case("K6", f"p* at traffic x{lam:g}", "p_star", pred["theory"]["p_star"][str(lam)], measured)
    sumo_gain = {lam: sumo[(lam, 0.0)] - sumo[(lam, 1.0)] for lam in lams if (lam, 0.0) in sumo and (lam, 1.0) in sumo}
    case("K5", "d* (x the calibrated morning traffic)", "d_star", pred["theory"]["d_star_lambda"], crossing(sumo_gain))
    verdicts = {}
    for k, text in CLAIMS.items():
        cs = [c for c in cases if c["claim"] == k]
        n = sum(c["pass"] for c in cs)
        verdicts[k] = {"claim": text, "passed": n, "cases": len(cs), "holds": bool(cs) and n >= 0.8 * len(cs)}
    (RESULTS / "scores.json").write_text(json.dumps({"verdicts": verdicts, "cases": cases}, indent=1))
    lines = ["# La Rochelle: the city chain and its theory against blind SUMO runs", "",
             f"Seeds {pred['seeds']}, predictions frozen {pred['made']}. {ACCEPT}.", "",
             "| statement | cases passed | verdict |", "|---|---|---|"]
    lines += [f"| {k}: {v['claim']} | {v['passed']}/{v['cases']} | {'holds' if v['holds'] else 'fails'} |"
              for k, v in verdicts.items()]
    lines += ["", "| statement | case | predicted | SUMO | tolerance | pass |", "|---|---|---|---|---|---|"]
    fmt = lambda x: "n/a" if x is None else f"{x:.3g}" if isinstance(x, float) else str(x)
    lines += [f"| {c['claim']} | {c['label']} | {fmt(c['predicted'])} | {fmt(c['measured'])} | {fmt(c['tolerance'])} "
              f"| {'yes' if c['pass'] else 'no'} |" for c in cases]
    (RESULTS / "verdicts.md").write_text("\n".join(lines) + "\n")
    print("\n".join(lines[:12]))


def empty_routes() -> None:
    """Every trip's fastest route through the empty city, on the street times measured in SUMO (junctions
    included), for every traffic level and test seed. SUMO's route finder uses length / speed and ignores the
    junctions, so its routes are not the fastest ones; drivers without the app now use these, in the chain,
    the theory and SUMO alike. The file keeps SUMO's route as a fifth column."""
    import torch
    city = City()
    for lam in LAMBDAS:
        for seed in TEST_SEEDS:
            path = demand_path(lam, seed)
            rows = [r[:4] + [r[4] if len(r) > 4 else r[2]] for r in json.loads(path.read_text())]
            orig = torch.tensor([city.index[r[4].split()[0]] for r in rows], device=city.device)
            last = [city.index[r[4].split()[-1]] for r in rows]
            dests = torch.tensor(sorted(set(last)), device=city.device)
            slot_of = {d: i for i, d in enumerate(dests.tolist())}
            slot = torch.tensor([slot_of[d] for d in last], device=city.device)
            _, nxt = city.tables((city.T + city.wait)[None], dests)
            nxt = nxt[0]
            hops, cur = [orig], orig
            for _ in range(city.E):
                cur = torch.where(cur >= 0, nxt[slot, cur.clamp(min=0)], -1)
                if not bool((cur >= 0).any()):
                    break
                hops.append(cur)
            seq = torch.stack(hops, 1).cpu().numpy()
            out = []
            for r, h in zip(rows, seq):
                route = " ".join(city.ids[i] for i in h if i >= 0)
                out.append([r[0], r[1], route, r[3], r[4]])
            path.write_text(json.dumps(out))
            changed = sum(a[2] != a[4] for a in out) / len(out)
            print(f"{path.name}: {changed:.0%} of trips take a faster route than SUMO's router gave", flush=True)


def validate() -> None:
    """Every route of every test file uses only turns that cars may take in SUMO (checked with sumolib)."""
    from rerouting import larochelle as L
    net = L.load_net()
    ok_turn = {}

    def turn(a, b):
        if (a, b) not in ok_turn:
            ea, eb = net.getEdge(a), net.getEdge(b)
            ok_turn[(a, b)] = any(c.getFromLane().allows("passenger") and c.getToLane().allows("passenger")
                                  for c in ea.getOutgoing().get(eb, []))
        return ok_turn[(a, b)]

    bad = 0
    for lam in LAMBDAS:
        for seed in TEST_SEEDS:
            for r in json.loads(demand_path(lam, seed).read_text()):
                es = r[2].split()
                if not all(turn(a, b) for a, b in zip(es, es[1:])):
                    bad += 1
                    if bad <= 5:
                        print("invalid route", lam, seed, r[0], flush=True)
    print(f"{bad} invalid routes")
    if bad:
        raise SystemExit(1)


def check() -> None:
    """One full morning of the chain at today's traffic (nobody / everybody on the app) and the theory there."""
    city = City()
    t0 = time.time()
    r = chain(FREEFLOW[0], FREEFLOW[1][0], 0.0, city=city, per_street=True)
    per = r.pop("per_street")
    print("chain nearly empty", json.dumps(r), f"{time.time() - t0:.0f}s", flush=True)
    # against SUMO's measured standing time on the same morning (seed 97, same routes)
    import xml.etree.ElementTree as ET
    sumo_wait = np.zeros(city.E)
    sumo_in = np.zeros(city.E)
    for edge in ET.parse(BUILD / "freeflow" / f"s{FREEFLOW[1][0]}" / "edges.xml").getroot().iter("edge"):
        if edge.get("id") in city.index:
            i = city.index[edge.get("id")]
            sumo_wait[i] += float(edge.get("waitingTime", 0))
            sumo_in[i] += float(edge.get("entered", 0))
    over = np.array(per["waited"]) - sumo_wait
    print("total wait, chain", float(np.sum(per["waited"])), "SUMO", float(sumo_wait.sum()))
    for i in np.argsort(-over)[:15]:
        print(json.dumps({"id": city.ids[i], "chain_wait": per["waited"][i], "sumo_wait": sumo_wait[i],
                          "chain_in": per["entered"][i], "sumo_in": sumo_in[i], "C": float(city.C[i]),
                          "holds": float(city.holds[i]), "lanes": float(city.lanes[i]),
                          "minor": bool(city.minor[i]), "light": bool(city.is_light[i]),
                          "T": float(city.T[i])}), flush=True)
    if os.environ.get("QUICK"):
        return
    r = chain(1.0, TEST_SEEDS[0], 0.0, city=city, probe=(int(7.5 * 3600), 8 * 3600))
    for snap in r.pop("probe"):
        print(json.dumps(snap), flush=True)
    print("chain", r, flush=True)
    print("chain", chain(1.0, TEST_SEEDS[0], 1.0, city=city), flush=True)


def smoke() -> None:
    """A short GPU check: tables on a few destinations, one assignment and ten minutes of the chain."""
    city = City()
    trips = Trips(city, 1.0, TEST_SEEDS[0])
    t0 = time.time()
    dist, nxt = city.tables(city.T[None], trips.dests[:64])
    print("tables", tuple(dist.shape), f"{time.time() - t0:.1f}s", "reachable", float(torch_isfinite(dist)))
    for p in (0.0, 0.5, 1.0):
        t0 = time.time()
        print("assignment", p, assignment(city, trips, p, iterations=5), f"{time.time() - t0:.1f}s", flush=True)
    global END
    saved, END = END, START + 1800
    try:
        for p in (0.0, 1.0):
            t0 = time.time()
            print("chain", p, chain(1.0, TEST_SEEDS[0], p, city=city), f"{time.time() - t0:.1f}s", flush=True)
    finally:
        END = saved


def torch_isfinite(x) -> float:
    return float(x.isfinite().float().mean())


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("step", choices=["streets", "demand", "freeflow", "theory", "chain", "predict", "sumo", "score", "check", "routes", "validate",
                                    "smoke"])
    p.add_argument("--workers", type=int, default=10)
    a = p.parse_args()
    {"streets": streets, "demand": lambda: demand(a.workers), "freeflow": lambda: freeflow(a.workers),
     "theory": theory_runs, "chain": chain_runs, "predict": predict, "sumo": lambda: sumo_runs(a.workers),
     "score": score, "smoke": smoke, "check": check, "routes": empty_routes, "validate": validate}[a.step]()


if __name__ == "__main__":
    main()
