"""
TBL-HWSP -- Gurobi runner. Same model, same flags, same report as tbl_ortools.py,
and the same solve() signature so run_all.py can use either via --solver.

    python tbl_gurobi.py --instance Instance1 --objective dollars --levers --gap 0.01 --time 600
    python run_all.py --solver gurobi --objective dollars --levers --gap 0.01 --time 300

MIP GAP -- the two solvers spell it differently:
    Gurobi   m.Params.MIPGap = 0.01 ; m.Params.TimeLimit = 600 ; read back m.MIPGap
    CP-SAT   solver.parameters.relative_gap_limit = 0.01
             solver.parameters.max_time_in_seconds = 600
             gap = (ObjectiveValue - BestObjectiveBound) / ObjectiveValue

THE ONE REAL MODEL DIFFERENCE: Gurobi takes fractional coefficients, so the A$ prices
go straight in and there is no SCALE factor and no fairness rescaling. CP-SAT is an
integer-only solver, so there every coefficient is multiplied by 10,000 and rounded --
which is why the carbon terms must not be rounded to whole cents in that file.
"""
import argparse, os, time
import gurobipy as gp
from gurobipy import GRB
import tbl_data as D

def solve(inst, C, objective="penalty", levers=False, gap=0.0, time_limit=120.,
          published=None, quiet=True, start=None, lowmem=False, memlimit=0.0):
    """start: an optional {(staff, day): shift} roster used as a MIP start.

    Handing Gurobi a known-good incumbent means it does not have to spend time finding
    one, so the whole budget goes on raising the LOWER BOUND -- which is the only thing
    a recovered roster is missing. A warm-started run is a normal, reportable run; state
    in the write-up that a MIP start was supplied."""
    P = C.P
    t_build = time.time()
    m = gp.Model(inst.name)
    m.Params.OutputFlag = 0 if quiet else 1
    m.Params.TimeLimit = time_limit
    if gap > 0: m.Params.MIPGap = gap                     # <-- Gurobi's MIP gap

    # ---------------- memory ----------------
    # Instance24 (150 staff x 364 days) dies with C++ std::bad_alloc: the branch-and-bound
    # tree does not fit in RAM. bad_alloc is a CRASH -- no status, no incumbent, nothing
    # recorded. SoftMemLimit converts that into a graceful stop with status MEM_LIMIT,
    # keeping whatever solution was found. Always worth setting on the big instances.
    if memlimit > 0:
        try: m.Params.SoftMemLimit = memlimit            # GB
        except Exception: pass                           # older Gurobi lacks it
    if lowmem:
        # Each thread keeps its own copy of tree data, so fewer threads = less memory.
        m.Params.Threads = 2
        # Spill the search tree to disk once it passes this many GB in RAM. This is the
        # single most effective setting for a model that will not fit.
        m.Params.NodefileStart = 0.5
        # Dual simplex rather than the concurrent optimiser, which runs several LP
        # algorithms at once and holds several factorisations in memory.
        m.Params.Method = 1
        # Prioritise finding good feasible solutions over proving the bound. Produces a
        # much smaller tree -- the right trade when the alternative is no answer at all.
        m.Params.MIPFocus = 1

    x = m.addVars(inst.staff, inst.days, inst.shifts, vtype=GRB.BINARY, name="x")
    def work(i, d): return gp.quicksum(x[i, d, s] for s in inst.shifts)

    for i in inst.staff:
        for d in inst.days: m.addConstr(work(i, d) <= 1)
    for (i, d) in inst.days_off: m.addConstr(work(i, d) == 0)
    for i in inst.staff:
        for d in inst.days[:-1]:
            for s in inst.shifts:
                for t in inst.cannot_follow[s]: m.addConstr(x[i, d, s] + x[i, d+1, t] <= 1)
    for i in inst.staff:
        for s in inst.shifts:
            m.addConstr(gp.quicksum(x[i, d, s] for d in inst.days) <= inst.max_shifts_of(i, s))
    for i in inst.staff:
        K = int(inst.S[i]["max_consec_shifts"])
        for st in range(inst.n_days - K):
            m.addConstr(gp.quicksum(work(i, d) for d in range(st, st+K+1)) <= K)
    for i in inst.staff:
        mn = int(inst.S[i]["min_consec_shifts"])
        for j in range(1, mn):
            for d in range(inst.n_days - j - 1):
                m.addConstr(gp.quicksum(work(i, d+t) for t in range(1, j+1))
                            - work(i, d) - work(i, d+j+1) <= j-1)
    for i in inst.staff:
        mn = int(inst.S[i]["min_consec_days_off"])
        for j in range(1, mn):
            for d in range(inst.n_days - j - 1):
                m.addConstr(work(i, d) + work(i, d+j+1)
                            - gp.quicksum(work(i, d+t) for t in range(1, j+1)) <= 1)
    Wk = m.addVars(inst.staff, range(len(inst.weekends)), vtype=GRB.BINARY, name="wk")
    for i in inst.staff:
        for k, (sat, sun) in enumerate(inst.weekends):
            m.addConstr(Wk[i, k] >= work(i, sat))
            if sun < inst.n_days: m.addConstr(Wk[i, k] >= work(i, sun))
        m.addConstr(gp.quicksum(Wk[i, k] for k in range(len(inst.weekends)))
                    <= int(inst.S[i]["max_weekends"]))

    hours = {i: gp.quicksum(inst.length_min[s] * x[i, d, s]
                            for d in inst.days for s in inst.shifts) for i in inst.staff}
    for i in inst.staff: m.addConstr(hours[i] >= int(inst.S[i]["min_total_min"]))

    cap_ag = int(P["agency_max_per_shift"]) if levers else 0
    cap_ot = int(P["overtime_max_min"])     if levers else 0
    a = m.addVars(inst.R.keys(), vtype=GRB.INTEGER, lb=0, ub=cap_ag, name="a")
    o = m.addVars(inst.staff, vtype=GRB.CONTINUOUS, lb=0, ub=cap_ot, name="o")
    for i in inst.staff: m.addConstr(hours[i] <= int(inst.S[i]["max_total_min"]) + o[i])

    sen_short = {}
    under = m.addVars(inst.R.keys(), vtype=GRB.CONTINUOUS, lb=0, name="u")
    over  = m.addVars(inst.R.keys(), vtype=GRB.CONTINUOUS, lb=0, name="v")
    for (d, s) in inst.R:
        staffed = gp.quicksum(x[i, d, s] for i in inst.staff)
        m.addConstr(staffed + a[d, s] + under[d, s] >= inst.R[d, s])
        m.addConstr(over[d, s] >= staffed + a[d, s] - inst.R[d, s])
        if levers:
            # SOFT skill mix: short of seniors costs money, it is never impossible.
            sen_short[d, s] = m.addVar(vtype=GRB.CONTINUOUS, lb=0, ub=C.senior_req[d, s])
            m.addConstr(gp.quicksum(x[i, d, s] for i in C.senior) + sen_short[d, s]
                        >= C.senior_req[d, s])

    if objective == "penalty":
        m.setObjective(
            gp.quicksum(wt * (1 - x[i, d, s]) for (i, d, s, wt) in inst.on_req)
          + gp.quicksum(wt * x[i, d, s]       for (i, d, s, wt) in inst.off_req)
          + gp.quicksum(inst.w_under[k] * under[k] for k in inst.R)
          + gp.quicksum(inst.w_over[k]  * over[k]  for k in inst.R), GRB.MINIMIZE)
    else:
        n = len(inst.staff)
        fair_s = inst.NIGHT or inst.shifts
        cnt = {i: gp.quicksum(x[i, d, s] for d in inst.days for s in fair_s) for i in inst.staff}
        tot = gp.quicksum(cnt.values())
        dev = m.addVars(inst.staff, vtype=GRB.CONTINUOUS, lb=0, name="dev")   # no rescaling needed
        for i in inst.staff:
            m.addConstr(dev[i] >= cnt[i] - tot / n)
            m.addConstr(dev[i] >= tot / n - cnt[i])
        g = {}
        if inst.NIGHT:
            for i in inst.staff:
                for d in range(inst.n_days - 2):
                    g[i, d] = m.addVar(vtype=GRB.BINARY, name=f"g_{i}_{d}")
                    m.addConstr(g[i, d] >= gp.quicksum(x[i, d+t, s] for t in range(3)
                                                       for s in inst.NIGHT) - 2)
        churn = []
        if published:
            for i in inst.staff:
                for d in inst.days:
                    prev = published.get((i, d))
                    if prev is None: continue
                    for s in inst.shifts:
                        churn.append((1 - x[i, d, s]) if prev == s else x[i, d, s])

        F_econ = (gp.quicksum(C.wage[i][s] * x[i, d, s]
                              for i in inst.staff for d in inst.days for s in inst.shifts)
                + gp.quicksum(C.ot_hourly[i] / 60.0 * o[i] for i in inst.staff)
                + gp.quicksum(P["agency_cost_per_shift"] * a[k] for k in inst.R)
                + gp.quicksum(P["rho_understaffing"] * under[k] for k in inst.R)
                + gp.quicksum(P["rho_senior_shortfall"] * t for t in sen_short.values()))
        F_env  = (gp.quicksum(C.p_kg * C.kg_day[i] * work(i, d)
                              for i in inst.staff for d in inst.days)
                + gp.quicksum(C.p_kg * P["agency_travel_kg"] * a[k] for k in inst.R)
                + gp.quicksum(C.p_kg * P["e_anc_kg_per_over_shift"] * over[k] for k in inst.R))
        F_soc  = (gp.quicksum(C.k_pref * wt * (1 - x[i, d, s]) for (i, d, s, wt) in inst.on_req)
                + gp.quicksum(C.k_pref * wt * x[i, d, s]       for (i, d, s, wt) in inst.off_req)
                + gp.quicksum(C.k_fair * dev[i] for i in inst.staff)
                + gp.quicksum(P["kappa_fatigue"] * t for t in g.values())
                + gp.quicksum(P["kappa_churn"]  * t for t in churn))
        m.setObjective(F_econ + F_soc + F_env, GRB.MINIMIZE)

    if start:
        # Only the x variables are set. Gurobi completes the rest of the start itself,
        # which is more reliable than guessing values for the auxiliary variables.
        for i in inst.staff:
            for d in inst.days:
                sh = start.get((i, d), "")
                for s in inst.shifts:
                    x[i, d, s].Start = 1.0 if s == sh else 0.0

    build_s = time.time() - t_build
    t0 = time.time(); m.optimize(); wall = time.time() - t0
    # Every status this model can realistically return, NAMED. An unmapped status used to
    # print as a bare integer -- "11" in a results table is worse than useless, and 11 is
    # INTERRUPTED, i.e. a solve the user killed, which must never be mistaken for a result.
    names = {GRB.OPTIMAL: "OPTIMAL", GRB.TIME_LIMIT: "FEASIBLE",
             GRB.INFEASIBLE: "INFEASIBLE", GRB.INTERRUPTED: "INTERRUPTED",
             GRB.INF_OR_UNBD: "INF_OR_UNBOUNDED", GRB.UNBOUNDED: "UNBOUNDED",
             GRB.SUBOPTIMAL: "SUBOPTIMAL", GRB.NUMERIC: "NUMERIC_TROUBLE",
             GRB.MEM_LIMIT: "OUT_OF_MEMORY", GRB.WORK_LIMIT: "WORK_LIMIT",
             GRB.NODE_LIMIT: "NODE_LIMIT", GRB.ITERATION_LIMIT: "ITERATION_LIMIT",
             GRB.SOLUTION_LIMIT: "SOLUTION_LIMIT", GRB.CUTOFF: "CUTOFF"}
    status = names.get(m.Status, f"GUROBI_STATUS_{m.Status}")
    # Gurobi also returns OPTIMAL once the solution is merely within MIPGap of the bound.
    # Relabel so a results table cannot claim proven optimality it does not have.
    if status == "OPTIMAL" and gap > 0 and m.SolCount > 0 and m.MIPGap > 1e-9:
        status = "WITHIN-GAP"
    if m.SolCount == 0:
        return None, dict(status=status, wall=wall, build_s=build_s, solve_s=wall)
    roster = {(i, d): next((s for s in inst.shifts if x[i, d, s].X > 0.5), "")
              for i in inst.staff for d in inst.days}
    return roster, dict(status=status, wall=wall, gap=m.MIPGap,
                        build_s=build_s, solve_s=m.Runtime,
                        agency={k: int(round(a[k].X)) for k in inst.R},
                        ot_min={i: o[i].X for i in inst.staff})

if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--instance", default="Instance1")
    ap.add_argument("--objective", choices=["penalty", "dollars"], default="penalty")
    ap.add_argument("--levers", action="store_true")
    ap.add_argument("--gap",  type=float, default=0.0)
    ap.add_argument("--time", type=float, default=120.)
    ap.add_argument("--carbon", type=float, default=None,
                    help="shorthand for --price carbon_price_aud_per_t=VALUE")
    ap.add_argument("--price", action="append", default=[], metavar="KEY=VALUE",
                    help="override any row of prices.csv for this run only, e.g. "
                         "--price rho_understaffing=2000 --price kappa_churn=100. Repeatable.")

    ap.add_argument("--published", default=None)
    args = ap.parse_args()

    inst = D.load_instance(args.instance)
    T, P = D.load_tbl(inst)
    if args.carbon is not None: P["carbon_price_aud_per_t"] = args.carbon
    D.apply_price_overrides(P, args.price)
    C = D.Coef(inst, T, P); C.check_price_ordering()
    pub = D.load_published(inst, args.published)

    roster, info = solve(inst, C, args.objective, args.levers, args.gap, args.time, pub)
    if roster is None:
        raise SystemExit(f"no solution: {info['status']}")
    e = D.evaluate(inst, C, roster, info["agency"], info["ot_min"], pub)
    tag = f"{args.objective}{'_levers' if args.levers else ''}"
    log = os.path.join(D.HERE, "results", f"{inst.name}_{tag}_gurobi.log")
    with D.Tee(log):
        D.print_roster(inst, roster)
        D.report(inst, C, e, "Gurobi", info["status"], info["wall"], info.get("gap"),
                 args.objective, info.get("build_s"), info.get("solve_s"))
    out = os.path.join(D.HERE, "results", f"{inst.name}_{tag}_gurobi.csv")
    D.write_roster_csv(inst, roster, out)
    print(f"  roster written to {out}")
