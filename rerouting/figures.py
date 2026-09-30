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
    fig, axes = plt.subplots(1, 4, figsize=(18, 3.9))
    ax0, ax1, ax2, ax3 = axes
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
    fig.legend(handles=handles, loc="lower center", ncol=5, fontsize=10, bbox_to_anchor=(0.5, -0.1))
    fig.tight_layout(w_pad=2.2)
    save(fig, "fig2_how_much")


def fig3_how_many():
    cc = chain_curves()
    rows = load("two_road_share_series")
    fig, axes = plt.subplots(1, 4, figsize=(18, 3.9))
    from rerouting.markov import swing, switches
    panels = [(1, "journey", 1 / 60, "average trip time (min)", "a   Trip time"),
              (4, "long_share_rerouters", 100, "rerouters on the detour (%)", "b   Where rerouters go"),
              (5, lambda r: swing(r["long_share_series"]), 1.0, "minute-to-minute swing", "c   How much they swing"),
              (6, lambda r: switches(r["long_share_series"]), 1.0, "switches per hour", "d   How often they switch")]
    for ax, (col, metric, scale, ylabel, title) in zip(axes, panels):
        for d, color in DEMAND_COLOR.items():
            curve = np.array(cc["share"][str(d)], dtype=float)
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
    handles = [plt.Line2D([], [], color=INK2, lw=1.8, label="Markov chain (line)"),
               plt.Line2D([], [], color=INK2, marker="o", mfc="white", ls="none", label="SUMO, 5 runs (dots, range)")]
    handles += [plt.Line2D([], [], color=c, lw=3, label=f"{d:,} cars/h") for d, c in DEMAND_COLOR.items()]
    fig.legend(handles=handles, loc="lower center", ncol=5, fontsize=10, bbox_to_anchor=(0.5, -0.1))
    fig.tight_layout(w_pad=2.2)
    save(fig, "fig3_how_many")


def fig4_old_news():
    cc = chain_curves()
    rows = load("two_road_information")
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(12.5, 3.9), gridspec_kw={"width_ratios": [1.4, 1]})
    sumo = next((r for r in load("two_road_share_series") if r["demand"] == 1800 and r["share"] == 1.0
                 and r["seed"] == 1), None)
    if sumo:
        t, q = zip(*[(t / 60, v) for t, v, *_ in sumo["long_share_series"] if t < 3600])
        ax1.plot(t, np.array(q) * 100, color=COLOR["live_1.0"], lw=1.7, label="SUMO (one run)")
    t, q = zip(*[(t / 60, v) for t, v, *_ in cc["series_example"] if t < 3600])
    ax1.plot(t, np.array(q) * 100, color=INK, lw=1.4, ls=(0, (3, 2)), label="Markov chain (one run)")
    ax1.set_xlabel("minute")
    ax1.set_ylabel("rerouters on the detour (%)")
    ax1.set_title("a   Everybody rerouting, 1,800 cars/h: the crowd swings")
    ax1.legend(loc="upper center", bbox_to_anchor=(0.5, -0.2), ncol=2, fontsize=10)
    for p in (0.5, 1.0):
        pol = f"live_{p:.1f}"
        curve = np.array(cc["window"][str(p)])
        ax2.plot(curve[:, 0], curve[:, 1] / 60, color=COLOR[pol], lw=1.8)
        sub = by([r for r in rows if r["share"] == p and not r["synchronize"]], "window")
        xs = sorted(k[0] for k in sub)
        dots(ax2, xs, {k[0]: v for k, v in sub.items()}, "journey", COLOR[pol], 1 / 60,
             "half the drivers" if p == 0.5 else "every driver")
    ax2.set_xscale("log")
    ax2.xaxis.set_major_formatter(mticker.FuncFormatter(lambda v, _: f"{v:g}"))
    ax2.set_xlabel("travel times averaged over the last … s")
    ax2.set_ylabel("average trip time (min)")
    ax2.set_title("b   Older news costs time (line: chain)")
    ax2.legend(loc="upper left", fontsize=9.5)
    fig.tight_layout(w_pad=2.5)
    save(fig, "fig4_old_news")


# --------------------------------------------------------------------------- figure 5: city grid and La Rochelle


def fig5_networks():
    grid, city, share, info = load("grid_demand"), load("city"), load("grid_share"), load("grid_information")
    fig, (ax1, ax2, ax3, ax4) = plt.subplots(1, 4, figsize=(18, 3.9), gridspec_kw={"width_ratios": [1.2, 1, 1, 1.1]})
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
    # (d) La Rochelle: trips finished and time, three traffic levels
    levels = sorted({r["demand"] for r in city})
    for k, pol in enumerate(("no_information", "live_0.5", "live_1.0")):
        vals = [np.mean([r["journey"] for r in city if policy(r) == pol and r["demand"] == d]) / 60 for d in levels]
        done = [np.mean([r["completed"] for r in city if policy(r) == pol and r["demand"] == d]) for d in levels]
        xs = np.arange(len(levels)) + (k - 1) * width
        ax4.bar(xs, vals, width * 0.92, color=COLOR[pol])
        for x, v, c in zip(xs, vals, done):
            ax4.text(x, v, f"{c:.0%}", ha="center", va="bottom", fontsize=8, color=INK2)
    ax4.set_xticks(range(len(levels)), [f"{d:,} cars/h" for d in levels])
    ax4.set_ylabel("average trip time (min)")
    ax4.set_title("d   La Rochelle (% = trips finished)")
    ax4.grid(axis="x", visible=False)
    handles, labels = ax1.get_legend_handles_labels()
    fig.legend(handles, labels, loc="lower center", ncol=3, fontsize=10, bbox_to_anchor=(0.5, -0.08))
    fig.tight_layout(w_pad=2.2)
    save(fig, "fig5_networks")


# --------------------------------------------------------------------------- figure 1: the Markov chain


def fig_chain():
    from matplotlib.patches import FancyArrowPatch, FancyBboxPatch, Circle
    fig, (ax, bx) = plt.subplots(1, 2, figsize=(15, 4.6), gridspec_kw={"width_ratios": [1.1, 1]})
    for a in (ax, bx):
        a.set_xlim(0, 10.6)
        a.set_ylim(-0.3, 6)
        a.set_aspect("equal")
        a.axis("off")

    def box(a, x, y, w, h, text, fc, ec, size=10.5, weight="normal"):
        a.add_patch(FancyBboxPatch((x, y), w, h, boxstyle="round,pad=0.08,rounding_size=0.15", fc=fc, ec=ec, lw=1.4))
        a.text(x + w / 2, y + h / 2, text, ha="center", va="center", fontsize=size, color=INK, weight=weight)

    def arrow(a, p, q, color=INK2, ls="-", rad=0.0, text=None, tx=0.0, ty=0.18, size=9.5):
        a.add_patch(FancyArrowPatch(p, q, arrowstyle="-|>", mutation_scale=13, color=color, lw=1.5, ls=ls,
                                    connectionstyle=f"arc3,rad={rad}"))
        if text:
            a.text((p[0] + q[0]) / 2 + tx, (p[1] + q[1]) / 2 + ty, text, ha="center", va="bottom", fontsize=size,
                   color=color)

    # (a) the whole system
    ax.set_title("a   The state: two queues and one piece of news", loc="left")
    box(ax, 0.0, 2.35, 1.6, 1.1, "a car\narrives\n(prob. d/3600)", "#f4f3ef", MUTED, 9.5)
    box(ax, 2.3, 2.35, 1.6, 1.1, "no app:\nshort road\napp: road s", "#fdebe3", COLOR["live_0.5"], 9.5)
    arrow(ax, (1.7, 2.9), (2.3, 2.9))
    for y, name, n, road in ((3.9, "short road", "n₁", "C₁"), (0.9, "detour", "n₂", "C₂")):
        box(ax, 5.0, y, 3.0, 0.9, "", "white", MUTED)
        ax.text(5.1, y + 1.02 if y > 2 else y - 0.35, f"{name}: queue {n}", fontsize=10, color=INK)
        for k in range(4):
            ax.add_patch(Circle((5.35 + 0.45 * k, y + 0.45), 0.16, color=COLOR["everyone"] if k < 3 else GRID))
        ax.add_patch(Circle((8.25, y + 0.45), 0.17, color="#2e9c5b"))
        arrow(ax, (8.5, y + 0.45), (9.7, y + 0.45), text=f"light lets one go\n(prob. {road}/3600)", ty=0.15, size=9)
    arrow(ax, (3.95, 3.1), (4.95, 4.3), color=INK2)
    arrow(ax, (3.95, 2.7), (4.95, 1.4), color=INK2)
    box(ax, 5.3, 2.45, 2.5, 0.9, "news s: which road\nlooks faster", "#e8f0fb", COLOR["others"], 9.5)
    arrow(ax, (6.3, 3.85), (6.3, 3.42), color=COLOR["others"], ls="--")
    arrow(ax, (6.3, 1.85), (6.3, 2.38), color=COLOR["others"], ls="--")
    arrow(ax, (5.25, 2.9), (3.95, 2.9), color=COLOR["others"], ls="--")
    ax.text(0.0, -0.2, "each second the news refreshes with prob. 1/τ:\ns becomes the road with the lower  T + 3600·n / C",
            fontsize=9.5, color=COLOR["others"], ha="left")

    # (b) one queue as a birth-death chain
    bx.set_title("b   One queue: a chain that goes up and down", loc="left")
    for k in range(5):
        x = 1.0 + 1.9 * k
        bx.add_patch(Circle((x, 4.3), 0.42, fc="white", ec=INK, lw=1.4))
        bx.text(x, 4.3, str(k) if k < 4 else "…", ha="center", va="center", fontsize=12)
        if k < 4:
            arrow(bx, (x + 0.45, 4.5), (x + 1.45, 4.5), color=COLOR["live_1.0"], rad=-0.35)
            arrow(bx, (x + 1.45, 4.1), (x + 0.45, 4.1), color="#2e9c5b", rad=-0.35)
    bx.text(1.95, 5.15, "a car arrives (x per hour)", fontsize=9.5, color=COLOR["live_1.0"])
    bx.text(1.95, 3.25, "the light lets one go (C per hour)", fontsize=9.5, color="#2e9c5b")
    rho = 0.7
    for k in range(5):
        x = 1.0 + 1.9 * k
        h = 1.4 * rho ** k
        if k < 3:
            bx.add_patch(FancyBboxPatch((x - 0.35, 1.0), 0.7, h, boxstyle="square,pad=0", fc=MUTED, ec="none"))
            bx.text(x, 0.6, f"P({k})", fontsize=9.5, color=INK2, ha="center")
    bx.text(5.3, 2.2, "balance: x·P(n) = C·P(n+1)\n→  P(n) = (1−ρ) ρⁿ,  ρ = x / C",
            fontsize=9.8, color=INK)
    bx.text(0.2, -0.2, "average queue ρ/(1−ρ)  →  trip time  t(x) = T + 3600·x / (C·(C − x))",
            fontsize=10, color=INK, weight="bold")
    save(fig, "fig1_markov_chain")


def main():
    fig_chain()
    fig2_how_much()
    fig3_how_many()
    fig4_old_news()
    fig5_networks()


if __name__ == "__main__":
    main()
