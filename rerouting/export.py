"""Clean, documented datasets for publication (Kaggle), built from the project's inputs and results.

    python -m rerouting.export larochelle     # dataset/larochelle-morning-rush
    python -m rerouting.export experiments    # dataset/rerouting-experiments

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


def larochelle() -> Path:
    import pyarrow as pa
    import pyarrow.parquet as pq
    from rerouting import larochelle as L
    out = OUT / "larochelle-morning-rush"
    if out.exists():
        shutil.rmtree(out)
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
    with open(L.network(), "rb") as src, gzip.open(out / "network/larochelle.net.xml.gz", "wb") as dst:
        shutil.copyfileobj(src, dst)
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
            "trip_id": [r[0] for r in d], "departure_s": [r[1] for r in d], "departure_clock": [clock(r[1]) for r in d],
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
                                                 ["street_id", "closed_lane", "begin_clock", "end_clock", "other_lanes_speed_kmh"],
                                                 ((e, f"{e}_0", "07:45:00", "08:30:00", 20) for e in site))
    gif = ROOT / "figures" / "larochelle.gif"
    if gif.exists():
        (out / "animation").mkdir(parents=True, exist_ok=True)
        shutil.copy(gif, out / "animation" / "larochelle.gif")
    shutil.copy(ROOT / "rerouting" / "datacards" / "larochelle.md", out / "README.md")
    (out / "dataset-metadata.json").write_text(json.dumps({
        "title": "La Rochelle morning rush: traffic simulation",
        "id": "tudoropr/la-rochelle-morning-rush-traffic-simulation",
        "subtitle": "Every car of a calibrated SUMO simulation of La Rochelle, with and without live rerouting",
        "licenses": [{"name": "ODbL-1.0"}],
        "keywords": ["transportation", "cities and urban areas", "simulations", "france"]}, indent=1))
    print(json.dumps(counts, indent=1))
    return out


# --------------------------------------------------------------------------- the controlled experiments


def experiments() -> Path:
    out = OUT / "rerouting-experiments"
    if out.exists():
        shutil.rmtree(out)
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
    for path in sorted(RESULTS.glob("*.jsonl")):
        name = path.stem
        if name.startswith("larochelle"):
            continue
        rows = jsonl(name)
        if not rows:
            continue
        cols = ["network", "demand_veh_per_h", "seed"] + [v for k, v in setting.items() if any(k in r for r in rows)] \
            + [rename[k] for k in summary if any(k in r for r in rows)]
        keys = ["network", "demand", "seed"] + [k for k in setting if any(k in r for r in rows)] \
            + [k for k in summary if any(k in r for r in rows)]
        folder = "two_road" if name.startswith("two_road") else "grid" if name.startswith("grid") else "other"
        counts[f"{folder}/{name}.csv"] = write_csv(out / folder / f"{name}.csv", cols,
                                                   ([None if r.get(k) != r.get(k) else r.get(k) for k in keys] for r in rows))
        series = [(r["demand"], r.get("share"), r.get("window"), r.get("synchronize"), r["seed"], t, q, n)
                  for r in rows for t, q, *rest in r.get("long_share_series", []) for n in (rest[0] if rest else None,)]
        if series:
            counts[f"{folder}/{name}_minute_series.csv"] = write_csv(
                out / folder / f"{name}_minute_series.csv",
                ["demand_veh_per_h", "app_share", "averaging_window_s", "synchronized_rechecks", "seed", "minute_start_s",
                 "share_on_detour", "cars_in_minute"], series)
    for extra in ("predictions.json", "conjectures.md"):
        if (RESULTS / extra).exists():
            (out / "theory").mkdir(parents=True, exist_ok=True)
            shutil.copy(RESULTS / extra, out / "theory" / extra)
    shutil.copy(ROOT / "rerouting" / "datacards" / "experiments.md", out / "README.md")
    (out / "dataset-metadata.json").write_text(json.dumps({
        "title": "Dynamic rerouting experiments (SUMO)",
        "id": "tudoropr/dynamic-rerouting-sumo-experiments",
        "subtitle": "Controlled SUMO runs: when does live rerouting cut travel time, and when does it herd?",
        "licenses": [{"name": "CC-BY-4.0"}],
        "keywords": ["transportation", "simulations"]}, indent=1))
    print(json.dumps(counts, indent=1))
    return out


if __name__ == "__main__":
    {"larochelle": larochelle, "experiments": experiments}[sys.argv[1]]()
