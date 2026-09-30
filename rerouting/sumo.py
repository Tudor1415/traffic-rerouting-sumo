"""Road networks, traffic demand and SUMO runs.

Demand is generated once per (network, demand level, seed) as a list of vehicles,
each with a departure time and a *fixed* route: the fastest route through the
empty network. That is what a driver without live information follows. Rerouting
drivers get SUMO's rerouting device and may change route from their departure on,
using edge travel times averaged over a recent window. Who reroutes is decided by
a persistent random rank per vehicle, so the group of rerouters at share 0.5
contains the group at share 0.25: comparisons differ only in who has live
information.
"""

from __future__ import annotations

import os
import random
import shutil
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path

import sumo as _sumo

SUMO_HOME = Path(_sumo.SUMO_HOME)
os.environ.setdefault("SUMO_HOME", str(SUMO_HOME))
BIN = Path(sys.prefix) / "bin"


def tool(name: str) -> str:
    """Path of a SUMO binary (sumo, netgenerate, netconvert, duarouter)."""
    path = shutil.which(name, path=str(BIN)) or shutil.which(name)
    if path is None:
        raise FileNotFoundError(f"SUMO tool {name!r} not found; install eclipse-sumo")
    return path


def run(cmd: list, cwd: Path | None = None) -> None:
    result = subprocess.run([str(c) for c in cmd], cwd=cwd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
                            text=True)
    if result.returncode != 0:
        raise RuntimeError(f"{Path(str(cmd[0])).name} failed:\n{result.stderr[-2000:]}")


# --------------------------------------------------------------------------- networks


def build_grid(out: Path, size: int = 6, block: float = 200.0, lanes: int = 2) -> Path:
    """A size x size grid of city streets (50 km/h) with fixed-time traffic lights at every crossing."""
    run([tool("netgenerate"), "--grid", "--grid.number", size, "--grid.length", block,
         "--default.lanenumber", lanes, "--default.speed", 13.89, "--default-junction-type", "traffic_light",
         "--tls.default-type", "static", "--no-turnarounds", "true", "--seed", 1, "-o", out])
    return out


def build_city(osm: Path, out: Path) -> Path:
    """A drivable network from an OpenStreetMap extract (traffic lights where the map has them)."""
    run([tool("netconvert"), "--osm-files", osm, "--geometry.remove", "--ramps.guess", "--junctions.join",
         "--tls.guess-signals", "--tls.discard-simple", "--tls.join", "--remove-edges.isolated",
         "--keep-edges.by-vclass", "passenger", "--edges.join", "--no-turnarounds", "true",
         "--no-warnings", "-o", out])
    return out


# Two routes between one origin and one destination. Route 1 ("short") is 1 km, route 2
# ("long") is a 2.2 km detour. A fixed-time traffic light on each route sets its capacity.
# Vehicles enter on a wide road and choose at the fork; the routes rejoin on separate
# lanes of a wide exit road, so neither the entrance nor the merge is a bottleneck.
TWO_ROAD_GREEN = {"short": 25, "long": 40}   # green seconds per 60 s cycle
TWO_ROAD_CYCLE = 60
SHORT_ROUTE = "in short1 short2 out"
LONG_ROUTE = "in long1 long2 long3 long4 out"


def build_two_road(out: Path, entry: float = 400.0) -> Path:
    """``entry``: length of the shared entry road (m) before the fork."""
    tmp = Path(tempfile.mkdtemp())
    nodes = f"""<nodes>
  <node id="O" x="-{entry:g}" y="0"/> <node id="A" x="0" y="0" type="priority"/>
  <node id="S" x="600" y="0" type="traffic_light"/> <node id="B" x="1000" y="0" type="priority"/>
  <node id="L1" x="0" y="600" type="priority"/> <node id="L" x="500" y="600" type="traffic_light"/>
  <node id="L2" x="1000" y="600" type="priority"/> <node id="D" x="1300" y="0"/>
</nodes>"""
    edges = """<edges>
  <edge id="in" from="O" to="A" numLanes="3" speed="13.89"/>
  <edge id="short1" from="A" to="S" numLanes="1" speed="13.89"/>
  <edge id="short2" from="S" to="B" numLanes="1" speed="13.89"/>
  <edge id="long1" from="A" to="L1" numLanes="1" speed="13.89"/>
  <edge id="long2" from="L1" to="L" numLanes="1" speed="13.89"/>
  <edge id="long3" from="L" to="L2" numLanes="1" speed="13.89"/>
  <edge id="long4" from="L2" to="B" numLanes="1" speed="13.89"/>
  <edge id="out" from="B" to="D" numLanes="2" speed="13.89"/>
</edges>"""
    connections = """<connections>
  <connection from="in" to="short1" fromLane="0" toLane="0"/>
  <connection from="in" to="short1" fromLane="1" toLane="0"/>
  <connection from="in" to="long1" fromLane="1" toLane="0"/>
  <connection from="in" to="long1" fromLane="2" toLane="0"/>
  <connection from="short2" to="out" fromLane="0" toLane="0"/>
  <connection from="long4" to="out" fromLane="0" toLane="1"/>
</connections>"""
    g_s, g_l, cyc = TWO_ROAD_GREEN["short"], TWO_ROAD_GREEN["long"], TWO_ROAD_CYCLE
    tls = f"""<additional>
  <tlLogic id="S" type="static" programID="0" offset="0">
    <phase duration="{g_s}" state="G"/> <phase duration="3" state="y"/> <phase duration="{cyc - g_s - 3}" state="r"/>
  </tlLogic>
  <tlLogic id="L" type="static" programID="0" offset="0">
    <phase duration="{g_l}" state="G"/> <phase duration="3" state="y"/> <phase duration="{cyc - g_l - 3}" state="r"/>
  </tlLogic>
</additional>"""
    for name, text in (("n.nod.xml", nodes), ("e.edg.xml", edges), ("c.con.xml", connections), ("t.tll.xml", tls)):
        (tmp / name).write_text(text)
    run([tool("netconvert"), "-n", tmp / "n.nod.xml", "-e", tmp / "e.edg.xml", "-x", tmp / "c.con.xml",
         "--tllogic-files", tmp / "t.tll.xml", "--no-turnarounds", "true", "-o", out])
    shutil.rmtree(tmp)
    return out


# --------------------------------------------------------------------------- demand


@dataclass(frozen=True)
class Vehicle:
    id: str
    depart: float
    route: str  # space-separated edges


def two_road_demand(veh_per_hour: float, seed: int, duration: float, route: str = "short") -> list[Vehicle]:
    """Poisson arrivals at the origin, all starting on ``route`` (the short one unless measuring a route)."""
    rng = random.Random(seed)
    edges = {"short": SHORT_ROUTE, "long": LONG_ROUTE}[route]
    t, out = 0.0, []
    while True:
        t += rng.expovariate(veh_per_hour / 3600.0)
        if t >= duration:
            return out
        out.append(Vehicle(f"v{len(out)}", round(t, 2), edges))


def network_demand(net: Path, veh_per_hour: float, seed: int, duration: float,
                   min_distance: float = 600.0) -> list[Vehicle]:
    """Trips between random streets, each on its fastest route through the *empty* network."""
    if veh_per_hour > 4 * 3600:  # randomTrips --binomial 4 inserts at most 4 cars per second
        raise ValueError(f"at most 14,400 cars per hour with --binomial 4, asked for {veh_per_hour}")
    with tempfile.TemporaryDirectory() as tmp:
        trips, routes = Path(tmp) / "trips.xml", Path(tmp) / "routes.xml"
        run([sys.executable, SUMO_HOME / "tools" / "randomTrips.py", "-n", net, "-o", trips, "-b", 0,
             "-e", duration, "-p", 3600.0 / veh_per_hour, "--binomial", 4, "--seed", seed,
             "--min-distance", min_distance, "--fringe-factor", 1, "--validate",
             "-r", Path(tmp) / "validated.rou.xml"])  # keep --validate's route file out of the working dir
        run([tool("duarouter"), "-n", net, "--route-files", trips, "-o", routes, "--ignore-errors",
             "--no-warnings", "--no-step-log", "--seed", seed, "--write-costs", "false",
             "--exit-times", "false"])
        return read_routes(routes)


def read_routes(path: Path) -> list[Vehicle]:
    """Vehicles and their (last chosen) routes from a SUMO route file."""
    out = []
    for v in ET.parse(path).getroot().iter("vehicle"):
        route = v.find("route")
        if route is None:  # route distribution (duaIterate output): take the last chosen route
            dist = v.find("routeDistribution")
            routes = dist.findall("route")
            last = int(dist.get("last", len(routes) - 1))
            route = routes[last]
        out.append(Vehicle(v.get("id"), float(v.get("depart")), route.get("edges")))
    return out


def rerouting_rank(vehicle_id: str, seed: int) -> float:
    """A fixed pseudo-random number in [0, 1) per vehicle: rerouters at share p are those with rank < p."""
    return random.Random(f"{seed}/{vehicle_id}").random()


def write_demand(vehicles: list[Vehicle], out: Path, share: float, seed: int) -> Path:
    """Writes the vehicles, giving the rerouting device to those whose rank is below ``share``."""
    lines = ["<routes>"]
    for v in sorted(vehicles, key=lambda v: v.depart):
        device = rerouting_rank(v.id, seed) < share
        lines.append(f'  <vehicle id="{v.id}" depart="{v.depart}" departLane="best" departSpeed="max">')
        lines.append(f'    <route edges="{v.route}"/>')
        if device:
            lines.append('    <param key="has.rerouting.device" value="true"/>')
        lines.append("  </vehicle>")
    lines.append("</routes>")
    out.write_text("\n".join(lines))
    return out


def experienced_routes(net: Path, vehicles: list[Vehicle], out_dir: Path, iterations: int = 25,
                       end: float = 7200.0) -> list[Vehicle]:
    """Routes of drivers who know the usual congestion: SUMO's iterative assignment (duaIterate).

    Starting from free-flow routes, each iteration simulates the day and lets part of
    the drivers switch to the route that was fastest on the previous day. After enough
    iterations the fixed routes are close to a dynamic user equilibrium.
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    trips = out_dir / "trips.xml"
    lines = ["<routes>"] + [f'  <trip id="{v.id}" depart="{v.depart}" from="{v.route.split()[0]}" '
                            f'to="{v.route.split()[-1]}" departLane="best" departSpeed="max"/>'
                            for v in sorted(vehicles, key=lambda v: v.depart)] + ["</routes>"]
    trips.write_text("\n".join(lines))
    run([sys.executable, SUMO_HOME / "tools" / "assign" / "duaIterate.py", "-n", net, "-t", trips,
         "-l", iterations, "-e", end, "--time-to-teleport=-1", "--disable-summary", "--no-gzip"], cwd=out_dir)
    last = out_dir / f"{iterations - 1:03d}"
    chosen = [p for p in last.glob("*.rou.xml") if not p.name.endswith(".alt.xml")]
    return read_routes(chosen[0])


# --------------------------------------------------------------------------- simulation


@dataclass
class Scenario:
    """One simulation: a network, its vehicles and the rerouting settings."""

    net: Path
    vehicles: list
    share: float = 0.0          # fraction of vehicles that reroute (nested by rank)
    period: float = 60.0        # seconds between two route checks of a rerouting vehicle
    window: int = 180           # seconds of recent traffic averaged into the travel times they use
    synchronize: bool = False   # all rerouters check at the same moments (herding stress test)
    end: float = 7200.0         # hard stop: trips still running count as unfinished
    seed: int = 0               # rank seed (who reroutes) and SUMO seed
    additional: tuple = ()      # extra SUMO input files (incidents, measurements)
    teleport: float = -1        # seconds a car may stay stuck before SUMO moves it on (-1: never)
    measure: tuple | None = None  # (begin, end): summarise only the trips due in that window
    keep: Path | None = None    # keep SUMO's trip, route and statistics outputs in this folder


def simulate(s: Scenario, series_bin: float = 0.0) -> dict:
    """Runs SUMO; returns run statistics and, if ``series_bin`` > 0, route shares over time."""
    with tempfile.TemporaryDirectory() as tmp:
        demand, tripinfo = Path(tmp) / "demand.xml", Path(tmp) / "tripinfo.xml"
        write_demand(s.vehicles, demand, s.share, s.seed)
        run([tool("sumo"), "-n", s.net, "-r", demand, "--end", s.end, "--seed", s.seed,
             "--time-to-teleport", s.teleport, "--no-step-log", "--no-warnings", "--duration-log.disable",
             *(["-a", ",".join(str(a) for a in s.additional)] if s.additional else []),
             "--statistic-output", Path(tmp) / "stats.xml",
             "--tripinfo-output", tripinfo, "--tripinfo-output.write-unfinished",
             "--device.rerouting.period", s.period, "--device.rerouting.adaptation-interval", 1,
             "--device.rerouting.adaptation-steps", s.window,
             "--device.rerouting.synchronize", str(s.synchronize).lower(),
             *(["--vehroute-output", Path(tmp) / "vehroutes.xml", "--vehroute-output.write-unfinished"]
               if series_bin > 0 or s.keep else [])])
        if s.keep:
            s.keep.mkdir(parents=True, exist_ok=True)
            for name in ("tripinfo.xml", "vehroutes.xml", "stats.xml"):
                shutil.copy(Path(tmp) / name, s.keep / name)
        routes = final_routes(Path(tmp) / "vehroutes.xml") if series_bin > 0 else None
        measured = s.vehicles if s.measure is None else [v for v in s.vehicles if s.measure[0] <= v.depart < s.measure[1]]
        stats = summarise(tripinfo, measured, s.end, s.share, s.seed, series_bin, routes)
        teleports = ET.parse(Path(tmp) / "stats.xml").getroot().find("teleports")
        stats["teleports"] = int(teleports.get("total", 0)) if teleports is not None else 0
        return stats


def final_routes(path: Path) -> dict[str, tuple[float, bool]]:
    """Actual departure time and whether the last route uses the long road, for every inserted vehicle."""
    out = {}
    for v in ET.parse(path).getroot().iter("vehicle"):
        route = v.find("route")
        if route is None:  # rerouted vehicles keep their routes in a routeDistribution; the last one is used
            route = list(v.iter("route"))[-1]
        out[v.get("id")] = (float(v.get("depart")), any(e.startswith("long") for e in route.get("edges").split()))
    return out


def summarise(tripinfo: Path, vehicles: list, end: float, share: float, seed: int, series_bin: float = 0.0,
              routes: dict | None = None) -> dict:
    """Statistics over *all requested* trips.

    Journey time = time from the requested departure to arrival, including any wait to
    enter the road. Trips still running, or never able to enter, at the end are counted
    with their time so far (a lower bound), and they are not "completed". Trips due
    after the end are not requested yet and are ignored.
    """
    info = {t.get("id"): t for t in ET.parse(tripinfo).getroot().findall("tripinfo")}
    vehicles = [v for v in vehicles if v.depart < end]  # only trips that were due before the end
    rows, arrivals, lost = [], [], []
    for v in vehicles:
        t = info.get(v.id)
        rerouter = rerouting_rank(v.id, seed) < share
        if t is None:  # never entered the network before the end
            rows.append((end - v.depart, False, rerouter, 0, 0.0, None))
            lost.append(end - v.depart)
            continue
        # time lost compared with driving the same route alone, plus the wait to enter
        lost.append(float(t.get("timeLoss", 0)) + float(t.get("departDelay", 0)))
        arrival = float(t.get("arrival", "-1"))
        done = arrival >= 0
        journey = (arrival if done else end) - v.depart
        rows.append((journey, done, rerouter, int(float(t.get("rerouteNo", "0"))), float(t.get("waitingTime")),
                     float(t.get("routeLength"))))
        if done:
            arrivals.append(arrival)

    def mean(values):
        values = list(values)
        return sum(values) / len(values) if values else float("nan")

    stats = {
        "requested": len(rows),
        "completed": mean(r[1] for r in rows),
        "journey": mean(r[0] for r in rows),
        "vehicle_hours": sum(r[0] for r in rows) / 3600.0,
        "waiting": mean(r[4] for r in rows),
        "time_lost": mean(lost),
        "distance_km": mean(r[5] / 1000.0 for r in rows if r[5] is not None),
        "reroutes": mean(r[3] for r in rows if r[2]),
        "journey_rerouters": mean(r[0] for r in rows if r[2]),
        "journey_others": mean(r[0] for r in rows if not r[2]),
        # vehicles leaving per hour between minute 15 and minute 60: the capacity when demand exceeds it
        "throughput": sum(900 <= a < 3600 for a in arrivals) / 2700 * 3600,
    }
    if series_bin > 0 and routes is not None:
        # route of every vehicle that entered the network (finished or not), binned by its actual
        # departure: the two-road detour share, overall after minute 10 and minute by minute. The
        # series follows the rerouters' own choices (everybody's when nobody reroutes): drivers on
        # the short road wait longer to enter once its queue spills back, which would mix the two.
        bins, picks = {}, []
        for v, r in zip(vehicles, rows):
            if v.id in routes:
                depart, uses_long = routes[v.id]
                if 600 <= depart < 3600:
                    picks.append((uses_long, r[2], r[0]))
                if share > 0 and not r[2]:
                    continue
                k = int(depart // series_bin)
                n, n_long = bins.get(k, (0, 0))
                bins[k] = (n + 1, n_long + uses_long)
        stats["long_share_series"] = [[k * series_bin, n_long / n, n] for k, (n, n_long) in sorted(bins.items())]
        stats["long_share"] = mean(u for u, _, _ in picks)
        stats["long_share_rerouters"] = mean(u for u, rer, _ in picks if rer)
        stats["long_share_others"] = mean(u for u, rer, _ in picks if not rer)
        stats["journey_short_route"] = mean(j for u, _, j in picks if not u)
        stats["journey_long_route"] = mean(j for u, _, j in picks if u)
    return stats
