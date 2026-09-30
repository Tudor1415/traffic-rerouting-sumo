"""The README figures, from results/*.jsonl.

    python -m rerouting.figures

Colours follow the kind of driver: no live information grey, experienced drivers
violet, live rerouting orange (half of the drivers) and red (everyone).
"""

from __future__ import annotations

import csv
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
    """The two roads as the chain sees them (measured in SUMO), and the onset d*."""
    from rerouting.conjectures import calibrate
    r1, r2 = calibrate()
    return {"roads": (r1, r2), "d_star": T.demand_threshold(r1, r2)}


# --------------------------------------------------------------------------- chain (lines) against SUMO (dots)

DEMAND_COLOR = {1200: "#e8a33d", 1500: "#d0632b", 1800: "#8f2d1f"}


def chain_curves() -> dict:
    """Chain curves for the figures, computed once (python -m rerouting.markov writes them)."""
    path = RESULTS / "chain_curves.json"
    if not path.exists():
        from rerouting import conjectures, markov
        path.write_text(json.dumps(markov.curves(*conjectures.calibrate())))
    return json.loads(path.read_text())


def dots(ax, xs, rows_by_x, metric, color, scale=1.0, label=None, marker="o"):
    """SUMO: mean over seeds (dot) and the range between seeds (bar)."""
    mx, my, lo, hi = [], [], [], []
    for x in xs:
        vals = [metric(r) if callable(metric) else r[metric] for r in rows_by_x.get(x, [])]
        vals = [v * scale for v in vals if v == v]
        if vals:
            mx.append(x)
            my.append(np.mean(vals))
            lo.append(np.mean(vals) - min(vals))
            hi.append(max(vals) - np.mean(vals))
    ax.errorbar(mx, my, yerr=[lo, hi], fmt=marker, ms=5, color=color, mfc="white", mew=1.6, elinewidth=1,
                capsize=0, label=label, zorder=3)


def fig2_how_much():
    cal = calibrate_two_road()
    r1, r2 = cal["roads"]
    cc = chain_curves()
    rows = load("two_road_demand")
    fig, axes = plt.subplots(2, 2, figsize=(11.5, 8.4))
    ax0, ax1, ax2, ax3 = axes.flat
    # (a) one road: the chain's stationary law against runs with everybody forced on that road
    forced = load("two_road_calibration") + load("two_road_queue_law")
    for route, road, ls in (("short", r1, "-"), ("long", r2, (0, (4, 2)))):
        x = np.linspace(0, 0.97 * road.C, 200)
        ax0.plot(x, [road.time(v) / 60 for v in x], color=INK, ls=ls, lw=1.6, label=f"chain, {route} road")
        rs = by([r for r in forced if r["route"] == route and r["demand"] < road.C], "demand")
        dots(ax0, sorted(k[0] for k in rs), {k[0]: v for k, v in rs.items()}, "journey", COLOR["no_information"],
             1 / 60, f"SUMO, {route} road", "o" if route == "short" else "s")
    ax0.set_ylim(0, 8)
    ax0.set_xlabel("cars per hour on the road")
    ax0.set_ylabel("trip time (min)")
    ax0.set_title("a   One road: drive + queue")
    ax0.legend(fontsize=8.5, loc="upper left")
    # (b) trip time against traffic, (d) trips finished
    g = by(rows, "policy", "demand")
    for pol, p in (("no_information", "0.0"), ("live_0.5", "0.5"), ("live_1.0", "1.0")):
        curve = np.array(cc["demand"][p])
        ax1.plot(curve[:, 0], curve[:, 1] / 60, color=COLOR[pol], lw=1.8)
        ax3.plot(curve[:, 0], curve[:, 2] * 100, color=COLOR[pol], lw=1.8)
        ds = sorted({r["demand"] for r in rows})
        dots(ax1, ds, {d: g.get((pol, d), []) for d in ds}, "journey", COLOR[pol], 1 / 60, LABEL[pol])
        dots(ax3, ds, {d: g.get((pol, d), []) for d in ds}, "completed", COLOR[pol], 100)
    ax1.set_yscale("log")
    plain_log(ax1.yaxis)
    ax1.set_ylabel("average trip time (min)")
    ax1.set_title("b   Two roads: trip time")
    ax3.set_ylabel("trips finished by the 2 h cut-off (%)")
    ax3.set_title("d   Trips finished")
    for ax in (ax1, ax3):
        ax.set_xlabel("traffic (cars per hour)")
    ax1.axvline(cal["d_star"], color=MUTED, lw=1, ls=(0, (4, 3)))
    # (c) the onset of the gain
    onset = np.array(sorted((int(k), v) for k, v in cc["onset"].items()))
    ax2.plot(onset[:, 0], onset[:, 1] / 60, color=INK, lw=1.8, label="chain")
    on = load("two_road_onset")
    ds = sorted({r["demand"] for r in on})
    gains = {}
    for d in ds:
        st = {r["seed"]: r["journey"] for r in on if r["demand"] == d and r["policy"] == "no_information"}
        lv = {r["seed"]: r["journey"] for r in on if r["demand"] == d and r["policy"] == "live"}
        gains[d] = [{"gain": st[s] - lv[s]} for s in st if s in lv]
    dots(ax2, ds, gains, "gain", COLOR["live_1.0"], 1 / 60, "SUMO")
    ax2.axvline(cal["d_star"], color=MUTED, lw=1, ls=(0, (4, 3)))
    ax2.text(cal["d_star"], 0.97, f" d* = {cal['d_star']:.0f}", transform=ax2.get_xaxis_transform(), fontsize=9.5,
             color=INK2, va="top")
    ax2.set_xlabel("traffic (cars per hour)")
    ax2.set_ylabel("time saved by rerouting (min)")
    ax2.set_title("c   When the gain starts")
    ax2.legend(fontsize=9, loc="upper left", bbox_to_anchor=(0, 0.88))
    handles = [plt.Line2D([], [], color=INK2, lw=1.8, label="Markov chain (line)"),
               plt.Line2D([], [], color=INK2, marker="o", mfc="white", ls="none", label="SUMO, 5 runs (dots, range)")]
    handles += [plt.Line2D([], [], color=COLOR[p], lw=3, label=LABEL[p]) for p in ("no_information", "live_0.5", "live_1.0")]
    fig.legend(handles=handles, loc="lower center", ncol=3, fontsize=10, bbox_to_anchor=(0.5, -0.07))
    fig.tight_layout(w_pad=2.2, h_pad=2.0)
    save(fig, "fig2_how_much")


def fig3_how_many():
    cc = chain_curves()
    rows = load("two_road_share_series")
    fig, axes = plt.subplots(2, 2, figsize=(11.5, 8.4))
    axes = axes.flat
    from rerouting.markov import swing, switches
    panels = [(1, "journey", 1 / 60, "average trip time (min)", "a   Trip time"),
              (4, "long_share_rerouters", 100, "rerouters on the detour (%)", "b   Where rerouters go"),
              (5, lambda r: swing(r["long_share_series"]), 1.0, "minute-to-minute swing", "c   How much they swing"),
              (6, lambda r: switches(r["long_share_series"]), 1.0, "switches per hour", "d   How often they switch")]
    for k, (ax, (col, metric, scale, ylabel, title)) in enumerate(zip(axes, panels)):
        for d, color in DEMAND_COLOR.items():
            curve = np.array(cc["share"][str(d)], dtype=float)
            if k < 2:   # the theory is reliable for trip time and where rerouters go, not for the swinging
                ax.plot(curve[:, 0], curve[:, col] * scale, color=color, lw=1.8)
            sub = by([r for r in rows if r["demand"] == d], "share")
            xs = sorted(k[0] for k in sub)
            dots(ax, xs, {k[0]: v for k, v in sub.items()}, metric, color, scale)
        ax.xaxis.set_major_formatter(mticker.PercentFormatter(1.0))
        ax.set_xlabel("share of drivers who reroute")
        ax.set_ylabel(ylabel)
        ax.set_title(title)
    axes[0].set_yscale("log")
    plain_log(axes[0].yaxis)
    handles = [plt.Line2D([], [], color=INK2, lw=1.8, label="Markov chain (line, panels a-b)"),
               plt.Line2D([], [], color=INK2, marker="o", mfc="white", ls="none", label="SUMO, 5 runs (dots, range)")]
    handles += [plt.Line2D([], [], color=c, lw=3, label=f"{d:,} cars/h") for d, c in DEMAND_COLOR.items()]
    fig.legend(handles=handles, loc="lower center", ncol=3, fontsize=10, bbox_to_anchor=(0.5, -0.07))
    fig.tight_layout(w_pad=2.2, h_pad=2.0)
    save(fig, "fig3_how_many")


def fig4_old_news():
    cc = chain_curves()
    rows = load("two_road_information")
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(12, 4.6), gridspec_kw={"width_ratios": [1.3, 1]})
    sumo = next((r for r in load("two_road_share_series") if r["demand"] == 1800 and r["share"] == 1.0
                 and r["seed"] == 1), None)
    if sumo:
        t, q = zip(*[(t / 60, v) for t, v, *_ in sumo["long_share_series"] if t < 3600])
        ax1.plot(t, np.array(q) * 100, color=COLOR["live_1.0"], lw=1.7, label="SUMO (one run)")

    ax1.set_xlabel("minute")
    ax1.set_ylabel("rerouters on the detour (%)")
    ax1.set_title("a   Everybody rerouting, 1,800 cars/h: the crowd swings")
    ax1.legend(loc="upper center", bbox_to_anchor=(0.5, -0.2), ncol=2, fontsize=10)
    for p in (0.5, 1.0):
        pol = f"live_{p:.1f}"
        sub = by([r for r in rows if r["share"] == p and not r["synchronize"]], "window")
        xs = sorted(k[0] for k in sub)
        dots(ax2, xs, {k[0]: v for k, v in sub.items()}, "journey", COLOR[pol], 1 / 60,
             "half the drivers" if p == 0.5 else "every driver")
    ax2.set_xscale("log")
    ax2.xaxis.set_major_formatter(mticker.FuncFormatter(lambda v, _: f"{v:g}"))
    ax2.set_xlabel("travel times averaged over the last … s")
    ax2.set_ylabel("average trip time (min)")
    ax2.set_title("b   Older news costs time")
    ax2.legend(loc="upper left", fontsize=9.5)
    fig.tight_layout(w_pad=2.5)
    save(fig, "fig4_old_news")


# --------------------------------------------------------------------------- figure 5: city grid and La Rochelle


def fig5_networks():
    grid, share, info = load("grid_demand"), load("grid_share"), load("grid_information")
    fig, axes = plt.subplots(2, 2, figsize=(11.5, 8.4))
    ax1, ax2, ax3, ax4 = axes.flat
    # (a) trip time against traffic
    demands = sorted({r["demand"] for r in grid})
    groups = by(grid, "policy", "demand")
    for pol in ("no_information", "live_0.5", "live_1.0"):
        band(ax1, demands, {d: groups.get((pol, d), []) for d in demands}, "journey", COLOR[pol], LABEL[pol], 1 / 60)
    ax1.set_yscale("log")
    plain_log(ax1.yaxis)
    ax1.set_xlabel("traffic (cars per hour)")
    ax1.xaxis.set_major_formatter(mticker.FuncFormatter(lambda v, _: f"{v / 1000:g}k"))
    ax1.set_ylabel("average trip time (min)")
    ax1.set_title("a   City grid: trip time")
    # (b) share of rerouters
    for d, color in ((11000, "#e8a33d"), (12000, "#8f2d1f")):
        sub = [r for r in share if r["demand"] == d]
        xs = sorted({r["share"] for r in sub})
        g = by(sub, "share")
        band(ax2, xs, {x: g[(x,)] for x in xs}, "journey", color, f"{d:,} cars/h", 1 / 60)
    ax2.set_yscale("log")
    plain_log(ax2.yaxis)
    ax2.xaxis.set_major_formatter(mticker.PercentFormatter(1.0))
    ax2.set_xlabel("share of drivers who reroute")
    ax2.set_title("b   City grid: how many reroute")
    ax2.legend(fontsize=9, loc="upper right")
    # (c) how old the news is, and whether drivers re-check at the same moment
    cases = [(30, False, "re-check every 30 s"), (120, False, "every 2 min"), (120, True, "every 2 min, all at once")]
    windows = sorted({r["window"] for r in info})
    width = 0.26
    for k, (period, sync, label) in enumerate(cases):
        vals = [np.mean([r["journey"] for r in info if r["window"] == w and r["period"] == period
                         and r["synchronize"] == sync] or [np.nan]) / 60 for w in windows]
        ax3.bar(np.arange(len(windows)) + (k - 1) * width, vals, width * 0.92,
                color=("#f3a683", "#eb6834", "#c0392b")[k], label=label)
    ax3.set_xticks(range(len(windows)), [f"{w} s" for w in windows])
    ax3.set_xlabel("travel times averaged over the last …")
    ax3.set_ylabel("average trip time (min)")
    ax3.set_title("c   City grid, 12,000 cars/h: old news")
    ax3.grid(axis="x", visible=False)
    ax3.legend(fontsize=8.5, loc="upper left")
    # (d) trips finished against traffic
    for pol in ("no_information", "live_0.5", "live_1.0"):
        band(ax4, demands, {d: groups.get((pol, d), []) for d in demands}, "completed", COLOR[pol], LABEL[pol], 100)
    ax4.set_xlabel("traffic (cars per hour)")
    ax4.xaxis.set_major_formatter(mticker.FuncFormatter(lambda v, _: f"{v / 1000:g}k"))
    ax4.set_ylabel("trips finished within 2 h (%)")
    ax4.set_title("d   City grid: trips that could finish")
    handles, labels = ax1.get_legend_handles_labels()
    fig.legend(handles, labels, loc="lower center", ncol=3, fontsize=10, bbox_to_anchor=(0.5, -0.05))
    fig.tight_layout(w_pad=2.2, h_pad=2.0)
    save(fig, "fig5_networks")


# --------------------------------------------------------------------------- figure 6: La Rochelle at full scale


def fig6_larochelle():
    runs = load("larochelle")
    counts_path = RESULTS / "larochelle_counts.csv"
    if not runs or not counts_path.exists():
        return
    counts = list(csv.DictReader(open(counts_path)))
    fig, axes = plt.subplots(2, 2, figsize=(11.5, 8.4))
    ax0, ax1, ax2, ax3 = axes.flat
    for used, color, label in (("fit", INK, "roads used to fit the volume"), ("check", COLOR["others"], "held-out roads")):
        xs = [float(r["target_peak_hour"]) for r in counts if r["used_for"] == used]
        ys = [float(r["simulated_peak_hour"]) for r in counts if r["used_for"] == used]
        ax0.plot(xs, ys, "o", ms=5, color=color, mfc="white" if used == "check" else color, label=label)
    top = max(max(float(r["target_peak_hour"]), float(r["simulated_peak_hour"])) for r in counts) * 1.05
    ax0.plot([0, top], [0, top], color=MUTED, lw=1)
    ax0.set_xlim(0, top)
    ax0.set_ylim(0, top)
    ax0.set_xlabel("counted, 7:30-8:30 (9 % of daily traffic, cars/h)")
    ax0.set_ylabel("simulated, 7:30-8:30 (cars/h)")
    ax0.set_title("a   Calibration against the 2023 road counts")
    ax0.legend(fontsize=9, loc="upper left")
    shares = sorted({r["share"] for r in runs})
    for day, color, label in (("normal", COLOR["live_0.5"], "normal morning"),
                              ("incident", COLOR["live_1.0"], "accident on the ring road, 7:45-8:30")):
        g = by([r for r in runs if r["day"] == day], "share")
        sub = {s: g.get((s,), []) for s in shares}
        band(ax1, shares, sub, "journey", color, label, 1 / 60)
        band(ax2, shares, sub, "time_lost", color, label, 1 / 60)
    normal = by([r for r in runs if r["day"] == "normal"], "share")
    sub = {s: normal.get((s,), []) for s in shares}
    band(ax3, [s for s in shares if s > 0], sub, "journey_rerouters", COLOR["rerouters"], "app users", 1 / 60)
    band(ax3, [s for s in shares if s < 1], sub, "journey_others", COLOR["others"], "the others", 1 / 60)
    ax3.legend(fontsize=9, loc="upper right")
    for ax, ylabel, title in ((ax1, "average trip time (min)", "b   Trip time, drivers leaving 7:00-9:00"),
                              (ax2, "time lost in congestion (min)", "c   Time lost"),
                              (ax3, "average trip time (min)", "d   Who gains (normal morning)")):
        ax.xaxis.set_major_formatter(mticker.PercentFormatter(1.0))
        ax.set_xlabel("share of drivers using the app")
        ax.set_ylabel(ylabel)
        ax.set_title(title)
    handles, labels = ax1.get_legend_handles_labels()
    fig.legend(handles, labels, loc="lower center", ncol=2, fontsize=10, bbox_to_anchor=(0.5, -0.04))
    fig.tight_layout(w_pad=2.2, h_pad=2.0)
    save(fig, "fig6_larochelle")


# --------------------------------------------------------------------------- figure 1: the Markov chain


def fig_chain():
    from matplotlib.patches import Circle, FancyArrowPatch, FancyBboxPatch
    fig, (ax, bx) = plt.subplots(1, 2, figsize=(15, 5.0), gridspec_kw={"width_ratios": [1.15, 1]})
    for a in (ax, bx):
        a.set_xlim(-0.3, 11.0)
        a.set_ylim(-0.6, 6.2)
        a.set_aspect("equal")
        a.axis("off")

    def box(a, x, y, w, h, text, fc, ec, size=9.5):
        a.add_patch(FancyBboxPatch((x, y), w, h, boxstyle="round,pad=0.08,rounding_size=0.15", fc=fc, ec=ec, lw=1.4,
                                   clip_on=False))
        a.text(x + w / 2, y + h / 2, text, ha="center", va="center", fontsize=size, color=INK)

    def arrow(a, p, q, color=INK2, ls="-"):
        a.add_patch(FancyArrowPatch(p, q, arrowstyle="-|>", mutation_scale=13, color=color, lw=1.5, ls=ls, clip_on=False))

    def cars(a, x, y, k, color, gap=0.42):
        for i in range(k):
            a.add_patch(Circle((x + gap * i, y), 0.15, color=color, clip_on=False))

    # (a) the state and the three rules
    ax.set_title("a   One chain: a line, two roads, one app", loc="left")
    box(ax, -0.2, 2.3, 2.3, 1.3, "cars arrive\n(prob. d/3600 each s)\nand wait in line,\none enters at a time", "#f4f3ef", MUTED)
    arrow(ax, (2.2, 2.95), (3.0, 2.95))
    box(ax, 3.05, 2.45, 1.6, 1.0, "no app: short\napp: road shown", "#fdebe3", COLOR["live_0.5"], 9)
    # short road: 72 s of driving, then the light
    arrow(ax, (4.7, 3.2), (5.4, 4.5))
    box(ax, 5.4, 4.05, 5.2, 0.9, "", "white", MUTED)
    ax.text(5.5, 5.12, "short road: 72 s of driving, then the light", fontsize=9.5, color=INK)
    cars(ax, 5.75, 4.5, 3, MUTED, gap=0.8)
    cars(ax, 8.4, 4.5, 4, COLOR["everyone"])
    ax.add_patch(Circle((10.35, 4.5), 0.17, color="#2e9c5b"))
    ax.text(10.35, 3.7, "one car every\n3600/C₁ s", fontsize=8.5, color="#2e9c5b", ha="center")
    ax.text(5.4, 3.55, "queue longer than the road\n(133 cars): nobody can enter", fontsize=8.5, color=COLOR["live_1.0"])
    # detour: narrow start
    arrow(ax, (4.7, 2.7), (5.4, 1.3))
    box(ax, 5.4, 0.85, 5.2, 0.9, "", "white", MUTED)
    ax.text(5.5, 0.35, "detour: its narrow start lets one car in every 3600/C₂ s", fontsize=9.5, color=INK)
    ax.add_patch(Circle((5.75, 1.3), 0.17, color="#2e9c5b"))
    cars(ax, 6.4, 1.3, 5, MUTED, gap=0.8)
    # the app
    box(ax, 0.1, 4.6, 3.6, 1.1, "app: average trip time of the last\nw seconds, seen from inside", "#e8f0fb",
        COLOR["others"], 9)
    arrow(ax, (3.75, 5.15), (5.35, 4.7), color=COLOR["others"], ls="--")
    arrow(ax, (3.2, 4.55), (3.7, 3.5), color=COLOR["others"], ls="--")
    # (b) one rule for every trip time
    bx.set_title("b   One rule for every trip time", loc="left")
    bx.text(0.0, 5.3, "trip time = T", fontsize=12, color=INK, weight="bold")
    bx.text(0.0, 4.6, "   + 0.54 s for each car driving ahead of you", fontsize=11, color=INK2)
    bx.text(0.0, 3.95, "   + 3600/C s for each car waiting ahead of you", fontsize=11, color=INK2)
    bx.text(0.0, 3.2, "0.54 s: driving the 7.5 m one car and its gap take, at 50 km/h.\n"
                      "3600/C: the time the bottleneck needs to let one car go.", fontsize=9.5, color=MUTED)
    cars(bx, 0.4, 2.2, 6, MUTED, gap=0.55)
    cars(bx, 4.2, 2.2, 5, COLOR["everyone"])
    bx.add_patch(Circle((6.55, 2.2), 0.17, color="#2e9c5b"))
    bx.text(1.8, 1.6, "driving: 0.54 s each", fontsize=9.5, color=INK2, ha="center")
    bx.text(5.1, 1.6, "waiting: 3600/C each", fontsize=9.5, color=INK2, ha="center")
    bx.text(0.0, 0.35, "on average, at x cars per hour:   t(x) = T (1 + x / 6667) + 1800 x / (C (C - x))",
            fontsize=10.5, color=INK, weight="bold")
    save(fig, "fig1_markov_chain")


def main():
    fig_chain()
    fig2_how_much()
    fig3_how_many()
    fig4_old_news()
    fig5_networks()
    fig6_larochelle()


if __name__ == "__main__":
    main()
