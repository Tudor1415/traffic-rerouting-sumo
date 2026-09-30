"""Simple theory of dynamic rerouting on two parallel routes.

Model
-----
A road takes ``T`` seconds to drive when empty, and its bottleneck (here a
traffic light) lets at most ``C`` cars per hour through. The bottleneck is a
queue: cars arrive at random and are served one at a time at rate ``mu = C``.
Seen as a Markov chain (the M/M/1 queue, :func:`queue_distribution`), a car
waits on average ``rho / (mu - lambda)`` in it, with the load ``rho = x / C``.
So at a flow of ``x`` cars per hour

    t(x) = T + 3600 x / (C (C - x))      (seconds, x < C).

The queueing term is tiny until the load approaches 1 and then explodes, so
moving a few cars off a nearly full road saves a lot of time.

Two parallel routes join the same origin and destination; route 1 is the
shorter one (``T1 < T2``). A demand of ``d`` vehicles per hour must travel.

* **Without live information** every driver takes the route that is fastest on
  an empty network: route 1.
* **With dynamic rerouting** drivers move to whichever route is faster until
  both take the same time (Wardrop's user equilibrium), or until route 2 is no
  longer worth it.
* A share ``p`` of drivers reroutes; the others stay on route 1.

Two Markov models explain the rest:

* :func:`queue_distribution`: the bottleneck of a road is a birth-death Markov
  chain (cars arrive and leave one at a time). Its stationary distribution gives
  the waiting time used in ``t(x)`` above, which is why delays explode near capacity.
* :func:`lagged_dynamics`: at each information update a fraction ``g`` of the
  drivers reconsiders using the travel times of the *previous* update. When too
  many react to the same stale information at once (``g`` above
  :func:`herding_threshold`), the split overshoots and oscillates ("herding").
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np


@dataclass(frozen=True)
class Road:
    """A road: free-flow time ``T`` (s) and a bottleneck of capacity ``C`` (veh/h)."""

    T: float
    C: float

    def time(self, x: float) -> float:
        """Travel time (s) at flow ``x`` (veh/h): driving time plus the M/M/1 wait at the bottleneck.

        Infinite at or above capacity (the queue grows without limit).
        """
        if x < 0:
            raise ValueError("negative flow")
        return self.T + 3600.0 * x / (self.C * (self.C - x)) if x < self.C else math.inf


# --------------------------------------------------------------------------- two routes


def equilibrium_flow(d: float, r1: Road, r2: Road) -> float:
    """Flow on route 1 at user equilibrium when every driver can reroute (Wardrop).

    If route 1 stays faster even with the whole demand on it, everyone uses it.
    Otherwise the flow splits so that both routes take the same time; the split
    is found by bisection (t1 increases and t2 decreases with the route-1 flow).
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


def system_optimum_flow(d: float, r1: Road, r2: Road) -> float:
    """Route-1 flow that minimises the *total* travel time (what a central planner would choose).

    Rerouting drivers each pick their own fastest route, which leads to the user
    equilibrium instead; the optimum puts less traffic on the busy route, because
    each extra car there also slows everyone behind it ("price of anarchy").
    Found by ternary search on the convex total time.
    """
    if d <= 0:
        return 0.0
    lo, hi = max(0.0, d - r2.C), min(d, r1.C)
    lo, hi = lo + 1e-9 * d, hi - 1e-9 * d
    total = lambda x: _flow_time(x, r1) + _flow_time(d - x, r2)  # noqa: E731
    for _ in range(300):
        m1, m2 = lo + (hi - lo) / 3, hi - (hi - lo) / 3
        if total(m1) <= total(m2):
            hi = m2
        else:
            lo = m1
    return (lo + hi) / 2


def mixed_flow(d: float, p: float, r1: Road, r2: Road) -> float:
    """Route-1 flow when a share ``p`` of drivers reroutes and the rest stay on route 1.

    The non-rerouters put ``(1 - p) d`` on route 1. The rerouters then fill route 2
    until the times are equal, which they can do only if they are numerous enough:
    the route-1 flow is ``max((1 - p) d, x_UE)``.
    """
    return max((1.0 - p) * d, equilibrium_flow(d, r1, r2))


def mean_times(d: float, p: float, r1: Road, r2: Road) -> dict:
    """Mean travel times (s) for everyone, for rerouters and for the others."""
    x1 = mixed_flow(d, p, r1, r2)
    t1 = r1.time(x1)
    rerouters_on_2 = d - x1
    rerouters_on_1 = max(0.0, p * d - rerouters_on_2)
    everyone = (_flow_time(x1, r1) + _flow_time(d - x1, r2)) / d if d > 0 else r1.T
    # a group with no one in it contributes nothing, even on a road over capacity
    rerouters = ((0.0 if rerouters_on_1 == 0 else rerouters_on_1 * t1) + _flow_time(rerouters_on_2, r2)) / (p * d) \
        if p > 0 and d > 0 else math.nan
    return {"everyone": everyone, "rerouters": rerouters, "others": t1 if p < 1 else math.nan,
            "route1_flow": x1}


def gain(d: float, p: float, r1: Road, r2: Road) -> float:
    """Relative saving in mean travel time compared with nobody rerouting (0 = no gain, 1 = all time saved)."""
    static = mean_times(d, 0.0, r1, r2)["everyone"]
    dynamic = mean_times(d, p, r1, r2)["everyone"]
    if math.isinf(static):
        return 1.0 if math.isfinite(dynamic) else 0.0
    return 1.0 - dynamic / static


def demand_threshold(r1: Road, r2: Road) -> float:
    """Below this demand rerouting cannot help: route 1 stays faster than an empty route 2.

    Solves ``t1(d) = T2``, i.e. the queue on route 1 costs as much as the detour
    ``dT = T2 - T1``: ``d* = C1 * a / (1 + a)`` with ``a = C1 dT / 3600`` (the number of
    cars the bottleneck serves while one extra detour is driven).
    """
    a = r1.C * (r2.T - r1.T) / 3600.0
    return r1.C * a / (1.0 + a)


def share_threshold(d: float, r1: Road, r2: Road) -> float:
    """Share of rerouters beyond which more rerouters change nothing: ``p* = 1 - x_UE / d``."""
    return 1.0 - equilibrium_flow(d, r1, r2) / d if d > 0 else 0.0


# --------------------------------------------------------------------------- Markov chains


def queue_distribution(load: float, n_max: int) -> np.ndarray:
    """Stationary distribution of the number of cars at a road's bottleneck, seen as a Markov chain.

    Cars arrive at random (rate ``lambda``) and leave one at a time (rate ``mu``):
    a birth-death chain whose balance equations ``pi(n+1) mu = pi(n) lambda`` give
    ``pi(n) = (1 - rho) rho^n`` with the load ``rho = lambda / mu`` (the M/M/1 queue).
    The mean number of cars is ``rho / (1 - rho)``, so by Little's law a car spends
    ``(1 / mu) / (1 - rho)`` there, of which ``rho / (mu - lambda)`` waiting: the
    queueing term of ``Road.time``. Delays explode as the load approaches 1.
    """
    if not 0 <= load < 1:
        raise ValueError("the chain is stable only for a load below 1")
    n = np.arange(n_max + 1)
    return (1 - load) * load ** n


def simulate_queue(arrival_rate: float, service_rate: float, horizon: float, seed: int = 0) -> float:
    """Simulates the birth-death chain event by event; returns the mean time a car spends in it."""
    rng = np.random.default_rng(seed)
    t, queue, arrivals, total_time = 0.0, [], 0, 0.0
    next_arrival = rng.exponential(1 / arrival_rate)
    next_departure = math.inf
    while t < horizon:
        if next_arrival <= next_departure:
            t = next_arrival
            queue.append(t)
            arrivals += 1
            if len(queue) == 1:
                next_departure = t + rng.exponential(1 / service_rate)
            next_arrival = t + rng.exponential(1 / arrival_rate)
        else:
            t = next_departure
            total_time += t - queue.pop(0)
            next_departure = t + rng.exponential(1 / service_rate) if queue else math.inf
    served = arrivals - len(queue)
    return total_time / served


def _flow_time(x: float, road: Road) -> float:
    """Total time ``x * t(x)``; an empty road contributes nothing even if it is over capacity."""
    return 0.0 if x <= 0 else x * road.time(x)


def logit_share(t1: float, t2: float, beta: float) -> float:
    """Probability of choosing route 1 when route times are perceived with sensitivity ``beta`` (1/s)."""
    if math.isinf(t1) or math.isinf(t2):
        return 0.0 if math.isinf(t1) and not math.isinf(t2) else (1.0 if math.isinf(t2) and not math.isinf(t1) else 0.5)
    z = max(-700.0, min(700.0, beta * (t2 - t1)))
    return 1.0 / (1.0 + math.exp(-z))


def lagged_dynamics(d: float, g: float, beta: float, r1: Road, r2: Road, fixed_flow: float = 0.0,
                    x0: float | None = None, steps: int = 300) -> np.ndarray:
    """Route-1 flow, update after update, when drivers react to *last update's* travel times.

    At each information update a fraction ``g`` of the flexible drivers (demand minus
    ``fixed_flow``) reconsiders, choosing route 1 with the logit probability of the
    times published at the previous update (sensitivity ``beta``):

        x_{k+1} = (1 - g) x_k + g [fixed_flow + flexible * logit(t2(d - x_k) - t1(x_k))]

    ``g`` is the share of drivers reacting to the same stale information: it is small
    when decisions are spread out in time and large when many decide at once.
    """
    flexible = d - fixed_flow
    x = np.empty(steps + 1)
    x[0] = d if x0 is None else x0
    for k in range(steps):
        target = fixed_flow + flexible * logit_share(r1.time(x[k]), r2.time(d - x[k]), beta)
        x[k + 1] = (1.0 - g) * x[k] + g * target
    return x


def herding_threshold(d: float, beta: float, r1: Road, r2: Road, fixed_flow: float = 0.0) -> float:
    """Largest share ``g`` of simultaneous reactions for which the split settles instead of oscillating.

    Linearising the update at its fixed point x* gives a slope ``1 - g (1 + K)`` with
    ``K = flexible * beta * s (1 - s) * (t1'(x*) + t2'(d - x*))`` (``s`` the logit share at x*),
    which is at least 0. The fixed point is stable iff ``|1 - g (1 + K)| < 1``, i.e.
    ``g < 2 / (1 + K)``. Many drivers reacting at once (large g), sharp reactions (large
    beta) and steep costs near capacity (large t') all push towards oscillations.
    """
    flexible = d - fixed_flow
    # fixed point of the logit response (bisection: the response decreases as x grows)
    f = lambda x: fixed_flow + flexible * logit_share(r1.time(x), r2.time(d - x), beta) - x  # noqa: E731
    lo, hi = max(fixed_flow, d - r2.C + 1e-6), min(d, r1.C - 1e-6)
    for _ in range(200):
        mid = (lo + hi) / 2
        lo, hi = (mid, hi) if f(mid) > 0 else (lo, mid)
    x = (lo + hi) / 2
    s = logit_share(r1.time(x), r2.time(d - x), beta)
    h = 1e-4 * d
    slope1 = (r1.time(x + h) - r1.time(x - h)) / (2 * h)
    slope2 = (r2.time(d - x + h) - r2.time(d - x - h)) / (2 * h)
    k = flexible * beta * s * (1 - s) * (slope1 + slope2)
    return 2.0 / (1.0 + k)


def cycle_mean_time(trajectory: np.ndarray, d: float, r1: Road, r2: Road, burn_in: int = 100) -> float:
    """Mean travel time averaged over the periods after ``burn_in`` of a lagged trajectory."""
    xs = trajectory[burn_in:]
    return float(np.mean([(_flow_time(x, r1) + _flow_time(d - x, r2)) / d for x in xs]))
