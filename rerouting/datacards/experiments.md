# Dynamic rerouting experiments (SUMO)

Controlled traffic simulations that ask how much live rerouting (drivers switching routes on live
traffic information) reduces travel time and congestion, and when it stops helping. They come from
the project [traffic-rerouting-sumo](https://github.com/tudor-opran/traffic-rerouting-sumo), which also
tests a Markov-chain theory against them.

## Contents

| folder | what it holds |
|---|---|
| `two_road/` | a short road and a longer detour, each with a traffic light: calibration runs (everybody forced on one road), demand sweeps, share of app users, age of the information, drivers who know the usual traffic, and the pre-registered test runs |
| | files ending `_blind_seeds_6_10` are the blind test runs; `_long_entry` and `_long_entry_blind_seeds_11_15` the same network with a 2.4 km entry road |
| `grid/` | a 6 x 6 grid of city streets with traffic lights: demand, share of app users, information age |
| `theory/` | three theory tests, each with the predictions saved before its runs (`predictions.json`) and how each fared (`conjectures.md`): the first chain (seeds 1-5), the locked chain blind on seeds 6-10, and the locked chain blind on a 2.4 km entry road (seeds 11-15) |

Every CSV has one line per simulation run (same traffic for every policy of a seed):
`demand_veh_per_h` (traffic entering during one hour), `seed`, the policy (`policy`: `no_information`,
`experienced`, `live`, `forced`; `app_share`; `averaging_window_s`; `recheck_period_s`;
`synchronized_rechecks`), then the outcomes: `mean_trip_time_s` (from the requested departure,
including any wait to enter), `share_finished` (within 2 hours), `mean_time_lost_s`,
`mean_waiting_time_s`, `mean_reroutes_per_app_user`, the trip times of app users and of the others,
`exit_rate_veh_per_h`, and on two roads the share of cars on the detour. `*_minute_series.csv` files
give, minute by minute, the share of app users on the detour (the herding series).

Trips still running at the 2-hour cut-off count with their time so far, so averages with unfinished
trips are lower bounds.

## Licence

CC BY 4.0. Simulated with [Eclipse SUMO](https://eclipse.dev/sumo/) 1.27.1.
