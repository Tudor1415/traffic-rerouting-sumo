import os, matplotlib; matplotlib.use('Agg')

import matplotlib.pyplot as _plt; _plt.show = lambda *a, **k: _plt.close('all')


import os
from pathlib import Path
import pandas as pd
import numpy as np
import matplotlib.pyplot as plt

pd.set_option("display.width", 140)
plt.rcParams.update({"figure.figsize": (9, 4.5), "axes.grid": True, "grid.alpha": 0.3,
                     "axes.spines.top": False, "axes.spines.right": False})
# Kaggle mounts the dataset somewhere under /kaggle/input: find its folder
INPUT = Path(os.environ.get("DATASET_DIR", "/kaggle/input"))
D = next(INPUT.rglob("beginner_scenarios.csv")).parent
print("dataset folder:", D)



scen = pd.read_csv(D / "beginner_scenarios.csv")
print(scen.shape)
scen.head(10)



scen.describe().round(2)



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



lost = scen.groupby(["day", "app_share_pct"])["avg_time_lost_minutes"].mean().unstack(0)
lost.plot(marker="o", ylabel="minutes lost in traffic per trip", xlabel="drivers using the app (%)",
          title="Time lost in traffic")
plt.show()
lost.round(2)



n = scen[scen.day == "normal"].groupby("app_share_pct")[["avg_trip_minutes_app_users", "avg_trip_minutes_others"]].mean()
n.plot(marker="o", xlabel="drivers using the app (%)", ylabel="average trip time (minutes)",
       title="Normal morning: app users vs the others")
plt.show()
n.round(2)



cost = (avg.pivot(index="app_share_pct", columns="day", values="mean")
           .assign(extra_minutes=lambda t: t.incident - t.normal))
cost["extra_minutes"].plot.bar(color="tab:red", ylabel="extra minutes per trip", xlabel="drivers using the app (%)",
                               title="Cost of the accident (incident - normal)")
plt.show()
cost.round(2)



t = pd.read_csv(D / "beginner_traffic_by_time.csv")
t.head()



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



a = pd.read_csv(D / "beginner_traffic_by_area.csv")
# a weighted average: each row counts in proportion to the hours cars spent driving there
a["weighted"] = a.speed_vs_limit_pct * a.vehicle_hours

peak = a[(a.time_clock >= "07:30") & (a.time_clock < "08:30")]
sums = peak.groupby(["area", "day", "app_share_pct"])[["weighted", "vehicle_hours"]].sum()
by_area = (sums.weighted / sums.vehicle_hours).rename("speed_vs_limit_pct").reset_index()
normal0 = by_area[(by_area.day == "normal") & (by_area.app_share_pct == 0)].sort_values("speed_vs_limit_pct")
normal0.plot.barh(x="area", y="speed_vs_limit_pct", legend=False, color="tab:red",
                  title="Rush hour (7:30-8:30), nobody on the app: speed as % of the limit")
plt.xlabel("% of the speed limit (lower = more congested)")
plt.show()



# Which areas gain most from the app? Heat map of speed vs limit over the morning, La Rochelle itself
lr = a[(a.area == "La Rochelle") & (a.day == "normal")]
sums = lr.groupby(["app_share_pct", "time_clock"])[["weighted", "vehicle_hours"]].sum()
heat = (sums.weighted / sums.vehicle_hours).unstack()
plt.figure(figsize=(12, 3))
plt.imshow(heat, aspect="auto", cmap="RdYlGn", vmin=heat.values.min(), vmax=100)
plt.yticks(range(len(heat.index)), [f"{p} % app" for p in heat.index])
plt.xticks(range(0, len(heat.columns), 2), heat.columns[::2], rotation=45)
plt.colorbar(label="% of the speed limit")
plt.title("La Rochelle, normal morning: how fast traffic moves")
plt.show()



od = pd.read_csv(D / "beginner_trips_between_areas.csv")
base = od[(od.day == "normal") & (od.app_share_pct == 0)].groupby(["home_area", "work_area"])["trip_count"].mean()
base.sort_values(ascending=False).head(12).round(0)



# Average trip time by home area, without and with the app
g = (od[od.day == "normal"].assign(total=lambda x: x.trip_count * x.avg_trip_minutes)
       .groupby(["home_area", "app_share_pct"])[["total", "trip_count"]].sum())
g = (g.total / g.trip_count).unstack()
g = g.loc[od.groupby("home_area").trip_count.sum().sort_values(ascending=False).index[:8]]
g[[0, 50, 100]].plot.barh(figsize=(9, 5), title="Average trip time by home area (normal morning)")
plt.xlabel("minutes")
plt.show()



two = pd.read_csv(D / "beginner_two_roads.csv")
fig, (a1, a2) = plt.subplots(1, 2, figsize=(13, 4.2))
sweep = two[two.app_share_pct.isin([0, 50, 100])].groupby(["traffic_per_hour", "app_share_pct"]).avg_trip_minutes.mean().unstack()
sweep.plot(marker="o", logy=True, ax=a1, title="Trip time as traffic grows")
a1.set_xlabel("cars per hour"); a1.set_ylabel("minutes (log scale)")
share = two[two.traffic_per_hour.isin([1200, 1500, 1800])].groupby(["app_share_pct", "traffic_per_hour"]).avg_trip_minutes.mean().unstack()
share.plot(marker="o", logy=True, ax=a2, title="Trip time against the share of app users")
a2.set_xlabel("drivers using the app (%)"); a2.set_ylabel("minutes (log scale)")
plt.tight_layout()
plt.show()



# read only the morning we want: accident, half of the drivers on the app, seed 1
cars = pd.read_parquet(D / "la_rochelle_trips.parquet",
                       filters=[("day", "==", "incident"), ("app_share_pct", "==", 50), ("seed", "==", 1)])
cars = cars[(cars.requested_departure_s >= 7 * 3600) & (cars.requested_departure_s < 9 * 3600)]
print(len(cars), "cars")
cars["trip_min"] = cars.trip_time_s / 60
cars.groupby("app_user").trip_min.describe().round(1)



cars.pivot(columns="app_user", values="trip_min").plot.hist(bins=80, alpha=0.6, range=(0, 40),
                                                             title="Trip times, app users (True) vs others (False)")
plt.xlabel("minutes")
plt.show()



from matplotlib.collections import LineCollection

streets = pd.read_csv(D / "la_rochelle_streets.csv")
flow = pd.read_parquet(D / "la_rochelle_street_traffic.parquet",
                       filters=[("day", "==", "normal"), ("app_share_pct", "==", 0), ("seed", "==", 1)])
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

print('notebook ran to the end')
