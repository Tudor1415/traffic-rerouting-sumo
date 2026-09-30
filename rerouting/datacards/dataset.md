# Does Live Rerouting Beat Traffic Jams?

Navigation apps send drivers onto another road when their usual route is jammed. Does that make traffic
better or worse, and when does it stop helping? This dataset answers with traffic simulations
([Eclipse SUMO](https://eclipse.dev/sumo/)) in which **the same cars drive the same morning with 0, 25, 50,
75 or 100 % of drivers following a live-rerouting app**: counterfactuals that real traffic data can never
provide. Project, code and animation: [github.com/tudor-opran/traffic-rerouting-sumo](https://github.com/tudor-opran/traffic-rerouting-sumo).

## Start here: `beginner/`

Small tables in plain units (minutes, km/h, hh:mm clock times, percent), ready for a spreadsheet or a first
pandas notebook. They summarise the simulations of **La Rochelle (France) on a weekday morning**, 6:30 to
11:00: 10 scenarios (a normal morning and a morning with an accident on the ring road, times 0/25/50/75/100 %
of drivers on the app), each simulated with 3 random seeds (slightly different traffic).

| file | one row per | what you can ask |
|---|---|---|
| `scenarios.csv` | simulated morning (30) | Does the app shorten trips? Does the accident? Do drivers without the app gain too? |
| `traffic_by_time.csv` | morning and 5 minutes | When is the rush hour? How many cars are on the road, how fast do they go? |
| `traffic_by_area.csv` | morning, commune and 15 minutes | Where are the jams? Which commune gains most from the app? |
| `trips_between_areas.csv` | morning, home commune and work commune | Who drives where, and how long does it take? |
| `two_roads.csv` | simulation run | The simplest case: two roads between two places. When does rerouting start to help, and how many drivers need the app? |

In the La Rochelle tables, trip times follow the drivers who leave between 7:00 and 9:00, from the moment they
want to leave; in `two_roads.csv`, all drivers of the one hour of traffic, within a two-hour limit.

## Going further: `advanced/`

Everything in full detail (CSV and Parquet), for modelling, forecasting and mapping.

| folder | what it holds |
|---|---|
| `advanced/la_rochelle/scenarios/` | `runs.csv` (one line per morning, in seconds); `trips/` one row per car and morning (about 19,000 cars from 6:30 to 9:30, 16,500 of them leaving 7:00-9:00, with their planned and driven routes); `streets/` the traffic on every used street every 5 minutes (cars in and out, speed, density, occupancy, travel time, time lost); `incident.csv` |
| `advanced/la_rochelle/demand/` | every morning trip of each seed: departure, origin and destination streets, trip kind, home and work communes, usual route |
| `advanced/la_rochelle/network/` | every street (name, type, lanes, speed limit, length, WKT geometry) and traffic light |
| `advanced/la_rochelle/inputs/` | the open data the morning is built from: 200 m population grid, workplaces, commuter flows between communes, communes, 2023 road counts, entry roads |
| `advanced/la_rochelle/calibration/` | how well each tested traffic volume matches the road counts, and the comparison road by road |
| `advanced/experiments/` | the controlled two-road and grid simulations, one line per run (`_blind_seeds_6_10`, `_long_entry` files: extra runs on fresh seeds and on a two-road network with a 2.4 km entry road); `*_minute_series.csv` gives minute by minute the share of app users on the detour |

Tables share ids: `street_id` / `edge_id` link streets, trips and street traffic; `seed` + `trip_id` link the
demand and the trips of every scenario of a seed. Clock times in La Rochelle are seconds after midnight
(`departure_s`, `arrival_s`, `begin_s`...); durations (`trip_time_s`, `time_lost_s`...) are elapsed seconds; in
the experiments, `minute_start_s` counts from the start of the simulation.

### How the La Rochelle morning was built

* **Streets**: OpenStreetMap, converted with SUMO's netconvert (lanes, turn lanes, speed limits,
  roundabouts and signals as mapped; signal timings are SUMO's defaults).
* **Who travels**: each INSEE commuting flow between two communes becomes car trips, using the
  INSEE share of workers who drive (52.4 % in La Rochelle, 80.7 % elsewhere in the agglomeration,
  85 % assumed further away). An end on the map is a street near a home (population grid) or a
  workplace of that commune; an end off the map is an entry road, chosen by its measured traffic and
  its distance to the commune. Departures spread from 6:30 to 9:30, peaking around 8:00.
* **Volume**: two numbers are fitted to the 2023 counts - the share of daily car commuters on the
  road this morning (it also stands for school runs, errands and deliveries) and the through
  traffic. The target is 9 % of the daily traffic between 7:30 and 8:30 (a common rule of thumb,
  not a measured hourly count). The volume is chosen on half of the counted roads and checked on the
  other half (whole road numbers held out).
* **Drivers' usual routes**: the equilibrium of 20 repeated mornings (SUMO duaIterate with the
  queue-based mesoscopic simulation): what commuters who know the usual traffic drive.
* **Scenarios**: detailed (microscopic) SUMO simulation, 6:30 to 11:00. Drivers without the app
  follow their usual route; app users are rerouted every minute on travel times averaged over the
  last 3 minutes, with perfect knowledge of the whole network. A car stuck for 5 minutes is moved on
  by SUMO (a "teleport"; counts in `runs.csv`). Summaries follow the drivers leaving 7:00-9:00; trips
  unfinished at 11:00 count with their time so far.

### Limits

This is a model, not a measurement of real traffic. Hourly counts are estimated from daily ones;
signal timings, parking and pedestrians are not modelled; the app is idealised; about 3 % of the
generated trips join streets that cannot reach each other and are left out.

## Licence and sources

ODbL 1.0 (the La Rochelle part contains a database derived from OpenStreetMap).

* Street network © OpenStreetMap contributors (ODbL).
* INSEE: Filosofi 2021 200 m population grid, 2022 commuting flows, 2023 census (Licence Ouverte / Etalab 2.0).
* SIRENE register of workplaces and its geolocation (INSEE, Licence Ouverte / Etalab 2.0).
* Commune outlines and centres: geo.api.gouv.fr (Licence Ouverte / Etalab 2.0).
* Road counts: TMJA 2023, DREAL Nouvelle-Aquitaine (SIGENA / data.gouv.fr, no access restriction).
* Simulation: Eclipse SUMO 1.27.1.
