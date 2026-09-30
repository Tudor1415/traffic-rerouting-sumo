"""What the Markov chain (rerouting/markov.py) gives in closed form, in its steady state.

* **One road.** Each car driving ahead costs 0.54 s (7.5 m at 13.89 m/s) and about T x / 3600 cars
  drive on the road at x cars per hour; each car waiting at the bottleneck costs 3600 / C s, and a
  bottleneck that lets cars go at a regular pace makes random arrivals wait on average
  1800 x / (C (C - x)) seconds (Webster's formula, half of the fully random queue). So

      t(x) = T (1 + x / 6667) + 1800 x / (C (C - x))       (Road.time)

* **When rerouting starts to help.** Drivers with fresh news leave the short road only once it takes
  longer than an empty detour: ``t1(d*) = T2`` (:func:`demand_threshold`).
* **How many rerouters are enough.** Fresh news spreads cars until both roads take the same time
  (the Wardrop equilibrium, :func:`equilibrium_flow`); drivers without the app all stay on the short
  road, so beyond a share ``p* = 1 - x_UE / d`` (:func:`share_threshold`) the extra rerouters have
  nothing left to balance.
* **Above capacity** the queues grow by ``d - C`` cars per hour and only ``C`` cars per hour get
  through (:func:`finished_share`).

The chain itself (not these formulas) is what is compared with SUMO. Only what held in both blind tests
is kept here; the chain's herding and stale-information predictions proved unreliable and are not used.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np


@dataclass(frozen=True)
class Road:
    """A road: free-flow time ``T`` (s) and a traffic light letting ``C`` cars per hour through."""

    T: float
    C: float

    def time(self, x: float) -> float:
        """Mean trip time (s) at ``x`` cars per hour in the chain's steady state; infinite at capacity."""
        if x < 0:
            raise ValueError("negative flow")
        if x >= self.C:
            return math.inf
        return self.T * (1 + x / 6667.0) + 1800.0 * x / (self.C * (self.C - x))


def equilibrium_flow(d: float, r1: Road, r2: Road) -> float:
    """Cars per hour on the short road when both roads take the same time (Wardrop).

    If the short road stays faster even with all ``d`` cars on it, everyone uses it.
    """
    if d <= 0:
        return 0.0
    if r1.time(d) <= r2.time(0.0):
        return float(d)
    lo, hi = max(0.0, d - r2.C), min(d, r1.C)
    for _ in range(200):
        mid = (lo + hi) / 2
        if r1.time(mid) < r2.time(d - mid):
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2


def demand_threshold(r1: Road, r2: Road) -> float:
    """Traffic below which rerouting cannot help: the short road still beats an empty detour, t1(d*) = T2."""
    lo, hi = 0.0, r1.C * (1 - 1e-9)
    for _ in range(200):
        mid = (lo + hi) / 2
        lo, hi = (mid, hi) if r1.time(mid) < r2.T else (lo, mid)
    return (lo + hi) / 2


def share_threshold(d: float, r1: Road, r2: Road) -> float:
    """Share of rerouters beyond which more of them change nothing: ``p* = 1 - x_UE / d``."""
    return 1.0 - equilibrium_flow(d, r1, r2) / d if d > 0 else 0.0


def finished_share(d: float, capacity: float, free_flow: float, demand_hours: float = 1.0,
                   horizon: float = 7200.0) -> float:
    """Share of the trips requested during ``demand_hours`` that end before ``horizon`` seconds,
    when ``capacity`` cars per hour get through from the moment the first car arrives."""
    served = capacity * (horizon - free_flow) / 3600.0
    return min(1.0, served / (d * demand_hours))


if __name__ == "__main__":
    # python -m rerouting.theory: the thresholds for the two measured roads
    from rerouting.conjectures import calibrate
    r1, r2 = calibrate()
    print(f"short road T={r1.T:.0f} s C={r1.C:.0f}/h; detour T={r2.T:.0f} s C={r2.C:.0f}/h")
    print(f"d* = {demand_threshold(r1, r2):.0f} cars/h")
    for d in (1200, 1500, 1800):
        print(f"p* at {d} cars/h = {share_threshold(d, r1, r2):.1%}")
