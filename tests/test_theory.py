import math

import numpy as np
import pytest

from rerouting.markov import Chain, run, swing, switches
from rerouting.theory import Road, demand_threshold, equilibrium_flow, finished_share, herding_swing, share_threshold

R1, R2 = Road(T=146.0, C=752.0), Road(T=244.0, C=1059.0)  # the two SUMO routes, as measured


# --------------------------------------------------------------------------- closed forms


def test_road_law_grows_with_traffic_and_blows_up_at_capacity():
    assert R1.time(0) == R1.T
    assert math.isinf(R1.time(R1.C))
    ts = np.array([R1.time(x) for x in np.linspace(0, 0.95 * R1.C, 10)])
    assert np.all(np.diff(ts) > 0) and np.all(np.diff(ts, 2) > 0)
    # far below capacity the drive term dominates: each car on the road costs 0.54 s
    assert R2.time(300) - R2.T == pytest.approx(R2.T * 300 / 6667 + 1800 * 300 / (R2.C * (R2.C - 300)))


def test_threshold_is_where_the_short_road_stops_beating_an_empty_detour():
    ds = demand_threshold(R1, R2)
    assert R1.time(ds) == pytest.approx(R2.T)
    assert equilibrium_flow(0.9 * ds, R1, R2) == 0.9 * ds


@pytest.mark.parametrize("d", [900.0, 1200.0, 1500.0])
def test_equilibrium_equalises_times(d):
    x1 = equilibrium_flow(d, R1, R2)
    assert 0 < x1 < d
    assert R1.time(x1) == pytest.approx(R2.time(d - x1), rel=1e-9)
    assert 0 < share_threshold(d, R1, R2) < 1


def test_herding_swing_and_finished_share():
    assert herding_swing(0.3, 0.4) == 0.0
    assert herding_swing(1.0, 0.5) == pytest.approx(0.5)
    assert finished_share(2400, 752, 146) == pytest.approx(752 * (7200 - 146) / 3600 / 2400)


# --------------------------------------------------------------------------- the chain


@pytest.mark.parametrize("road,x", [("short", 300.0), ("short", 550.0), ("long", 600.0)])
def test_chain_reproduces_the_road_law(road, x):
    chain = Chain(R1, R2, x, split=float(road == "long"), demand_seconds=20_000, horizon=24_000)
    expected = (R1 if road == "short" else R2).time(x)
    assert run(chain, replicas=30, seed=1)["journey"] == pytest.approx(expected, rel=0.06)


def test_light_traffic_rerouting_changes_nothing():
    static = run(Chain(R1, R2, 400.0), replicas=60, seed=2)
    live = run(Chain(R1, R2, 400.0, share=1.0), replicas=60, seed=2)
    assert abs(static["journey"] - live["journey"]) < 3.0
    assert live["long_share"] < 0.02


def test_few_rerouters_all_take_the_detour_and_many_swing():
    few = run(Chain(R1, R2, 1800.0, share=0.3), replicas=30, seed=3)
    many = run(Chain(R1, R2, 1800.0, share=1.0), replicas=30, seed=3)
    assert few["long_share_rerouters"] > 0.95 and few["swing"] < 0.05
    assert many["swing"] > 0.3 and many["switches"] > 2


def test_detour_admits_its_capacity():
    r = run(Chain(R1, R2, 2400.0, split=1.0), replicas=20, seed=4)
    assert r["completed"] == pytest.approx(finished_share(2400.0, R2.C, 0.0), abs=0.05)


def test_swing_removes_sampling_noise_and_switches_count_crossings():
    rng = np.random.default_rng(0)
    minutes = range(10, 60)
    noise = [[60.0 * m, rng.binomial(30, 0.5) / 30, 30] for m in minutes]
    assert swing(noise) < 0.05
    square = [[60.0 * m, 1.0 if (m // 5) % 2 else 0.0, 30] for m in minutes]
    assert swing(square) == pytest.approx(0.5, abs=0.03)
    assert switches(square) == pytest.approx(12, abs=1.5)
    assert switches([[60.0 * m, 0.4, 30] for m in minutes]) == 0.0
