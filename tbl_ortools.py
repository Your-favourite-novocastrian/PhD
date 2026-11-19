"""
TBL-HWSP -- OR-Tools CP-SAT runner.

    python tbl_ortools.py --instance Instance1                        stage 1 (should print 607)
    python tbl_ortools.py --instance Instance1 --objective dollars    stage 2
    python tbl_ortools.py --instance Instance1 --objective dollars --levers   stage 3
    python tbl_ortools.py --instance Instance21 --gap 0.01 --time 600
    python tbl_ortools.py --instance Instance1 --carbon 500

STAGES exist so you add one thing at a time and always know what caused a change:
  stage 1  identical model to the published benchmark; only the REPORTING is new
  stage 2  one line differs -- what goes inside Minimize()
  stage 3  adds agency, overtime and skill-mix variables/constraints
"""
import argparse, os, time
from ortools.sat.python import cp_model
import tbl_data as D

SCALE = 10_000                      # CP-SAT takes integer coefficients only
def M(aud): return int(round(aud * SCALE))

def build_core(inst, C, levers):
    """Every variable and every CONSTRAINT -- but no objective.

    Split out so that solve() (weighted sum) and tbl_epsilon.py (epsilon-constraint)
    build provably the same feasible region. If this were copy-pasted into the epsilon
    module the two would drift the first time either was touched, and a frontier drawn
    over a different feasible set than the scalarised runs is worse than no frontier.
    """
    P = C.P
    m = cp_model.CpModel()
    UB = len(inst.staff) + 5
    x = {(i, d, s): m.NewBoolVar(f"x_{i}_{d}_{s}")
         for i in inst.staff for d in inst.days for s in inst.shifts}
    def work(i, d): return sum(x[i, d, s] for s in inst.shifts)

    # ---------------- hard constraints (identical in every stage) ----------------
    for i in inst.staff:
        for d in inst.days: m.Add(work(i, d) <= 1)
    for (i, d) in inst.days_off: m.Add(work(i, d) == 0)
    for i in inst.staff:
        for d in inst.days[:-1]:
            for s in inst.shifts:
                for t in inst.cannot_follow[s]: m.Add(x[i, d, s] + x[i, d+1, t] <= 1)
    for i in inst.staff:
        for s in inst.shifts:
            m.Add(sum(x[i, d, s] for d in inst.days) <= inst.max_shifts_of(i, s))
    for i in inst.staff:
        K = int(inst.S[i]["max_consec_shifts"])
        for st in range(inst.n_days - K):
            m.Add(sum(work(i, d) for d in range(st, st+K+1)) <= K)
    for i in inst.staff:
        mn = int(inst.S[i]["min_consec_shifts"])
        for j in range(1, mn):
            for d in range(inst.n_days - j - 1):
                m.Add(sum(work(i, d+t) for t in range(1, j+1))
                      - work(i, d) - work(i, d+j+1) <= j-1)
    for i in inst.staff:
        mn = int(inst.S[i]["min_consec_days_off"])
        for j in range(1, mn):
            for d in range(inst.n_days - j - 1):
                m.Add(work(i, d) + work(i, d+j+1)
                      - sum(work(i, d+t) for t in range(1, j+1)) <= 1)
    Wk = {(i, k): m.NewBoolVar(f"wk_{i}_{k}")
          for i in inst.staff for k in range(len(inst.weekends))}
    for i in inst.staff:
        for k, (sat, sun) in enumerate(inst.weekends):
            m.Add(Wk[i, k] >= work(i, sat))
            if sun < inst.n_days: m.Add(Wk[i, k] >= work(i, sun))
        m.Add(sum(Wk[i, k] for k in range(len(inst.weekends))) <= int(inst.S[i]["max_weekends"]))

    hours = {i: sum(inst.length_min[s] * x[i, d, s] for d in inst.days for s in inst.shifts)
             for i in inst.staff}
    for i in inst.staff: m.Add(hours[i] >= int(inst.S[i]["min_total_min"]))

    # ---------------- levers (stage 3 only) ----------------
    cap_ag = int(P["agency_max_per_shift"]) if levers else 0
    cap_ot = int(P["overtime_max_min"])     if levers else 0
    a = {k: m.NewIntVar(0, cap_ag, f"a_{k}") for k in inst.R}
    o = {i: m.NewIntVar(0, cap_ot, f"o_{i}") for i in inst.staff}
    for i in inst.staff: m.Add(hours[i] <= int(inst.S[i]["max_total_min"]) + o[i])

    sen_short = {}
    under = {k: m.NewIntVar(0, UB, f"u_{k}") for k in inst.R}
    over  = {k: m.NewIntVar(0, UB, f"v_{k}") for k in inst.R}
    for (d, s) in inst.R:
        staffed = sum(x[i, d, s] for i in inst.staff)
        m.Add(staffed + a[d, s] + under[d, s] >= inst.R[d, s])
        m.Add(over[d, s] >= staffed + a[d, s] - inst.R[d, s])
        if levers:
            # SOFT skill mix: short of seniors costs money, it is never impossible.
            sen_short[d, s] = m.NewIntVar(0, C.senior_req[d, s], f"ss_{d}_{s}")
            m.Add(sum(x[i, d, s] for i in C.senior) + sen_short[d, s] >= C.senior_req[d, s])

    return dict(m=m, x=x, a=a, o=o, under=under, over=over,
                sen_short=sen_short, work=work, hours=hours)


def solve(inst, C, objective="penalty", levers=False, gap=0.0, time_limit=120.,
          published=None, quiet=False, warmstart=True, workers=8):
    P = C.P
    t_build = time.time()
    core = build_core(inst, C, levers)
    m, x, a, o = core["m"], core["x"], core["a"], core["o"]
    under, over, sen_short, work = (core["under"], core["over"],
                                    core["sen_short"], core["work"])

    # ---------------- objective ----------------
    if objective == "penalty":
        OBJ = (sum(wt * (1 - x[i, d, s]) for (i, d, s, wt) in inst.on_req)
             + sum(wt * x[i, d, s]       for (i, d, s, wt) in inst.off_req)
             + sum(inst.w_under[k] * under[k] for k in inst.R)
             + sum(inst.w_over[k]  * over[k]  for k in inst.R))
    else:
        n = len(inst.staff)
        fair_s = inst.NIGHT or inst.shifts
        cnt = {i: sum(x[i, d, s] for d in inst.days for s in fair_s) for i in inst.staff}
        tot = sum(cnt.values())
        DEV = {i: m.NewIntVar(0, n * inst.n_days, f"DEV_{i}") for i in inst.staff}  # = n * dev_i
        for i in inst.staff:
            m.Add(DEV[i] >= n * cnt[i] - tot)
            m.Add(DEV[i] >= tot - n * cnt[i])
        g = {}
        if inst.NIGHT:
            for i in inst.staff:
                for d in range(inst.n_days - 2):
                    g[i, d] = m.NewBoolVar(f"g_{i}_{d}")
                    m.Add(g[i, d] >= sum(x[i, d+t, s] for t in range(3) for s in inst.NIGHT) - 2)
        churn = []
        if published:
            for i in inst.staff:
                for d in inst.days:
                    prev = published.get((i, d))
                    if prev is None: continue
                    for s in inst.shifts:
                        churn.append((1 - x[i, d, s]) if prev == s else x[i, d, s])

        F_econ = (sum(M(C.wage[i][s]) * x[i, d, s]
                      for i in inst.staff for d in inst.days for s in inst.shifts)
                + sum(M(C.ot_hourly[i] / 60.0) * o[i] for i in inst.staff)
                + sum(M(P["agency_cost_per_shift"]) * a[k] for k in inst.R)
                + sum(M(P["rho_understaffing"]) * under[k] for k in inst.R)
                + sum(M(P["rho_senior_shortfall"]) * t for t in sen_short.values()))
        F_env  = (sum(M(C.p_kg * C.kg_day[i]) * work(i, d) for i in inst.staff for d in inst.days)
                + sum(M(C.p_kg * P["agency_travel_kg"]) * a[k] for k in inst.R)
                + sum(M(C.p_kg * P["e_anc_kg_per_over_shift"]) * over[k] for k in inst.R))
        F_soc  = (sum(M(C.k_pref * wt) * (1 - x[i, d, s]) for (i, d, s, wt) in inst.on_req)
                + sum(M(C.k_pref * wt) * x[i, d, s]       for (i, d, s, wt) in inst.off_req)
                + sum(M(C.k_fair / n) * DEV[i] for i in inst.staff)
                + sum(M(P["kappa_fatigue"]) * t for t in g.values())
                + sum(M(P["kappa_churn"])  * t for t in churn))
        OBJ = F_econ + F_soc + F_env

    # ---------------- solve ----------------
    # UNKNOWN from CP-SAT does NOT mean infeasible. It means the time limit expired
    # before ANY feasible roster was found. On the big instances the min-consecutive
    # and max-weekend rules make that first solution genuinely hard to hit.
    #
    # So: optimise normally with the full budget. Only if that comes back with NOTHING
    # do we fall back to a feasibility-only solve (no objective, much easier) so you get
    # a valid roster instead of a blank row. Running the fallback FIRST was tried and is
    # worse -- it eats the budget and anchors the search on a poor starting roster.
    build_s = time.time() - t_build
    m.Minimize(OBJ)
    sv = cp_model.CpSolver()
    sv.parameters.max_time_in_seconds = time_limit
    sv.parameters.num_search_workers = workers
    if gap > 0: sv.parameters.relative_gap_limit = gap      # <-- CP-SAT's MIP gap
    t0 = time.time(); status = sv.Solve(m); wall = time.time() - t0

    if status not in (cp_model.OPTIMAL, cp_model.FEASIBLE) and warmstart:
        if not quiet:
            print(f"  {sv.StatusName(status)} after {wall:.0f}s -- no roster found at all.\n"
                  f"  This is NOT infeasibility: the solver ran out of time before finding a\n"
                  f"  first solution. Falling back to a feasibility-only solve...")
        m2 = m.Clone()
        m2.ClearObjective()
        sv2 = cp_model.CpSolver()
        # Capped: with --time inf or some huge value, half of it is not a budget.
        sv2.parameters.max_time_in_seconds = min(600.0, max(30.0, 0.5 * time_limit))
        sv2.parameters.num_search_workers = workers
        st2 = sv2.Solve(m2); wall = time.time() - t0
        if st2 in (cp_model.OPTIMAL, cp_model.FEASIBLE):
            roster = {(i, d): next((s for s in inst.shifts if sv2.Value(x[i, d, s])), "")
                      for i in inst.staff for d in inst.days}
            if not quiet:
                print(f"  found a feasible roster in {wall:.0f}s -- but it is NOT optimised.\n"
                      f"  Re-run with a larger --time to actually minimise the objective.")
            return roster, dict(status="FEASIBLE-NOT-OPTIMISED", wall=wall, gap=float("nan"),
                                warmstart=True, build_s=build_s, solve_s=wall,
                                agency={k: sv2.Value(a[k]) for k in inst.R},
                                ot_min={i: sv2.Value(o[i]) for i in inst.staff})
        if not quiet:
            print(f"  still nothing after the fallback. This instance needs a much larger\n"
                  f"  --time (feasibility alone can take minutes at 100+ staff), or a\n"
                  f"  metaheuristic rather than an exact solver.")
        return None, dict(status=sv.StatusName(status), wall=wall, warmstart=True, build_s=build_s, solve_s=wall)

    if status not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        return None, dict(status=sv.StatusName(status), wall=wall, warmstart=False, build_s=build_s, solve_s=wall)

    roster = {(i, d): next((s for s in inst.shifts if sv.Value(x[i, d, s])), "")
              for i in inst.staff for d in inst.days}
    obj, bound = sv.ObjectiveValue(), sv.BestObjectiveBound()
    achieved = abs(obj - bound) / max(1e-9, abs(obj))
    # CP-SAT reports OPTIMAL when it stops because relative_gap_limit was MET, not only
    # when optimality is proven. Those are different claims and a results table must not
    # conflate them, so relabel the tolerance case.
    name = sv.StatusName(status)
    if name == "OPTIMAL" and gap > 0 and achieved > 1e-9:
        name = "WITHIN-GAP"
    return roster, dict(status=name, wall=wall, warmstart=False,
                        build_s=build_s, solve_s=wall,
                        gap=achieved,
                        agency={k: sv.Value(a[k]) for k in inst.R},
                        ot_min={i: sv.Value(o[i]) for i in inst.staff})

if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--instance", default="Instance1")
    ap.add_argument("--objective", choices=["penalty", "dollars"], default="penalty")
    ap.add_argument("--levers", action="store_true")
    ap.add_argument("--gap",  type=float, default=0.0)
    ap.add_argument("--time", type=float, default=120.)
    ap.add_argument("--workers", type=int, default=8,
                    help="CP-SAT search threads. Use 1 for a reproducible search.")
    ap.add_argument("--no-fallback", action="store_true",
                    help="on UNKNOWN, do not fall back to a feasibility-only solve")
    ap.add_argument("--carbon", type=float, default=None,
                    help="shorthand for --price carbon_price_aud_per_t=VALUE")
    ap.add_argument("--price", action="append", default=[], metavar="KEY=VALUE",
                    help="override any row of prices.csv for this run only, e.g. "
                         "--price rho_understaffing=2000 --price kappa_churn=100. Repeatable.")

    ap.add_argument("--published", default=None, help="CSV of a previously published roster")
    args = ap.parse_args()

    inst = D.load_instance(args.instance)
    T, P = D.load_tbl(inst)
    if args.carbon is not None: P["carbon_price_aud_per_t"] = args.carbon
    D.apply_price_overrides(P, args.price)
    C = D.Coef(inst, T, P); C.check_price_ordering()
    pub = D.load_published(inst, args.published)

    roster, info = solve(inst, C, args.objective, args.levers, args.gap, args.time, pub,
                         warmstart=not args.no_fallback, workers=args.workers)
    if roster is None:
        raise SystemExit(f"no solution: {info['status']}")

    e = D.evaluate(inst, C, roster, info["agency"], info["ot_min"], pub)
    tag = f"{args.objective}{'_levers' if args.levers else ''}"
    log = os.path.join(D.HERE, "results", f"{inst.name}_{tag}_ortools.log")
    with D.Tee(log):
        D.print_roster(inst, roster)
        D.report(inst, C, e, "OR-Tools CP-SAT", info["status"], info["wall"], info.get("gap"),
                 args.objective, info.get("build_s"), info.get("solve_s"))
    out = os.path.join(D.HERE, "results", f"{inst.name}_{tag}_ortools.csv")
    D.write_roster_csv(inst, roster, out)
    print(f"  roster written to {out}")
