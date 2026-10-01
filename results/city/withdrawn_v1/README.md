# Withdrawn predictions (first freeze)

Frozen 2026-10-01 in commit 647ed61, withdrawn before any SUMO test result was read: SUMO refused the
first test run because some empty-city routes used turns reserved for buses and bikes (the street graph
took every connection of the network, whatever the lane). The routes, and so every prediction made on
them, were invalid. The one SUMO run that finished before the refusal was deleted unread. The street
graph now keeps only turns between car lanes, every route is checked against SUMO's network before
freezing (`python -m rerouting.city validate`), and the predictions were made again.
