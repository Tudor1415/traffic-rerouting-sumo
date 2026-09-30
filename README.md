# Does live rerouting beat traffic jams? A Markov chain and SUMO

Navigation apps send drivers onto another road when their usual route is jammed. This project asks
**how much this dynamic rerouting reduces travel time and congestion, and when it stops helping.**

It answers twice. First in theory: dynamic rerouting is modelled as **one simple Markov chain**, and the
answer is read from it. Then by experiment: the chain's numbers were written down *before* running the
traffic simulator [SUMO](https://eclipse.dev/sumo/), and then checked against it. Finally, the same
questions are asked on a 6 × 6 city grid and on the real streets of central La Rochelle.

## Why it matters

Most drivers now follow live navigation, so rerouting is no longer an individual trick: it decides how
traffic spreads over a city. It can clear a jam by using forgotten roads, or create new jams when
thousands of drivers are sent the same way at once. Road authorities (should they share live traffic
data, and how often?), navigation services (should everybody get the same advice?) and drivers all need
to know when it helps.

## The theory: rerouting as a Markov chain

Take a short road and a longer detour between the same two places. Each ends with a traffic light that
lets through at most `C` cars per hour (its *capacity*); driving it when empty takes `T` seconds. For our
roads, measured once in SUMO, the short road has `T₁ = 146 s, C₁ = 752` and the detour
`T₂ = 244 s, C₂ = 1,059`. Nothing else is measured or tuned.

The state of the traffic is three numbers: the queue at each light (`n₁`, `n₂`) and the **news** `s`,
which road the app says is faster. Every second (Figure 1a):

* a car arrives with probability `d / 3600` (`d` cars per hour). Without the app it takes the short road;
  with the app it takes road `s`;
* each light lets one car go with probability `C / 3600`;
* the news refreshes with probability `1 / τ`: `s` becomes the road with the lower `T + 3600·n / C`.
  Between refreshes drivers act on old news. Here `τ` is how old the news is: half the time over which the
  app averages travel times, plus the 72 s a car needs to reach the light.

A car that joins a road behind `n` cars takes `T + 3600·n / C`. The next second depends only on the
present state, so this is a Markov chain. Simulating it takes a second. Its steady state, written with
very short time steps, gives the answer in five equations. With one-second steps the chain waits slightly
less (by a factor `1 − C/3600`), which moves `d*` below from 717 to 725 cars per hour.

**1. A road is a drive plus a queue.** A single queue goes up by one when a car arrives and down by one
when the light lets one go (Figure 1b). Balancing the two gives the chance of `n` waiting cars,
`P(n) = (1 − ρ) ρⁿ` with the load `ρ = x / C`, so on average `ρ / (1 − ρ)` cars wait and

> **t(x) = T + 3600·x / (C·(C − x))**

This time stays nearly flat, then explodes as the traffic `x` approaches the capacity `C`. Moving a few cars
off a nearly full road saves a lot; adding them to a quiet road costs almost nothing.

**2. Rerouting helps only above a traffic level d\*.** Leaving the short road is worth it only once its
queue costs more than the extra drive of the detour, `t₁(d) > T₂`:

> **d\* = C₁·a / (1 + a)** with **a = C₁·(T₂ − T₁) / 3600**  →  **717 cars per hour** here.

**3. Only a share p\* of drivers needs the app.** With fresh news, rerouters fill the detour until both
roads take the same time (the *Wardrop equilibrium*: `x` cars stay on the short road). Drivers without the
app all stay on the short road, so once `(1 − p)·d ≤ x` the extra rerouters have nothing left to balance:

> **p\* = 1 − x / d**  →  **40 %** at 1,200 cars per hour, **52 %** at 1,500, **58 %** at 1,800.

**4. Above p\*, the crowd swings.** With old news every rerouter follows the same `s` at once, so at any
moment they are all on the detour or all on the short road. If they spend a fraction `q` of the time on the
detour, roughly the share needed there (`q ≈ p*/p`), the share of rerouters on the detour jumps between 1
and 0 and spreads by

> **swing = √(q·(1 − q))**, at most **0.5**.

Below `p*` there is no swing: the detour is always the better choice, so `q = 1`.

**5. Capacity is the hard limit.** Beyond `C₁ + C₂ = 1,811` cars per hour the chain has no steady state:
the queues grow whatever the routing, and only the capacity gets through. In a one-hour rush, rerouting
still helps there by using the detour's capacity too, but it cannot stop the queues from growing.

So the theory's answer is: rerouting saves almost nothing below `d*` and a great deal above it. It needs
only a share `p*` of drivers, turns into herding beyond it, and cannot beat the total capacity.

## The test

Traffic enters for one hour and every run stops after two. A trip still running at that cut-off counts as
unfinished, with the time spent so far. The chain's numbers for 216 situations, with a pass rule for
each, were committed to the repository (`results/predictions.json`) **before** the new SUMO runs were
made. Each situation was run 5 times with different random traffic. A statement *holds* if at least
80 % of its situations pass. Situations marked † in `results/conjectures.md` reuse runs made before the
predictions. They count only for statements 7 and 8, which have no new runs, so those two are checks
rather than blind tests.

| | prediction of the chain | result in SUMO | verdict |
|---|---|---|---|
| 1 | trip time on one road (`t(x)`) | right on the short road; the detour is up to 0.6 min slower | fails (3/5) |
| 2 | gain appears around d\* = 717 | +7 s predicted vs +5 s measured at 700; +35 vs +31 at 750; +97 vs +100 at 800 | holds (7/7) |
| 3 | drivers who know the usual traffic split as in equilibrium | 15 / 36 / 49 % on the detour predicted, 19 / 39 / 50 % measured | holds (5/6) |
| 4 | rerouters fill the detour only up to what is needed | 26 of 30 shares within 5 points | holds (26/30) |
| 5 | beyond p\*, rerouters swing together | swing 0.46 predicted and measured at 100 %; near zero below p\* | holds (56/60) |
| 6 | trip time against the share of rerouters | 1,500 cars/h: every share within 0.4 min | holds (10/10) |
| 7 | older news costs more when everybody reroutes | right direction, but SUMO's cost is 2.7 × larger | fails (8/14) |
| 8 | trip time and trips finished against traffic | 49 of 54 (misses: everybody rerouting from 1,650, half at 2,400) | holds (49/54) |

Six of the eight predictions hold; 188 of the 216 situations pass. The two failures come from two
things the chain leaves out, discussed below.

![Figure 1](figures/fig1_markov_chain.png)

## What we learned

**How much rerouting saves, and when it starts** (Figure 2). Below about 700 cars per hour, every driver
takes 2.5 to 3 minutes whatever the news: the chain predicts a gain under 10 seconds and SUMO measures
the same. The gain appears at `d*` and then grows very fast. At 1,200 cars per hour, drivers without
live information take 20 minutes (chain 20.3, SUMO 20.1) and rerouting drivers 4.6 minutes (both).
Without information, trips start to miss the two-hour cut-off from 1,500 cars per hour: at 2,400,
61 % finish, exactly the share the short road's capacity allows.

![Figure 2](figures/fig2_how_much.png)

**How many drivers need it** (Figure 3). The rerouters take the detour until it is no longer faster, and
then stop: below `p*`, 94 to 100 % of them are on the detour; above it, only as many as needed (panel b). The
drivers *without* the app gain too, because the short road clears. At 1,500 cars per hour the average
trip falls from 25 minutes (10 % rerouting) to 5.2 minutes (60 %); going further to 100 % makes it slightly
worse (6.2 minutes). The chain matches every share within 0.4 minutes.

**When too many react to the same news, the crowd swings** (Figures 3c–d and 4a). Beyond `p*`, the
rerouters move as one block: all on the detour, then all back, every few minutes. The swing appears
close to where the chain places it (above 40 % of rerouters at 1,200 cars per hour, 58 % at 1,800), and
its size matches (0.46 predicted and measured when everybody reroutes). One miss: at 1,500 cars per hour
with half of the drivers rerouting, the chain expects a small swing (0.10) and SUMO shows none. The swings cost time: at 1,800
cars per hour the best result is reached with 60 % of drivers rerouting (7.1 minutes); with everybody
rerouting, trips take 12.4 minutes.

![Figure 3](figures/fig3_how_many.png)

**Old news makes it worse, and where the chain falls short** (Figure 4). The older the travel times the
app uses, the longer the swings last and the more they cost. With half the drivers rerouting, the chain
predicts this closely (10-minute instead of 10-second averages add 165 s in the chain, 144 s in
SUMO). With every driver rerouting, SUMO is much worse than the chain (12.4 against
8.1 minutes with 3-minute averages). The reason was checked afterwards: when the whole crowd piles onto
the short road, its queue grows longer than the road and blocks the shared entrance. In one run cars then
waited 6 minutes on average just to enter (against 3 seconds with 60 % of drivers rerouting). The chain has no
such *spillback*. The detour's small miss (failure 1) has another cause: cars on it slow down in traffic
without stopping (time stopped stays at 3–4 seconds), which a queue at the light alone cannot capture.

![Figure 4](figures/fig4_old_news.png)

**On a city grid and on real streets** (Figure 5, 3 runs each; no chain was built for these networks).
The same pattern appears.
* **Light traffic:** rerouting costs a little (6 × 6 grid, 9,000 cars per hour: 3.6 minutes without,
  4.4 with every driver rerouting).
* **Near capacity:** it saves the network. At 11,000 cars per hour, trips take 30 minutes and 64 % finish
  without rerouting, against 6 minutes and 100 % with half of the drivers. Everybody rerouting is worse
  again (10 minutes).
* **Beyond capacity:** it cannot prevent the collapse (14,000 cars per hour: at most 40 % of trips finish).
* **Old news:** at 12,000 cars per hour with everybody rerouting (2 runs), trips take 15 minutes when
  drivers re-check every 30 seconds with fresh travel times, and 37 to 41 minutes with 10-minute averages.
* **La Rochelle:** at 2,000 cars per hour, rerouting cuts trips from 23 to 11 minutes and brings the
  finished share from 79 % to 100 %. At 3,000 the centre is gridlocked (22 % to 43 % of trips finish).

These grid runs vary a lot between random seeds.

![Figure 5](figures/fig5_networks.png)

## The answer

**How much does dynamic rerouting help?** Nothing in light traffic, and enormously near capacity: on two
roads it cuts trips from 20 to under 5 minutes at 1,200 cars per hour. On a grid near capacity, it takes
trips from 30 to 6 minutes, and on real streets it lets every trip finish instead of 79 %. The Markov
chain explains why: queue waiting grows like `x / (C − x)`, so spreading cars over both roads pays most
exactly when one road is nearly full.

**When does it stop helping?**
1. **Below `d*`** (about 720 cars per hour here), the detour is not faster on average: only random bursts
   of queue give a few seconds of gain.
2. **Beyond a share `p*`** of rerouting drivers (40 to 58 % here), more rerouters bring nothing. The
   drivers without the app already benefit.
3. **Beyond `p*` with old news**, rerouters swing together between the roads, which costs time. Once the
   swinging queue grows longer than its road and blocks the entrance, this cost becomes large
   (12.4 instead of 7.1 minutes).
4. **Above the total capacity**, no routing can serve more cars than the roads let through: rerouting
   still spreads the queue over both roads (54 to 25 minutes at 2,400 cars per hour in our one-hour
   rush), but the queues keep growing while the rush lasts.

## Limits

The traffic is random, not measured, and La Rochelle is used for its street layout only. SUMO's app
follows one rule (the fastest route on recently observed speeds); real apps also predict traffic, which
should reduce herding. The chain is built for the two-road network only: it leaves out slowdowns while
driving and queues that spill back onto other roads. Those are exactly its two failures. The grid and
La Rochelle use 3 random runs (2 for the old-news test), and some grid results depend on a single run.

## Code

* **Theory:** `rerouting/markov.py` (the chain) and `rerouting/theory.py` (its steady state).
* **Test:** `rerouting/conjectures.py` holds the predictions and pass rules. Run
  `python -m rerouting.conjectures` to score them; the full table is in `results/conjectures.md`.
* **Experiments:** `rerouting/experiments.py`, with SUMO driven by `rerouting/sumo.py`. The raw results
  are in `results/`.
* **Figures and tests:** `rerouting/figures.py` draws the figures; the tests are in `tests/`.

*Map data © OpenStreetMap contributors (ODbL).*
