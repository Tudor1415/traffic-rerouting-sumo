# La Rochelle: the city chain and its theory against blind SUMO runs

Seeds [4, 5, 6], predictions frozen 2026-10-01 01:30:08. a statement holds if at least 80% of its cases pass.

| statement | cases passed | verdict |
|---|---|---|
| K1: chain: trip time against traffic, nobody and everybody on the app | 4/16 | fails |
| K2: chain: the app's gain against traffic | 1/8 | fails |
| K3: chain: trip time against the share of app users | 3/22 | fails |
| K4: theory: the app's gain against traffic | 1/8 | fails |
| K5: theory: d*, the traffic at which the app starts to help | 1/1 | holds |
| K6: theory: p*, the share of app users after which more of them add little | 0/2 | fails |

| statement | case | predicted | SUMO | tolerance | pass |
|---|---|---|---|---|---|
| K1 | traffic x0.25, 0% on the app | 623 | 613 | 15 | yes |
| K1 | traffic x0.25, 100% on the app | 622 | 670 | 15 | no |
| K2 | traffic x0.25 | 0.52 | -56.5 | 15 | no |
| K4 | traffic x0.25 | 0.182 | -56.5 | 15 | no |
| K1 | traffic x0.5, 0% on the app | 630 | 627 | 15 | yes |
| K1 | traffic x0.5, 100% on the app | 626 | 667 | 15 | no |
| K2 | traffic x0.5 | 3.68 | -40.2 | 15 | no |
| K4 | traffic x0.5 | 6.11 | -40.2 | 15 | no |
| K1 | traffic x0.75, 0% on the app | 686 | 728 | 18.9 | no |
| K1 | traffic x0.75, 100% on the app | 637 | 676 | 15 | no |
| K2 | traffic x0.75 | 49.8 | 52 | 15 | yes |
| K4 | traffic x0.75 | 69.8 | 52 | 20.9 | yes |
| K1 | traffic x1, 0% on the app | 965 | 1.11e+03 | 88.4 | no |
| K1 | traffic x1, 100% on the app | 651 | 692 | 15 | no |
| K2 | traffic x1 | 315 | 417 | 94.5 | no |
| K4 | traffic x1 | 192 | 417 | 57.7 | no |
| K1 | traffic x1.25, 0% on the app | 1.23e+03 | 1.75e+03 | 155 | no |
| K1 | traffic x1.25, 100% on the app | 674 | 718 | 15.7 | no |
| K2 | traffic x1.25 | 557 | 1.03e+03 | 167 | no |
| K4 | traffic x1.25 | 287 | 1.03e+03 | 86.2 | no |
| K1 | traffic x1.5, 0% on the app | 1.51e+03 | 2.84e+03 | 225 | no |
| K1 | traffic x1.5, 100% on the app | 720 | 771 | 27.4 | no |
| K2 | traffic x1.5 | 793 | 2.07e+03 | 238 | no |
| K4 | traffic x1.5 | 453 | 2.07e+03 | 136 | no |
| K1 | traffic x1.75, 0% on the app | 1.83e+03 | 3.57e+03 | 305 | no |
| K1 | traffic x1.75, 100% on the app | 827 | 842 | 53.8 | yes |
| K2 | traffic x1.75 | 1e+03 | 2.72e+03 | 301 | no |
| K4 | traffic x1.75 | 587 | 2.72e+03 | 176 | no |
| K1 | traffic x2, 0% on the app | 2.1e+03 | 5.27e+03 | 373 | no |
| K1 | traffic x2, 100% on the app | 1.02e+03 | 964 | 103 | yes |
| K2 | traffic x2 | 1.08e+03 | 4.3e+03 | 325 | no |
| K4 | traffic x2 | 774 | 4.3e+03 | 232 | no |
| K3 | traffic x1, 0% on the app | 965 | 1.11e+03 | 88.4 | no |
| K3 | traffic x1, 10% on the app | 837 | 1.09e+03 | 56.2 | no |
| K3 | traffic x1, 20% on the app | 743 | 999 | 32.7 | no |
| K3 | traffic x1, 30% on the app | 673 | 919 | 15.2 | no |
| K3 | traffic x1, 40% on the app | 659 | 854 | 15 | no |
| K3 | traffic x1, 50% on the app | 653 | 770 | 15 | no |
| K3 | traffic x1, 60% on the app | 652 | 745 | 15 | no |
| K3 | traffic x1, 70% on the app | 652 | 705 | 15 | no |
| K3 | traffic x1, 80% on the app | 651 | 691 | 15 | no |
| K3 | traffic x1, 90% on the app | 651 | 691 | 15 | no |
| K3 | traffic x1, 100% on the app | 651 | 692 | 15 | no |
| K6 | p* at traffic x1 | 0.5 | 0.7 | 0.1 | no |
| K3 | traffic x2, 0% on the app | 2.1e+03 | 5.27e+03 | 373 | no |
| K3 | traffic x2, 10% on the app | 1.9e+03 | 5.04e+03 | 322 | no |
| K3 | traffic x2, 20% on the app | 1.76e+03 | 3.94e+03 | 287 | no |
| K3 | traffic x2, 30% on the app | 1.65e+03 | 3.41e+03 | 259 | no |
| K3 | traffic x2, 40% on the app | 1.5e+03 | 2.75e+03 | 222 | no |
| K3 | traffic x2, 50% on the app | 1.32e+03 | 2.41e+03 | 178 | no |
| K3 | traffic x2, 60% on the app | 1.26e+03 | 1.78e+03 | 163 | no |
| K3 | traffic x2, 70% on the app | 1.24e+03 | 1.38e+03 | 158 | yes |
| K3 | traffic x2, 80% on the app | 1.14e+03 | 1.12e+03 | 133 | yes |
| K3 | traffic x2, 90% on the app | 1.18e+03 | 1.01e+03 | 142 | no |
| K3 | traffic x2, 100% on the app | 1.02e+03 | 964 | 103 | yes |
| K6 | p* at traffic x2 | 0.7 | 1 | 0.1 | no |
| K5 | d* (x the calibrated morning traffic) | 0.535 | 0.65 | 0.134 | yes |
