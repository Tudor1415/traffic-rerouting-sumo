import math

import numpy as np
import pytest

from rerouting.markov import Chain, run, swing, switches
from rerouting.theory import (
    Road, demand_threshold, equilibrium_flow, finished_share, herding_swing, queue_distribution, share_threshold,
)

R1, R2 = Road(T=146.0, C=752.0), Road(T=244.0, C=1059.0)  # the two SUMO routes, as measured


# --------------------------------------------------------------------------- closed forms


def test_queue_delay_is_convex_and_blows_up_at_capacity():
    assert R1.time(0) == R1.T
    assert R1.time(R1.C / 2) == pytest.approx(R1.T + 3600 / R1.C)  # half load: one service time of waiting
    assert math.isinf(R1.time(R1.C))
    ts = np.array([R1.time(x) for x in np.linspace(0, 0.95 * R1.C, 10)])
    assert np.all(np.diff(ts, 2) > 0)


def test_birth_death_chain_gives_the_mean_queue():
    for load in (0.3, 0.7, 0.9):
        pi = queue_distribution(load, 2000)
        assert pi.sum() == pytest.approx(1.0, abs=1e-6)
        assert float(np.dot(np.arange(len(pi)), pi)) == pytest.approx(load / (1 - load), rel=1e-6)


def test_threshold_formula_is_exact():
    ds = demand_threshold(R1, R2)
    assert R1.time(ds) == pytest.approx(R2.T)
    assert equilibrium_flow(0.9 * ds, R1, R2) == 0.9 * ds  # below it the detour is never worth it


@pytest.mark.parametrize("d", [900.0, 1200.0, 1500.0])
def test_equilibrium_equalises_times(d):
    x1 = equilibrium_flow(d, R1, R2)
    assert 0 < x1 < d
    assert R1.time(x1) == pytest.approx(R2.time(d - x1), rel=1e-9)
    assert 0 < share_threshold(d, R1, R2) < 1


def test_herding_swing_and_finished_share():
    assert herding_swing(0.3, 0.4) == 0.0
    assert herding_swing(1.0, 0.5) == pytest.approx(0.5)
    assert finished_share(1000, 752, 146) == 1.0
    assert finished_share(2400, 752, 146) == pytest.approx(752 * (7200 - 146) / 3600 / 2400)


# --------------------------------------------------------------------------- the chain


def test_chain_matches_its_steady_state_on_one_road():
    for x in (300.0, 550.0):
        chain = Chain(R1, R2, x, demand_seconds=30_000, horizon=40_000)
        assert run(chain, replicas=40, seed=1)["journey"] == pytest.approx(R1.time(x), rel=0.06)


def test_chain_light_traffic_rerouting_changes_nothing():
    static = run(Chain(R1, R2, 400.0), replicas=100, seed=2)
    live = run(Chain(R1, R2, 400.0, share=1.0), replicas=100, seed=2)
    assert abs(static["journey"] - live["journey"]) < 2.0
    assert live["long_share"] < 0.01


def test_chain_few_rerouters_all_take_the_detour_and_many_swing():
    few = run(Chain(R1, R2, 1800.0, share=0.3), replicas=50, seed=3)
    many = run(Chain(R1, R2, 1800.0, share=1.0), replicas=50, seed=3)
    assert few["long_share_rerouters"] > 0.95 and few["swing"] < 0.05
    p_star = share_threshold(1800.0, R1, R2)
    assert 0.5 * herding_swing(1.0, p_star) < many["swing"] <= herding_swing(1.0, p_star) + 0.02


def test_chain_above_capacity_serves_only_capacity():
    r = run(Chain(R1, R2, 2400.0), replicas=100, seed=4)
    assert r["completed"] == pytest.approx(finished_share(2400.0, R1.C, R1.T), abs=0.03)


def test_swing_removes_sampling_noise_and_switches_count_crossings():
    rng = np.random.default_rng(0)
    minutes = range(10, 60)
    noise = [[60.0 * m, rng.binomial(30, 0.5) / 30, 30] for m in minutes]
    assert swing(noise) < 0.05
    square = [[60.0 * m, 1.0 if (m // 5) % 2 else 0.0, 30] for m in minutes]
    assert swing(square) == pytest.approx(0.5, abs=0.03)
    assert switches(square) == pytest.approx(12, abs=1.5)  # a 10-minute cycle crosses twice
    assert switches([[60.0 * m, 0.4, 30] for m in minutes]) == 0.0
