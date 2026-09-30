"""One Markov chain for the two-road network, from which every prediction is computed.

The state, second by second, is three numbers:

    n1  cars queued at the short road's traffic light
    n2  cars queued at the detour's traffic light
    s   the road the live information currently says is faster (0 short, 1 detour)

Each second, three things can happen:

    a car arrives        with probability d/3600. A driver without the app takes the short road,
                         a driver with the app (share p) takes road s, a driver with a fixed
                         route takes the detour with probability ``split``. It joins that queue.
    a light lets a car go  with probability C/3600 for each road (if its queue is not empty).
    the information refreshes  with probability 1/age: s becomes the road with the lower time
                         T + 3600 n / C right now. Between refreshes drivers act on old news.

A car that joins road i behind n cars takes T_i + 3600 n / C_i seconds (the time to drive the
road plus the time the light needs to serve the cars ahead of it). Nothing is fitted: T and C
are the free-flow time and capacity measured in SUMO, and the information age is fixed by the
network (half the SUMO averaging window, plus the 72 s a car needs to reach the short road's
light, during which it is committed but not yet visible in the queue).
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

from rerouting.theory import Road

DRIVE_TO_LIGHT = 72.0  # seconds from the start to the short road's light (1,000 m at 13.89 m/s)


def information_age(window: float) -> float:
    """Mean age of what the app shows: half the averaging window plus the drive to the queue."""
    return window / 2.0 + DRIVE_TO_LIGHT


@dataclass
class Chain:
    short: Road
    long: Road
    demand: float                 # cars per hour while traffic enters
    share: float = 0.0            # drivers who follow the live information
    split: float = 0.0            # drivers with a fixed route sent to the detour (experienced, forced)
    age: float = information_age(180)
    demand_seconds: int = 3600
    horizon: int = 7200


def run(chain: Chain, replicas: int = 400, seed: int = 0) -> dict:
    """Runs ``replicas`` copies of the chain side by side; returns the same statistics as SUMO."""
    rng = np.random.default_rng(seed)
    T = np.array([chain.short.T, chain.long.T])
    C = np.array([chain.short.C, chain.long.C])
    n = np.zeros((replicas, 2))
    s = np.zeros(replicas, dtype=int)
    rows = np.arange(replicas)
    minutes = chain.demand_seconds // 60
    sums = {k: 0.0 for k in ("journey", "done", "count", "j_rer", "c_rer", "j_oth", "c_oth", "long_rer", "long_all",
                             "n_rer_w", "n_all_w", "j_short", "c_short", "j_long", "c_long")}
    per_min = np.zeros((replicas, minutes, 2))  # (followers of the app, of them on the detour) per minute
    for t in range(chain.horizon):
        if t < chain.demand_seconds:
            arrive = rng.random(replicas) < chain.demand / 3600.0
            follower = rng.random(replicas) < chain.share
            fixed_long = rng.random(replicas) < chain.split
            road = np.where(follower, s, fixed_long.astype(int))
            time = T[road] + 3600.0 * n[rows, road] / C[road]
            left = chain.horizon - t
            journey = np.minimum(time, left)
            a = arrive
            sums["journey"] += journey[a].sum()
            sums["done"] += (time[a] <= left).sum()
            sums["count"] += a.sum()
            sums["j_rer"] += journey[a & follower].sum()
            sums["c_rer"] += (a & follower).sum()
            sums["j_oth"] += journey[a & ~follower].sum()
            sums["c_oth"] += (a & ~follower).sum()
            if t >= 600:
                sums["long_rer"] += (a & follower & (road == 1)).sum()
                sums["n_rer_w"] += (a & follower).sum()
                sums["long_all"] += (a & (road == 1)).sum()
                sums["n_all_w"] += a.sum()
                sums["j_short"] += journey[a & (road == 0)].sum()
                sums["c_short"] += (a & (road == 0)).sum()
                sums["j_long"] += journey[a & (road == 1)].sum()
                sums["c_long"] += (a & (road == 1)).sum()
            watched = a & (follower if chain.share > 0 else np.ones(replicas, bool))
            per_min[:, t // 60, 0] += watched
            per_min[:, t // 60, 1] += watched & (road == 1)
            n[rows[a], road[a]] += 1
        served = (rng.random((replicas, 2)) < C / 3600.0) & (n > 0)
        n -= served
        refresh = rng.random(replicas) < 1.0 / chain.age
        s = np.where(refresh, np.argmin(T + 3600.0 * n / C, axis=1), s)

    def ratio(a, b):
        return sums[a] / sums[b] if sums[b] else float("nan")

    series = [[[m * 60.0, (k / c) if c else 0.0, c] for m, (c, k) in enumerate(per_min[r])] for r in range(replicas)]
    swings = [swing(x) for x in series]
    flips = [switches(x) for x in series]
    return {"journey": ratio("journey", "count"), "completed": ratio("done", "count"),
            "journey_rerouters": ratio("j_rer", "c_rer"), "journey_others": ratio("j_oth", "c_oth"),
            "long_share": ratio("long_all", "n_all_w"), "long_share_rerouters": ratio("long_rer", "n_rer_w"),
            "swing": float(np.nanmean(swings)) if chain.share > 0 else float("nan"),
            "switches": float(np.nanmean(flips)) if chain.share > 0 else float("nan"),
            "journey_short_route": ratio("j_short", "c_short"), "journey_long_route": ratio("j_long", "c_long"),
            "series_example": series[0]}


def swing(series: list) -> float:
    """Minute-to-minute spread of a detour share over minutes 10-60, sampling noise removed.

    ``series`` holds [start second, share, number of cars] per minute. Each minute is a draw of
    a few dozen cars, so part of the spread is chance: sqrt(var(q) - mean(q (1-q) / (n-1))).
    Used identically on SUMO runs and on the chain.
    """
    pts = [(q, n) for t, q, n in series if 600 <= t < 3600 and n > 1]
    if len(pts) < 10:
        return float("nan")
    q = np.array([a for a, _ in pts])
    noise = float(np.mean([a * (1 - a) / (n - 1) for a, n in pts]))
    return math.sqrt(max(0.0, q.var(ddof=1) - noise))


def switches(series: list) -> float:
    """How many times per hour the detour share crosses its own average, over minutes 10-60,
    after a 3-minute moving average (so that the chance of a single minute does not count).
    Used identically on SUMO runs and on the chain."""
    q = np.array([share for t, share, n in series if 600 <= t < 3600 and n > 1])
    if len(q) < 10:
        return float("nan")
    smooth = np.convolve(q, np.ones(3) / 3, mode="valid")
    above = smooth > smooth.mean()
    return float(np.sum(above[1:] != above[:-1])) * 60.0 / len(q)


def wardrop_split(short: Road, long: Road, demand: float, replicas: int = 400, seed: int = 0) -> float:
    """Fixed share sent to the detour such that both roads take the same mean time in the chain:
    what drivers who learned the usual traffic (and never react) settle on."""
    lo, hi = 0.0, 1.0
    for _ in range(12):
        f = (lo + hi) / 2
        # with fixed routes the two queues are separate chains, fed by (1-f) d and f d cars per hour
        t_short = run(Chain(short, long, demand * (1 - f)), replicas, seed)["journey"]
        t_long = run(Chain(short, long, demand * f, split=1.0), replicas, seed)["journey"]
        lo, hi = (f, hi) if t_short > t_long else (lo, f)
    return (lo + hi) / 2


def curves(short: Road, long: Road, replicas: int = 300) -> dict:
    """Smooth chain curves for the figures (no SUMO involved)."""
    out = {"demand": {}, "onset": {}, "share": {}, "window": {}}
    for p in (0.0, 0.5, 1.0):
        out["demand"][str(p)] = [[d, *(lambda r: (r["journey"], r["completed"]))(run(Chain(short, long, d, share=p),
                                                                                         replicas))]
                                 for d in range(300, 2401, 75)]
    for d in range(600, 851, 10):
        g = run(Chain(short, long, d), replicas)["journey"] - run(Chain(short, long, d, share=1.0), replicas)["journey"]
        out["onset"][str(d)] = g
    for d in (1200, 1500, 1800):
        rows = []
        for p in np.round(np.arange(0.0, 1.0001, 0.05), 2):
            r = run(Chain(short, long, d, share=float(p)), replicas)
            rows.append([float(p), r["journey"], r["journey_rerouters"], r["journey_others"],
                         r["long_share_rerouters"], r["swing"], r["switches"]])
        out["share"][str(d)] = rows
    for p in (0.5, 1.0):
        out["window"][str(p)] = [[w, run(Chain(short, long, 1800, share=p, age=information_age(w)), replicas)["journey"]]
                                 for w in (10, 20, 30, 60, 120, 180, 300, 450, 600)]
    out["series_example"] = run(Chain(short, long, 1800, share=1.0), 1, seed=1)["series_example"]
    return out
