import math

import numpy as np
import pytest

from rerouting.theory import (
    Road, cycle_mean_time, demand_threshold, equilibrium_flow, queue_distribution, simulate_queue,
    gain, herding_threshold, lagged_dynamics, mean_times, mixed_flow, share_threshold, system_optimum_flow,
)

R1, R2 = Road(T=146.0, C=752.0), Road(T=244.0, C=1059.0)  # the two SUMO routes, as measured


def test_queue_delay_is_convex_and_blows_up_at_capacity():
    assert R1.time(0) == R1.T
    assert R1.time(R1.C / 2) == pytest.approx(R1.T + 3600 / R1.C)  # half load: one service time of waiting
    assert math.isinf(R1.time(R1.C))
    xs = np.linspace(0, 0.95 * R1.C, 10)
    ts = np.array([R1.time(x) for x in xs])
    assert np.all(np.diff(ts, 2) > 0)  # convex


def test_light_traffic_everyone_on_short_route():
    d = 0.9 * demand_threshold(R1, R2)
    assert equilibrium_flow(d, R1, R2) == d
    assert gain(d, 1.0, R1, R2) == pytest.approx(0.0)


def test_threshold_formula_is_exact():
    ds = demand_threshold(R1, R2)
    assert R1.time(ds) == pytest.approx(R2.T)
    assert 700 < ds < R1.C  # rerouting starts to help just below the short road's capacity


@pytest.mark.parametrize("d", [900.0, 1200.0, 1500.0])
def test_equilibrium_equalises_times(d):
    x1 = equilibrium_flow(d, R1, R2)
    assert 0 < x1 < d
    assert R1.time(x1) == pytest.approx(R2.time(d - x1), rel=1e-9)


def test_gain_is_positive_once_route_one_is_busy_and_total_when_static_breaks():
    assert gain(740.0, 1.0, R1, R2) > 0.0
    assert gain(1200.0, 1.0, R1, R2) == 1.0  # static route 1 is over capacity, rerouting is not
    assert gain(1900.0, 1.0, R1, R2) == 0.0  # both routes saturated: no steady state either way


def test_share_saturates():
    d = 900.0
    ps = share_threshold(d, R1, R2)
    assert 0 < ps < 1
    full = mean_times(d, 1.0, R1, R2)["everyone"]
    assert mean_times(d, ps, R1, R2)["everyone"] == pytest.approx(full, rel=1e-9)
    assert mean_times(d, (ps + 1) / 2, R1, R2)["everyone"] == pytest.approx(full, rel=1e-9)
    assert mean_times(d, ps / 2, R1, R2)["everyone"] > full
    # non-rerouters benefit too: their time falls as others reroute, and is finite once enough do
    others = [mean_times(d, p, R1, R2)["others"] for p in np.linspace(0, ps, 11)]
    assert all(a >= b for a, b in zip(others, others[1:]))
    assert math.isinf(others[0]) and math.isfinite(others[-1])


def test_mixed_flow_bounds():
    d = 900.0
    for p in np.linspace(0, 1, 11):
        x1 = mixed_flow(d, p, R1, R2)
        assert (1 - p) * d - 1e-9 <= x1 <= d


def test_queue_markov_chain_gives_the_travel_time_law():
    for load in (0.3, 0.7, 0.9):
        pi = queue_distribution(load, 2000)
        assert pi.sum() == pytest.approx(1.0, abs=1e-6)
        mean_cars = float(np.dot(np.arange(len(pi)), pi))
        assert mean_cars == pytest.approx(load / (1 - load), rel=1e-6)
    # Little's law: time on the road = T / (1 - load), with T = 1 / service rate
    road = Road(T=1 / 0.5, C=0.5 * 3600)          # a bottleneck serving 0.5 car/s, nothing else
    for arrival in (0.1, 0.3, 0.4):
        simulated = simulate_queue(arrival, 0.5, horizon=200_000, seed=1)
        assert simulated == pytest.approx(road.time(arrival * 3600), rel=0.05)


def test_selfish_rerouting_is_not_the_system_optimum():
    d = 900.0
    x_so, x_ue = system_optimum_flow(d, R1, R2), equilibrium_flow(d, R1, R2)
    assert x_so < x_ue  # the optimum puts less traffic on the busy short route
    total = lambda x: x * R1.time(x) + (d - x) * R2.time(d - x)  # noqa: E731
    assert total(x_so) < total(x_ue)


def test_lagged_dynamics_settle_below_threshold_and_oscillate_above():
    d, beta = 1200.0, 0.02
    g_max = herding_threshold(d, beta, R1, R2)
    assert 0 < 2 * g_max <= 1.0                    # the cases below are feasible shares
    calm = lagged_dynamics(d, 0.5 * g_max, beta, R1, R2, steps=2000)
    just_above = lagged_dynamics(d, 1.2 * g_max, beta, R1, R2, steps=2000)
    far_above = lagged_dynamics(d, 2.0 * g_max, beta, R1, R2, steps=2000)
    assert np.ptp(calm[-200:]) < 1e-3 * d          # settles
    assert np.ptp(just_above[-200:]) > 1e-2 * d    # keeps swinging
    # small swings cost little; when many react at once the swings overload each road in turn
    t_calm = cycle_mean_time(calm, d, R1, R2, burn_in=1000)
    assert cycle_mean_time(far_above, d, R1, R2, burn_in=1000) > 1.1 * t_calm


def test_herding_threshold_falls_as_reactions_get_sharper():
    d = 1200.0
    assert herding_threshold(d, 0.02, R1, R2) > herding_threshold(d, 0.05, R1, R2) > herding_threshold(d, 0.2, R1, R2)


def test_rerouter_time_is_finite_when_all_rerouters_take_the_detour():
    # static drivers overload route 1, the few rerouters all use the empty detour
    t = mean_times(1200.0, 0.05, R1, R2)
    assert t["rerouters"] == pytest.approx(R2.time(0.05 * 1200.0))
    assert math.isinf(t["others"])
