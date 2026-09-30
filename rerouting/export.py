"""Clean, documented datasets for publication (Kaggle), built from the project's inputs and results.

    python -m rerouting.export 1    # dataset/does-live-rerouting-beat-traffic-jams, version 1 (two roads, grid)
    python -m rerouting.export 2    # version 2: adds the full-scale La Rochelle simulation

Every table is CSV (small) or Parquet (large, zstd), with units in the column names
(_s seconds, _m metres, _kmh km/h, _pct percent) and one README data card per dataset.
"""

from __future__ import annotations

import csv
import gzip
import json
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "dataset"
RESULTS = ROOT / "results"
DATA = ROOT / "data" / "larochelle"


def write_csv(path: Path, header: list[str], rows) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    n = 0
    with open(path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(header)
        for r in rows:
            w.writerow(["" if v is None else v for v in r])
            n += 1
    return n


def clock(seconds: float) -> str:
    return f"{int(seconds // 3600):02d}:{int(seconds % 3600 // 60):02d}:{int(seconds % 60):02d}"


def jsonl(name: str) -> list[dict]:
    path = RESULTS / f"{name}.jsonl"
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()] if path.exists() else []


# --------------------------------------------------------------------------- La Rochelle


def larochelle(out: Path) -> Path:
    import pyarrow as pa
    import pyarrow.parquet as pq
    from rerouting import larochelle as L
    net = L.load_net()
    counts = {}
    # network
    rows = []
    for e in net.getEdges():
        shape = [net.convertXY2LonLat(x, y) for x, y in e.getShape()]
        rows.append((e.getID(), e.getName() or None, e.getType().replace("highway.", ""), e.getLaneNumber(),
                     round(e.getSpeed() * 3.6, 1), round(e.getLength(), 1), e.getFromNode().getID(), e.getToNode().getID(),
                     "LINESTRING (" + ", ".join(f"{lon:.6f} {lat:.6f}" for lon, lat in shape) + ")"))
    counts["network/streets.csv"] = write_csv(out / "network/streets.csv", ["street_id", "name", "road_type", "lanes",
                                             "speed_limit_kmh", "length_m", "from_junction", "to_junction", "geometry_wkt"], rows)
    tls = [(t.getID(), len(t.getLinks())) for t in net.getTrafficLights()]
    counts["network/traffic_lights.csv"] = write_csv(out / "network/traffic_lights.csv", ["light_id", "controlled_links"], tls)
    # inputs
    counts["inputs/population_cells.csv"] = write_csv(
        out / "inputs/population_cells.csv", ["lon", "lat", "people", "adults_18_64", "commune_code"],
        ((r["lon"], r["lat"], r["people"], r["adults"], r["commune"] or None) for r in L.read_csv_gz("population.csv.gz")))
    counts["inputs/workplaces.csv"] = write_csv(
        out / "inputs/workplaces.csv", ["lon", "lat", "jobs_estimate", "commune_code"],
        ((r["lon"], r["lat"], r["jobs"], r["commune"]) for r in L.read_csv_gz("jobs.csv.gz")))
    counts["inputs/commute_flows.csv"] = write_csv(
        out / "inputs/commute_flows.csv", ["home_commune_code", "work_commune_code", "commuters_2022"],
        ((r["home"], r["work"], r["commuters"]) for r in L.read_csv_gz("flows.csv.gz")))
    with gzip.open(DATA / "communes.json.gz", "rt") as f:
        communes = json.load(f)
    counts["inputs/communes.csv"] = write_csv(
        out / "inputs/communes.csv", ["commune_code", "name", "lon", "lat", "population", "share_on_map", "car_share_to_work"],
        ((c, v["name"], v["lon"], v["lat"], int(v["population"]), v["on_map"], v["car_share"]) for c, v in sorted(communes.items())))
    with gzip.open(DATA / "counts.json.gz", "rt") as f:
        sections = json.load(f)
    counts["inputs/traffic_counts.csv"] = write_csv(
        out / "inputs/traffic_counts.csv", ["road", "daily_vehicles_2023", "geometry_wkt"],
        ((s["road"], s["daily"], "LINESTRING (" + ", ".join(f"{lon} {lat}" for lon, lat in s["coords"]) + ")") for s in sections))
    z = L.zones(net)
    counts["inputs/entry_roads.csv"] = write_csv(
        out / "inputs/entry_roads.csv", ["street_id", "kind", "daily_vehicles", "lon", "lat"],
        ((g["edge"], g["kind"], g["daily"], *[round(v, 6) for v in net.convertXY2LonLat(*g["xy"])]) for g in z["gates"]))
    # calibration
    cal = jsonl("larochelle_calibration")
    counts["calibration/calibration_grid.csv"] = write_csv(
        out / "calibration/calibration_grid.csv",
        ["commute_share", "through_trips_thousands", "fit_sections", "fit_share_geh_under_5", "fit_r2",
         "check_sections", "check_share_geh_under_5", "check_r2", "check_model_over_count"],
        ((r["commute"], r["crossing"], r["fit"]["sections"], round(r["fit"]["geh_under_5"], 3), round(r["fit"]["r2"], 3),
          r["check"]["sections"], round(r["check"]["geh_under_5"], 3), round(r["check"]["r2"], 3),
          round(r["check"]["model_over_count"], 3)) for r in cal))
    dump = sorted((L.BUILD / "usual_s1" / "019").glob("dump_*.xml"))
    if dump:
        flows = L.dump_flows(dump[0], *L.PEAK)
        counts["calibration/count_comparison.csv"] = write_csv(
            out / "calibration/count_comparison.csv",
            ["road", "daily_vehicles_2023", "target_peak_hour_vehicles", "simulated_peak_hour_vehicles", "geh", "used_for", "street_ids"],
            ((s["road"], s["daily"], round(L.PEAK_SHARE_OF_DAILY * s["daily"]), round(sum(flows.get(e, 0) for e in es)),
              round(L.geh(sum(flows.get(e, 0) for e in es), L.PEAK_SHARE_OF_DAILY * s["daily"]), 2),
              "check" if L.holdout(s["road"]) else "fit", " ".join(es)) for s, es in L.counted_edges(net)))
    # demand and usual routes
    for seed in L.SEEDS:
        path = L.BUILD / f"demand_s{seed}.json"
        if not path.exists():
            continue
        routes = {i: r for i, _, r in json.loads((L.BUILD / f"usual_s{seed}.json").read_text())}
        d = json.loads(path.read_text())
        (out / "demand").mkdir(parents=True, exist_ok=True)
        pq.write_table(pa.table({
            "trip_id": [r[0] for r in d], "requested_departure_s": [r[1] for r in d], "departure_clock": [clock(r[1]) for r in d],
            "origin_street": [r[2] for r in d], "destination_street": [r[3] for r in d], "kind": [r[4] for r in d],
            "home_commune_code": [r[5] or None for r in d], "work_commune_code": [r[6] or None for r in d],
            "usual_route": [routes.get(r[0]) for r in d]}), out / f"demand/trips_seed{seed}.parquet", compression="zstd")
        counts[f"demand/trips_seed{seed}.parquet"] = len(d)
    # scenarios
    runs = jsonl("larochelle")
    keys = ["day", "share", "seed", "requested", "completed", "journey", "time_lost", "distance_km", "waiting",
            "reroutes", "journey_rerouters", "journey_others", "teleports", "minutes"]
    names = ["day", "app_share", "seed", "trips_7_to_9", "share_finished_by_11", "mean_trip_time_s", "mean_time_lost_s",
             "mean_distance_km", "mean_waiting_time_s", "mean_reroutes_per_app_user", "mean_trip_time_app_users_s",
             "mean_trip_time_others_s", "teleports", "compute_minutes"]
    counts["scenarios/runs.csv"] = write_csv(out / "scenarios/runs.csv", names,
                                             ([None if r.get(k) != r.get(k) else r.get(k) for k in keys] for r in runs))
    for r in runs:
        tag = f"{r['day']}_app{int(r['share'] * 100)}_seed{r['seed']}"
        src = L.BUILD / "runs" / f"{r['day']}_p{r['share']:g}_s{r['seed']}"
        for kind in ("trips", "edges"):
            if (src / f"{kind}.parquet").exists():
                dst = out / "scenarios" / ("trips" if kind == "trips" else "streets") / f"{tag}.parquet"
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy(src / f"{kind}.parquet", dst)
    site = L.incident_site(net) if (L.BUILD / "usual_s1").exists() else []
    counts["scenarios/incident.csv"] = write_csv(out / "scenarios/incident.csv",
                                                 ["street_id", "blocked_lane", "blocked_lane_speed_kmh", "other_lanes_speed_kmh",
                                                  "begin_clock", "end_clock"],
                                                 ((e, f"{e}_0", 3.6, 20, "07:45:00", "08:30:00") for e in site))
    print(json.dumps(counts, indent=1))
    return out


# --------------------------------------------------------------------------- the controlled experiments


def experiments(out: Path) -> Path:
    summary = ["requested", "completed", "journey", "time_lost", "vehicle_hours", "waiting", "reroutes",
               "journey_rerouters", "journey_others", "throughput", "long_share", "long_share_rerouters",
               "long_share_others", "journey_short_route", "journey_long_route", "teleports"]
    rename = {"requested": "trips", "completed": "share_finished", "journey": "mean_trip_time_s",
              "time_lost": "mean_time_lost_s", "vehicle_hours": "vehicle_hours", "waiting": "mean_waiting_time_s",
              "reroutes": "mean_reroutes_per_app_user", "journey_rerouters": "mean_trip_time_app_users_s",
              "journey_others": "mean_trip_time_others_s", "throughput": "exit_rate_veh_per_h",
              "long_share": "share_on_detour", "long_share_rerouters": "share_on_detour_app_users",
              "long_share_others": "share_on_detour_others", "journey_short_route": "mean_trip_time_short_road_s",
              "journey_long_route": "mean_trip_time_detour_s", "teleports": "teleports"}
    setting = {"policy": "policy", "share": "app_share", "route": "forced_route", "window": "averaging_window_s",
               "period": "recheck_period_s", "synchronize": "synchronized_rechecks"}
    counts = {}
    sources = [(p, "") for p in sorted(RESULTS.glob("*.jsonl"))] + \
              [(p, "_blind_seeds_6_10") for p in sorted((RESULTS / "holdout").glob("*.jsonl"))] + \
              [(p, "_long_entry") for p in sorted((RESULTS / "long_entry").glob("*.jsonl"))] + \
              [(p, "_long_entry_blind_seeds_11_15") for p in sorted((RESULTS / "long_entry" / "holdout").glob("*.jsonl"))]
    for path, suffix in sources:
        name = path.stem
        if name.startswith("larochelle") or name == "city":   # the city runs have their own dataset
            continue
        rows = [json.loads(line) for line in path.read_text().splitlines() if line.strip()]   # this file, not its namesake
        if not rows:
            continue
        cols = ["network", "demand_veh_per_h", "seed"] + [v for k, v in setting.items() if any(k in r for r in rows)] \
            + [rename[k] for k in summary if any(k in r for r in rows)]
        keys = ["network", "demand", "seed"] + [k for k in setting if any(k in r for r in rows)] \
            + [k for k in summary if any(k in r for r in rows)]
        folder = "two_road" if name.startswith("two_road") else "grid"
        name += suffix
        counts[f"{folder}/{name}.csv"] = write_csv(out / folder / f"{name}.csv", cols,
                                                   ([None if r.get(k) != r.get(k) else r.get(k) for k in keys] for r in rows))
        series = [(r["demand"], r.get("share"), r.get("window"), r.get("synchronize"), r["seed"], t, q, n)
                  for r in rows for t, q, *rest in r.get("long_share_series", []) for n in (rest[0] if rest else None,)]
        if series:
            counts[f"{folder}/{name}_minute_series.csv"] = write_csv(
                out / folder / f"{name}_minute_series.csv",
                ["demand_veh_per_h", "app_share", "averaging_window_s", "synchronized_rechecks", "seed", "minute_start_s",
                 "share_on_detour", "cars_in_minute"], series)
    return out


# --------------------------------------------------------------------------- Kaggle metadata (usability)

CARDS = ROOT / "rerouting" / "datacards"
KAGGLE_USER = "tudoropr"
KEYWORDS = ["transportation", "geospatial analysis", "europe", "tabular"]
FILES = {
    "beginner_scenarios.csv": "START HERE: one row per simulated La Rochelle morning (day, share of app users, seed) with its average trip time",
    "beginner_traffic_by_time.csv": "The whole city every 5 minutes of each morning: cars on the road, average speed, time lost",
    "beginner_traffic_by_area.csv": "Each area (commune) every 15 minutes of each morning: speed, share of the speed limit, km and hours driven, hours lost",
    "beginner_trips_between_areas.csv": "Trips from each home area to each work area, per morning: count, average trip time and distance",
    "beginner_two_roads.csv": "The two-road experiment: average trip time for each traffic level and share of app users, one row per run",
    "la_rochelle_trips.parquet": "Every car of every La Rochelle morning (30 mornings), with its planned and driven route",
    "la_rochelle_street_traffic.parquet": "Traffic on every used street every 5 minutes (2 minutes in two mornings) of every La Rochelle morning",
    "la_rochelle_demand.parquet": "The morning car trips of each seed: origin, destination, trip kind, home and work communes, usual route",
    "la_rochelle_runs.csv": "One summary line per La Rochelle morning, in seconds",
    "la_rochelle_incident.csv": "Where and when the accident happens on the incident mornings",
    "la_rochelle_streets.csv": "Every street of the simulated road network with its full geometry (WKT)",
    "la_rochelle_traffic_lights.csv": "Signalised junctions of the network",
    "la_rochelle_input_population_cells.csv": "Input: residents on a 200 m grid (INSEE Filosofi 2021)",
    "la_rochelle_input_workplaces.csv": "Input: workplaces with employees and their estimated jobs (SIRENE)",
    "la_rochelle_input_commute_flows.csv": "Input: commuters between communes (INSEE 2022), for flows touching the map",
    "la_rochelle_input_communes.csv": "Input: communes involved, with their share on the map and car share",
    "la_rochelle_input_traffic_counts.csv": "Input: counted road sections with their 2023 average daily traffic",
    "la_rochelle_input_entry_roads.csv": "Input: roads where traffic enters or leaves the simulated area",
    "la_rochelle_calibration_grid.csv": "Calibration: each tested traffic volume and how well it matches the road counts",
    "la_rochelle_count_comparison.csv": "Calibration: each counted section, its target and simulated rush-hour flow",
    "two_road_runs.csv": "Every simulation on two parallel roads (short road and detour), one line per run",
    "two_road_minute_series.csv": "Minute by minute, the share of app users on the detour in the two-road runs (the herding series)",
    "grid_runs.csv": "Every simulation on a 6 x 6 grid of city streets, one line per run",
}
LAROCHELLE_SOURCES = (
    "Street network: OpenStreetMap contributors (ODbL). Population: INSEE, Filosofi 2021 200 m grid. "
    "Workplaces: INSEE SIRENE register and its geolocation. Commuting: INSEE 2022 home-work flows between "
    "communes; car shares from the INSEE 2023 census. Road counts: DREAL Nouvelle-Aquitaine, TMJA 2023 "
    "(SIGENA). Simulation: Eclipse SUMO 1.27.1; code at https://github.com/tudor-opran/traffic-rerouting-sumo")
EXPERIMENT_SOURCES = ("Simulated with Eclipse SUMO 1.27.1 by the project "
                      "https://github.com/tudor-opran/traffic-rerouting-sumo (all runs and seeds).")


def columns_of(path: Path) -> list[str]:
    if path.suffix == ".csv":
        with open(path) as f:
            return next(csv.reader(f))
    import pyarrow.parquet as pq
    return pq.read_schema(path).names


def describe(rel: str, files: dict) -> str:
    for key, text in files.items():
        if rel == key or (key.endswith("/") and rel.startswith(key)):
            stem = Path(rel).stem
            return f"{text} ({stem})" if key.endswith("/") else text
    raise KeyError(f"no description for {rel}")


def metadata(out: Path, title: str, slug: str, subtitle: str, licence: str, card: str, files: dict, sources: str) -> None:
    """dataset-metadata.json with every file and column described (Kaggle usability: completeness,
    credibility, compatibility), the data card as README and description, and a cover image."""
    assert 6 <= len(title) <= 50 and 20 <= len(subtitle) <= 80, (title, subtitle)
    cols = json.loads((CARDS / "columns.json").read_text())
    resources = []
    for path in sorted(p for p in out.rglob("*") if p.suffix in (".csv", ".parquet")):
        rel = path.relative_to(out).as_posix()
        fields = []
        for c in columns_of(path):
            if c not in cols:
                raise KeyError(f"column {c!r} of {rel} has no description in columns.json")
            fields.append({"name": c, "description": cols[c][1], "type": cols[c][0]})
        resources.append({"path": rel, "description": describe(rel, files), "schema": {"fields": fields}})
    text = (CARDS / card).read_text()
    shutil.copy(CARDS / card, out / "README.md")
    (out / "dataset-metadata.json").write_text(json.dumps({
        "title": title, "id": f"{KAGGLE_USER}/{slug}", "subtitle": subtitle, "description": text,
        "licenses": [{"name": licence}], "keywords": KEYWORDS, "expectedUpdateFrequency": "never",
        "userSpecifiedSources": sources, "image": "cover.png", "resources": resources}, indent=1))


def cover_from_figure(src: Path, dst: Path) -> None:
    """A 1120 x 560 cover image cut from a figure."""
    from PIL import Image
    im = Image.open(src).convert("RGB")
    w, h = im.size
    target = 2.0
    if w / h > target:
        nw = int(h * target)
        im = im.crop(((w - nw) // 2, 0, (w - nw) // 2 + nw, h))
    else:
        nh = int(w / target)
        im = im.crop((0, 0, w, nh))
    im.resize((1120, 560)).save(dst)


def cover_larochelle(net, dst: Path) -> None:
    """A 1120 x 560 cover: the streets of La Rochelle coloured by speed at 8:15 on the accident morning."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import numpy as np
    from matplotlib.collections import LineCollection
    from rerouting import larochelle as L
    run = np.load(L.BUILD / "runs" / "incident_p0_s1" / "frames.npz")
    k = int(np.argmin(np.abs(run["times"] - 8.25 * 3600)))
    ids = list(run["edges"])
    r = run["ratio"][k]
    cmap = matplotlib.colormaps["RdYlGn"]
    colors = [(0.86, 0.86, 0.84, 1.0) if np.isnan(v) else cmap(min(1.0, v / 0.8)) for v in r]
    fig = plt.figure(figsize=(11.2, 5.6), dpi=100)
    ax = fig.add_axes([0, 0, 1, 1])
    ax.add_collection(LineCollection([net.getEdge(e).getShape() for e in ids], colors=colors,
                                     linewidths=[0.4 + 0.5 * net.getEdge(e).getLaneNumber() for e in ids]))
    (x0, y0), (x1, y1) = net.getBBoxXY()
    cx, cy, half_w = (x0 + x1) / 2, (y0 + y1) / 2, (x1 - x0) / 2
    ax.set_xlim(cx - half_w, cx + half_w)
    ax.set_ylim(cy - half_w / 2, cy + half_w / 2)
    ax.axis("off")
    ax.text(0.015, 0.95, "La Rochelle, 8:15 - green: flowing, red: jammed", transform=ax.transAxes, fontsize=16)
    fig.savefig(dst, dpi=100, facecolor="white")
    plt.close(fig)


def street_areas(streets: list[dict]) -> dict[str, str]:
    """The commune (by name) containing each street's midpoint; "outside the communes" otherwise."""
    from shapely.geometry import Point, shape
    from rerouting import larochelle as L
    outlines = [(f["properties"]["nom"], shape(f["geometry"]))
                for f in json.load(open(L.RAW / "communes17.geojson"))["features"]]
    out = {}
    for r in streets:
        pts = [tuple(map(float, p.split())) for p in r["geometry_wkt"][r["geometry_wkt"].index("(") + 1:-1].split(", ")]
        mid = Point(pts[len(pts) // 2])
        out[r["street_id"]] = next((name for name, geom in outlines if geom.contains(mid)), "outside the communes")
    return out


def beginner(out: Path, advanced: Path, version: int) -> None:
    """A high-level (meso) view in plain units - minutes, km/h, clock times, percent - small enough for a spreadsheet."""
    import pyarrow.parquet as pq
    rows = []
    for name in ("two_road_share_series", "two_road_demand"):
        for r in jsonl(name):
            if r.get("policy") not in ("live", "no_information"):
                continue
            share = r.get("share", 0.0) if r["policy"] == "live" else 0.0
            rows.append((r["demand"], round(share * 100), r["seed"], round(r["journey"] / 60, 2),
                         None if r.get("journey_rerouters") != r.get("journey_rerouters") else round(r["journey_rerouters"] / 60, 2),
                         None if r.get("journey_others") != r.get("journey_others") else round(r["journey_others"] / 60, 2),
                         round(100 * r["completed"], 1)))
    write_csv(out / "two_roads.csv", ["traffic_per_hour", "app_share_pct", "seed", "avg_trip_minutes",
                                      "avg_trip_minutes_app_users", "avg_trip_minutes_others", "finished_pct"], sorted(set(rows)))
    if version < 2:
        return
    lr = advanced / "la_rochelle"
    runs = list(csv.DictReader(open(lr / "scenarios" / "runs.csv")))

    def minutes(x):
        return round(float(x) / 60, 2) if x not in ("", None) else None
    write_csv(out / "scenarios.csv",
              ["day", "app_share_pct", "seed", "trips_7_to_9", "avg_trip_minutes", "avg_time_lost_minutes", "finished_pct",
               "avg_distance_km", "avg_trip_minutes_app_users", "avg_trip_minutes_others"],
              ((r["day"], round(100 * float(r["app_share"])), r["seed"], r["trips_7_to_9"], minutes(r["mean_trip_time_s"]),
                minutes(r["mean_time_lost_s"]), round(100 * float(r["share_finished_by_11"]), 1),
                round(float(r["mean_distance_km"]), 2), minutes(r["mean_trip_time_app_users_s"]),
                minutes(r["mean_trip_time_others_s"])) for r in runs))
    streets = list(csv.DictReader(open(lr / "network" / "streets.csv")))
    area = street_areas(streets)
    limit = {r["street_id"]: float(r["speed_limit_kmh"]) / 3.6 for r in streets}
    by_time, by_area = [], []
    for r in runs:
        tag = f"{r['day']}_app{round(100 * float(r['app_share']))}_seed{r['seed']}"
        t = pq.read_table(lr / "scenarios" / "streets" / f"{tag}.parquet").to_pydict()
        tb: dict = {}
        ab: dict = {}
        def pieces(b, end, width):
            """(bin start, fraction of the interval inside it) for every bin the interval [b, end) overlaps."""
            k = int(b // width) * width
            while k < end:
                yield k, (min(end, k + width) - max(b, k)) / (end - b)
                k += width
        for b, end, e, veh_s, v, lost in zip(t["begin_s"], t["end_s"], t["edge_id"], t["vehicle_seconds"],
                                             t["speed_mps"], t["time_lost_s"]):
            if not veh_s or v is None:
                continue
            for k5, f in pieces(b, end, 300):          # intervals are 5 or 2 minutes long: split by overlap
                a = tb.setdefault(k5, [0.0, 0.0, 0.0])
                a[0] += f * veh_s
                a[1] += f * v * veh_s
                a[2] += f * (lost or 0.0)
            for k15, f in pieces(b, end, 900):
                a = ab.setdefault((area.get(e, "outside the communes"), k15), [0.0, 0.0, 0.0, 0.0])
                a[0] += f * veh_s
                a[1] += f * v * veh_s
                a[2] += f * (min(1.0, v / limit[e]) * veh_s if e in limit else 0.0)
                a[3] += f * (lost or 0.0)
        for k5 in sorted(tb):
            veh_s, sv, lost = tb[k5]
            by_time.append((r["day"], round(100 * float(r["app_share"])), r["seed"], clock(k5)[:5],
                            round(veh_s / 300, 1), round(3.6 * sv / veh_s, 1), round(lost / 3600, 2)))
        for (name, k15) in sorted(ab):
            veh_s, sv, sl, lost = ab[(name, k15)]
            by_area.append((r["day"], round(100 * float(r["app_share"])), r["seed"], name, clock(k15)[:5],
                            round(3.6 * sv / veh_s, 1), round(100 * sl / veh_s, 1), round(sv / 1000, 1),
                            round(veh_s / 3600, 2), round(lost / 3600, 2)))
    write_csv(out / "traffic_by_time.csv", ["day", "app_share_pct", "seed", "time_clock", "cars_on_road", "avg_speed_kmh",
                                            "time_lost_hours"], by_time)
    write_csv(out / "traffic_by_area.csv", ["day", "app_share_pct", "seed", "area", "time_clock", "avg_speed_kmh",
                                            "speed_vs_limit_pct", "vehicle_km", "vehicle_hours", "time_lost_hours"], by_area)
    names = {c: v["name"] for c, v in json.load(gzip.open(DATA / "communes.json.gz", "rt")).items()}
    od = []
    for seed in sorted({int(r["seed"]) for r in runs}):
        d = pq.read_table(lr / "demand" / f"trips_seed{seed}.parquet").to_pydict()
        ends = {}
        for i, kind, h, w in zip(d["trip_id"], d["kind"], d["home_commune_code"], d["work_commune_code"]):
            home = names.get(h, "outside the map") if kind in ("inside", "out") else "outside the map"
            work = names.get(w, "outside the map") if kind in ("inside", "in") else "outside the map"
            ends[i] = (home, work)
        for r in (r for r in runs if int(r["seed"]) == seed):
            tag = f"{r['day']}_app{round(100 * float(r['app_share']))}_seed{seed}"
            t = pq.read_table(lr / "scenarios" / "trips" / f"{tag}.parquet",
                              columns=["trip_id", "requested_departure_s", "trip_time_s", "time_lost_s", "route_length_m"]).to_pydict()
            agg: dict = {}
            for i, dep, tt, lost, length in zip(t["trip_id"], t["requested_departure_s"], t["trip_time_s"], t["time_lost_s"],
                                                t["route_length_m"]):
                if 7 * 3600 <= dep < 9 * 3600 and i in ends:
                    a = agg.setdefault(ends[i], [0, 0.0, 0.0, 0.0])
                    a[0] += 1
                    a[1] += tt
                    a[2] += lost
                    a[3] += length
            for (home, work), (n, tt, lost, length) in sorted(agg.items()):
                od.append((r["day"], round(100 * float(r["app_share"])), seed, home, work, n, round(tt / n / 60, 2),
                           round(lost / n / 60, 2), round(length / n / 1000, 2)))
    write_csv(out / "trips_between_areas.csv", ["day", "app_share_pct", "seed", "home_area", "work_area", "trip_count",
                                                "avg_trip_minutes", "avg_time_lost_minutes", "avg_distance_km"], od)


def flatten(nested: Path, flat: Path) -> None:
    """One folder, few tidy files (Kaggle attaches file and column descriptions only to files uploaded one by one):
    per-run tables are stacked into one table with its scenario columns."""
    import pyarrow as pa
    import pyarrow.csv as pacsv
    import pyarrow.parquet as pq
    flat.mkdir(parents=True, exist_ok=True)
    for f in sorted((nested / "beginner").glob("*.csv")):
        shutil.copy(f, flat / f"beginner_{f.name}")
    lr = nested / "advanced" / "la_rochelle"
    if lr.exists():
        simple = {"network/streets.csv": "la_rochelle_streets.csv", "network/traffic_lights.csv": "la_rochelle_traffic_lights.csv",
                  "scenarios/runs.csv": "la_rochelle_runs.csv", "scenarios/incident.csv": "la_rochelle_incident.csv",
                  "calibration/calibration_grid.csv": "la_rochelle_calibration_grid.csv",
                  "calibration/count_comparison.csv": "la_rochelle_count_comparison.csv"}
        for src, dst in simple.items():
            shutil.copy(lr / src, flat / dst)
        for f in sorted((lr / "inputs").glob("*.csv")):
            shutil.copy(f, flat / f"la_rochelle_input_{f.name}")

        def stack(files, extra):
            tables = []
            for f in files:
                t = pq.read_table(f)
                for name, value in reversed(extra(f)):
                    t = t.add_column(0, name, pa.array([value] * t.num_rows))
                tables.append(t)
            return pa.concat_tables(tables)

        def scenario(f):
            day, app, seed = f.stem.split("_")
            return [("day", day), ("app_share_pct", int(app[3:])), ("seed", int(seed[4:]))]
        pq.write_table(stack(sorted((lr / "scenarios" / "trips").glob("*.parquet")), scenario),
                       flat / "la_rochelle_trips.parquet", compression="zstd")
        pq.write_table(stack(sorted((lr / "scenarios" / "streets").glob("*.parquet")), scenario),
                       flat / "la_rochelle_street_traffic.parquet", compression="zstd")
        pq.write_table(stack(sorted((lr / "demand").glob("*.parquet")), lambda f: [("seed", int(f.stem.split("seed")[1]))]),
                       flat / "la_rochelle_demand.parquet", compression="zstd")
    ex = nested / "advanced" / "experiments"
    for net in ("two_road", "grid"):
        for kind, pattern in (("runs", "*.csv"), ("minute_series", "*_minute_series.csv")):
            files = [f for f in sorted((ex / net).glob(pattern)) if (kind == "runs") != f.stem.endswith("_minute_series")]
            if not files:
                continue
            tables = []
            for f in files:
                stem = f.stem.replace("_minute_series", "")
                variant = ("long_entry_blind_seeds_11_15" if stem.endswith("_long_entry_blind_seeds_11_15") else
                           "long_entry" if stem.endswith("_long_entry") else
                           "blind_seeds_6_10" if stem.endswith("_blind_seeds_6_10") else "main")
                experiment = stem.split("_blind")[0].split("_long_entry")[0].removeprefix(f"{net}_")
                t = pacsv.read_csv(f)
                t = t.add_column(0, "variant", pa.array([variant] * t.num_rows))
                t = t.add_column(0, "experiment", pa.array([experiment] * t.num_rows))
                tables.append(t)
            table = pa.concat_tables(tables, promote_options="permissive")
            pacsv.write_csv(table, flat / f"{net}_{kind}.csv")


def dataset(version: int) -> Path:
    """The Kaggle dataset, simulation data only. Version 1: the controlled two-road and grid simulations;
    version 2 adds the full-scale La Rochelle simulation. ``beginner/`` is a high-level (meso) view in
    plain units, ``advanced/`` everything in full detail (every car, every street every 5 minutes)."""
    out = OUT / "does-live-rerouting-beat-traffic-jams"
    if out.exists():
        shutil.rmtree(out)
    experiments(out / "advanced" / "experiments")
    if version >= 2:
        from rerouting import larochelle as L
        larochelle(out / "advanced" / "la_rochelle")
        cover_larochelle(L.load_net(), out / "cover.png")
    else:
        cover_from_figure(ROOT / "figures" / "fig3_how_many.png", out / "cover.png")
    beginner(out / "beginner", out / "advanced", version)
    flat = OUT / "kaggle-flat"
    if flat.exists():
        shutil.rmtree(flat)
    flatten(out, flat)
    shutil.copy(out / "cover.png", flat / "cover.png")
    shutil.rmtree(out)
    flat.rename(out)
    metadata(out, title="Does Waze Make Traffic Worse?", slug="does-live-rerouting-beat-traffic-jams",
             subtitle="Same cars, 0-100 % on a Waze-style app: a real city's rush hour simulated", licence="ODbL-1.0",
             card="dataset.md" if version >= 2 else "dataset_v1.md", files=FILES,
             sources=EXPERIMENT_SOURCES + (" " + LAROCHELLE_SOURCES if version >= 2 else ""))
    return out


if __name__ == "__main__":
    print(dataset(int(sys.argv[1]) if len(sys.argv) > 1 else 2))
