"""La Rochelle at full scale: a real morning rush hour, with and without live rerouting.

    python -m rerouting.larochelle fetch       # download the open data into build/larochelle_raw
    python -m rerouting.larochelle prepare     # keep what the model needs in data/larochelle
    python -m rerouting.larochelle calibrate   # traffic volume that best matches the 2023 counts
    python -m rerouting.larochelle routes      # drivers' usual routes, one set per seed
    python -m rerouting.larochelle run         # the scenarios (results/larochelle.jsonl)
    python -m rerouting.larochelle gif         # the animation (figures/larochelle.gif)

The heavy steps run on Jean Zay (jz/larochelle.slurm). Everything comes from open data:

* streets, lanes, speed limits, roundabouts and traffic lights: OpenStreetMap (ODbL);
* where people live: INSEE, population on a 200 m grid (Filosofi 2021);
* where they work: the SIRENE register of workplaces with their size (INSEE, geolocated),
  scaled to the 50,497 jobs INSEE counts in La Rochelle (2023 census);
* who commutes where: INSEE 2022 commuting flows between communes;
* who drives: INSEE 2023 census, 52.4 % of workers living in La Rochelle and 80.7 % of those living
  elsewhere in the agglomeration drive to work (85 % assumed further out);
* how much traffic each road carries: average daily traffic (TMJA) 2023 of the regional counting
  stations (DREAL / SIGENA Nouvelle-Aquitaine).

Every commuting flow between two communes becomes car trips. An end on the map is a street near a
home (200 m population cells) or a workplace of that commune; an end off the map is an entry road,
chosen by its measured traffic and its distance to the commune. Departures spread over 6:30-9:30 and
the results follow the drivers who leave between 7:00 and 9:00. Two volumes are fitted to the counts:
morning traffic as a share of the daily commuters (it also stands for school runs, errands and
deliveries), and traffic crossing the map. Drivers know the usual traffic: their routes are the
equilibrium of repeated mornings (SUMO's duaIterate, queue-based simulation). Drivers with the app
also reroute live, with perfect knowledge of current travel times.
"""

from __future__ import annotations

import argparse
import csv
import gzip
import json
import math
import os
import shutil
import subprocess
import sys
import time
import xml.etree.ElementTree as ET
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import numpy as np

from rerouting import sumo as S

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data" / "larochelle"
RAW = ROOT / "build" / "larochelle_raw"
BUILD = ROOT / "build" / "larochelle"
RESULTS = ROOT / "results"
FIGURES = ROOT / "figures"

BBOX = (46.120, -1.250, 46.200, -1.080)          # south, west, north, east
LA_ROCHELLE = "17300"
JOBS_IN_COMMUNE = 50_497         # INSEE 2023, jobs located in La Rochelle
CAR_SHARE = {"city": 0.524, "agglomeration": 0.807, "beyond": 0.85}
GATE_KM = 10.0                   # an entry road 10 km further from a commune is e times less likely
PEAK_SHARE_OF_DAILY = 0.09       # two-way flow in the morning peak hour / daily traffic (rule of thumb)
# departures per quarter hour from 6:30 to 9:30, peaking around 8:00
PROFILE = [0.02, 0.04, 0.07, 0.10, 0.13, 0.15, 0.15, 0.12, 0.09, 0.06, 0.04, 0.03]
START, COHORT, END = int(6.5 * 3600), (7 * 3600, 9 * 3600), 11 * 3600
PEAK = (7.5 * 3600, 8.5 * 3600)  # the counted hour
SEEDS = [1, 2, 3]
SHARES = [0.0, 0.25, 0.5, 0.75, 1.0]
SIRENE_JOBS = {"01": 1.5, "02": 4, "03": 7.5, "11": 15, "12": 35, "21": 75, "22": 150, "31": 225, "32": 375,
               "41": 750, "42": 1500, "51": 3500, "52": 7500, "53": 10000}

OVERPASS = "https://overpass-api.de/api/interpreter"
POPULATION_URL = ("https://static.data.gouv.fr/resources/revenus-pauvrete-et-niveau-de-vie-donnees-carroyees-"
                  "dispositif-fichier-localise-social-et-fiscal-filosofi/20260309-120901/carreaux-200m-met-3035-2021.parquet")
GEOLOC_URL = ("https://static.data.gouv.fr/resources/geolocalisation-des-etablissements-du-repertoire-sirene-pour-"
              "les-etudes-statistiques/20260921-065930/geoloc-geolocalisationetablissement-sirene-pour-etudes-"
              "statistiques-parquet.parquet")
SIRENE_URL = ("https://static.data.gouv.fr/resources/base-sirene-des-entreprises-et-de-leurs-etablissements-siren-"
              "siret/20260901-090503/stock-stocketablissement-parquet.parquet")
COUNTS_WFS = ("https://datacarto.sigena.fr/wfs?service=WFS&version=2.0.0&request=GetFeature&typeNames="
              "l_tmja2023_l_r74&srsName=EPSG:4326&outputFormat=GML3&bbox={s},{w},{n},{e},urn:ogc:def:crs:EPSG::4326")
FLOWS_URL = "https://www.insee.fr/fr/statistiques/fichier/8582949/base-flux-mobilite-domicile-lieu-travail-2022_csv.zip"
COMMUNES_URL = "https://geo.api.gouv.fr/departements/17/communes?fields=code,nom,population&format=geojson&geometry=contour"
CENTRES_URL = "https://geo.api.gouv.fr/communes?fields=code,nom,centre,population&format=json"
EPCI_URL = "https://geo.api.gouv.fr/epcis/241700434/communes?fields=code,nom"


# --------------------------------------------------------------------------- open data


def curl(url: str, out: Path, data: str | None = None) -> None:
    cmd = ["curl", "-sL", "-m", "900", "-o", str(out)] + (["--data-urlencode", f"data={data}"] if data else []) + [url]
    subprocess.run(cmd, check=True)


def fetch() -> None:
    """Downloads the raw open data (a few minutes; the parquet files are queried remotely)."""
    import duckdb
    import pyproj
    RAW.mkdir(parents=True, exist_ok=True)
    s, w, n, e = BBOX
    roads = ('way["highway"~"^(motorway|motorway_link|trunk|trunk_link|primary|primary_link|secondary|'
             'secondary_link|tertiary|tertiary_link|unclassified|residential|living_street)$"]')
    curl(OVERPASS, RAW / "roads.osm", f"[out:xml][timeout:300];({roads}({s},{w},{n},{e}););(._;>;);out body;")
    curl(COUNTS_WFS.format(s=s, w=w, n=n, e=e), RAW / "counts.gml")
    curl(COMMUNES_URL, RAW / "communes17.geojson")
    curl(CENTRES_URL, RAW / "centres.json")
    curl(EPCI_URL, RAW / "epci.json")
    curl(FLOWS_URL, RAW / "flux.zip")
    subprocess.run(["unzip", "-o", "-q", str(RAW / "flux.zip"), "-d", str(RAW)], check=True)
    con = duckdb.connect()
    con.execute("INSTALL httpfs; LOAD httpfs;")
    to3035 = pyproj.Transformer.from_crs(4326, 3035, always_xy=True)
    xs, ys = zip(*[to3035.transform(lon, lat) for lon in (w, e) for lat in (s, n)])
    con.execute(f"""COPY (SELECT (bbox.xmin + bbox.xmax) / 2 AS x, (bbox.ymin + bbox.ymax) / 2 AS y, ind,
        ind_18_24 + ind_25_39 + ind_40_54 + ind_55_64 AS adults FROM '{POPULATION_URL}'
        WHERE bbox.xmin >= {min(xs)} AND bbox.xmax <= {max(xs)} AND bbox.ymin >= {min(ys)} AND bbox.ymax <= {max(ys)})
        TO '{RAW / "population.csv"}' (HEADER)""")
    con.execute(f"""CREATE TABLE g AS SELECT siret, x_longitude AS lon, y_latitude AS lat, plg_code_commune AS commune
        FROM '{GEOLOC_URL}' WHERE y_latitude BETWEEN {s} AND {n} AND x_longitude BETWEEN {w} AND {e}""")
    con.execute(f"""CREATE TABLE t AS SELECT siret, trancheEffectifsEtablissement AS tranche FROM '{SIRENE_URL}'
        WHERE codeCommuneEtablissement LIKE '17%' AND etatAdministratifEtablissement = 'A'""")
    con.execute(f"COPY (SELECT g.*, t.tranche FROM g JOIN t USING (siret)) TO '{RAW / 'workplaces.csv'}' (HEADER)")


def prepare() -> None:
    """Keeps what the model needs, in small files under data/larochelle."""
    import pyproj
    from shapely.geometry import LineString, Point, box, shape
    DATA.mkdir(parents=True, exist_ok=True)
    s, w, n, e = BBOX
    with open(RAW / "roads.osm", "rb") as src, gzip.open(DATA / "roads.osm.gz", "wb") as dst:
        shutil.copyfileobj(src, dst)
    # communes: the map ones by their outline, every one by its centre
    outlines = {f["properties"]["code"]: (f["properties"], shape(f["geometry"]))
                for f in json.load(open(RAW / "communes17.geojson"))["features"]}
    frame = box(w, s, e, n)
    on_map = {c: p for c, (p, geom) in outlines.items() if geom.intersects(frame)}
    epci = {c["code"] for c in json.load(open(RAW / "epci.json"))}
    centres = {c["code"]: c for c in json.load(open(RAW / "centres.json")) if "centre" in c}

    def commune_of(lon, lat):
        pt = Point(lon, lat)
        for c in on_map:
            if outlines[c][1].contains(pt):
                return c
        return ""
    # population cells, with their commune
    to_wgs = pyproj.Transformer.from_crs(3035, 4326, always_xy=True)
    people_on_map: dict[str, float] = {}
    with gzip.open(DATA / "population.csv.gz", "wt") as f:
        f.write("lon,lat,people,adults,commune\n")
        for r in csv.DictReader(open(RAW / "population.csv")):
            lon, lat = to_wgs.transform(float(r["x"]), float(r["y"]))
            c = commune_of(lon, lat)
            people_on_map[c] = people_on_map.get(c, 0.0) + float(r["ind"])
            f.write(f"{lon:.6f},{lat:.6f},{float(r['ind']):.1f},{float(r['adults']):.1f},{c}\n")
    # workplaces with employees, scaled to INSEE's jobs in La Rochelle (the register misses part of the public sector)
    rows = [r for r in csv.DictReader(open(RAW / "workplaces.csv")) if r["tranche"] in SIRENE_JOBS]
    scale = JOBS_IN_COMMUNE / sum(SIRENE_JOBS[r["tranche"]] for r in rows if r["commune"] == LA_ROCHELLE)
    with gzip.open(DATA / "jobs.csv.gz", "wt") as f:
        f.write("lon,lat,jobs,commune\n")
        for r in rows:
            f.write(f"{float(r['lon']):.6f},{float(r['lat']):.6f},{SIRENE_JOBS[r['tranche']] * scale:.1f},{r['commune']}\n")
    # the communes that matter: on the map, or exchanging commuters with it, or crossing it
    flows = [(r["CODGEO"], r["DCLT"], float(r["NBFLUX_C22_ACTOCC15P"]))
             for r in csv.DictReader(open(RAW / "base-flux-mobilite-domicile-lieu-travail-2022.csv", encoding="utf-8"),
                                     delimiter=";")]
    lr = centres[LA_ROCHELLE]["centre"]["coordinates"]

    def near(c, km=70):
        if c not in centres:
            return False
        lon, lat = centres[c]["centre"]["coordinates"]
        return math.hypot((lon - lr[0]) * 77, (lat - lr[1]) * 111) < km

    keep = []
    for h, wk, f in flows:
        if h == wk and h not in on_map:
            continue
        if h in on_map or wk in on_map:
            keep.append((h, wk, f))
        elif near(h) and near(wk) and LineString([centres[h]["centre"]["coordinates"],
                                                  centres[wk]["centre"]["coordinates"]]).intersects(frame):
            keep.append((h, wk, f))       # commuters whose straight line crosses the map
    with gzip.open(DATA / "flows.csv.gz", "wt") as f:
        f.write("home,work,commuters\n")
        for h, wk, x in keep:
            f.write(f"{h},{wk},{x:.1f}\n")
    communes = {}
    for c in {x for h, wk, _ in keep for x in (h, wk)}:
        if c not in centres:
            continue
        lon, lat = centres[c]["centre"]["coordinates"]
        pop = float(centres[c].get("population") or 0)
        share = min(1.0, people_on_map.get(c, 0.0) / pop) if c in on_map and pop else 0.0
        communes[c] = {"name": centres[c]["nom"], "lon": lon, "lat": lat, "population": pop,
                       "on_map": round(share, 3),
                       "car_share": CAR_SHARE["city"] if c == LA_ROCHELLE else
                       CAR_SHARE["agglomeration"] if c in epci else CAR_SHARE["beyond"]}
    with gzip.open(DATA / "communes.json.gz", "wt") as f:
        json.dump(communes, f)
    # counted road sections
    ns = {"gml": "http://www.opengis.net/gml"}
    sections = []
    for feat in ET.parse(RAW / "counts.gml").getroot().iter("{http://mapserver.gis.umn.edu/mapserver}l_tmja2023_l_r74"):
        field = {c.tag.split("}")[1]: (c.text or "") for c in feat}
        pos = feat.find(".//gml:posList", ns)
        km = float(field.get("long_km") or 0)
        if pos is None or km < 0.2:          # keep sections long enough to be matched to streets
            continue
        v = list(map(float, pos.text.split()))
        coords = [(round(v[i + 1], 6), round(v[i], 6)) for i in range(0, len(v), 2)]
        sections.append({"road": field["numero"], "daily": round(float(field["veh_km"]) / km),
                         "coords": coords[:: max(1, len(coords) // 20)] + [coords[-1]]})
    with gzip.open(DATA / "counts.json.gz", "wt") as f:
        json.dump(sections, f)
    print(f"{len(rows)} workplaces (jobs x {scale:.2f}), {len(keep)} commuting flows, {len(communes)} communes, "
          f"{len(sections)} counted sections")


def read_csv_gz(name: str) -> list[dict]:
    with gzip.open(DATA / name, "rt") as f:
        return list(csv.DictReader(f))


# --------------------------------------------------------------------------- network


def network() -> Path:
    BUILD.mkdir(parents=True, exist_ok=True)
    out = BUILD / "larochelle.net.xml"
    if not out.exists():
        S.run([S.tool("netconvert"), "--osm-files", DATA / "roads.osm.gz", "--geometry.remove", "--ramps.guess",
               "--junctions.join", "--roundabouts.guess", "--tls.guess-signals", "--tls.join",
               "--remove-edges.isolated", "--keep-edges.by-vclass", "passenger", "--edges.join",
               "--no-turnarounds.except-deadend", "true", "--osm.turn-lanes", "true",
               "--output.street-names", "true", "--no-warnings", "-o", out])
    return out


def load_net():
    import sumolib
    return sumolib.net.readNet(str(network()))


MAJOR = ("highway.motorway", "highway.trunk", "highway.primary", "highway.secondary", "highway.tertiary")
FAST = ("highway.motorway", "highway.trunk")


def is_local(edge) -> bool:
    """A street where trips can start or end (not a fast road or a ramp)."""
    t = edge.getType()
    return edge.allows("passenger") and not t.startswith(FAST) and "link" not in t


def nearest_street(net, lon: float, lat: float) -> str | None:
    x, y = net.convertLonLat2XY(lon, lat)
    near = sorted((d, e.getID()) for e, d in net.getNeighboringEdges(x, y, 400) if is_local(e))
    return near[0][1] if near else None


def section_lines(net):
    from shapely.geometry import LineString
    with gzip.open(DATA / "counts.json.gz", "rt") as f:
        sections = json.load(f)
    return [(s, LineString([net.convertLonLat2XY(lon, lat) for lon, lat in s["coords"]])) for s in sections]


def gates(net) -> list[dict]:
    """Entry and exit roads at the edge of the map, with their measured daily traffic."""
    from shapely.geometry import LineString
    from shapely.strtree import STRtree
    lines = section_lines(net)
    tree = STRtree([ln for _, ln in lines])
    s, w, n, e_ = BBOX
    margin = 0.004   # about 400 m: roads starting (or ending) beyond this inner box lead out of the map

    def at_border(node):
        lon, lat = net.convertXY2LonLat(*node.getCoord())
        return not (s + margin < lat < n - margin and w + margin < lon < e_ - margin)

    out = []
    for e in net.getEdges():
        if not e.getType().startswith(MAJOR) or not e.allows("passenger"):
            continue
        entry = at_border(e.getFromNode()) and all(i.getFromNode() == e.getToNode() for i in e.getFromNode().getIncoming())
        leave = at_border(e.getToNode()) and all(o.getToNode() == e.getFromNode() for o in e.getToNode().getOutgoing())
        if not (entry or leave):
            continue
        mid = LineString(e.getShape()).interpolate(0.5, normalized=True)
        i = tree.nearest(mid)
        daily = lines[i][0]["daily"] if lines[i][1].distance(mid) < 150 else 2000.0
        out.append({"edge": e.getID(), "kind": "entry" if entry else "exit", "daily": daily, "xy": tuple(mid.coords[0])})
    return out


# --------------------------------------------------------------------------- demand


def zones(net) -> dict:
    """Streets where each commune's car commuters live and work, and the entry roads (cached)."""
    path = BUILD / "zones.json"
    if path.exists():
        return json.loads(path.read_text())
    homes: dict[str, dict[str, float]] = {}
    for r in read_csv_gz("population.csv.gz"):
        e = nearest_street(net, float(r["lon"]), float(r["lat"])) if r["commune"] and float(r["adults"]) > 0 else None
        if e:
            homes.setdefault(r["commune"], {})
            homes[r["commune"]][e] = homes[r["commune"]].get(e, 0.0) + float(r["adults"])
    jobs: dict[str, dict[str, float]] = {}
    for r in read_csv_gz("jobs.csv.gz"):
        e = nearest_street(net, float(r["lon"]), float(r["lat"]))
        if e:
            jobs.setdefault(r["commune"], {})
            jobs[r["commune"]][e] = jobs[r["commune"]].get(e, 0.0) + float(r["jobs"])
    z = {"homes": homes, "jobs": jobs, "gates": gates(net)}
    path.write_text(json.dumps(z))
    return z


def trips(z: dict, net, commute: float, crossing: float, seed: int) -> list[tuple[str, str, float, str]]:
    """Morning car trips (from street, to street, departure second, kind).

    ``commute`` is the share of daily car commuters driving this morning between 6:30 and 9:30
    (standing also for the other morning trips); ``crossing`` adds that many thousand trips between
    entry roads on opposite sides of the map (through traffic that is not commuting)."""
    with gzip.open(DATA / "communes.json.gz", "rt") as f:
        communes = json.load(f)
    flows = [(r["home"], r["work"], float(r["commuters"])) for r in read_csv_gz("flows.csv.gz")]
    rng = np.random.default_rng(seed)
    entries = [g for g in z["gates"] if g["kind"] == "entry"]
    exits = [g for g in z["gates"] if g["kind"] == "exit"]

    def gate_weights(gs, c):
        x, y = net.convertLonLat2XY(communes[c]["lon"], communes[c]["lat"])
        d = np.array([math.dist((x, y), g["xy"]) for g in gs]) / 1000.0
        w = np.array([g["daily"] for g in gs]) * np.exp(-(d - d.min()) / GATE_KM)   # relative: no underflow far away
        return w / w.sum()

    cache: dict = {}

    def pick(c, kind, n):
        """``n`` streets for commune ``c``: a home/work street on the map, or an entry/exit road."""
        key = (c, kind)
        if key not in cache:
            spots = (z["homes"] if kind == "home" else z["jobs"]).get(c) or z["homes"].get(c) or {}
            ids, w = list(spots), np.array(list(spots.values()), dtype=float)
            gs = entries if kind == "home" else exits
            cache[key] = (ids, w / w.sum() if len(w) else w, [g["edge"] for g in gs], gate_weights(gs, c))
        ids, w, gate_ids, gw = cache[key]
        on = rng.random(n) < (communes[c]["on_map"] if ids else 0.0)
        out = np.empty(n, dtype=object)
        if on.any():
            out[on] = np.array(ids, dtype=object)[rng.choice(len(ids), size=on.sum(), p=w)]
        if (~on).any():
            out[~on] = np.array(gate_ids, dtype=object)[rng.choice(len(gate_ids), size=(~on).sum(), p=gw)]
        return out, on

    def departures(n):
        q = rng.choice(len(PROFILE), size=n, p=np.array(PROFILE) / sum(PROFILE))
        return START + q * 900 + rng.uniform(0, 900, size=n)

    out = []
    for h, wk, f in flows:
        if h not in communes or wk not in communes:
            continue
        n = rng.poisson(commute * f * communes[h]["car_share"])
        if n == 0:
            continue
        a, a_on = pick(h, "home", n)
        b, b_on = pick(wk, "work", n)
        for src, dst, t, x, y in zip(a, b, departures(n), a_on, b_on):
            if src == dst or (not x and not y and math.dist(net.getEdge(src).getShape()[0],
                                                              net.getEdge(dst).getShape()[0]) < 3000):
                continue          # same street, or an off-map trip that would not really cross the map
            out.append((src, dst, t, "inside" if x and y else "in" if y else "out" if x else "cross", h, wk))
    # through traffic that is not commuting, between entry roads more than 3 km apart
    ew = np.array([g["daily"] for g in entries]); xw = np.array([g["daily"] for g in exits])
    n = rng.poisson(crossing * 1000)
    kept = 0
    while kept < n:
        a, b = entries[rng.choice(len(entries), p=ew / ew.sum())], exits[rng.choice(len(exits), p=xw / xw.sum())]
        if math.dist(a["xy"], b["xy"]) > 3000:
            out.append((a["edge"], b["edge"], float(departures(1)[0]), "through", "", ""))
            kept += 1
    out.sort(key=lambda r: r[2])
    return out


def write_trips(trip_list: list, path: Path, keep: set | None = None) -> None:
    path.write_text("\n".join(["<routes>"] + [
        f'  <trip id="t{i}" depart="{t:.1f}" from="{a}" to="{b}" departLane="best" departSpeed="max"/>'
        for i, (a, b, t, *_) in enumerate(trip_list) if keep is None or f"t{i}" in keep] + ["</routes>"]))


def free_routes(net_path: Path, trip_list: list, out_dir: Path) -> list[S.Vehicle]:
    """Fastest routes through the empty city (trips between unconnected streets are dropped)."""
    out_dir.mkdir(parents=True, exist_ok=True)
    write_trips(trip_list, out_dir / "trips.xml")
    S.run([S.tool("duarouter"), "-n", net_path, "--route-files", out_dir / "trips.xml", "-o", out_dir / "free.rou.xml",
           "--ignore-errors", "--no-warnings", "--no-step-log", "--write-costs", "false", "--exit-times", "false"])
    return S.read_routes(out_dir / "free.rou.xml")


def equilibrium(net_path: Path, trip_list: list, out_dir: Path, iterations: int = 20) -> tuple[list[S.Vehicle], Path]:
    """Routes of drivers who know the usual traffic: SUMO's iterative assignment (duaIterate) with the
    fast queue-based simulation, street traffic recorded every 15 minutes. Returns the routes of the
    last morning and its street data."""
    out_dir.mkdir(parents=True, exist_ok=True)
    reachable = {v.id for v in free_routes(net_path, trip_list, out_dir / "free")}
    write_trips(trip_list, out_dir / "trips.xml", reachable)
    (out_dir / "dropped.txt").write_text(f"{len(trip_list) - len(reachable)} of {len(trip_list)} trips unreachable\n")
    S.run([sys.executable, S.SUMO_HOME / "tools" / "assign" / "duaIterate.py", "-n", net_path,
           "-t", out_dir / "trips.xml", "-l", iterations, "-b", START, "-e", END, "--mesosim", "--aggregation", 900,
           "--meso-junctioncontrol", "--time-to-teleport=300", "--disable-summary", "--no-gzip"], cwd=out_dir)
    last = out_dir / f"{iterations - 1:03d}"
    chosen = [p for p in last.glob("*.rou.xml") if not p.name.endswith(".alt.xml")]
    return S.read_routes(chosen[0]), sorted(last.glob("dump_*.xml"))[0]


# --------------------------------------------------------------------------- calibration against the counts


def counted_edges(net) -> list[tuple[dict, list[str]]]:
    """For each counted two-way road section inside the map, the simulated street in each direction."""
    out, seen = [], set()
    s, w, n, e_ = BBOX
    for sec, line in section_lines(net):
        mid = line.interpolate(0.5, normalized=True)
        lon, lat = net.convertXY2LonLat(mid.x, mid.y)
        if not (s + 0.006 < lat < n - 0.006 and w + 0.006 < lon < e_ - 0.006) or sec["daily"] < 2000:
            continue
        a, b = line.interpolate(0.45, normalized=True), line.interpolate(0.55, normalized=True)
        heading = math.atan2(b.y - a.y, b.x - a.x)
        best = {}
        for edge, dist in net.getNeighboringEdges(mid.x, mid.y, 60):
            if not edge.allows("passenger") or edge.getFunction() == "internal":
                continue
            p, q = edge.getShape()[0], edge.getShape()[-1]
            c = math.cos(math.atan2(q[1] - p[1], q[0] - p[0]) - heading)
            if abs(c) < 0.85:
                continue
            forward = c > 0
            if forward not in best or dist < best[forward][0]:
                best[forward] = (dist, edge.getID())
        key = tuple(sorted(eid for _, eid in best.values()))
        if len(best) == 2 and key not in seen:     # both directions, each street pair once
            seen.add(key)
            out.append((sec, list(key)))
    return out


def geh(model: float, count: float) -> float:
    """The traffic engineers' GEH statistic for hourly flows; under 5 is a good match."""
    return math.sqrt(2 * (model - count) ** 2 / (model + count)) if model + count > 0 else 0.0


def dump_flows(dump: Path, begin: float, end: float) -> dict[str, float]:
    """Cars per hour entering each street between ``begin`` and ``end``, from SUMO edge data."""
    out: dict[str, float] = {}
    for interval in ET.parse(dump).getroot().iter("interval"):
        if float(interval.get("begin")) >= begin and float(interval.get("end")) <= end:
            for edge in interval.iter("edge"):
                out[edge.get("id")] = out.get(edge.get("id"), 0.0) + float(edge.get("entered", 0))
    return {k: v * 3600.0 / (end - begin) for k, v in out.items()}


def holdout(road: str) -> bool:
    """Roads used only to check the calibration (every other road number, whole corridors at once)."""
    return sum(road.encode()) % 2 == 1


def score(flows: dict[str, float], matches, which: str = "all") -> dict:
    """How well hourly street flows (7:30-8:30) match 9 % of the daily counts."""
    sel = [(s, es) for s, es in matches if which == "all" or holdout(s["road"]) == (which == "check")]
    model = np.array([sum(flows.get(e, 0.0) for e in es) for _, es in sel])
    count = np.array([PEAK_SHARE_OF_DAILY * s["daily"] for s, _ in sel])
    g = np.array([geh(m, c) for m, c in zip(model, count)])
    return {"mean_geh": float(np.mean(g)), "geh_under_5": float(np.mean(g < 5)), "geh_under_10": float(np.mean(g < 10)),
            "r2": float(1 - np.sum((model - count) ** 2) / np.sum((count - count.mean()) ** 2)),
            "model_over_count": float(np.median(model / count)), "sections": len(sel)}


def _calibration_case(args):
    commute, crossing, seed = args
    net_path, net = network(), load_net()
    t0 = time.time()
    own = f"_j{os.environ['SLURM_JOB_ID']}" if os.environ.get("CASES") else ""   # split jobs never share a folder
    _, dump = equilibrium(net_path, trips(zones(net), net, commute, crossing, seed),
                          BUILD / f"eq_c{commute:g}_x{crossing:g}_s{seed}{own}")
    flows, matches = dump_flows(dump, *PEAK), counted_edges(net)
    return {"commute": commute, "crossing": crossing, "seed": seed, "fit": score(flows, matches, "fit"),
            "check": score(flows, matches, "check"), "minutes": round((time.time() - t0) / 60, 1)}


def calibration_grid(workers: int) -> None:
    """Equilibrium flows for a grid of volumes, scored against the counts (results/larochelle_calibration.jsonl).
    The volume is chosen on half of the roads ("fit") and checked on the other half ("check")."""
    zones(load_net())                       # shared inputs, built once before the workers start
    cases = [(c, x, 1) for c in (0.25, 0.35, 0.45, 0.55, 0.65) for x in (0.0, 2.0, 4.0)]
    if os.environ.get("CASES"):             # e.g. CASES="0.55:4+0.65:0" (+ survives sbatch --export) to split the grid over several jobs
        cases = [(float(c), float(x), 1) for c, x in (p.split(":") for p in os.environ["CASES"].replace("+", ",").split(","))]
    out = RESULTS / "larochelle_calibration.jsonl"
    done = {(r["commute"], r["crossing"], r["seed"]) for r in map(json.loads, out.read_text().splitlines())} \
        if out.exists() else set()
    RESULTS.mkdir(exist_ok=True)
    with ProcessPoolExecutor(workers) as pool, out.open("a") as f:
        for r in pool.map(_calibration_case, [c for c in cases if c not in done]):
            f.write(json.dumps(r) + "\n")
            f.flush()
            print(r, flush=True)


def rescore() -> None:
    """Scores every finished calibration case from its saved street data (results/larochelle_calibration_scored.jsonl)."""
    net = load_net()
    matches = counted_edges(net)
    rows = []
    for d in sorted(BUILD.glob("eq_c*_x*_s*")):
        dumps = sorted((d / "019").glob("dump_*.xml"))
        if not dumps:
            continue
        name = d.name.split("_")
        commute, crossing, seed = float(name[1][1:]), float(name[2][1:]), int(name[3][1:])
        flows = dump_flows(dumps[0], *PEAK)
        rows.append({"commute": commute, "crossing": crossing, "seed": seed, "fit": score(flows, matches, "fit"),
                     "check": score(flows, matches, "check")})
    rows = list({(r["commute"], r["crossing"], r["seed"]): r for r in rows}.values())
    (RESULTS / "larochelle_calibration_scored.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows))
    for r in sorted(rows, key=lambda r: r["fit"]["mean_geh"]):
        print(r["commute"], r["crossing"], {k: round(v, 3) for k, v in r["fit"].items()},
              {k: round(v, 3) for k, v in r["check"].items()})


def chosen_volume() -> tuple[float, float]:
    """The calibrated volume: the lowest mean GEH on the fitted roads (the check roads are never used).

    (The share of roads under GEH 5 was the first choice; with 29 fitted roads it proved too coarse.)"""
    rows = [json.loads(line) for line in (RESULTS / "larochelle_calibration_scored.jsonl").read_text().splitlines()]
    best = min(rows, key=lambda r: r["fit"]["mean_geh"])
    return best["commute"], best["crossing"]


# --------------------------------------------------------------------------- usual routes and scenarios


def _routes_case(seed):
    commute, crossing = chosen_volume()
    net_path, net = network(), load_net()
    trip_list = trips(zones(net), net, commute, crossing, seed)
    veh, dump = equilibrium(net_path, trip_list, BUILD / f"usual_s{seed}")
    (BUILD / f"usual_s{seed}.json").write_text(json.dumps([[v.id, v.depart, v.route] for v in veh]))
    routed = {v.id for v in veh}
    (BUILD / f"demand_s{seed}.json").write_text(json.dumps(
        [[f"t{i}", round(t, 1), a, b, kind, home, work] for i, (a, b, t, kind, home, work) in enumerate(trip_list)
         if f"t{i}" in routed]))
    return seed, len(veh), score(dump_flows(dump, *PEAK), counted_edges(net), "check")


def usual_routes(workers: int) -> None:
    net = load_net()
    zones(net)
    with ProcessPoolExecutor(workers) as pool:
        for r in pool.map(_routes_case, SEEDS):
            print(r, flush=True)
    count_table(net)


def count_table(net) -> None:
    """results/larochelle_counts.csv: every counted section, its target and the simulated usual morning (seed 1)."""
    flows = dump_flows(sorted((BUILD / "usual_s1" / "019").glob("dump_*.xml"))[0], *PEAK)
    with open(RESULTS / "larochelle_counts.csv", "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["road", "daily_vehicles_2023", "target_peak_hour", "simulated_peak_hour", "geh", "used_for"])
        for sec, es in counted_edges(net):
            model, target = sum(flows.get(e, 0.0) for e in es), PEAK_SHARE_OF_DAILY * sec["daily"]
            w.writerow([sec["road"], sec["daily"], round(target), round(model), round(geh(model, target), 2),
                        "check" if holdout(sec["road"]) else "fit"])


def load_usual(seed: int) -> list[S.Vehicle]:
    return [S.Vehicle(i, d, r) for i, d, r in json.loads((BUILD / f"usual_s{seed}.json").read_text())]


def incident_site(net, seed: int = 1) -> list[str]:
    """The busiest two-lane stretch of the ring road (N237) in the counted hour: 3 consecutive edges."""
    flows = dump_flows(sorted((BUILD / f"usual_s{seed}" / "019").glob("dump_*.xml"))[0], *PEAK)
    ring = [e for e in net.getEdges() if e.getType().startswith("highway.trunk") and e.getLaneNumber() >= 2
            and "link" not in e.getType() and "Added" not in e.getID()]    # not where a ramp joins
    start = max(ring, key=lambda e: flows.get(e.getID(), 0.0))
    site, e = [start.getID()], start
    while len(site) < 3:
        nxt = [o for o in e.getOutgoing() if o.getLaneNumber() >= 2 and o.getType().startswith("highway.trunk")
               and "Added" not in o.getID()]
        if not nxt:
            break
        e = nxt[0]
        site.append(e.getID())
    return site


def incident_file(net, path: Path) -> Path:
    """7:45-8:30: an accident on the busiest ring-road stretch. Its right lane is almost blocked (cars crawl past at
    walking pace, 1 m/s) and the other lanes slow to 20 km/h. No lane is closed outright, so no connection disappears."""
    lines = ["<additional>"]
    for e in incident_site(net):
        edge = net.getEdge(e)
        for i in range(edge.getLaneNumber()):
            lines.append(f'  <variableSpeedSign id="slow_{e}_{i}" lanes="{e}_{i}">')
            lines.append(f'    <step time="{7.75 * 3600:.0f}" speed="{1.0 if i == 0 else 5.56}"/>')
            lines.append(f'    <step time="{8.5 * 3600:.0f}" speed="{edge.getSpeed():.2f}"/>')
            lines.append("  </variableSpeedSign>")
    lines.append("</additional>")
    path.write_text("\n".join(lines))
    return path


def _scenario(args):
    day, share, seed, frames = args
    net_path, net = network(), load_net()
    work = BUILD / "runs" / f"{day}_p{share:g}_s{seed}"
    work.mkdir(parents=True, exist_ok=True)
    extra = []
    if day == "incident":
        extra.append(incident_file(net, work / "incident.add.xml"))
    edge_out = work / "edges.xml"
    period = 120 if frames else 300
    (work / "measure.add.xml").write_text(
        f'<additional><edgeData id="m" file="{edge_out}" begin="{START}" end="{END}" period="{period}" '
        f'excludeEmpty="true"/></additional>')
    extra.append(work / "measure.add.xml")
    veh = load_usual(seed)
    t0 = time.time()
    stats = S.simulate(S.Scenario(net_path, veh, share=share, end=END, seed=seed, teleport=300,
                                  additional=tuple(extra), measure=COHORT, keep=work))
    if frames:
        np.savez_compressed(work / "frames.npz", **frames_from(edge_out))
    save_run_tables(work, veh, share, seed, edge_out)
    return {"day": day, "share": share, "seed": seed, **stats, "minutes": round((time.time() - t0) / 60, 1)}


def scenarios(workers: int) -> None:
    """Normal and incident mornings, for every share of app users and seed (results/larochelle.jsonl)."""
    out = RESULTS / "larochelle.jsonl"
    done = {(r["day"], r["share"], r["seed"]) for r in map(json.loads, out.read_text().splitlines())} \
        if out.exists() else set()
    seeds = [int(x) for x in os.environ["RUN_SEEDS"].split("+")] if os.environ.get("RUN_SEEDS") else SEEDS
    cases = [(d, p, s, d == "incident" and s == 1 and p in (0.0, 0.5))
             for s in seeds for d in ("normal", "incident") for p in SHARES]
    with ProcessPoolExecutor(workers) as pool, out.open("a") as f:
        for r in pool.map(_scenario, [c for c in cases if c[:3] not in done]):
            f.write(json.dumps(r) + "\n")
            f.flush()
            print(r, flush=True)


def save_run_tables(work: Path, vehicles: list, share: float, seed: int, edge_xml: Path) -> None:
    """One clean Parquet table per run for the trips and one for the streets; the SUMO XML is removed."""
    import pyarrow as pa
    import pyarrow.parquet as pq
    info = {t.get("id"): t for t in ET.parse(work / "tripinfo.xml").getroot().iter("tripinfo")}
    final = {}
    for v in ET.parse(work / "vehroutes.xml").getroot().iter("vehicle"):
        r = v.find("route")
        r = r if r is not None else list(v.iter("route"))[-1]
        final[v.get("id")] = r.get("edges")
    cols = {k: [] for k in ("trip_id", "app_user", "requested_departure_s", "departure_s", "arrival_s", "finished",
                            "trip_time_s", "route_length_m", "time_lost_s", "waiting_time_s", "reroutes",
                            "planned_route", "driven_route")}
    for v in sorted(vehicles, key=lambda v: v.depart):
        t = info.get(v.id)
        arrival = float(t.get("arrival", -1)) if t is not None else -1.0
        cols["trip_id"].append(v.id)
        cols["app_user"].append(S.rerouting_rank(v.id, seed) < share)
        cols["requested_departure_s"].append(v.depart)
        cols["departure_s"].append(float(t.get("depart")) if t is not None and float(t.get("depart", -1)) >= 0 else None)
        cols["arrival_s"].append(arrival if arrival >= 0 else None)
        cols["finished"].append(arrival >= 0)
        cols["trip_time_s"].append((arrival if arrival >= 0 else END) - v.depart)
        cols["route_length_m"].append(float(t.get("routeLength")) if t is not None else 0.0)
        cols["time_lost_s"].append(float(t.get("timeLoss")) + float(t.get("departDelay")) if t is not None else END - v.depart)
        cols["waiting_time_s"].append(float(t.get("waitingTime")) if t is not None else 0.0)
        cols["reroutes"].append(int(float(t.get("rerouteNo", 0))) if t is not None else 0)
        cols["planned_route"].append(v.route)
        cols["driven_route"].append(final.get(v.id))
    pq.write_table(pa.table(cols), work / "trips.parquet", compression="zstd")
    fields = {"entered": "entered", "left": "left", "speed": "speed_mps", "density": "density_veh_per_km",
              "occupancy": "occupancy_pct", "traveltime": "travel_time_s", "waitingTime": "waiting_time_s",
              "timeLoss": "time_lost_s", "sampledSeconds": "vehicle_seconds"}
    rows = {k: [] for k in ["begin_s", "end_s", "edge_id", *fields.values()]}
    for interval in ET.parse(edge_xml).getroot().iter("interval"):
        b, e = float(interval.get("begin")), float(interval.get("end"))
        for edge in interval.iter("edge"):
            rows["begin_s"].append(b)
            rows["end_s"].append(e)
            rows["edge_id"].append(edge.get("id"))
            for src, dst in fields.items():
                x = edge.get(src)
                rows[dst].append(float(x) if x not in (None, "") else None)
    pq.write_table(pa.table(rows), work / "edges.parquet", compression="zstd")
    for name in ("tripinfo.xml", "vehroutes.xml", "edges.xml"):
        (work / name).unlink(missing_ok=True)


# --------------------------------------------------------------------------- animation


def frames_from(edge_xml: Path) -> dict:
    """Speed / speed limit of every used street, per 2-minute interval (for the animation)."""
    net = load_net()
    ids = [e.getID() for e in net.getEdges()]
    index = {e: i for i, e in enumerate(ids)}
    limit = np.array([net.getEdge(e).getSpeed() for e in ids])
    times, rows = [], []
    for interval in ET.parse(edge_xml).getroot().iter("interval"):
        ratio = np.full(len(ids), np.nan, dtype=np.float32)
        for e in interval.iter("edge"):
            if e.get("id") in index and e.get("speed"):
                i = index[e.get("id")]
                ratio[i] = min(1.0, float(e.get("speed")) / limit[i])
        times.append(float(interval.get("begin")))
        rows.append(ratio)
    return {"times": np.array(times), "ratio": np.array(rows), "edges": np.array(ids)}


def animation() -> Path:
    """figures/larochelle.gif: the incident morning seen from above, without and with the app."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.collections import LineCollection
    from PIL import Image
    net = load_net()
    runs = {p: np.load(BUILD / "runs" / f"incident_p{p:g}_s1" / "frames.npz") for p in (0.0, 0.5)}
    ids = list(runs[0.0]["edges"])
    shapes = [net.getEdge(e).getShape() for e in ids]
    width = np.array([0.4 + 0.5 * net.getEdge(e).getLaneNumber() for e in ids])
    site = set(incident_site(net))
    cmap = matplotlib.colormaps["RdYlGn"]
    (x0, y0), (x1, y1) = net.getBBoxXY()
    images = []
    for k, t in enumerate(runs[0.0]["times"]):
        if t < 7 * 3600 or t >= 10 * 3600:
            continue
        fig, axes = plt.subplots(1, 2, figsize=(12, 5.4), dpi=80)
        for ax, (p, title) in zip(axes, ((0.0, "Nobody uses the app"), (0.5, "Half of the drivers use the app"))):
            r = runs[p]["ratio"][k]
            used = ~np.isnan(r)
            # full green from 80 % of the speed limit, so that slowdowns stand out
            colors = [(0.86, 0.86, 0.84, 1.0) if not u else cmap(min(1.0, v / 0.8)) for u, v in zip(used, np.nan_to_num(r))]
            ax.add_collection(LineCollection(shapes, colors=colors, linewidths=width))
            if 7.75 * 3600 <= t < 8.5 * 3600:
                sx, sy = net.getEdge(sorted(site)[0]).getShape()[0]
                ax.plot(sx, sy, marker="X", color="black", ms=11)
            ax.set_xlim(x0, x1)
            ax.set_ylim(y0, y1)
            ax.set_aspect("equal")
            ax.axis("off")
            ax.set_title(title, fontsize=13, loc="left")
            ax.plot([x0 + 500, x0 + 2500], [y0 + 500, y0 + 500], color="black", lw=2)
            ax.text(x0 + 1500, y0 + 700, "2 km", ha="center", fontsize=10)
        clock = f"{int(t // 3600)}:{int(t % 3600 // 60):02d}"
        fig.suptitle(f"La Rochelle, {clock}  -  green: traffic flows, orange: slow, red: jammed"
                     + ("  -  X: accident on the ring road" if 7.75 * 3600 <= t < 8.5 * 3600 else ""), fontsize=13)
        fig.tight_layout()
        fig.canvas.draw()
        images.append(Image.fromarray(np.asarray(fig.canvas.buffer_rgba())[..., :3]).convert("P", palette=Image.ADAPTIVE))
        plt.close(fig)
    FIGURES.mkdir(exist_ok=True)
    out = FIGURES / "larochelle.gif"
    images[0].save(out, save_all=True, append_images=images[1:], duration=250, loop=0, optimize=True)
    return out


def street_series(tag: str) -> tuple[np.ndarray, dict[str, int], np.ndarray, np.ndarray]:
    """Per street and 5-minute interval: speed / speed limit and time lost (s), from a run's street table."""
    import pyarrow.parquet as pq
    t = pq.read_table(BUILD / "runs" / tag / "edges.parquet").to_pydict()
    net = load_net()
    times = np.array(sorted({int(b) for b in t["begin_s"]}))
    ti = {v: i for i, v in enumerate(times)}
    ids = sorted({e for e in t["edge_id"] if net.hasEdge(e)})
    ei = {e: i for i, e in enumerate(ids)}
    ratio = np.full((len(times), len(ids)), np.nan, dtype=np.float32)
    lost = np.zeros((len(times), len(ids)), dtype=np.float32)
    for b, e, v, lo in zip(t["begin_s"], t["edge_id"], t["speed_mps"], t["time_lost_s"]):
        if e in ei and v is not None:
            ratio[ti[int(b)], ei[e]] = min(1.0, v / net.getEdge(e).getSpeed())
            lost[ti[int(b)], ei[e]] = lo or 0.0
    return times, ei, ratio, lost


def animation_zoom(seed: int = 2, share: float = 0.5, size: float = 2600.0) -> Path:
    """figures/larochelle.gif: the whole city, and a zoom on the area where the app removes the most time lost
    in jams, without and with the app (normal morning, one seed), every 5 minutes from 7:00 to 10:00."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.collections import LineCollection
    from matplotlib.patches import Rectangle
    from PIL import Image
    net = load_net()
    t0, e0, r0, l0 = street_series(f"normal_p0_s{seed}")
    t1, e1, r1, l1 = street_series(f"normal_p{share:g}_s{seed}")
    ids = sorted(set(e0) & set(e1))
    shapes = {e: net.getEdge(e).getShape() for e in ids}
    mid = {e: shapes[e][len(shapes[e]) // 2] for e in ids}
    # where the app helps: time lost 7:30-9:00 without the app minus with it, per street
    w0 = (t0 >= 7.5 * 3600) & (t0 < 9 * 3600)
    w1 = (t1 >= 7.5 * 3600) & (t1 < 9 * 3600)
    gain = {e: l0[w0, e0[e]].sum() - l1[w1, e1[e]].sum() for e in ids}
    pts = np.array([mid[e] for e in ids])
    g = np.array([max(0.0, gain[e]) for e in ids])
    best, centre = -1.0, pts[0]
    for c in pts[g > np.quantile(g, 0.99)]:           # try windows centred on the streets that gain most
        inside = (np.abs(pts[:, 0] - c[0]) < size / 2) & (np.abs(pts[:, 1] - c[1]) < size * 0.4)
        if g[inside].sum() > best:
            best, centre = g[inside].sum(), c
    box = (centre[0] - size / 2, centre[1] - size * 0.4, size, size * 0.8)
    cmap = matplotlib.colormaps["RdYlGn"]
    all_edges = [e.getID() for e in net.getEdges()]
    grey = (0.86, 0.86, 0.84, 1.0)
    (x0, y0), (x1, y1) = net.getBBoxXY()
    frames = []
    for k, t in enumerate(t0):
        if t < 7 * 3600 or t >= 10 * 3600 or t not in set(t1):
            continue
        k1 = int(np.where(t1 == t)[0][0])
        fig = plt.figure(figsize=(14, 5.6), dpi=80)
        axes = [fig.add_axes([0.0, 0.02, 0.33, 0.86]), fig.add_axes([0.34, 0.02, 0.32, 0.86]),
                fig.add_axes([0.675, 0.02, 0.32, 0.86])]

        def draw(ax, ratio, index, width_scale):
            cols, segs, widths = [], [], []
            for e in all_edges:
                if e not in shapes:
                    segs.append(net.getEdge(e).getShape()); cols.append(grey); widths.append(0.3 * width_scale)
                    continue
                v = ratio[index, (e0 if ratio is r0 else e1)[e]]
                segs.append(shapes[e]); widths.append((0.4 + 0.5 * net.getEdge(e).getLaneNumber()) * width_scale)
                cols.append(grey if np.isnan(v) else cmap(min(1.0, v / 0.8)))
            ax.add_collection(LineCollection(segs, colors=cols, linewidths=widths))
            ax.set_aspect("equal")
            ax.axis("off")
        draw(axes[0], r0, k, 1.0)
        axes[0].set_xlim(x0, x1); axes[0].set_ylim(y0, y1)
        axes[0].add_patch(Rectangle(box[:2], box[2], box[3], fill=False, ec="black", lw=2))
        axes[0].set_title("La Rochelle, nobody on the app", fontsize=12, loc="left")
        for ax, ratio, index, title in ((axes[1], r0, k, "Zoom: nobody on the app"),
                                        (axes[2], r1, k1, f"Zoom: {share:.0%} of drivers on the app")):
            draw(ax, ratio, index, 2.2)
            ax.set_xlim(box[0], box[0] + box[2]); ax.set_ylim(box[1], box[1] + box[3])
            ax.set_title(title, fontsize=12, loc="left")
            ax.plot([box[0] + 150, box[0] + 650], [box[1] + 120, box[1] + 120], color="black", lw=2)
            ax.text(box[0] + 400, box[1] + 170, "500 m", ha="center", fontsize=10)
        fig.suptitle(f"{int(t // 3600)}:{int(t % 3600 // 60):02d}   green: traffic flows   orange: slow   red: jammed",
                     fontsize=13, y=0.99)
        fig.canvas.draw()
        frames.append(Image.fromarray(np.asarray(fig.canvas.buffer_rgba())[..., :3]).convert("P", palette=Image.ADAPTIVE))
        plt.close(fig)
    out = FIGURES / "larochelle.gif"
    frames[0].save(out, save_all=True, append_images=frames[1:], duration=450, loop=0, optimize=True)
    return out


# --------------------------------------------------------------------------- command line


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("step", choices=["fetch", "prepare", "calibrate", "rescore", "routes", "run", "gif", "smoke"])
    p.add_argument("--workers", type=int, default=2)
    a = p.parse_args()
    if a.step == "fetch":
        fetch()
    elif a.step == "prepare":
        prepare()
    elif a.step == "calibrate":
        calibration_grid(a.workers)
    elif a.step == "rescore":
        rescore()
    elif a.step == "routes":
        usual_routes(a.workers)
    elif a.step == "run":
        scenarios(a.workers)
    elif a.step == "gif":
        print(animation_zoom())
    elif a.step == "smoke":
        net_path, net = network(), load_net()
        z = zones(net)
        tr = trips(z, net, 0.1, 0.5, 1)
        kinds = {k: sum(1 for t in tr if t[3] == k) for k in ("inside", "in", "out", "cross", "through")}
        veh, dump = equilibrium(net_path, tr, BUILD / "smoke", iterations=2)
        print("smoke ok:", len(veh), "routes", kinds, score(dump_flows(dump, *PEAK), counted_edges(net)))


if __name__ == "__main__":
    main()
