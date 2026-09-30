import os, matplotlib; matplotlib.use('Agg')

import matplotlib.pyplot as _plt; _plt.show = lambda *a, **k: _plt.close('all')


import math
import numpy as np
import pandas as pd
import matplotlib.pyplot as plt
from dataclasses import dataclass

plt.rcParams.update({"figure.figsize": (9, 4.5), "axes.grid": True, "grid.alpha": 0.3})
E = os.environ["DATASET_DIR"] + "/advanced/experiments/two_road"


"""What the Markov chain (rerouting/markov.py) gives in closed form, in its steady state.

* **One road.** Each car driving ahead costs 0.54 s (7.5 m at 13.89 m/s) and about T x / 3600 cars
  drive on the road at x cars per hour; each car waiting at the bottleneck costs 3600 / C s, and a
  bottleneck that lets cars go at a regular pace makes random arrivals wait on average
  1800 x / (C (C - x)) seconds (Webster's formula, half of the fully random queue). So

      t(x) = T (1 + x / 6667) + 1800 x / (C (C - x))       (Road.time)

* **When rerouting starts to help.** Drivers with fresh news leave the short road only once it takes
  longer than an empty detour: ``t1(d*) = T2`` (:func:`demand_threshold`).
* **How many rerouters are enough.** Fresh news spreads cars until both roads take the same time
  (the Wardrop equilibrium, :func:`equilibrium_flow`); drivers without the app all stay on the short
  road, so beyond a share ``p* = 1 - x_UE / d`` (:func:`share_threshold`) the extra rerouters have
  nothing left to balance.
* **Above capacity** the queues grow by ``d - C`` cars per hour and only ``C`` cars per hour get
  through (:func:`finished_share`).

The chain itself (not these formulas) is what is compared with SUMO. Only what held in both blind tests
is kept here; the chain's herding and stale-information predictions proved unreliable and are not used.
"""


import math
from dataclasses import dataclass

import numpy as np


@dataclass(frozen=True)
class Road:
    """A road: free-flow time ``T`` (s) and a traffic light letting ``C`` cars per hour through."""

    T: float
    C: float

    def time(self, x: float) -> float:
        """Mean trip time (s) at ``x`` cars per hour in the chain's steady state; infinite at capacity."""
        if x < 0:
            raise ValueError("negative flow")
        if x >= self.C:
            return math.inf
        return self.T * (1 + x / 6667.0) + 1800.0 * x / (self.C * (self.C - x))


def equilibrium_flow(d: float, r1: Road, r2: Road) -> float:
    """Cars per hour on the short road when both roads take the same time (Wardrop).

    If the short road stays faster even with all ``d`` cars on it, everyone uses it.
    """
    if d <= 0:
        return 0.0
    if r1.time(d) <= r2.time(0.0):
        return float(d)
    lo, hi = max(0.0, d - r2.C), min(d, r1.C)
    for _ in range(200):
        mid = (lo + hi) / 2
        if r1.time(mid) < r2.time(d - mid):
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2


def demand_threshold(r1: Road, r2: Road) -> float:
    """Traffic below which rerouting cannot help: the short road still beats an empty detour, t1(d*) = T2."""
    lo, hi = 0.0, r1.C * (1 - 1e-9)
    for _ in range(200):
        mid = (lo + hi) / 2
        lo, hi = (mid, hi) if r1.time(mid) < r2.T else (lo, mid)
    return (lo + hi) / 2


def share_threshold(d: float, r1: Road, r2: Road) -> float:
    """Share of rerouters beyond which more of them change nothing: ``p* = 1 - x_UE / d``."""
    return 1.0 - equilibrium_flow(d, r1, r2) / d if d > 0 else 0.0


def finished_share(d: float, capacity: float, free_flow: float, demand_hours: float = 1.0,
                   horizon: float = 7200.0) -> float:
    """Share of the trips requested during ``demand_hours`` that end before ``horizon`` seconds,
    when ``capacity`` cars per hour get through from the moment the first car arrives."""
    served = capacity * (horizon - free_flow) / 3600.0
    return min(1.0, served / (d * demand_hours))


cal = pd.read_csv(f"{E}/two_road_calibration.csv")
def road(route):
    r = cal[cal.forced_route == route]
    T = r[r.demand_veh_per_h == r.demand_veh_per_h.min()].mean_trip_time_s.mean()
    C = r.groupby("demand_veh_per_h").exit_rate_veh_per_h.mean().max()
    return Road(T, C)
short, detour = road("short"), road("long")
print(f"short road: T = {short.T:.0f} s, C = {short.C:.0f} cars/h;  detour: T = {detour.T:.0f} s, C = {detour.C:.0f} cars/h")
print(f"d* = {demand_threshold(short, detour):.0f} cars/h")
for d in (1200, 1500, 1800):
    print(f"p* at {d} cars/h = {share_threshold(d, short, detour):.0%}")



x = np.linspace(0, 0.97, 200)
fig, ax = plt.subplots()
for rd, route, style in [(short, "short", "-"), (detour, "long", "--")]:
    ax.plot(x * rd.C, [rd.time(v * rd.C) / 60 for v in x], "k" + style, label=f"theory, {route} road")
    r = cal[(cal.forced_route == route) & (cal.demand_veh_per_h < rd.C)].groupby("demand_veh_per_h").mean_trip_time_s.mean()
    ax.plot(r.index, r / 60, "o", label=f"SUMO, {route} road")
ax.set_xlabel("cars per hour on the road"); ax.set_ylabel("trip time (min)"); ax.set_ylim(0, 8); ax.legend()
plt.show()


"""One Markov chain for the two-road network, from which every prediction is computed.

Three rules, all measured or taken from the network (nothing is fitted):

1. **Every car ahead of you costs you time**: 0.54 s if it is driving on your road (the time to
   drive the 7.5 m that one car and its gap occupy at 13.89 m/s), 3600/C s if it is waiting at your
   road's bottleneck (the time the bottleneck needs to let one car through).
2. **Cars enter one at a time.** The next car to enter is drawn from the waiting line in proportion to the
   waiting drivers of each kind (with and without the app). The short road's bottleneck is its traffic light,
   600 m past the fork: it admits cars until their queue reaches back to the entrance (133 cars,
   the 1,000 m from the entrance to the light at 7.5 m per car). The detour's bottleneck is its
   narrow start at the fork: it admits one car every 3600/C2 seconds. A car that cannot enter
   holds up every car behind it.
3. **The app shows the average trip time of the last ``window`` seconds, seen from inside the
   network** (SUMO's rule). A car that took the short road reaches the back of its queue 72 s later
   (1,000 m at 13.89 m/s), so the app sees it there only then.

Every second the state is: the line of cars waiting to enter, the cars on their way to the short
road's light, the queue at that light, the cars driving on each road, and the app's averaged
travel times. A car arrives with probability d/3600; the light lets a car go every 3600/C1 seconds.
"""


import math
from dataclasses import dataclass

import numpy as np


DRIVE_TO_LIGHT = 72.0      # seconds from the start to the short road's light (1,000 m at 13.89 m/s)
CAR_SPACE = 7.5            # metres a stopped car takes, with its gap (SUMO: 5 m car + 2.5 m gap)
SPEED = 13.89              # m/s, the speed limit everywhere
PER_CAR_AHEAD = CAR_SPACE / SPEED            # 0.54 s
SHORT_ROAD_HOLDS = (400 + 600) / CAR_SPACE   # queued cars from the short road's light back to the entrance


def information_age(window: float) -> float:
    """What the app averages over (seconds): its window, as in SUMO."""
    return window


@dataclass
class Chain:
    short: Road
    long: Road
    demand: float                 # cars per hour while traffic enters
    share: float = 0.0            # drivers who follow the live information
    split: float = 0.0            # drivers with a fixed route sent to the detour (experienced, forced)
    age: float = information_age(180)   # the app's averaging window (s)
    entry: float = 400.0                # length of the shared entry road (m): sets the two geometric constants
    demand_seconds: int = 3600
    horizon: int = 7200


def run(chain: Chain, replicas: int = 400, seed: int = 0) -> dict:
    """Runs ``replicas`` copies of the chain side by side; returns the same statistics as SUMO."""
    rng = np.random.default_rng(seed)
    T = np.array([chain.short.T, chain.long.T])
    D = 3600.0 / np.array([chain.short.C, chain.long.C])      # seconds per car at each bottleneck
    lag = int(DRIVE_TO_LIGHT * (chain.entry + 600) / 1000)       # 72 s with the 400 m entry road
    holds = SHORT_ROAD_HOLDS * (chain.entry + 600) / 1000       # 133 cars with the 400 m entry road
    rows = np.arange(replicas)
    n = np.zeros(replicas)                 # waiting at the short road's light
    credit = np.zeros(replicas)            # progress of the car being let through the light (fraction of D1)
    gate = np.ones(replicas)               # the detour's entrance: 1 means it can admit a car now
    m = np.zeros((replicas, 2))            # driving on each road
    # cars on their way to the short road's light, by the second they entered: count and summed
    # driving time, for app users (index 1) and the others (index 0)
    coming = np.zeros((replicas, lag, 2))
    base = np.zeros((replicas, lag, 2))
    line = np.zeros((replicas, 2, 2))      # waiting to enter, by group (others, app users) and chosen road
    first = np.full(replicas, -1)          # first car of the line: -1 none, 0 other, 1 app user
    first_road = np.zeros(replicas, dtype=int)
    W = max(1, int(round(chain.age)))
    seen = np.tile(T, (replicas, W, 1))    # the last W seconds of trip times, as seen from inside
    news = np.tile(T, (replicas, 1)) * 1.0 # their average: what the app shows
    s = np.zeros(replicas, dtype=int)
    names = ("journey", "done", "count", "j_rer", "c_rer", "j_oth", "c_oth", "long_rer", "long_all", "n_rer_w",
             "n_all_w", "j_short", "c_short", "j_long", "c_long")
    sums = dict.fromkeys(names, 0.0)
    per_min = np.zeros((replicas, chain.horizon // 60 + 1, 2))

    def add_trips(journeys_sum, count, group, left, road, entered_at):
        """Books ``count`` trips (summed trip time ``journeys_sum``) of a group, capped at the horizon."""
        mean = np.divide(journeys_sum, count, out=np.zeros_like(journeys_sum), where=count > 0)
        capped = np.minimum(mean, left) * count
        sums["journey"] += capped.sum()
        sums["done"] += count[mean <= left].sum()
        sums["j_rer" if group else "j_oth"] += capped.sum()
        if 600 <= entered_at < 3600:
            key = "short" if road == 0 else "long"
            sums[f"j_{key}"] += journeys_sum.sum()
            sums[f"c_{key}"] += count.sum()

    def looks():
        """Trip time on each road as seen now from inside the network (cars at the light, cars driving)."""
        ahead = np.where(n > 0, n - credit, 0.0)
        return T + PER_CAR_AHEAD * m + np.stack([D[0] * ahead, np.zeros(replicas)], axis=1)

    for t in range(chain.horizon):
        if t < chain.demand_seconds:
            arrive = rng.random(replicas) < chain.demand / 3600.0
            follower = rng.random(replicas) < chain.share
            # a driver picks a road on arriving: the app's road, or a fixed one
            fixed_long = (rng.random(replicas) < chain.split).astype(int)
            np.add.at(line, (rows, 1, s), arrive & follower)
            np.add.at(line, (rows, 0, fixed_long), arrive & ~follower)
            sums["count"] += arrive.sum()
            sums["c_rer"] += (arrive & follower).sum()
            sums["c_oth"] += (arrive & ~follower).sum()
        # app users still waiting re-check the app about once a minute (SUMO's pre-departure rerouting)
        switch = rng.binomial(line[rows, 1, 1 - s].astype(int), 1.0 / 60.0)
        line[rows, 1, 1 - s] -= switch
        line[rows, 1, s] += switch
        # the next car of the line comes to the front with the road it picked
        need = (first < 0) & (line.sum(axis=(1, 2)) > 0)
        if need.any():
            flat = line.reshape(replicas, 4)
            total = flat.sum(axis=1, keepdims=True)
            cum = np.cumsum(np.divide(flat, total, out=np.zeros_like(flat), where=total > 0), axis=1)
            k = np.minimum((rng.random((replicas, 1)) > cum).sum(axis=1), 3)
            k = np.where(need, k, -1)
            took = k >= 0
            flat[rows[took], k[took]] -= 1
            first = np.where(took, k // 2, first)
            first_road = np.where(took, k % 2, first_road)
        # waiting in the line counts for everyone still in it
        in_line = line.sum(axis=2)
        in_line[:, 1] += first == 1
        in_line[:, 0] += first == 0
        sums["journey"] += in_line.sum()
        sums["j_rer"] += in_line[:, 1].sum()
        sums["j_oth"] += in_line[:, 0].sum()
        # the first car enters if its road admits it
        on_way = coming.sum(axis=(1, 2))
        can = np.where(first_road == 0, n + on_way < holds, gate >= 1.0)
        enter = (first >= 0) & can
        if enter.any():
            road = first_road
            drive = T[road] + PER_CAR_AHEAD * m[rows, road]
            pick_f, pick_o = enter & (first == 1), enter & (first == 0)
            left = chain.horizon - t
            # the detour: its only queue was the entrance, so the trip time is known now
            for grp, pick in ((1, pick_f), (0, pick_o)):
                sel = (pick & (road == 1)).astype(float)
                add_trips(drive * sel, sel, grp, left, 1, t)
                # the short road: the wait at the light is booked when the car gets there
                short = pick & (road == 0)
                coming[:, t % lag, grp] += short
                base[:, t % lag, grp] += np.where(short, drive, 0.0)
            if 600 <= t < 3600:
                sums["long_rer"] += (pick_f & (road == 1)).sum()
                sums["n_rer_w"] += pick_f.sum()
                sums["long_all"] += (enter & (road == 1)).sum()
                sums["n_all_w"] += enter.sum()
            watched = pick_f if chain.share > 0 else enter
            per_min[:, t // 60, 0] += watched
            per_min[:, t // 60, 1] += watched & (road == 1)
            gate -= enter & (road == 1)
            m[rows[enter], road[enter]] += 1
            first = np.where(enter, -1, first)
        # cars that entered the short road 72 s ago reach its light and queue behind those waiting
        slot = (t + 1) % lag
        entered_at = t + 1 - lag
        if entered_at >= 0:
            ahead = np.where(n > 0, n - credit, 0.0)
            for grp in (0, 1):
                k = coming[:, slot, grp]
                # the k cars wait D * (ahead + 0), D * (ahead + 1), ... in turn
                wait = D[0] * (k * ahead + k * (k - 1) / 2)
                add_trips(base[:, slot, grp] + wait, k, grp, chain.horizon - entered_at, 0, entered_at)
                ahead = ahead + k
                n += k
        coming[:, slot] = 0
        base[:, slot] = 0
        # the light lets cars go at a regular pace; cars driving leave the road after about T
        busy = n > 0
        credit = np.where(busy, credit + 1.0 / D[0], 0.0)
        out = busy & (credit >= 1.0)
        n -= out
        credit -= out
        gate = np.minimum(gate + 1.0 / D[1], 1.0 + 1.0 / D[1])   # one car every 3600/C2 s on average (keeps the fraction)
        m -= m / T
        now = looks()
        news += (now - seen[:, t % W]) / W
        seen[:, t % W] = now
        s = np.argmin(news, axis=1)
    # cars still on their way to the light at the end: their time so far
    for grp in (0, 1):
        k = coming[:, :, grp].sum()
        sums["journey"] += k * lag / 2
        sums["j_rer" if grp else "j_oth"] += k * lag / 2

    def ratio(a, b):
        return sums[a] / sums[b] if sums[b] else float("nan")

    series = [[[k * 60.0, (x / c) if c else 0.0, c] for k, (c, x) in enumerate(per_min[r])] for r in range(replicas)]
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


def wardrop_split(short: Road, long: Road, demand: float, replicas: int = 400, seed: int = 0, entry: float = 400.0) -> float:
    """Fixed share sent to the detour such that both roads take the same mean time in the chain:
    what drivers who learned the usual traffic (and never react) settle on."""
    lo, hi = 0.0, 1.0
    for _ in range(12):
        f = (lo + hi) / 2
        # with fixed routes the two queues are separate chains, fed by (1-f) d and f d cars per hour
        t_short = run(Chain(short, long, demand * (1 - f), entry=entry), replicas, seed)["journey"]
        t_long = run(Chain(short, long, demand * f, split=1.0, entry=entry), replicas, seed)["journey"]
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


dem = pd.read_csv(f"{E}/two_road_demand.csv")
gain = (dem[dem.policy == "no_information"].groupby("demand_veh_per_h").mean_trip_time_s.mean()
        - dem[(dem.policy == "live") & (dem.app_share == 1.0)].groupby("demand_veh_per_h").mean_trip_time_s.mean())
ds = [300, 450, 600, 750, 900, 1050]
chain_gain = [run(Chain(short, detour, d), 150)["journey"] - run(Chain(short, detour, d, share=1.0), 150)["journey"] for d in ds]
plt.plot(gain.index, gain / 60, "o", label="SUMO (5 seeds)")
plt.plot(ds, np.array(chain_gain) / 60, "k-", label="Markov chain")
plt.axvline(demand_threshold(short, detour), color="grey", ls=":", label="d*")
plt.xlim(250, 1100); plt.ylim(-0.5, 15)
plt.xlabel("cars per hour"); plt.ylabel("minutes saved by rerouting"); plt.legend(); plt.show()



sh = pd.read_csv(f"{E}/two_road_share_series.csv")
fig, ax = plt.subplots()
for d, color in [(1200, "tab:orange"), (1500, "tab:red"), (1800, "darkred")]:
    s = sh[sh.demand_veh_per_h == d].groupby("app_share").mean_trip_time_s.mean() / 60
    ax.plot(s.index * 100, s, "o", color=color, label=f"SUMO {d} cars/h")
    ps = [0.1, 0.3, 0.5, 0.7, 1.0]
    ax.plot([p * 100 for p in ps], [run(Chain(short, detour, d, share=p), 100)["journey"] / 60 for p in ps], "-", color=color)
    ax.axvline(100 * share_threshold(d, short, detour), color=color, ls=":")
ax.set_yscale("log"); ax.set_xlabel("drivers using the app (%)"); ax.set_ylabel("trip time (min)")
ax.set_title("Dots: SUMO. Lines: Markov chain. Dotted: p*"); ax.legend(); plt.show()



m = pd.read_csv(f"{E}/two_road_share_series_minute_series.csv")
one = m[(m.demand_veh_per_h == 1800) & (m.app_share == 1.0) & (m.seed == 1)]
plt.plot(one.minute_start_s / 60, one.share_on_detour * 100)
plt.xlabel("minute"); plt.ylabel("app users on the detour (%)")
plt.title("1,800 cars/h, every driver on the app: the crowd swings between the roads"); plt.show()

print('notebook ran to the end')
