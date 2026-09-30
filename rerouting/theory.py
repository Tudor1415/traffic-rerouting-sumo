"""What the Markov chain (rerouting/markov.py) gives in closed form, in its steady state.

The chain follows two queues, one per road, and the news ``s`` of which road looks faster.

* **One road.** Its queue goes up by one when a car arrives (``x`` per hour) and down by one
  when the light lets a car go (``C`` per hour): a birth-death chain. Its balance equations
  ``x P(n) = C P(n+1)`` give ``P(n) = (1 - rho) rho^n`` with the load ``rho = x / C``
  (:func:`queue_distribution`), so ``rho / (1 - rho)`` cars wait on average and a car
  arriving behind them waits ``3600 rho / (C - x)`` seconds:

      t(x) = T + 3600 x / (C (C - x))       (Road.time)

* **When rerouting starts to help.** Drivers with fresh news leave the short road only once
  its wait exceeds the extra driving time of the detour: at ``d*`` (:func:`demand_threshold`).
* **How many rerouters are enough.** Fresh news spreads cars until both roads take the same
  time (the Wardrop equilibrium, :func:`equilibrium_flow`); drivers without the app all stay on
  the short road, so beyond a share ``p* = 1 - x_UE / d`` (:func:`share_threshold`) the extra
  rerouters have nothing left to balance.
* **Herding.** With old news every rerouter follows the same ``s`` at once: the crowd is on
  the detour or on the short road, roughly a fraction ``q = p*/p`` of the time on the detour,
  so the share of rerouters there spreads by ``sqrt(q (1 - q))`` (:func:`herding_swing`).
* **Above capacity** the chain has no steady state: the queue grows by ``d - C`` cars per hour
  and only ``C`` cars per hour get through (:func:`finished_share`).

These formulas are the chain with very short time steps. With the one-second steps of the chain
itself, a light lets a car go with probability ``C/3600`` per second and the wait is slightly
shorter (by a factor ``1 - C/3600``).

The chain itself (not these formulas) is what is compared with SUMO.
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
        return self.T + 3600.0 * x / (self.C * (self.C - x)) if x < self.C else math.inf


def queue_distribution(load: float, n_max: int) -> np.ndarray:
    """Steady-state probability of ``n = 0 .. n_max`` cars waiting at one light: ``(1 - rho) rho^n``."""
    if not 0 <= load < 1:
        raise ValueError("the queue settles only for a load below 1")
    n = np.arange(n_max + 1)
    return (1 - load) * load ** n


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
    """Traffic below which rerouting cannot help: ``t1(d*) = T2``.

    Solving ``3600 d / (C1 (C1 - d)) = T2 - T1`` gives ``d* = C1 a / (1 + a)`` with
    ``a = C1 (T2 - T1) / 3600``, the number of cars the light serves while one detour is driven.
    """
    a = r1.C * (r2.T - r1.T) / 3600.0
    return r1.C * a / (1.0 + a)


def share_threshold(d: float, r1: Road, r2: Road) -> float:
    """Share of rerouters beyond which more of them change nothing: ``p* = 1 - x_UE / d``."""
    return 1.0 - equilibrium_flow(d, r1, r2) / d if d > 0 else 0.0


def herding_swing(p: float, p_star: float) -> float:
    """Minute-to-minute spread of the share of rerouters on the detour when they all move together.

    They are all on the detour or all on the short road; if they spend a fraction ``q`` of the time on
    the detour, about the share needed there (``q = p*/p``, and 1 below ``p*``), the share jumps between
    1 and 0 and spreads by ``sqrt(q (1 - q))``, at most 0.5.
    """
    q = min(1.0, p_star / p) if p > 0 else 1.0
    return math.sqrt(q * (1.0 - q))


def finished_share(d: float, capacity: float, free_flow: float, demand_hours: float = 1.0,
                   horizon: float = 7200.0) -> float:
    """Share of the trips requested during ``demand_hours`` that end before ``horizon`` seconds,
    when ``capacity`` cars per hour get through from the moment the first car arrives."""
    served = capacity * (horizon - free_flow) / 3600.0
    return min(1.0, served / (d * demand_hours))
