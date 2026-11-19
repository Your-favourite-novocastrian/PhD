"""
plot_fronts.py -- Pareto fronts from tbl_nsga.py, the epsilon-constraint front, and the
weighted-sum solutions, together on the same axes.

    python plot_fronts.py --instance 1                   everything on record for Instance1
    python plot_fronts.py --instance 1 --algo nsga2      one NSGA algorithm (+ exact methods)
    python plot_fronts.py --instance 1 --surface         add a translucent surface
    python plot_fronts.py --instance 1 --seeds           every seed's front, not the merged one
    python plot_fronts.py --run Instance1_levers_nsga2_s3   one seed's front only
    python plot_fronts.py --range 1 5 --no-show          files for instances 1-5, open nothing

TWO FILES PER PLOT, in <out>\\plots\\  (out = results_nsga unless --out is given)
    .html  INTERACTIVE -- opens in your browser. Drag to rotate, scroll to zoom, right-drag
           to pan, double-click to reset. Hover a point to see that roster's numbers. Click
           a legend entry to hide/show that method (in all four panels at once).
    .png   static picture of the same thing, for the thesis.

WHAT IS PLOTTED
    NSGA-II / NSGA-III  For each algorithm the MERGED front: every seed's front pooled, then
                        only the non-dominated rosters kept -- the best-known front.
    e-constraint        results\\<Inst>_front3.csv from tbl_epsilon3.py; if that is missing,
                        the 2-objective results\\<Inst>_frontier.csv from tbl_epsilon.py.
                        The console says which file was used, or why there is none.
    Weighted sum        Every dollars-objective Gurobi / OR-Tools result for the instance found
                        in results\\summary_*.csv (one point per run; a carbon-price sweep
                        gives several). The console lists exactly which rows were used --
                        check it the first time. --no-ws leaves them out.

    Axes: cost F_econ (A$), social F_soc (A$), emissions in kg CO2e. Kg rather than F_env
    dollars because F_env = kg x carbon price: same shape, and weighted-sum runs made at
    different carbon prices stay comparable in kg. Star / diamond-outline = the lowest total
    (F_econ + F_soc + F_env) of each NSGA front.

Reads only; never changes any result file.
"""
import argparse, csv, glob, os, re, webbrowser
import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
EXACT = os.path.join(HERE, "results")

# Validated categorical palette, fixed order. Each series also has its own marker shape, and
# the two lighter hues get a dark outline, so no series depends on colour alone.
STYLE = {
    "nsga2": dict(label="NSGA-II",      color="#2a78d6", mpl="o", p3="circle",  p2="circle",      edge="white"),
    "nsga3": dict(label="NSGA-III",     color="#eb6834", mpl="^", p3="cross",   p2="triangle-up", edge="white"),
    "eps":   dict(label="e-constraint", color="#1baf7a", mpl="s", p3="square",  p2="square",      edge="#1f1f1e"),
    "ws":    dict(label="Weighted sum", color="#eda100", mpl="D", p3="diamond", p2="diamond",     edge="#1f1f1e"),
}
INK, MUTED, GRID = "#1f1f1e", "#6b6a64", "#e4e3dd"
LAB = ["Cost F_econ (A$)", "Social F_soc (A$)", "Emissions (kg CO2e)"]
BAD_STATUS = {"UNKNOWN", "ERROR", "INTERRUPTED", "INFEASIBLE", "FEASIBLE-NOT-OPTIMISED",
              "OUT_OF_MEMORY", "INF_OR_UNBOUNDED", "UNBOUNDED", "MODEL_INVALID", ""}


def inum(s):
    d = "".join(c for c in str(s) if c.isdigit())
    return int(d) if d else 0


def norm(k):
    return re.sub(r"[^a-z0-9]", "", str(k).lower())


def _f(v):
    try:
        return float(str(v).replace(",", ""))
    except (TypeError, ValueError):
        return None


# ------------------------------------------------------------------ a "series" is
#   P    : n x 4 array  [F_econ, F_soc, kg, F_env]
#   meta : n dicts with whatever else is known (source, total, shifts, agency, ...)
def read_front(path, source, seed=None):
    """seed: NSGA group files hold every seed's front -- keep only this seed's rows."""
    if not os.path.exists(path):
        return None, None
    P, meta = [], []
    with open(path, newline="") as f:
        for r in csv.DictReader(f):
            if "status" in r and r["status"] not in ("OPTIMAL", "FEASIBLE"):
                continue
            if seed is not None and "seed" in r and str(r["seed"]) != str(seed):
                continue
            e, s, v = _f(r.get("F_econ")), _f(r.get("F_soc")), _f(r.get("F_env"))
            if None in (e, s, v):
                continue
            kg = _f(r.get("kg_co2e"))
            P.append([e, s, kg if kg is not None else v, v])
            meta.append(dict(source=source, total=_f(r.get("total")) or e + s + v,
                             shifts=r.get("shifts", ""), agency=r.get("agency", ""),
                             under=r.get("understaffed", ""), penalty=r.get("penalty", "")))
    return (np.array(P), meta) if P else (None, None)


def nondominated(P, meta):
    """Keep rosters not dominated on (F_econ, F_soc, F_env); drop exact duplicates."""
    F = P[:, [0, 1, 3]]
    keep, seen = [], set()
    for i in range(len(P)):
        key = tuple(np.round(F[i], 6))
        if key in seen:
            continue
        if ((F <= F[i]).all(1) & (F < F[i]).any(1)).any():
            continue
        seen.add(key)
        keep.append(i)
    return P[keep], [meta[i] for i in keep]


def read_eps(inst, kg_per_aud):
    """The epsilon-constraint front for `inst`, and a one-line note on where it came from.

    1. results\\<Inst>_front3.csv    -- tbl_epsilon3.py, three objectives (preferred)
    2. results\\<Inst>_frontier.csv  -- tbl_epsilon.py, cost (F_econ + F_soc) vs kg. Every
       point that is efficient for (F_econ + F_soc, kg) is also non-dominated on the three
       pillars, so it is a valid exact reference. F_env is rebuilt as kg x carbon price.
    """
    p3 = os.path.join(EXACT, f"{inst}_front3.csv")
    p2 = os.path.join(EXACT, f"{inst}_frontier.csv")
    if os.path.exists(p3):
        P, meta = read_front(p3, "3-objective grid point")
        if P is not None:
            return P, meta, f"{len(P)} point(s) from results\\{inst}_front3.csv"
        with open(p3, newline="") as f:
            st = [r.get("status", "?") for r in csv.DictReader(f)]
        counts = ", ".join(f"{k} {st.count(k)}" for k in sorted(set(st))) or "empty file"
        note = f"results\\{inst}_front3.csv has no usable cells ({counts})"
    else:
        note = f"no results\\{inst}_front3.csv"
    if os.path.exists(p2):
        if not kg_per_aud:
            return None, None, note + f"; {inst}_frontier.csv exists but needs an NSGA run of " \
                                      f"this instance to convert kg to F_env"
        P, meta = [], []
        with open(p2, newline="") as f:
            for r in csv.DictReader(f):
                e, s, kg = _f(r.get("F_econ")), _f(r.get("F_soc")), _f(r.get("kg_co2e"))
                if None in (e, s, kg) or r.get("status", "OPTIMAL") not in ("OPTIMAL", "FEASIBLE"):
                    continue
                v = kg / kg_per_aud
                P.append([e, s, kg, v])
                meta.append(dict(source="2-objective frontier point", total=e + s + v,
                                 shifts=r.get("shifts", ""), agency=r.get("agency", ""),
                                 under=r.get("understaffed", ""), penalty=r.get("penalty", "")))
        if P:
            return np.array(P), meta, note + f" -- using {len(P)} point(s) from the " \
                                             f"2-objective results\\{inst}_frontier.csv"
        note += f"; {inst}_frontier.csv has no usable rows"
    others = sorted(f for f in os.listdir(EXACT) if f.startswith(inst + "_") and "front" in f) \
        if os.path.isdir(EXACT) else []
    if others:
        note += f" (other files for {inst}: {', '.join(others)})"
    return None, None, note


def coverage(A, B, tol=1e-6):
    """C(A, B): share of B's points matched or beaten by some point of A on all pillars."""
    if A is None or B is None or len(A) == 0 or len(B) == 0:
        return None
    A, B = A[:, [0, 1, 3]], B[:, [0, 1, 3]]
    return float(np.mean([(A <= b + tol).all(1).any() for b in B]))


def read_weighted_sum(inst, levers, kg_per_aud):
    """Dollars-objective solver results for `inst` from results\\summary_*.csv.

    Column names are matched loosely (F_econ, 'F_econ (A$)', 'f econ' are all the same)
    because the summary format is written by run_all.py. Penalty-objective rows are
    excluded when an objective column or a 'penalty' file name says so; unusable statuses
    (UNKNOWN, ERROR, ...) are always excluded."""
    P, meta, used, skipped = [], [], [], []
    for path in sorted(glob.glob(os.path.join(EXACT, "summary_*.csv"))):
        name = os.path.basename(path)
        if "penalty" in name.lower():
            continue
        try:
            with open(path, newline="", encoding="utf-8-sig") as f:
                rows = list(csv.DictReader(l for l in f if not l.lstrip().startswith("#")))
        except Exception:
            continue
        if not rows:
            continue
        cols = {norm(k): k for k in rows[0] if k}

        def col(*starts):
            for n, k in cols.items():
                if any(n.startswith(s) for s in starts):
                    return k
            return None
        c_inst, c_e, c_s, c_v = col("instance"), col("fecon"), col("fsoc"), col("fenv")
        if not (c_inst and c_e and c_s and c_v):
            continue
        c_kg, c_obj, c_lev = col("kg"), col("objective"), col("levers")
        c_solver, c_carbon = col("solver"), col("carbon")
        c_status = next((cols[n] for n in cols if n in ("correctedstatus", "status")), None) \
            or col("status", "reportedstatus")
        n_here = 0
        for r in rows:
            if inum(r.get(c_inst, "")) != inum(inst):
                continue
            if c_obj and str(r.get(c_obj, "")).strip().lower() not in ("dollars", "tbl", ""):
                continue
            if c_lev and str(r.get(c_lev, "")).strip().lower() not in \
                    (("1", "true", "yes", "on") if levers else ("0", "false", "no", "off", "")):
                continue
            if c_status and str(r.get(c_status, "")).strip().upper() in BAD_STATUS:
                continue
            e, s, v = _f(r.get(c_e)), _f(r.get(c_s)), _f(r.get(c_v))
            if None in (e, s, v):
                continue
            carbon = _f(r.get(c_carbon)) if c_carbon else None
            kg = _f(r.get(c_kg)) if c_kg else None
            if kg is None and carbon is not None and carbon <= 0:
                skipped.append(f"{name}: carbon A$0/t row (F_env = 0, so its kg cannot be "
                               f"recovered without a kg column)")
                continue
            if kg is None:
                if carbon:                       # F_env = kg x (A$/t / 1000)
                    kg = v * 1000.0 / carbon
                elif kg_per_aud:
                    kg = v * kg_per_aud          # same carbon price as the NSGA runs
                else:
                    kg = v
            src = " ".join(x for x in (
                str(r.get(c_solver, "")).strip() if c_solver else "",
                f"carbon A${carbon:g}/t" if carbon is not None else "",
                f"[{name}]") if x)
            P.append([e, s, kg, v])
            meta.append(dict(source=src, total=e + s + v, shifts="", agency="", under="",
                             penalty=""))
            n_here += 1
        if n_here:
            used.append(f"{name} ({n_here} row{'s' if n_here > 1 else ''})")
    for msg in skipped:
        print(f"  weighted sum  skipped {msg}")
    if not P:
        return None, None, used
    P = np.array(P)
    _, idx = np.unique(np.round(P[:, [0, 1, 3]], 4), axis=0, return_index=True)
    idx = sorted(idx)
    return P[idx], [meta[i] for i in idx], used


# ------------------------------------------------------------------ static PNG
def plot_png(series, title, path, surface):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    plt.rcParams.update({"font.size": 9, "axes.edgecolor": MUTED, "axes.labelcolor": INK,
                         "xtick.color": MUTED, "ytick.color": MUTED, "text.color": INK})
    fig = plt.figure(figsize=(15, 7.5))
    gs = fig.add_gridspec(3, 2, width_ratios=[1.55, 1], hspace=0.55, wspace=0.38)
    ax3 = fig.add_subplot(gs[:, 0], projection="3d")
    axes2 = [fig.add_subplot(gs[k, 1]) for k in range(3)]
    pairs = [(0, 1), (0, 2), (1, 2)]

    for s in series:
        st, P = STYLE[s["key"]], s["P"]
        ax3.scatter(P[:, 0], P[:, 1], P[:, 2], s=s["size"], c=st["color"], marker=st["mpl"],
                    alpha=s["alpha"], edgecolors=st["edge"], linewidths=0.6, depthshade=False,
                    label=s["legend"])
        for ax, (i, j) in zip(axes2, pairs):
            ax.scatter(P[:, i], P[:, j], s=s["size"] * 0.7, c=st["color"], marker=st["mpl"],
                       alpha=s["alpha"], edgecolors=st["edge"], linewidths=0.5)
        if surface and s["key"] in ("nsga2", "nsga3", "eps") and len(P) >= 4:
            try:
                ax3.plot_trisurf(P[:, 0], P[:, 1], P[:, 2], color=st["color"], alpha=0.18,
                                 linewidth=0, antialiased=True)
            except Exception:
                pass
        if s["star"]:
            b = int((P[:, 0] + P[:, 1] + P[:, 3]).argmin())
            ax3.scatter([P[b, 0]], [P[b, 1]], [P[b, 2]], s=220, marker="*", c=st["color"],
                        edgecolors=INK, linewidths=0.8, depthshade=False)
            for ax, (i, j) in zip(axes2, pairs):
                ax.scatter([P[b, i]], [P[b, j]], s=150, marker="*", c=st["color"],
                           edgecolors=INK, linewidths=0.8, zorder=5)

    ax3.set_xlabel(LAB[0], labelpad=10)
    ax3.set_ylabel(LAB[1], labelpad=10)
    ax3.set_zlabel(LAB[2], labelpad=10)
    ax3.view_init(elev=22, azim=-55)
    for pane in (ax3.xaxis, ax3.yaxis, ax3.zaxis):
        pane.pane.set_facecolor((1, 1, 1, 0))
        pane.pane.set_edgecolor(GRID)
    ax3.legend(loc="upper left", frameon=False)
    for ax, (i, j) in zip(axes2, pairs):
        ax.set_xlabel(LAB[i])
        ax.set_ylabel(LAB[j])
        ax.grid(True, color=GRID, linewidth=0.6)
        ax.set_axisbelow(True)
        for sp in ("top", "right"):
            ax.spines[sp].set_visible(False)
        ax.ticklabel_format(style="plain", useOffset=False)
    fig.suptitle(title, x=0.02, ha="left", fontsize=12, fontweight="bold")
    fig.text(0.02, 0.925, "Each point is one roster. Star = lowest total (F_econ + F_soc + "
             "F_env) of each NSGA front. Lower is better on every axis.", color=MUTED)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    fig.savefig(path, dpi=160, bbox_inches="tight")
    plt.close(fig)
    print(f"  saved {path}")


# ------------------------------------------------------------------ interactive HTML
def plot_html(series, title, path, surface):
    try:
        import plotly.graph_objects as go
        from plotly.subplots import make_subplots
    except ImportError:
        print("  (interactive HTML skipped: run  pip install plotly  to get it)")
        return None

    fig = make_subplots(
        rows=3, cols=2, column_widths=[0.62, 0.38], horizontal_spacing=0.09,
        vertical_spacing=0.1,
        specs=[[{"type": "scene", "rowspan": 3}, {"type": "xy"}],
               [None, {"type": "xy"}], [None, {"type": "xy"}]])
    pairs = [(0, 1), (0, 2), (1, 2)]

    def hover(s):
        cd, txt = [], []
        for m, p in zip(s["meta"], s["P"]):
            extra = ""
            if m.get("shifts") not in ("", None):
                extra = (f"<br>shifts {m['shifts']} · agency {m['agency']} · "
                         f"understaffed {m['under']}")
            cd.append([p[0], p[1], p[2], p[3], m["total"]])
            txt.append(f"<b>{STYLE[s['key']]['label']}</b> {m['source']}{extra}")
        tmpl = ("%{text}<br>F_econ  A$%{customdata[0]:,.2f}<br>F_soc   A$%{customdata[1]:,.2f}"
                "<br>F_env   A$%{customdata[3]:,.4f}  (%{customdata[2]:,.1f} kg)"
                "<br><b>total   A$%{customdata[4]:,.2f}</b><extra></extra>")
        return cd, txt, tmpl

    for s in series:
        st, P = STYLE[s["key"]], s["P"]
        cd, txt, tmpl = hover(s)
        grp = s["group"]
        fig.add_trace(go.Scatter3d(
            x=P[:, 0], y=P[:, 1], z=P[:, 2], mode="markers", name=s["legend"],
            legendgroup=grp, customdata=cd, text=txt, hovertemplate=tmpl,
            marker=dict(size=4.5 if s["alpha"] > 0.5 else 3, color=st["color"],
                        symbol=st["p3"], opacity=s["alpha"],
                        line=dict(color=st["edge"], width=0.6))), row=1, col=1)
        if surface and s["key"] in ("nsga2", "nsga3", "eps") and len(P) >= 4:
            fig.add_trace(go.Mesh3d(x=P[:, 0], y=P[:, 1], z=P[:, 2], color=st["color"],
                                    opacity=0.18, legendgroup=grp, showlegend=False,
                                    hoverinfo="skip"), row=1, col=1)
        for k, (i, j) in enumerate(pairs):
            fig.add_trace(go.Scatter(
                x=P[:, i], y=P[:, j], mode="markers", legendgroup=grp, showlegend=False,
                customdata=cd, text=txt, hovertemplate=tmpl,
                marker=dict(size=8 if s["alpha"] > 0.5 else 6, color=st["color"],
                            symbol=st["p2"], opacity=s["alpha"],
                            line=dict(color=st["edge"], width=0.8))), row=k + 1, col=2)
        if s["star"]:
            b = int((P[:, 0] + P[:, 1] + P[:, 3]).argmin())
            fig.add_trace(go.Scatter3d(
                x=[P[b, 0]], y=[P[b, 1]], z=[P[b, 2]], mode="markers", legendgroup=grp,
                showlegend=False, customdata=[cd[b]], text=[txt[b] + "  — lowest total"],
                hovertemplate=tmpl,
                marker=dict(size=11, color="rgba(0,0,0,0)", symbol="diamond-open",
                            line=dict(color=INK, width=3))), row=1, col=1)
            for k, (i, j) in enumerate(pairs):
                fig.add_trace(go.Scatter(
                    x=[P[b, i]], y=[P[b, j]], mode="markers", legendgroup=grp,
                    showlegend=False, customdata=[cd[b]], text=[txt[b] + "  — lowest total"],
                    hovertemplate=tmpl,
                    marker=dict(size=17, color=st["color"], symbol="star",
                                line=dict(color=INK, width=1))), row=k + 1, col=2)

    axis = dict(gridcolor=GRID, zeroline=False, tickformat=",", title_font=dict(size=12))
    fig.update_layout(
        title=dict(text=f"<b>{title}</b><br><span style='font-size:12px;color:{MUTED}'>"
                        "Drag to rotate · scroll to zoom · hover a point for its numbers · "
                        "click a legend entry to hide/show it</span>", x=0.01, y=0.985,
                   yanchor="top"),
        template="plotly_white", height=840, margin=dict(l=10, r=20, t=125, b=40),
        font=dict(family="Inter, Segoe UI, Arial, sans-serif", color=INK, size=12),
        legend=dict(orientation="h", yanchor="bottom", y=1.035, x=0.0, itemsizing="constant"),
        hoverlabel=dict(bgcolor="white", font=dict(family="Consolas, monospace", size=12)),
        scene=dict(xaxis=dict(title=LAB[0], **axis), yaxis=dict(title=LAB[1], **axis),
                   zaxis=dict(title=LAB[2], **axis), aspectmode="cube",
                   camera=dict(eye=dict(x=-1.6, y=-1.6, z=0.9))))
    for k, (i, j) in enumerate(pairs):
        fig.update_xaxes(title_text=LAB[i], row=k + 1, col=2, **axis)
        fig.update_yaxes(title_text=LAB[j], row=k + 1, col=2, **axis)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    fig.write_html(path, include_plotlyjs=True, full_html=True,
                   config=dict(displaylogo=False, scrollZoom=True,
                               toImageButtonOptions=dict(format="png", scale=2)))
    print(f"  saved {path}")
    return path


def draw(series, title, stem, out, surface, show):
    base = os.path.join(out, "plots", stem)
    plot_png(series, title, base + ".png", surface)
    html = plot_html(series, title, base + ".html", surface)
    if show and html:
        webbrowser.open("file:///" + os.path.abspath(html).replace("\\", "/"))


def read_runs(out):
    p = os.path.join(out, "nsga_runs.csv")
    if not os.path.exists(p):
        return []
    with open(p, newline="") as f:
        return list(csv.DictReader(f))


def main():
    ap = argparse.ArgumentParser(description="Plot Pareto fronts (NSGA, e-constraint, "
                                             "weighted sum)")
    ap.add_argument("--instance", default=None, help="e.g. 1 or Instance1")
    ap.add_argument("--range", nargs=2, type=int, metavar=("FIRST", "LAST"))
    ap.add_argument("--only", nargs="*", help="e.g. --only 1 5 9")
    ap.add_argument("--run", default=None, help="plot one seed's front, by run id, e.g. "
                    "Instance1_levers_nsga2_s3")
    ap.add_argument("--algo", choices=["nsga2", "nsga3", "both", "none"], default="both",
                    help="which NSGA fronts to draw ('none' = exact methods only)")
    ap.add_argument("--base", action="store_true", help="runs made WITHOUT --levers "
                    "(default: the --levers runs)")
    ap.add_argument("--seeds", action="store_true", help="every seed's front separately "
                    "(faint) instead of the merged front")
    ap.add_argument("--no-eps", action="store_true", help="leave out the e-constraint front")
    ap.add_argument("--no-ws", action="store_true", help="leave out the weighted-sum points")
    ap.add_argument("--surface", action="store_true", help="translucent surface through "
                    "each front (rough: triangulated over cost x social)")
    ap.add_argument("--out", default="results_nsga", help="the tbl_nsga.py output folder")
    ap.add_argument("--no-show", action="store_true", help="write the files, open nothing")
    a = ap.parse_args()

    out = a.out if os.path.isabs(a.out) else os.path.join(HERE, a.out)
    runs = read_runs(out)
    show = not a.no_show

    if a.run:
        stem = os.path.splitext(os.path.basename(a.run))[0]
        hit = [r for r in runs if r.get("run_id") == stem
               or os.path.splitext(os.path.basename(r["front_file"]))[0] == stem]
        if not hit:
            raise SystemExit(f"run '{stem}' not found in {out}\\nsga_runs.csv")
        r = hit[0]
        P, meta = read_front(os.path.join(HERE, r["front_file"]), f"seed {r['seed']}",
                             r["seed"])
        if P is None:
            raise SystemExit(f"front file missing: {r['front_file']}")
        key = r["algorithm"]
        draw([dict(key=key, P=P, meta=meta, size=34, alpha=0.9, star=True, group=key,
                   legend=f"{STYLE[key]['label']} seed {r['seed']} ({len(P)})")],
             f"{r['instance']} — {STYLE[key]['label']}, seed {r['seed']} "
             f"({'levers' if r['levers'] == '1' else 'base'})", stem, out, a.surface, show)
        return

    lev = "0" if a.base else "1"
    tag = "base" if a.base else "levers"
    if a.only:
        names = [f"Instance{inum(t)}" for t in a.only]
    elif a.range:
        names = [f"Instance{k}" for k in range(a.range[0], a.range[1] + 1)]
    elif a.instance:
        names = [f"Instance{inum(a.instance)}"]
    else:
        names = sorted({r["instance"] for r in runs if r["levers"] == lev}, key=inum)
    if not names:
        raise SystemExit("nothing to plot -- give --instance, --range or --only")
    algos = {"both": ["nsga2", "nsga3"], "none": []}.get(a.algo, [a.algo])

    for inst in names:
        print(f"\n{inst} [{tag}]")
        series, kg_per_aud = [], None
        for al in algos:
            rs = [r for r in runs if inum(r["instance"]) == inum(inst) and r["levers"] == lev
                  and r["algorithm"] == al]
            got = []
            for r in rs:
                P, meta = read_front(os.path.join(HERE, r["front_file"]), f"seed {r['seed']}",
                                     r["seed"])
                if P is not None:
                    got.append((r, P, meta))
            if not got:
                continue
            for _, P, _m in got:                       # kg per A$ of F_env at NSGA's price
                ok = P[:, 3] > 0
                if ok.any():
                    kg_per_aud = float(np.median(P[ok, 2] / P[ok, 3]))
            lbl = STYLE[al]["label"]
            if a.seeds:
                for r, P, meta in got:
                    series.append(dict(key=al, P=P, meta=meta, size=18, alpha=0.4,
                                       star=False, group=f"{al}{r['seed']}",
                                       legend=f"{lbl} seed {r['seed']} ({len(P)})"))
            else:
                P = np.concatenate([g[1] for g in got])
                meta = [m for g in got for m in g[2]]
                P, meta = nondominated(P, meta)
                series.append(dict(key=al, P=P, meta=meta, size=34, alpha=0.9, star=True,
                                   group=al, legend=f"{lbl} ({len(P)}, {len(got)} seeds)"))
            print(f"  {lbl:<13} {len(got)} run(s)")
        if not a.no_eps and not a.base:
            P, meta, note = read_eps(inst, kg_per_aud)
            if P is not None:
                P, meta = nondominated(P, meta)
                series.append(dict(key="eps", P=P, meta=meta, size=40, alpha=0.95,
                                   star=False, group="eps",
                                   legend=f"e-constraint ({len(P)})"))
            print(f"  e-constraint  {note}")
        if not a.no_ws:
            P, meta, used = read_weighted_sum(inst, not a.base, kg_per_aud)
            if P is not None:
                series.append(dict(key="ws", P=P, meta=meta, size=70, alpha=1.0, star=False,
                                   group="ws", legend=f"Weighted sum ({len(P)})"))
                print(f"  weighted sum  {len(P)} point(s) from: {', '.join(used)}")
                for p, m in zip(P, meta):
                    print(f"                  A${p[0]:>14,.2f} {p[1]:>11,.2f} "
                          f"{p[2]:>10,.1f} kg   {m['source']}")
            else:
                print("  weighted sum  none found in results\\summary_*.csv")
        eps_s = next((x for x in series if x["key"] == "eps"), None)
        if eps_s is not None:
            for x in series:
                if x["key"] in ("nsga2", "nsga3") and x["star"]:
                    c1, c2 = coverage(eps_s["P"], x["P"]), coverage(x["P"], eps_s["P"])
                    print(f"  coverage      {STYLE[x['key']]['label']} merged: {c1:.0%} of its "
                          f"{len(x['P'])} rosters are matched or beaten by an e-constraint "
                          f"roster; it matches or beats {c2:.0%} of the {len(eps_s['P'])} "
                          f"e-constraint rosters")
        if not series:
            print("  nothing to plot")
            continue
        what = ("seeds" if a.seeds else "merged") + \
               ("" if a.algo == "both" else f"_{a.algo}")
        title = (f"{inst} — Pareto front{'s by seed' if a.seeds else 's'}  [{tag}]")
        draw(series, title, f"{inst}_{tag}_{what}", out, a.surface, show)


if __name__ == "__main__":
    main()
