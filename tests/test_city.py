"""The city theory's pieces on tiny hand-made networks (CPU)."""

import math
from types import SimpleNamespace

import pytest

torch = pytest.importorskip("torch")

from rerouting import city as C  # noqa: E402


def tiny(T, lanes=1.0, speed=13.89, C_=1800.0, minor=None, node=None, arcs=()):
    n = len(T)
    f = lambda xs: torch.tensor(xs, dtype=torch.float32)
    city = SimpleNamespace(torch=torch, device="cpu", E=n, T=f(T), wait=torch.zeros(n),
                           per_car=f([C.CAR_SPACE / speed / lanes] * n), C=f([C_] * n),
                           minor=torch.tensor(minor or [False] * n), node=torch.tensor(node or list(range(n))),
                           nodes=n, arc_from=torch.tensor([a for a, _ in arcs], dtype=torch.long),
                           arc_to=torch.tensor([b for _, b in arcs], dtype=torch.long))
    city.tables = lambda cost, dests, chunk=512: C.City.tables(city, cost, dests, chunk)
    return city


def test_road_law_matches_the_lab_below_capacity():
    city = tiny([100.0])
    for x in (100.0, 600.0, 1200.0):
        law = float(C.road_time(city, torch.tensor([x]), city.C))
        lab = 100 * (1 + x / 6667) + 1800 * x / (1800 * (1800 - x))
        assert law == pytest.approx(lab, rel=0.02, abs=1.0)


def test_road_law_above_capacity_is_the_growing_queue():
    city = tiny([100.0])
    x = 2700.0                                  # 1.5 x capacity for one hour
    law = float(C.road_time(city, torch.tensor([x]), city.C))
    assert law - 100 * (1 + x / 6667) == pytest.approx(1800 * (x / 1800 - 1), rel=0.02)


def test_give_way_capacity_is_the_share_of_quiet_seconds():
    city = tiny([10.0, 10.0], minor=[False, True], node=[0, 0])
    cap = C.give_way(city, torch.tensor([1800.0, 0.0]))
    assert float(cap[0]) == pytest.approx(1800.0)
    assert float(cap[1]) == pytest.approx(1800.0 * math.exp(-0.5), rel=1e-4)


def test_tables_find_the_fastest_way_and_the_next_street():
    # 0 -> 1 -> 3 (10 + 10 + 1) against 0 -> 2 -> 3 (10 + 50 + 1)
    city = tiny([10.0, 10.0, 50.0, 1.0], arcs=[(0, 1), (0, 2), (1, 3), (2, 3)])
    dist, nxt = city.tables(city.T[None], torch.tensor([3]))
    assert float(dist[0, 0, 0]) == pytest.approx(21.0)
    assert int(nxt[0, 0, 0]) == 1 and int(nxt[0, 0, 3]) == -1
    slow = city.T.clone()
    slow[1] = 100.0
    _, nxt = city.tables(slow[None], torch.tensor([3]))
    assert int(nxt[0, 0, 0]) == 2


def test_share_star_and_crossing():
    assert C.share_star({0.0: 100.0, 0.5: 60.0, 0.8: 52.0, 1.0: 50.0}, [0.0, 0.5, 0.8, 1.0]) == 0.5
    assert C.crossing({0.5: 0.0, 1.0: 10.0, 1.5: 40.0}) == pytest.approx(1.0 + 5 / 30 * 0.5)
    assert C.crossing({0.5: 0.0, 1.0: 5.0}) is None
