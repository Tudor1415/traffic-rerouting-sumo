"""The README figures, from results/*.jsonl.

    python -m rerouting.figures

Colours follow the kind of driver: no live information grey, experienced drivers
violet, live rerouting orange (half of the drivers) and red (everyone).
"""

from __future__ import annotations

import json
import math
from collections import defaultdict
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import matplotlib.ticker as mticker  # noqa: E402
import numpy as np  # noqa: E402

from rerouting import theory as T  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
RESULTS, FIGURES = ROOT / "results", ROOT / "figures"

INK, INK2, MUTED, GRID = "#1f1f1e", "#52514e", "#a3a29c", "#e6e5e0"
COLOR = {"no_information": "#7a7974", "experienced": "#4a3aa7", "live_0.5": "#eb6834", "live_1.0": "#c0392b",
         "rerouters": "#eb6834", "others": "#2a78d6", "everyone": INK}
LABEL = {"no_information": "No live information", "experienced": "Experienced drivers (know the usual jams)",
         "live_0.5": "Live rerouting, half of the drivers", "live_1.0": "Live rerouting, every driver"}

plt.rcParams.update({
    "figure.facecolor": "white", "axes.facecolor": "white", "savefig.facecolor": "white",
    "font.family": "DejaVu Sans", "font.size": 11.5, "axes.titlesize": 12.5, "axes.titleweight": "bold",
    "axes.titlelocation": "left", "axes.titlepad": 8, "axes.labelsize": 11.5, "axes.labelcolor": INK2,
    "axes.edgecolor": MUTED, "axes.spines.top": False, "axes.spines.right": False,
    "axes.grid": True, "grid.color": GRID, "grid.linewidth": 0.8, "axes.axisbelow": True,
    "xtick.color": INK2, "ytick.color": INK2, "legend.fontsize": 10, "legend.frameon": False,
    "lines.linewidth": 2.4, "text.color": INK,
})


def load(name: str) -> list[dict]:
    path = RESULTS / f"{name}.jsonl"
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()] if path.exists() else []


def policy(r: dict) -> str:
    return f"live_{r['share']:.1f}" if r["policy"] == "live" else r["policy"]


def by(rows, *keys):
    """Groups rows by the given keys; returns {key tuple: [rows]}."""
    out = defaultdict(list)
    for r in rows:
        out[tuple(r[k] if k != "policy" else policy(r) for k in keys)].append(r)
    return out


def band(ax, x, groups, metric, color, label, scale=1.0, ls="-"):
    """Mean over seeds (line) and min-max over seeds (shaded band)."""
    xs, mean, lo, hi = [], [], [], []
    for xv in x:
        vals = [g[metric] * scale for g in groups.get(xv, []) if not math.isnan(g[metric])]
        if vals:
            xs.append(xv)
            mean.append(np.mean(vals))
            lo.append(min(vals))
            hi.append(max(vals))
    ax.plot(xs, mean, color=color, label=label, ls=ls, marker="o", ms=4)
    ax.fill_between(xs, lo, hi, color=color, alpha=0.15, lw=0)


def plain_log(axis):
    """Log axis with plain numbers (2, 5, 10, 20 ...) instead of powers of ten."""
    axis.set_major_locator(mticker.LogLocator(base=10, subs=(1, 2, 5)))
    axis.set_major_formatter(mticker.FuncFormatter(lambda v, _: f"{v:g}"))
    axis.set_minor_formatter(mticker.NullFormatter())


def save(fig, name):
    FIGURES.mkdir(exist_ok=True)
    fig.savefig(FIGURES / f"{name}.png", dpi=140, bbox_inches="tight")
    plt.close(fig)
    print("wrote", FIGURES / f"{name}.png")


# --------------------------------------------------------------------------- calibration of the two roads


def calibrate_two_road() -> dict:
    """Free-flow time (s) and capacity (veh/h) of each route, measured with everyone forced on it."""
    rows = load("two_road_calibration")
    out = {}
    for route in ("short", "long"):
        rs = [r for r in rows if r["route"] == route]
        light = [r["journey"] for r in rs if r["demand"] == min(x["demand"] for x in rs)]
        # capacity: the highest sustained exit rate (reached once demand exceeds it)
        out[route] = {"T": float(np.mean(light)), "C": float(max(np.mean([r["throughput"] for r in rs if r["demand"] == d])
                                                                   for d in {r["demand"] for r in rs}))}
    r1 = T.Road(out["short"]["T"], out["short"]["C"])
    r2 = T.Road(out["long"]["T"], out["long"]["C"])
    out["d_star"] = T.demand_threshold(r1, r2)
    out["roads"] = (r1, r2)
    return out




# --------------------------------------------------------------------------- figure 1: when does rerouting help?


def fig1_demand():
    rows = load("two_road_demand")
    cal = calibrate_two_road()
    r1, r2 = cal["roads"]
    helps_from, both_full = T.demand_threshold(r1, r2), r1.C + r2.C
    demands = sorted({r["demand"] for r in rows})
    groups = by(rows, "policy", "demand")
    fig, (ax1, ax2, ax3) = plt.subplots(1, 3, figsize=(14, 3.6), gridspec_kw={"width_ratios": [1.5, 1, 1]})
    for pol in ("no_information", "experienced", "live_0.5", "live_1.0"):
        g = {d: groups.get((pol, d), []) for d in demands}
        band(ax1, demands, g, "journey", COLOR[pol], LABEL[pol], scale=1 / 60)
        band(ax2, demands, g, "completed", COLOR[pol], LABEL[pol], scale=100)
    for x, text in ((helps_from, "theory: rerouting starts to help"), (both_full, "both roads full")):
        ax1.axvline(x, color=MUTED, lw=1, ls=(0, (4, 3)))
        ax1.text(x, 1.0, " " + text, transform=ax1.get_xaxis_transform(), fontsize=9.5, color=INK2, va="top")
    for ax in (ax1, ax2):
        ax.set_xlabel("traffic (cars per hour)")
    ax1.set_yscale("log")
    plain_log(ax1.yaxis)
    ax1.set_ylabel("average trip time (min)")
    ax1.set_title("a   Two roads: trip time as traffic grows", pad=18)
    handles, labels = ax1.get_legend_handles_labels()
    fig.legend(handles, labels, loc="lower center", ncol=4, fontsize=10, bbox_to_anchor=(0.5, -0.08))
    ax2.set_ylabel("trips finished (%)")
    ax2.set_title("b   Finished by the 2 h cut-off", pad=18)
    # (c) why jams explode: a road as a Markov chain (M/M/1 queue) against the measured routes
    rho = np.linspace(0, 0.985, 300)
    for route, marker, ls in (("short", "o", "-"), ("long", "s", (0, (4, 2)))):
        c = cal[route]
        road = T.Road(c["T"], c["C"])
        ax3.plot(rho, [road.time(x * c["C"]) / 60 for x in rho], color=INK, lw=1.8, ls=ls,
                 label=f"Markov-chain model, {route} road")
        rs = [r for r in load("two_road_calibration") if r["route"] == route]
        xs = [x for x in sorted({r["demand"] for r in rs}) if x / c["C"] < 1]
        ys = [np.mean([r["journey"] for r in rs if r["demand"] == x]) / 60 for x in xs]
        ax3.plot(np.array(xs) / c["C"], ys, marker, color=COLOR["no_information"], ms=6,
                 mfc="white" if route == "long" else COLOR["no_information"], label=f"simulated {route} road", ls="none")
    ax3.set_xlim(0, 1.0)
    ax3.set_ylim(0, 12)
    ax3.set_xlabel("load (traffic ÷ capacity)")
    ax3.set_ylabel("trip time (min)")
    ax3.set_title("c   Why jams explode near capacity", pad=18)
    ax3.legend(loc="upper left", fontsize=8.5)
    fig.tight_layout(w_pad=2.5)
    save(fig, "fig1_when_it_helps")



# --------------------------------------------------------------------------- figure 2: how many drivers need it?


def fig2_share():
    rows = load("two_road_share")
    grid = load("grid_share")
    cal = calibrate_two_road()
    r1, r2 = cal["roads"]
    panels = [("two_road", 1200, rows), ("two_road", 1800, rows)]
    panels += [("grid", d, grid) for d in sorted({r["demand"] for r in grid})]
    fig, axes = plt.subplots(1, len(panels), figsize=(3.5 * len(panels), 3.5), sharey=False)
    for k, (ax, (net, d, data)) in enumerate(zip(np.atleast_1d(axes), panels)):
        sub = [r for r in data if r["demand"] == d]
        shares = sorted({r["share"] for r in sub})
        g = by(sub, "share")
        for metric, key, label in (("journey", "everyone", "everyone"), ("journey_rerouters", "rerouters", "drivers who reroute"),
                                   ("journey_others", "others", "drivers who don't")):
            band(ax, shares, {s: g[(s,)] for s in shares}, metric, COLOR[key], label, scale=1 / 60)
        if net == "two_road":
            p_star = T.share_threshold(d, r1, r2)
            ax.axvline(p_star, color=MUTED, lw=1, ls=(0, (4, 3)))
            ax.text(p_star, 1.0, f" theory: {p_star:.0%} is enough", transform=ax.get_xaxis_transform(),
                    fontsize=9.5, color=INK2, va="top")
        ax.set_yscale("log")
        plain_log(ax.yaxis)
        ax.xaxis.set_major_formatter(mticker.PercentFormatter(1.0))
        ax.set_xlabel("share of drivers who reroute")
        where = "Two roads" if net == "two_road" else "City grid"
        ax.set_title(f"{'abcd'[k]}   {where}, {d:,} cars/h", pad=18)
        if k == 0:
            ax.set_ylabel("average trip time (min)")
            handles, labels = ax.get_legend_handles_labels()
            fig.legend(handles, labels, loc="lower center", ncol=3, fontsize=10, bbox_to_anchor=(0.5, -0.1))
    fig.tight_layout(w_pad=2.5)
    save(fig, "fig2_how_many")


# --------------------------------------------------------------------------- figure 3: stale information and herding


def fig3_information():
    rows = load("two_road_information")
    fig, (ax1, ax2, ax3, ax4) = plt.subplots(1, 4, figsize=(17, 3.6), gridspec_kw={"width_ratios": [1.3, 1, 1, 1.1]})
    # (a) the minute-by-minute split, half vs every driver rerouting
    for share, color in ((0.5, COLOR["live_0.5"]), (1.0, COLOR["live_1.0"])):
        r = next(r for r in rows if r["share"] == share and r["window"] == 180 and not r["synchronize"] and r["seed"] == 1)
        t, s = zip(*[(t / 60, v) for t, v, *_ in r["long_share_series"] if t < 3600])
        ax1.plot(t, np.array(s) * 100, color=color, lw=1.6,
                 label=f"{'half' if share == 0.5 else 'every'} driver{'s' if share == 0.5 else ''} rerouting")
    ax1.set_xlabel("time (min)")
    ax1.set_ylabel("cars taking the detour (%)")
    ax1.set_title("a   Herding: everyone jumps at once (one run)")
    ax1.legend(loc="upper center", bbox_to_anchor=(0.5, -0.22), fontsize=9, ncol=2)
    # (b) cost of stale information
    windows = sorted({r["window"] for r in rows})
    for share in (0.5, 1.0):
        g = by([r for r in rows if r["share"] == share and not r["synchronize"]], "window")
        band(ax2, windows, {w: g[(w,)] for w in windows}, "journey", COLOR[f"live_{share:.1f}"],
             f"{'half' if share == 0.5 else 'every'} driver{'s' if share == 0.5 else ''}", scale=1 / 60)
    ax2.set_xscale("log")
    ax2.xaxis.set_major_formatter(mticker.FuncFormatter(lambda v, _: f"{v:g}"))
    ax2.set_xlabel("travel times averaged over the last … s")
    ax2.set_ylabel("average trip time (min)")
    ax2.set_title("b   Older information costs time")
    ax2.legend(loc="upper left", fontsize=9.5)
    # (c) theory: drivers reacting to the previous update's travel times (lagged decisions)
    cal = calibrate_two_road()
    r1, r2 = cal["roads"]
    d, beta = 1200.0, 0.02   # our two roads; beta (how sharply drivers react) is illustrative
    g_star = T.herding_threshold(d, beta, r1, r2)
    for g, color, label in ((0.4 * g_star, COLOR["live_0.5"], f"{0.4 * g_star:.0%} react at once: settles"),
                            (0.9, COLOR["live_1.0"], "90% react at once: herding")):
        x = T.lagged_dynamics(d, g, beta, r1, r2, x0=d, steps=20)
        ax3.plot(np.arange(len(x)), 100 * (d - x) / d, "-o", ms=3.5, color=color, lw=1.6, label=label)
    ax3.set_xlabel("information update")
    ax3.set_ylabel("cars taking the detour (%)")
    ax3.set_title("c   Theory: herding")
    ax3.legend(loc="upper center", bbox_to_anchor=(0.5, -0.22), fontsize=9, ncol=1)
    # (d) city grid near collapse: how often drivers re-check, and with how old information
    grid = load("grid_information")
    if grid:
        cases = [(30, 30, False, "every 30 s"), (30, 120, False, "every 2 min"), (30, 120, True, "every 2 min,\nall at once")]
        windows = sorted({r["window"] for r in grid})
        width = 0.26
        for k, (_, period, sync, label) in enumerate(cases):
            vals = [np.mean([r["journey"] for r in grid if r["window"] == w and r["period"] == period
                             and r["synchronize"] == sync]) / 60 if any(r["window"] == w and r["period"] == period
                                                                         and r["synchronize"] == sync for r in grid) else np.nan
                    for w in windows]
            ax4.bar(np.arange(len(windows)) + (k - 1) * width, vals, width * 0.92,
                    color=("#f3a683", "#eb6834", "#c0392b")[k], label=f"re-check {label}")
        ax4.set_xticks(range(len(windows)), [f"{w} s" for w in windows])
        ax4.set_xlabel("travel times averaged over the last …")
        ax4.set_ylabel("average trip time (min)")
        ax4.set_title("d   City grid, 12,000 cars/h")
        ax4.grid(axis="x", visible=False)
        ax4.legend(loc="upper center", bbox_to_anchor=(0.5, -0.22), fontsize=9, ncol=2)
    fig.tight_layout(w_pad=2.5)
    save(fig, "fig3_information")


# --------------------------------------------------------------------------- figure 4: real street networks


def fig4_networks():
    grid, city = load("grid_demand"), load("city")
    fig, (ax1, ax2, ax3) = plt.subplots(1, 3, figsize=(14, 3.6), gridspec_kw={"width_ratios": [1.3, 1, 1.1]})
    demands = sorted({r["demand"] for r in grid})
    groups = by(grid, "policy", "demand")
    for pol in ("no_information", "live_0.5", "live_1.0"):
        g = {d: groups.get((pol, d), []) for d in demands}
        band(ax1, demands, g, "journey", COLOR[pol], LABEL[pol], scale=1 / 60)
        band(ax2, demands, g, "completed", COLOR[pol], LABEL[pol], scale=100)
    ax1.set_yscale("log")
    plain_log(ax1.yaxis)
    for ax in (ax1, ax2):
        ax.set_xlabel("traffic (cars per hour)")
        ax.xaxis.set_major_formatter(mticker.FuncFormatter(lambda v, _: f"{v / 1000:g}k"))
    ax1.set_ylabel("average trip time (min)")
    ax1.set_title("a   City grid (6 × 6 junctions)")
    ax2.set_ylabel("trips finished within 2 h (%)")
    ax2.set_title("b   City grid: trips that could finish")
    handles, labels = ax1.get_legend_handles_labels()
    fig.legend(handles, labels, loc="lower center", ncol=4, fontsize=10, bbox_to_anchor=(0.5, -0.08))
    # (c) La Rochelle: trips finished and time, three traffic levels
    if city:
        levels = sorted({r["demand"] for r in city})
        width = 0.26
        for k, pol in enumerate(("no_information", "live_0.5", "live_1.0")):
            vals = [np.mean([r["journey"] for r in city if policy(r) == pol and r["demand"] == d]) / 60 for d in levels]
            done = [np.mean([r["completed"] for r in city if policy(r) == pol and r["demand"] == d]) for d in levels]
            xs = np.arange(len(levels)) + (k - 1) * width
            ax3.bar(xs, vals, width * 0.92, color=COLOR[pol])
            for x, v, c in zip(xs, vals, done):
                ax3.text(x, v, f"{c:.0%}", ha="center", va="bottom", fontsize=8.5, color=INK2)
        ax3.set_xticks(range(len(levels)), [f"{d:,} cars/h" for d in levels])
        ax3.set_ylabel("average trip time (min)")
        ax3.set_title("c   La Rochelle (% = trips finished)")
        ax3.grid(axis="x", visible=False)
    fig.tight_layout(w_pad=2.5)
    save(fig, "fig4_real_networks")


def main():
    fig1_demand()
    fig2_share()
    fig3_information()
    fig4_networks()


if __name__ == "__main__":
    main()
