# Does Waze Make Traffic Worse?

Waze and other navigation apps send drivers onto another road when their usual route is jammed. Does
that make traffic better or worse, and when does it stop helping? This dataset answers with traffic simulations
([Eclipse SUMO](https://eclipse.dev/sumo/)) in which **the same cars drive the same morning with 0, 25, 50,
75 or 100 % of drivers following a live-rerouting app**: counterfactuals that real traffic data can never
provide. Project, code and animation: [github.com/tudor-opran/traffic-rerouting-sumo](https://github.com/tudor-opran/traffic-rerouting-sumo).

*Not affiliated with Waze or Google: "Waze" stands here for any live-rerouting navigation app. In the simulations the app is SUMO's rerouting device.*

## Start here: `beginner/two_roads.csv`

One row per simulation of the simplest case: a short road and a longer detour between two places. For each
traffic level (cars per hour) and share of drivers following a live-rerouting app, the average trip time of
all drivers, of app users and of the others, and the share of trips finished within two hours.

## Going further: `advanced/experiments/`

Every run of the controlled simulations: `two_road/` (a short road and a detour) and `grid/` (a 6 x 6 grid of
city streets). One line per run with the traffic level, the share of app users, the age of the information
they use, and the outcomes (trip times, trips finished, share on the detour). `*_minute_series.csv` gives,
minute by minute, the share of app users on the detour: the herding when too many follow the same advice.
Files ending `_blind_seeds_6_10` and `_long_entry` are extra runs on fresh seeds and on a two-road network
with a 2.4 km entry road.

Version 2 adds a full-scale simulation of La Rochelle's morning rush hour.

## Licence

ODbL 1.0. Simulated with Eclipse SUMO 1.27.1.
