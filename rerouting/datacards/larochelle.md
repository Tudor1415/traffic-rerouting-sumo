# La Rochelle morning rush: traffic simulation

A calibrated, city-scale traffic simulation of the morning rush hour in La Rochelle (France), built
only from open data, with every car's trip and the traffic on every street every 5 minutes. It
compares a normal morning with a morning where an accident nearly blocks a lane of the ring road (7:45-8:30), when 0,
25, 50, 75 or 100 % of drivers follow a navigation app that reroutes them live.

It comes from the project [traffic-rerouting-sumo](https://github.com/tudor-opran/traffic-rerouting-sumo),
which asks how much dynamic rerouting reduces travel time and congestion, and when it stops helping.

## Contents

| folder | file | what it holds |
|---|---|---|
| `network/` | `streets.csv` | every street of the simulated network: id, name, road type, lanes, speed limit (km/h), length (m), end junctions, geometry (WKT, WGS84) |
| | `traffic_lights.csv` | signalised junctions and the number of movements they control |
| | `larochelle.net.xml.gz` | the SUMO network itself |
| `inputs/` | `population_cells.csv` | residents on a 200 m grid (INSEE Filosofi 2021): people, adults aged 18-64, commune |
| | `workplaces.csv` | workplaces with employees (SIRENE), estimated jobs (middle of the size band, scaled to INSEE's 50,497 jobs in La Rochelle), commune |
| | `commute_flows.csv` | workers commuting from each home commune to each work commune (INSEE 2022), for flows touching the map |
| | `communes.csv` | communes involved: centre, population, share of residents on the map, share driving to work |
| | `traffic_counts.csv` | average daily traffic (both directions) on counted road sections, 2023 |
| | `entry_roads.csv` | roads through which traffic enters or leaves the map, with their daily traffic |
| `calibration/` | `calibration_grid.csv` | how well each tested traffic volume matches the counts (fit roads / held-out check roads) |
| | `count_comparison.csv` | for every counted section: 9 % of its daily traffic against the simulated 7:30-8:30 flow, GEH statistic |
| `demand/` | `trips_seed{1,2,3}.parquet` | every morning car trip: departure time, origin and destination streets, kind (inside / in / out / cross / through), home and work communes, usual route |
| `scenarios/` | `runs.csv` | one line per simulation: day, share of app users, seed, mean trip time, time lost, distance, trips finished, teleports |
| | `trips/*.parquet` | one row per car and simulation: app user or not, requested and actual departure, arrival, trip time, route length, time lost, waiting time, reroutes, planned and driven routes |
| | `streets/*.parquet` | per street and 5-minute interval (2 minutes for the animated runs): cars entering and leaving, mean speed (m/s), density (veh/km), occupancy (%), travel time, waiting time, time lost |
| | `incident.csv` | where and when the accident happens |
| `animation/` | `larochelle.gif` | the accident morning seen from above, without and with the app |

Times are seconds after midnight (`_s`); `departure_clock` gives the same as hh:mm:ss.
File names encode the scenario: `incident_app50_seed1` = accident morning, 50 % app users, seed 1.

## How it was built

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

## Limits

This is a model, not a measurement of real traffic. Hourly counts are estimated from daily ones;
signal timings, parking and pedestrians are not modelled; the app is idealised; about 3 % of the
generated trips join streets that cannot reach each other and are left out.

## Sources and licences

* Street network © OpenStreetMap contributors, [ODbL 1.0](https://opendatacommons.org/licenses/odbl/1-0/).
  The network-derived files are shared under the same licence.
* INSEE: Filosofi 2021 200 m grid, 2022 commuting flows, 2023 census (Licence Ouverte / Etalab 2.0).
* SIRENE register of workplaces and its geolocation (INSEE, Licence Ouverte / Etalab 2.0).
* Commune outlines and centres: geo.api.gouv.fr (Licence Ouverte / Etalab 2.0).
* Road counts: TMJA 2023, DREAL Nouvelle-Aquitaine (published on SIGENA / data.gouv.fr, no access restriction).

This dataset is released under ODbL 1.0 (it contains a database derived from OpenStreetMap).
