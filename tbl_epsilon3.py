"""
tbl_epsilon3.py -- THREE-objective epsilon-constraint pilot. Measures the front's shape
and, more importantly, measures whether the grid is worth scaling up.

    python tbl_epsilon3.py --instance Instance1 --K 6
    python tbl_epsilon3.py --instance Instance1 --K 8 --time 60
    python tbl_epsilon3.py --instance Instance5 --K 5 --time 300

Writes results\\<Instance>_front3.csv -- one row per grid cell, and a summary telling you
how many DISTINCT non-dominated points you actually got for the solves you paid for.

THE MODEL
    min  F_econ
    s.t. F_soc + s1 = eps_soc          <- equality + surplus, not <=
         F_env + s2 = eps_env
         ... every original rostering constraint (shared build_core) ...

F_econ is always the minimised objective; the other two are bounded and swept over a
K x K grid. One solve per cell, one point per feasible cell.

WHY EQUALITY-PLUS-SURPLUS, NOT '<='
    With a plain '<=' the solver may stop anywhere at or below the cap, which manufactures
    WEAKLY efficient points -- same cost, needlessly worse on the bounded pillars. Those
    would contaminate exactly the measurement this script exists to make, because they
    inflate the duplicate count with an artefact of the method rather than a property of
    the problem. So the objective is lexicographic: minimise cost first, then maximise the
    surpluses. Done exactly in integers as  min F_econ*W - (s1+s2)  with W larger than the
    total surplus range, so one cent of cost always outranks all of the surplus.
    (AUGMECON2, Mavrotas & Florios 2013.)

WHAT THE SUMMARY TELLS YOU
    cells solved        what you paid for
    infeasible cells    the dead corner -- tightening two pillars at once admits no roster
    distinct points     unique (F_econ, F_soc, F_env) triples
    non-dominated       after filtering: the actual front
    duplicate rate      how much of the grid was wasted
    per-axis spread     min/max gap between consecutive points on each axis, and the
                        ratio -- a ratio near 1 is an even spread, 5x+ is 'cooked'
    axis movement       the range each pillar actually spans across the front. If one is
                        near zero, the front is effectively lower-dimensional and a finer
                        grid on that axis buys you nothing.

Uses CP-SAT via tbl_ortools.build_core, so the feasible region is provably identical to
your weighted-sum runs and to tbl_epsilon.py. If the pilot says the grid is worth scaling,
port it to Gurobi -- roughly 17x faster on this model by your own measurements.
"""
import argparse, csv, itertools, os, time
from ortools.sat.python import cp_model
import tbl_data as D
import tbl_ortools as ORT

SCALE = 10_000          # 4 decimal places on every pillar; CP-SAT is integer-only
INT64_SAFE = 4e18


def _pillars(inst, C, core):
    """F_econ, F_soc, F_env as separate INTEGER CP-SAT expressions (A$ x SCALE).

    Mirrors tbl_data.evaluate() term for term. evaluate() still produces the reported
    numbers; these expressions only steer the search.
    """
    P, m, x, a, o = C.P, core["m"], core["x"], core["a"], core["o"]
    under, over, sen_short, work = core["under"], core["over"], core["sen_short"], core["work"]
    n = len(inst.staff)

    def S(v): return int(round(v * SCALE))

    fair_s = inst.NIGHT or inst.shifts
    cnt = {i: sum(x[i, d, s] for d in inst.days for s in fair_s) for i in inst.staff}
    tot = sum(cnt.values())
    # DEV[i] = n * |cnt[i] - mean|, kept integer by multiplying through by n; the /n is
    # undone in the coefficient below. Same trick as tbl_ortools.
    DEV = {i: m.NewIntVar(0, n * inst.n_days, f"E3_DEV_{i}") for i in inst.staff}
    for i in inst.staff:
        m.Add(DEV[i] >= n * cnt[i] - tot)
        m.Add(DEV[i] >= tot - n * cnt[i])
    fat = {}
    if inst.NIGHT:
        for i in inst.staff:
            for d in range(inst.n_days - 2):
                fat[i, d] = m.NewBoolVar(f"E3_g_{i}_{d}")
                m.Add(fat[i, d] >= sum(x[i, d+t, s] for t in range(3) for s in inst.NIGHT) - 2)

    F_econ = (sum(S(C.wage[i][s]) * x[i, d, s]
                  for i in inst.staff for d in inst.days for s in inst.shifts)
            + sum(S(C.ot_hourly[i] / 60.0) * o[i] for i in inst.staff)
            + sum(S(P["agency_cost_per_shift"]) * a[k] for k in inst.R)
            + sum(S(P["rho_understaffing"]) * under[k] for k in inst.R)
            + sum(S(P["rho_senior_shortfall"]) * t for t in sen_short.values()))
    F_soc = (sum(S(C.k_pref * wt) * (1 - x[i, d, s]) for (i, d, s, wt) in inst.on_req)
           + sum(S(C.k_pref * wt) * x[i, d, s]       for (i, d, s, wt) in inst.off_req)
           + sum(S(C.k_fair / n) * DEV[i] for i in inst.staff)
           + sum(S(P["kappa_fatigue"]) * t for t in fat.values()))
    F_env = (sum(S(C.p_kg * C.kg_day[i]) * work(i, d) for i in inst.staff for d in inst.days)
           + sum(S(C.p_kg * P["agency_travel_kg"]) * a[k] for k in inst.R)
           + sum(S(C.p_kg * P["e_anc_kg_per_over_shift"]) * over[k] for k in inst.R))
    return F_econ, F_soc, F_env


def _run(m, tl, workers):
    """-> (solver, usable?, STATUS NAME). The name matters: a cell that merely hit the
    time limit comes back FEASIBLE, and a FEASIBLE cell is not a proven front point."""
    sv = cp_model.CpSolver()
    sv.parameters.max_time_in_seconds = tl
    sv.parameters.num_search_workers = workers
    st = sv.Solve(m)
    return (sv, st in (cp_model.OPTIMAL, cp_model.FEASIBLE), sv.StatusName(st))


def _score(inst, C, sv, core):
    """Extract the roster and score it with the SAME evaluate() everything else uses."""
    x, a, o = core["x"], core["a"], core["o"]
    roster = {(i, d): next((s for s in inst.shifts if sv.Value(x[i, d, s])), "")
              for i in inst.staff for d in inst.days}
    ag = {k: sv.Value(a[k]) for k in inst.R}
    ot = {i: sv.Value(o[i]) for i in inst.staff}
    return roster, D.evaluate(inst, C, roster, ag, ot, None)


def anchors(inst, C, tl, workers, quiet=False):
    """Payoff table: minimise each pillar in turn, lexicographically tie-broken by the
    sum of the other two so the anchor is a single well-defined point rather than an
    arbitrary member of a large tied set."""
    out = {}
    for name in ("econ", "soc", "env"):
        core = ORT.build_core(inst, C, True)
        fe, fs, fv = _pillars(inst, C, core)
        m = core["m"]
        primary = {"econ": fe, "soc": fs, "env": fv}[name]
        others = [v for k, v in (("econ", fe), ("soc", fs), ("env", fv)) if k != name]
        m.Minimize(primary)
        sv, ok, st = _run(m, tl, workers)
        if not ok: raise SystemExit(f"anchor '{name}' unsolved -- raise --time")
        z = sv.Value(primary)
        m.Add(primary <= z)
        m.Minimize(others[0] + others[1])
        sv, ok, st = _run(m, tl, workers)
        _, e = _score(inst, C, sv, core)
        out[name] = e
        if not quiet:
            print(f"  min {name:<5} -> econ {e['F_econ']:>13,.2f}   soc {e['F_soc']:>12,.2f}"
                  f"   env {e['F_env']:>9,.4f}")
    return out


ap = argparse.ArgumentParser()
ap.add_argument("--instance", default=None, help="one instance, e.g. Instance1 or 1")
ap.add_argument("--range", nargs=2, type=int, metavar=("FIRST", "LAST"), default=None,
                help="pilot instances FIRST..LAST, e.g. --range 1 5")
ap.add_argument("--only", nargs="*", default=None,
                help="pilot these, e.g. --only 1 5 9  (bare numbers or full names)")
ap.add_argument("--K", type=int, default=6, help="grid levels on EACH bounded pillar; "
                                                 "total cells = K*K")
ap.add_argument("--time", type=float, default=60., help="seconds per cell")
ap.add_argument("--workers", type=int, default=8)
ap.add_argument("--price", action="append", default=[], metavar="KEY=VALUE")
a = ap.parse_args()


def inst_no_s(t):
    d = "".join(c for c in str(t) if c.isdigit())
    return int(d) if d else 0


def pilot(name):
    """Run the whole pilot for ONE instance. Returns a summary dict for the cross-instance
    table, or None if the instance could not be pilotted. Every instance writes its own
    <Instance>_front3.csv and .log, so a batch never overwrites an earlier result."""
    inst = D.load_instance(name)
    T, P = D.load_tbl(inst)
    D.apply_price_overrides(P, a.price)
    C = D.Coef(inst, T, P)

    log = os.path.join(D.HERE, "results", f"{inst.name}_front3.log")
    os.makedirs(os.path.dirname(log), exist_ok=True)
    Tee = getattr(D, "Tee", None)
    ctx = Tee(log) if Tee else open(os.devnull, "w")

    with ctx:
        print(f"3-OBJECTIVE EPSILON-CONSTRAINT PILOT -- {inst.name} "
              f"({len(inst.staff)} staff, {inst.n_days} days)")
        print(f"min F_econ   s.t.  F_soc <= eps_soc,  F_env <= eps_env")
        print(f"grid {a.K} x {a.K} = {a.K*a.K} cells, {a.time:g}s each "
              f"-> worst case {a.K*a.K*a.time/60:.0f} min\n")

        print("PAYOFF TABLE")
        A = anchors(inst, C, a.time, a.workers)

        soc_lo, soc_hi = A["soc"]["F_soc"], max(A[k]["F_soc"] for k in A)
        env_lo, env_hi = A["env"]["F_env"], max(A[k]["F_env"] for k in A)
        print(f"\n  eps_soc range  {soc_lo:,.2f} .. {soc_hi:,.2f}   (span {soc_hi-soc_lo:,.2f})")
        print(f"  eps_env range  {env_lo:,.4f} .. {env_hi:,.4f}   (span {env_hi-env_lo:,.4f})")
        if soc_hi - soc_lo < 1e-6 or env_hi - env_lo < 1e-6:
            print("\n  *** one pillar does not vary across the anchors: the front is")
            print("      lower-dimensional than 3, and gridding that axis buys nothing. ***")

        K = max(2, a.K)
        soc_grid = [soc_hi - t * (soc_hi - soc_lo) / (K - 1) for t in range(K)]
        env_grid = [env_hi - t * (env_hi - env_lo) / (K - 1) for t in range(K)]

        # Print EVERY column the CSV carries. It is a wide table -- maximise the window --
        # but seeing 'total', 'penalty' and 'kg_co2e' beside the pillars while the grid runs
        # is worth the width, and it matches the file exactly so there is no second format
        # to reconcile.
        hdr = (f"\n{'eps_soc':>11}{'eps_env':>10}{'status':>11}{'solve':>7}"
               f"{'F_econ':>13}{'F_soc':>11}{'F_env':>9}{'total':>13}"
               f"{'penalty':>9}{'kg_co2e':>10}{'sh':>5}{'ag':>4}{'un':>4}")
        print(hdr); print("-" * len(hdr))

        rows, t0 = [], time.time()
        n_infeas = 0
        for es, ev in itertools.product(soc_grid, env_grid):
            core = ORT.build_core(inst, C, True)
            fe, fs, fv = _pillars(inst, C, core)
            m = core["m"]
            ES, EV = int(round(es * SCALE)), int(round(ev * SCALE))
            r_soc = max(1, int(round((soc_hi - soc_lo) * SCALE)))
            r_env = max(1, int(round((env_hi - env_lo) * SCALE)))
            s1 = m.NewIntVar(0, r_soc, "s1")
            s2 = m.NewIntVar(0, r_env, "s2")
            m.Add(fs + s1 == ES)
            m.Add(fv + s2 == EV)
            W = r_soc + r_env + 1
            # Guard: the lexicographic trick must not overflow int64.
            if soc_hi * SCALE * W > INT64_SAFE:
                raise SystemExit("lexicographic weight would overflow -- lower SCALE")
            m.Minimize(fe * W - (s1 + s2))
            tc = time.time()
            sv, ok, st = _run(m, a.time, a.workers)
            dt = time.time() - tc
            if not ok:
                n_infeas += 1
                print(f"{es:>11,.2f}{ev:>10,.4f}{'INFEASIBLE':>11}{dt:>7.1f}")
                rows.append(dict(eps_soc=round(es, 4), eps_env=round(ev, 6),
                                 status="INFEASIBLE", solve_s=round(dt, 2)))
                continue
            _, e = _score(inst, C, sv, core)
            print(f"{es:>11,.2f}{ev:>10,.4f}{st:>11}{dt:>7.1f}"
                  f"{e['F_econ']:>13,.2f}{e['F_soc']:>11,.2f}{e['F_env']:>9,.4f}"
                  f"{e['total']:>13,.2f}{e['penalty']:>9,.0f}{e['kg']:>10,.2f}"
                  f"{e['shifts']:>5}{e['n_agency']:>4}{e['n_under']:>4}")
            rows.append(dict(eps_soc=round(es, 4), eps_env=round(ev, 6), status=st,
                             solve_s=round(dt, 2), F_econ=round(e["F_econ"], 2),
                             F_soc=round(e["F_soc"], 2), F_env=round(e["F_env"], 4),
                             total=round(e["total"], 2), penalty=round(e["penalty"]),
                             kg_co2e=round(e["kg"], 3), shifts=e["shifts"],
                             agency=e["n_agency"], understaffed=e["n_under"]))
        print("-" * len(hdr))

        # ---------------------------------------------------------------- analysis
        got = [r for r in rows if r["status"] in ("OPTIMAL", "FEASIBLE")]
        proven = [r for r in got if r["status"] == "OPTIMAL"]
        timed_out = [r for r in got if r["status"] != "OPTIMAL"]
        trip = [(r["F_econ"], r["F_soc"], r["F_env"]) for r in got]
        uniq = sorted(set(trip))

        def dominates(p, q):
            return all(p[k] <= q[k] for k in range(3)) and any(p[k] < q[k] for k in range(3))
        nd = [p for p in uniq if not any(dominates(q, p) for q in uniq if q != p)]

        print(f"\nWHAT YOU GOT FOR {len(rows)} SOLVE(S)")
        print(f"  cells solved                 {len(rows)}")
        print(f"  INFEASIBLE cells             {n_infeas}   "
              f"({n_infeas/len(rows)*100:.0f}% of the grid is a dead corner)")
        print(f"  feasible cells               {len(got)}")
        print(f"  DISTINCT points              {len(uniq)}")
        print(f"  NON-DOMINATED (the front)    {len(nd)}")
        print(f"  proven optimal cells         {len(proven)}")
        if timed_out:
            print(f"  TIMED OUT cells              {len(timed_out)}   <-- these points are NOT")
            print(f"                               provably on the front. Raise --time or")
            print(f"                               exclude them before claiming a Pareto set.")
        dup = len(got) - len(uniq)
        print(f"  duplicate solves             {dup}"
              f"   ({dup/len(got)*100:.0f}% of feasible cells repeated a point)"
              if got else "")
        if uniq:
            eff = len(nd) / len(rows) * 100
            print(f"  EFFICIENCY                   {eff:.0f}% of solves produced a front point")

        for k, nm in ((0, "F_econ"), (1, "F_soc"), (2, "F_env")):
            vals = sorted({p[k] for p in nd})
            span = vals[-1] - vals[0] if len(vals) > 1 else 0.0
            print(f"\n  {nm}: {len(vals)} distinct level(s), span {span:,.4f}")
            if len(vals) > 2:
                gaps = [vals[i+1] - vals[i] for i in range(len(vals)-1)]
                ratio = max(gaps) / min(gaps) if min(gaps) > 0 else float("inf")
                print(f"     gap min {min(gaps):,.4f}  max {max(gaps):,.4f}  "
                      f"ratio {ratio:,.1f}x   (1x = perfectly even)")
                print(f"     a uniform spread would be {span/(len(vals)-1):,.4f} apart")
            elif len(vals) <= 2:
                print(f"     *** this axis barely moves -- the front is effectively "
                      f"{3 - 1} dimensional ***")

        print(f"\ntotal wall clock {(time.time()-t0)/60:.1f} min")

    out = os.path.join(D.HERE, "results", f"{inst.name}_front3.csv")
    cols = ["eps_soc", "eps_env", "status", "solve_s", "F_econ", "F_soc", "F_env", "total",
            "penalty", "kg_co2e", "shifts", "agency", "understaffed"]
    def _w(p):
        with open(p, "w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=cols); w.writeheader()
            for r in rows: w.writerow({c: r.get(c, "") for c in cols})
    sw = getattr(D, "safe_write", None)
    out = (sw(out, _w, "3-obj front") or out) if sw else (_w(out) or out)
    print(f"\n    grid written to {out}")
    return dict(instance=inst.name, staff=len(inst.staff), days=inst.n_days,
                cells=len(rows), infeasible=n_infeas, distinct=len(uniq),
                front=len(nd), proven=len(proven), timed_out=len(timed_out),
                minutes=(time.time() - t0) / 60.0,
                econ_lo=min((p[0] for p in nd), default=None),
                econ_hi=max((p[0] for p in nd), default=None),
                soc_levels=len({p[1] for p in nd}), env_levels=len({p[2] for p in nd}))


# ------------------------------------------------------------------ driver
prepared = D.list_instances()
if a.only:
    want = {inst_no_s(t) for t in a.only}
    names = [n for n in prepared if inst_no_s(n) in want]
elif a.range:
    lo, hi = a.range
    names = [n for n in prepared if lo <= inst_no_s(n) <= hi]
elif a.instance:
    key = a.instance if not str(a.instance).isdigit() else f"Instance{a.instance}"
    names = [n for n in prepared if n.lower() == key.lower()]
else:
    names = [n for n in prepared if inst_no_s(n) == 1]
names.sort(key=inst_no_s)
if not names:
    raise SystemExit(f"nothing matched.\n   prepared: {prepared}")

per = a.K * a.K + 6           # cells + 6 anchor solves
print(f"PILOT: {len(names)} instance(s) x {per} solve(s) x {a.time:g}s "
      f"-> worst case {len(names)*per*a.time/3600:.1f} h")
print(f"  {', '.join(names)}\n")

SUM = []
for nm in names:
    try:
        r = pilot(nm)
        if r: SUM.append(r)
    except KeyboardInterrupt:
        print(f"\n[interrupted] stopping after {len(SUM)} instance(s); "
              f"each completed instance already wrote its own CSV.")
        break
    except BaseException as ex:
        print(f"\n[!] {nm} failed: {str(ex).splitlines()[0][:80]}")

if len(SUM) > 1:
    h = (f"\n{'instance':<12}{'staff':>6}{'days':>5}{'cells':>7}{'infeas':>7}{'distinct':>9}"
         f"{'front':>7}{'proven':>7}{'timeout':>8}{'soc lv':>7}{'env lv':>7}{'min':>7}")
    print("\n" + "=" * len(h.strip()))
    print("  CROSS-INSTANCE SUMMARY")
    print("=" * len(h.strip()))
    print(h); print("-" * len(h.strip()))
    for r in SUM:
        print(f"{r['instance']:<12}{r['staff']:>6}{r['days']:>5}{r['cells']:>7}"
              f"{r['infeasible']:>7}{r['distinct']:>9}{r['front']:>7}{r['proven']:>7}"
              f"{r['timed_out']:>8}{r['soc_levels']:>7}{r['env_levels']:>7}"
              f"{r['minutes']:>7.1f}")
    print("-" * len(h.strip()))
    tc = sum(r['cells'] for r in SUM); tf = sum(r['front'] for r in SUM)
    print(f"{'TOTAL':<12}{'':>6}{'':>5}{tc:>7}{sum(r['infeasible'] for r in SUM):>7}"
          f"{sum(r['distinct'] for r in SUM):>9}{tf:>7}"
          f"{sum(r['proven'] for r in SUM):>7}{sum(r['timed_out'] for r in SUM):>8}"
          f"{'':>7}{'':>7}{sum(r['minutes'] for r in SUM):>7.1f}")
    print(f"\n  overall efficiency: {tf/tc*100:.0f}% of solves produced a front point")
    to = sum(r['timed_out'] for r in SUM)
    if to:
        print(f"  {to} cell(s) TIMED OUT -- those points are not provably efficient. "
              f"Raise --time for the instances flagged above.")
