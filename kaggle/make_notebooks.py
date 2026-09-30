"""Builds the two Kaggle notebooks attached to the dataset, and a plain script of each for testing.

    python kaggle/make_notebooks.py            # kaggle/eda/, kaggle/theory/  (+ *_test.py)

The theory notebook embeds the project's Markov chain verbatim (rerouting/markov.py, rerouting/theory.py),
so what readers run is exactly what was tested.
"""

import json
import re
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
USER = "tudoropr"
DATASET = "does-live-rerouting-beat-traffic-jams"
D = f"/kaggle/input/{DATASET}"


def notebook(cells):
    return {"nbformat": 4, "nbformat_minor": 5,
            "metadata": {"kernelspec": {"name": "python3", "display_name": "Python 3", "language": "python"},
                         "language_info": {"name": "python"}},
            "cells": [{"cell_type": kind, "metadata": {}, "source": src.strip("\n"),
                       **({"outputs": [], "execution_count": None} if kind == "code" else {})}
                      for kind, src in cells]}


def md(text):
    return ("markdown", text)


def code(text):
    return ("code", text)


# --------------------------------------------------------------------------- EDA notebook (beginner friendly)

EDA = [
    md("""
# Does live rerouting beat traffic jams? A gentle first look

Navigation apps (Waze, Google Maps...) send drivers onto another road when their usual route is jammed.
**Does that make everybody's trip faster, and when does it stop helping?**

This dataset answers with traffic simulations of **La Rochelle (France)** on a weekday morning. The special
thing about simulations: **we can replay the very same morning with 0 %, 25 %, 50 %, 75 % or 100 % of drivers
using the app**, and on a normal day or a day with an accident. Real traffic data can never show that.

This notebook needs no traffic knowledge. We use only `pandas` and `matplotlib`, and the small tables of the
`beginner/` folder; at the end we peek into the full `advanced/` data.

**What we will find out:**
1. Does the app shorten trips?
2. Who gains: app users only, or everybody?
3. What does an accident cost?
4. When is the rush hour?
5. Where are the jams?
6. Who drives where?
7. The simplest case: two roads.
"""),
    code(f"""
import pandas as pd
import numpy as np
import matplotlib.pyplot as plt

pd.set_option("display.width", 140)
plt.rcParams.update({{"figure.figsize": (9, 4.5), "axes.grid": True, "grid.alpha": 0.3,
                     "axes.spines.top": False, "axes.spines.right": False}})
D = "{D}"
B = f"{{D}}/beginner"
"""),
    md("""
## 0. The scenarios

Each **scenario** is one simulated morning, 6:30 to 11:00, with about 16,500 cars leaving between 7:00 and
9:00 (we follow those). A scenario is defined by:

* `day`: `normal`, or `incident` (from 7:45 to 8:30 an accident slows one lane of the ring road to walking pace and the other lanes to 20 km/h);
* `app_share_pct`: the share of drivers who follow the live-rerouting app;
* `seed`: each scenario is simulated 3 times with slightly different random traffic (seeds 1, 2, 3), so we
  can see how much results vary by chance.

The other drivers follow the route they usually take, as people who know the city do.
"""),
    code("""
scen = pd.read_csv(f"{B}/scenarios.csv")
print(scen.shape)
scen.head(10)
"""),
    code("""
scen.describe().round(2)
"""),
    md("""
## 1. Does the app shorten trips?

We average the three seeds and plot the average trip time against the share of drivers using the app.
"""),
    code("""
avg = scen.groupby(["day", "app_share_pct"])["avg_trip_minutes"].agg(["mean", "min", "max"]).reset_index()
fig, ax = plt.subplots()
for day, color in [("normal", "tab:orange"), ("incident", "tab:red")]:
    d = avg[avg.day == day]
    ax.plot(d.app_share_pct, d["mean"], "o-", color=color, label=f"{day} morning")
    ax.fill_between(d.app_share_pct, d["min"], d["max"], color=color, alpha=0.15)  # spread between seeds
ax.set_xlabel("drivers using the app (%)")
ax.set_ylabel("average trip time (minutes)")
ax.set_title("Average trip time, drivers leaving 7:00-9:00")
ax.legend()
plt.show()
avg.pivot(index="app_share_pct", columns="day", values="mean").round(2)
"""),
    md("""
**Reading:** with nobody on the app, the average trip takes about 15 minutes; with everybody, about 11.5.
Most of the gain comes with the **first half** of the drivers: after 50 %, more app users change little.
The shaded band is the spread between the three seeds: wide at 0 % (seed 2 had a bad jam: 17.8 minutes
against about 13.4 for the others), narrow afterwards - with the app, the results vary much less from one
random morning to another.

Time lost in jams tells the same story more strongly:
"""),
    code("""
lost = scen.groupby(["day", "app_share_pct"])["avg_time_lost_minutes"].mean().unstack(0)
lost.plot(marker="o", ylabel="minutes lost in traffic per trip", xlabel="drivers using the app (%)",
          title="Time lost in traffic")
plt.show()
lost.round(2)
"""),
    md("""
## 2. Who gains?

Do the app users gain at the expense of the others, or does everybody gain?
"""),
    code("""
n = scen[scen.day == "normal"].groupby("app_share_pct")[["avg_trip_minutes_app_users", "avg_trip_minutes_others"]].mean()
n.plot(marker="o", xlabel="drivers using the app (%)", ylabel="average trip time (minutes)",
       title="Normal morning: app users vs the others")
plt.show()
n.round(2)
"""),
    md("""
**Reading:** app users are faster, but the drivers **without** the app gain too: when a quarter of drivers
use it, the others already save about 1.4 minutes (14.9 to 13.5), because the rerouted cars leave room on the
busy roads.
"""),
    md("""
## 3. What does the accident cost?

From 7:45 to 8:30, an accident on the busiest ring-road stretch slows one lane to walking pace (3.6 km/h)
and the other lanes to 20 km/h. How many minutes does it add to the average trip, and does the app soften it?
"""),
    code("""
cost = (avg.pivot(index="app_share_pct", columns="day", values="mean")
           .assign(extra_minutes=lambda t: t.incident - t.normal))
cost["extra_minutes"].plot.bar(color="tab:red", ylabel="extra minutes per trip", xlabel="drivers using the app (%)",
                               title="Cost of the accident (incident - normal)")
plt.show()
cost.round(2)
"""),
    md("""
**Reading:** with nobody on the app, the accident adds about 0.8 minute to the average trip (averaged over
all 16,500 drivers, most of whom never pass there). With a quarter to half of the drivers on the app the
cost falls to under half a minute - but with **everybody** on the app it rises to a full minute. More app
users is not always better.
"""),
    md("""
## 4. When is the rush hour?

`traffic_by_time.csv` describes the whole city every 5 minutes: how many cars are driving, their average speed,
and the hours lost in traffic.
"""),
    code("""
t = pd.read_csv(f"{B}/traffic_by_time.csv")
t.head()
"""),
    code("""
fig, (a1, a2) = plt.subplots(1, 2, figsize=(13, 4.2))
for pct, color in [(0, "grey"), (50, "tab:orange"), (100, "tab:red")]:
    d = t[(t.day == "normal") & (t.app_share_pct == pct)].groupby("time_clock")[["cars_on_road", "avg_speed_kmh"]].mean()
    a1.plot(d.index, d.cars_on_road, color=color, label=f"{pct} % on the app")
    a2.plot(d.index, d.avg_speed_kmh, color=color, label=f"{pct} % on the app")
for a in (a1, a2):
    a.set_xticks(a.get_xticks()[::6])
    a.tick_params(axis="x", rotation=45)
    a.legend()
a1.set_title("Cars on the road (normal morning)")
a2.set_title("Average speed (km/h)")
plt.tight_layout()
plt.show()
"""),
    md("""
**Reading:** traffic builds from 7:00, peaks around 8:10 and drains by 9:30. Speeds dip at the peak;
compare the three curves to see how the app changes the dip.
"""),
    md("""
## 5. Where are the jams?

`traffic_by_area.csv` splits the city by commune, every 15 minutes. `speed_vs_limit_pct` is the average speed
as a share of the speed limit: near 100 % the traffic flows freely, low values mean jams. To combine several
rows we weight them by `vehicle_hours` (the time cars spent driving), the same weighting used to build them.
"""),
    code("""
a = pd.read_csv(f"{B}/traffic_by_area.csv")
peak = a[(a.time_clock >= "07:30") & (a.time_clock < "08:30")]
by_area = (peak.groupby(["area", "day", "app_share_pct"])
               .apply(lambda g: np.average(g.speed_vs_limit_pct, weights=g.vehicle_hours))
               .rename("speed_vs_limit_pct").reset_index())
normal0 = by_area[(by_area.day == "normal") & (by_area.app_share_pct == 0)].sort_values("speed_vs_limit_pct")
normal0.plot.barh(x="area", y="speed_vs_limit_pct", legend=False, color="tab:red",
                  title="Rush hour (7:30-8:30), nobody on the app: speed as % of the limit")
plt.xlabel("% of the speed limit (lower = more congested)")
plt.show()
"""),
    code("""
# Which areas gain most from the app? Heat map of speed vs limit over the morning, La Rochelle itself
lr = a[(a.area == "La Rochelle") & (a.day == "normal")]
heat = lr.groupby(["app_share_pct", "time_clock"]).apply(lambda g: np.average(g.speed_vs_limit_pct, weights=g.vehicle_hours)).unstack()
plt.figure(figsize=(12, 3))
plt.imshow(heat, aspect="auto", cmap="RdYlGn", vmin=heat.values.min(), vmax=100)
plt.yticks(range(len(heat.index)), [f"{p} % app" for p in heat.index])
plt.xticks(range(0, len(heat.columns), 2), heat.columns[::2], rotation=45)
plt.colorbar(label="% of the speed limit")
plt.title("La Rochelle, normal morning: how fast traffic moves")
plt.show()
"""),
    md("""
## 6. Who drives where?

`trips_between_areas.csv` groups the trips by where they start (`home_area`) and end (`work_area`).
`outside the map` covers commuters coming from (or going to) farther away and the traffic crossing the city;
they enter by the main roads (N11, N137, the Île de Ré bridge...).
"""),
    code("""
od = pd.read_csv(f"{B}/trips_between_areas.csv")
base = od[(od.day == "normal") & (od.app_share_pct == 0)].groupby(["home_area", "work_area"])["trip_count"].mean()
base.sort_values(ascending=False).head(12).round(0)
"""),
    code("""
# Average trip time by home area, without and with the app
g = (od[od.day == "normal"].assign(total=lambda x: x.trip_count * x.avg_trip_minutes)
       .groupby(["home_area", "app_share_pct"])[["total", "trip_count"]].sum())
g = (g.total / g.trip_count).unstack()
g = g.loc[od.groupby("home_area").trip_count.sum().sort_values(ascending=False).index[:8]]
g[[0, 50, 100]].plot.barh(figsize=(9, 5), title="Average trip time by home area (normal morning)")
plt.xlabel("minutes")
plt.show()
"""),
    md("""
## 7. The simplest case: two roads

Before a whole city, the project simulated the simplest possible network: a **short road** and a longer
**detour** between the same two places. `two_roads.csv` gives the average trip time for each traffic level
(`traffic_per_hour`) and share of app users.
"""),
    code("""
two = pd.read_csv(f"{B}/two_roads.csv")
fig, (a1, a2) = plt.subplots(1, 2, figsize=(13, 4.2))
sweep = two[two.app_share_pct.isin([0, 50, 100])].groupby(["traffic_per_hour", "app_share_pct"]).avg_trip_minutes.mean().unstack()
sweep.plot(marker="o", logy=True, ax=a1, title="Trip time as traffic grows")
a1.set_xlabel("cars per hour"); a1.set_ylabel("minutes (log scale)")
share = two[two.traffic_per_hour.isin([1200, 1500, 1800])].groupby(["app_share_pct", "traffic_per_hour"]).avg_trip_minutes.mean().unstack()
share.plot(marker="o", logy=True, ax=a2, title="Trip time against the share of app users")
a2.set_xlabel("drivers using the app (%)"); a2.set_ylabel("minutes (log scale)")
plt.tight_layout()
plt.show()
"""),
    md("""
**Reading:** up to about 700 cars per hour the app saves almost nothing (the short road rarely has a queue);
above, it saves a great deal (20 minutes down to under 5 at 1,200 cars per hour). And only part of the
drivers needs the app: beyond roughly 40-60 %, more app users bring nothing - and at 1,800 cars per hour,
everybody on the app makes trips much longer (12.4 minutes against 7.1 with 60 %), because the drivers all
switch roads together.
"""),
    md("""
## 8. Going further: the `advanced/` folder

The advanced folder has every car and every street. Two examples.

**Every car of one morning** (accident, half of the drivers on the app):
"""),
    code("""
cars = pd.read_parquet(f"{D}/advanced/la_rochelle/scenarios/trips/incident_app50_seed1.parquet")
cars = cars[(cars.requested_departure_s >= 7 * 3600) & (cars.requested_departure_s < 9 * 3600)]
print(len(cars), "cars")
cars["trip_min"] = cars.trip_time_s / 60
cars.groupby("app_user").trip_min.describe().round(1)
"""),
    code("""
cars.pivot(columns="app_user", values="trip_min").plot.hist(bins=80, alpha=0.6, range=(0, 40),
                                                             title="Trip times, app users (True) vs others (False)")
plt.xlabel("minutes")
plt.show()
"""),
    md("""
**A map of the city at 8:15** (normal morning, nobody on the app): every street coloured by its speed.
"""),
    code("""
from matplotlib.collections import LineCollection

streets = pd.read_csv(f"{D}/advanced/la_rochelle/network/streets.csv")
flow = pd.read_parquet(f"{D}/advanced/la_rochelle/scenarios/streets/normal_app0_seed1.parquet")
at = flow[(flow.begin_s >= 8 * 3600) & (flow.end_s <= 8.5 * 3600)].groupby("edge_id").speed_mps.mean()
streets["ratio"] = streets.street_id.map(at * 3.6) / streets.speed_limit_kmh

def xy(wkt):
    return [tuple(map(float, p.split())) for p in wkt[wkt.index("(") + 1:-1].split(", ")]

lines = [xy(w) for w in streets.geometry_wkt]
colors = [plt.cm.RdYlGn(min(1, r / 0.8)) if r == r else (0.85, 0.85, 0.85, 1) for r in streets.ratio]
fig, ax = plt.subplots(figsize=(10, 8))
ax.add_collection(LineCollection(lines, colors=colors, linewidths=0.4 + 0.4 * streets.lanes))
ax.autoscale(); ax.set_aspect(1.45); ax.axis("off")
ax.set_title("La Rochelle, 8:00-8:30: green flows, red jammed")
plt.show()
"""),
    md("""
## What we learned

* The app cuts the average morning trip by about a fifth and halves the time lost in traffic.
* Most of the gain comes with the first half of the drivers; the drivers without the app gain too.
* An accident costs less when part of the drivers can be rerouted - but with *everybody* on the app it
  costs more again.
* On two roads, rerouting helps only once one road is nearly full, and only a share of the drivers needs it.

**Ideas to go further** with the `advanced/` data: predict each car's trip time; forecast street speeds 15
minutes ahead on the road graph; estimate the causal effect of the app per origin-destination pair (the same
trips exist in every scenario of a seed, matched by `seed` and `trip_id`); compare the simulation with the 2023 road counts
(`advanced/la_rochelle/calibration/`). The Markov-chain theory behind the two-road case is explained in the
companion notebook.
"""),
]


# --------------------------------------------------------------------------- theory notebook

def module_source(path: Path, drop: list[str]) -> str:
    text = path.read_text()
    text = text.split('\nif __name__ == "__main__":')[0]
    for pattern in drop:
        text = re.sub(pattern, "", text, flags=re.M)
    return text.strip()


THEORY_SRC = module_source(ROOT / "rerouting" / "theory.py", [r"^from __future__ import annotations\n"])
MARKOV_SRC = module_source(ROOT / "rerouting" / "markov.py", [r"^from __future__ import annotations\n",
                                                               r"^from rerouting\.theory import Road\n"])

THEORY = [
    md("""
# A Markov chain for live rerouting, tested against the simulations

This companion notebook explains the **theory** behind the two-road experiments of the dataset and checks it
against the simulations. A short road and a longer detour join the same two places; some drivers follow a
live-rerouting app. Can a very simple random model predict what the detailed traffic simulator (SUMO) does?

**The model is a Markov chain with three rules, all taken from the network (nothing is fitted):**

1. **Every car ahead of you costs you time**: 0.54 s if it is driving (the time to drive the 7.5 m one car and
   its gap take at 50 km/h), `3600/C` s if it is waiting at your road's bottleneck (`C` = cars per hour the
   bottleneck lets through).
2. **Cars enter one at a time** (the next one is drawn from the waiting line); a queue longer than its road
   blocks the entrance.
3. **The app shows the average trip time of the last few minutes**, seen from inside the network.

Its steady state gives three equations; we keep only what held in two blind tests (at the end).
"""),
    code("""
import math
import numpy as np
import pandas as pd
import matplotlib.pyplot as plt
from dataclasses import dataclass

plt.rcParams.update({"figure.figsize": (9, 4.5), "axes.grid": True, "grid.alpha": 0.3})
E = "%s/advanced/experiments/two_road"
""" % D),
    md("""
## The equations (steady state of the chain)

**A road is a drive plus a queue.** At `x` cars per hour:

$$ t(x) = T\\,\\Big(1 + \\frac{x}{6667}\\Big) + \\frac{1800\\,x}{C\\,(C - x)} $$

**Rerouting helps only above d\\***, where the short road stops beating an empty detour: `t1(d*) = T2`.
**Only a share p\\* of drivers needs the app**: rerouters fill the detour until both roads take the same time;
the others stay on the short road, so beyond `p* = 1 - x/d` more app users have nothing to balance.
"""),
    code(THEORY_SRC),
    md("""
## The two roads, measured once

`T` (time on an empty road) and `C` (capacity) of each road are measured in the calibration runs, where every
driver is forced onto one road: `T` at 100 cars per hour, `C` = the highest exit rate once saturated.
"""),
    code("""
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
"""),
    md("## Test 1: a road is a drive plus a queue"),
    code("""
x = np.linspace(0, 0.97, 200)
fig, ax = plt.subplots()
for rd, route, style in [(short, "short", "-"), (detour, "long", "--")]:
    ax.plot(x * rd.C, [rd.time(v * rd.C) / 60 for v in x], "k" + style, label=f"theory, {route} road")
    r = cal[(cal.forced_route == route) & (cal.demand_veh_per_h < rd.C)].groupby("demand_veh_per_h").mean_trip_time_s.mean()
    ax.plot(r.index, r / 60, "o", label=f"SUMO, {route} road")
ax.set_xlabel("cars per hour on the road"); ax.set_ylabel("trip time (min)"); ax.set_ylim(0, 8); ax.legend()
plt.show()
"""),
    md("""
## The chain itself

The equations are the chain's steady state. The chain itself runs second by second (many copies side by
side) and gives the same statistics as SUMO. This is the project's code, unchanged.
"""),
    code(MARKOV_SRC),
    md("## Test 2: when does rerouting start to help?"),
    code("""
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
"""),
    md("## Test 3: how many drivers need the app?"),
    code("""
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
"""),
    md("""
## Blind tests: where the theory is reliable

The chain's predictions for 216 situations were committed to the project repository **before** SUMO was run
on fresh random seeds; a statement holds if 80 % of its situations pass. It was tested twice: on this
network (seeds 6-10, files `*_blind_seeds_6_10`) and on a network with a 2.4 km entry road (seeds 11-15,
files `*_long_entry_blind_seeds_11_15`).

| statement | two roads | long entry road |
|---|---|---|
| a road is a drive plus a queue | 10/10 | 9/10 |
| rerouting helps only above d* | 6/7 | 7/7 |
| app users fill the detour only as far as needed | 29/30 | 25/30 |
| trip time against the share of app users | 27/30 | 25/30 |
| trip time and trips finished against traffic | 51/54 | 47/54 |

**Where it is not reliable** (tested, failed): how often the crowd swings between roads, what old information
costs when nearly everyone follows the app, and the routes of drivers who know the usual traffic. The swings
matter: at 1,800 cars per hour, everybody on the app makes trips 74 % longer than 60 % on the app
(12.4 against 7.1 minutes). In SUMO,
drivers keep re-checking the app while driving to the fork; the chain decides once. The swinging is real,
though - here it is in the simulation:
"""),
    code("""
m = pd.read_csv(f"{E}/two_road_share_series_minute_series.csv")
one = m[(m.demand_veh_per_h == 1800) & (m.app_share == 1.0) & (m.seed == 1)]
plt.plot(one.minute_start_s / 60, one.share_on_detour * 100)
plt.xlabel("minute"); plt.ylabel("app users on the detour (%)")
plt.title("1,800 cars/h, every driver on the app: the crowd swings between the roads"); plt.show()
"""),
    md("""
## Summary

* Rerouting saves almost nothing below **d\\*** (about 730 cars per hour here: a few seconds from random
  queues) and a great deal above.
* Only a share **p\\*** of drivers needs the app (about 40-60 % here); beyond it, more app users bring
  nothing - and with everyone swinging together, trips can get much longer.
* No routing beats capacity.
* When nearly everybody follows the same advice, the crowd swings between roads - a real effect that this
  simple chain does not capture.

Full code, the pre-registered predictions and the scoring:
[github.com/tudor-opran/traffic-rerouting-sumo](https://github.com/tudor-opran/traffic-rerouting-sumo).
"""),
]


def write(folder: str, cells, slug: str, title: str):
    out = HERE / folder
    out.mkdir(exist_ok=True)
    (out / "notebook.ipynb").write_text(json.dumps(notebook(cells), indent=1))
    (out / "kernel-metadata.json").write_text(json.dumps({
        "id": f"{USER}/{slug}", "title": title, "code_file": "notebook.ipynb", "language": "python",
        "kernel_type": "notebook", "is_private": False, "enable_gpu": False, "enable_internet": False,
        "dataset_sources": [f"{USER}/{DATASET}"], "competition_sources": [], "kernel_sources": []}, indent=1))
    # a plain script of the code cells, for testing outside Kaggle (dataset path given by DATASET_DIR)
    script = ["import os, matplotlib; matplotlib.use('Agg')", "import matplotlib.pyplot as _plt; _plt.show = lambda *a, **k: _plt.close('all')"]
    script += [src for kind, src in cells if kind == "code"]
    text = "\n\n".join(script).replace(D, "__DATASET__")
    text = text.replace('"__DATASET__', 'os.environ["DATASET_DIR"] + "')
    (out / f"{folder}_test.py").write_text(text + "\nprint('notebook ran to the end')\n")


if __name__ == "__main__":
    write("eda", EDA, "does-live-rerouting-beat-traffic-jams-eda", "Does live rerouting beat traffic jams? EDA")
    write("theory", THEORY, "a-markov-chain-for-live-rerouting", "A Markov chain for live rerouting")
    for f in ("larochelle", "experiments"):
        for name in ("notebook.ipynb", "kernel-metadata.json"):
            (HERE / f / name).unlink(missing_ok=True)
        if (HERE / f).exists():
            (HERE / f).rmdir()
    print("wrote kaggle/eda and kaggle/theory")
