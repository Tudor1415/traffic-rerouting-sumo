"""Checks of the simulation layer on small, fast cases (a few seconds in total)."""

import pytest

pytest.importorskip("sumo")

import sumolib  # noqa: E402

from rerouting import experiments as E  # noqa: E402
from rerouting import sumo as S  # noqa: E402


@pytest.fixture(scope="module")
def two_road(tmp_path_factory):
    return S.build_two_road(tmp_path_factory.mktemp("net") / "two.net.xml")


def route_length(net, edges):
    n = sumolib.net.readNet(str(net))
    return sum(n.getEdge(e).getLength() for e in edges.split())


def test_two_road_network(two_road):
    n = sumolib.net.readNet(str(two_road))
    assert {t.getID() for t in n.getTrafficLights()} == {"S", "L"}
    short, long = route_length(two_road, S.SHORT_ROUTE), route_length(two_road, S.LONG_ROUTE)
    assert short < 2000 < long  # the threshold used to tell the routes apart


def test_demand_is_reproducible_and_poisson():
    a = S.two_road_demand(1200, seed=7, duration=3600)
    assert a == S.two_road_demand(1200, seed=7, duration=3600)
    assert a != S.two_road_demand(1200, seed=8, duration=3600)
    assert 1100 < len(a) < 1300
    assert all(0 <= v.depart < 3600 for v in a)


def test_rerouting_groups_are_nested():
    ids = [f"v{i}" for i in range(2000)]
    quarter = {i for i in ids if S.rerouting_rank(i, 3) < 0.25}
    half = {i for i in ids if S.rerouting_rank(i, 3) < 0.5}
    assert quarter < half
    assert 0.45 < len(half) / len(ids) < 0.55


def test_light_traffic_rerouting_changes_nothing(two_road):
    vehicles = S.two_road_demand(300, seed=1, duration=1200)
    static = S.simulate(S.Scenario(two_road, vehicles, share=0.0, end=3600, seed=1))
    live = S.simulate(S.Scenario(two_road, vehicles, share=1.0, end=3600, seed=1))
    assert static["completed"] == live["completed"] == 1.0
    assert static["journey"] == pytest.approx(live["journey"])


def test_only_rerouters_leave_the_short_route_in_congestion(two_road):
    vehicles = S.two_road_demand(1600, seed=2, duration=1200)
    r0 = S.simulate(S.Scenario(two_road, vehicles, share=0.0, end=3600, seed=2), series_bin=300)
    r5 = S.simulate(S.Scenario(two_road, vehicles, share=0.5, end=3600, seed=2), series_bin=300)
    assert max(share for _, share in r0["long_share_series"]) == 0.0
    assert max(share for _, share in r5["long_share_series"]) > 0.2
    assert r5["journey"] < r0["journey"]
    assert r5["reroutes"] > 0


def test_vehicles_that_never_entered_count_as_unfinished(two_road):
    vehicles = S.two_road_demand(3000, seed=3, duration=1800)
    r = S.simulate(S.Scenario(two_road, vehicles, share=0.0, end=900, seed=3))
    assert r["requested"] == sum(v.depart < 900 for v in vehicles)  # trips due later are not counted
    assert r["completed"] < 0.5
    assert r["journey"] > 0 and r["vehicle_hours"] > 0


def test_experiment_keys_are_unique():
    keys = [E.key(t) for name in E.EXPERIMENTS for t in E.tasks(name)]
    assert len(keys) == len(set(keys))
