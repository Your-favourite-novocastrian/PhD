"""
TBL-HWSP benchmark runner -- Google OR-Tools CP-SAT version.

This one is NOT just a third API translation -- CP-SAT is a genuinely
different solving paradigm from Gurobi/CPLEX, and it's worth reading the
comments below to see how that changes the code, not just the syntax.

    gurobipy / docplex (MIP)              OR-Tools (CP-SAT)
    ------------------------------        ------------------------------
    Continuous relaxation + branch &      Constraint propagation + SAT-style
      bound over LP relaxations             clause learning + branch & bound,
                                             no LP relaxation at all
    addVar(vtype=CONTINUOUS)              NewIntVar(lb, ub, name)  -- CP-SAT
                                             has NO continuous variables,
                                             only booleans and bounded integers
    quicksum(...) <= rhs                  model.Add(sum(...) <= rhs)
    m.addVars(..., vtype=BINARY)          model.NewBoolVar(name) per var
    m.setObjective(expr, MINIMIZE)        model.Minimize(expr)
    m.optimize()                          solver.Solve(model)
    m.Params.TimeLimit = 120              solver.parameters.max_time_in_seconds = 120
    var.X / .solution_value               solver.Value(var)
    m.ObjVal                              solver.ObjectiveValue()
    m.Status == GRB.OPTIMAL               status == cp_model.OPTIMAL
                                           (also check cp_model.FEASIBLE -- CP-SAT
                                            hit the time limit but still has SOME
                                            usable integer-feasible solution)

Practical consequence for THIS model: every "continuous" variable we used
in the Gurobi/CPLEX files (under, over, dev) is actually integer-valued at
the optimum anyway (coverage gaps and workload deviations are counts of
whole shifts), so swapping them to NewIntVar with a generous upper bound
loses nothing here. If your model ever needed a genuinely fractional
variable, CP-SAT would not be the right tool -- it is an integer/boolean
solver through and through, not a relaxation-based one.

Usage:  python bench_convert.py Instance1.txt   (once per instance)
        python tbl_hwsp_bench_ortools.py

No license/size cap at all -- OR-Tools CP-SAT is fully open source
(Apache 2.0) and free for any instance size, which is the main reason to
reach for it once your Gurobi/CPLEX trial or academic license runs out or
caps you. It also tends to be very strong on this exact problem family
(scheduling/rostering is a classic CP-SAT sweet spot), though on huge
instances (e.g. Instance24: 150 staff, 364 days) it can still take a long
time to close the gap to proven optimality, same as any exact solver.
"""

import csv
import os
import webbrowser
from ortools.sat.python import cp_model
import visualize_roster

MODE = "benchmark"          # "benchmark"  or  "tbl"

DATA_DIR = os.path.join(os.path.dirname(__file__), "data_bench")

# =====================================================================
# 1. LOAD THE CONVERTED INSTANCE   (identical to the Gurobi/CPLEX versions)
# =====================================================================

def read_csv(name):
    with open(os.path.join(DATA_DIR, name)) as f:
        return list(csv.DictReader(f))

n_days = int(read_csv("horizon.csv")[0]["n_days"])
days = list(range(n_days))

shift_rows = read_csv("shifts.csv")
shifts = [r["shift_id"] for r in shift_rows]
length_min = {r["shift_id"]: int(r["length_min"]) for r in shift_rows}
cannot_follow = {r["shift_id"]: [t for t in r["cannot_follow"].split("|") if t]
                 for r in shift_rows}

staff_rows = read_csv("staff.csv")
staff = [r["staff_id"] for r in staff_rows]
S = {r["staff_id"]: r for r in staff_rows}

def max_shifts_of(i, s):
    d = {}
    for part in S[i]["max_shifts"].split("|"):
        k, v = part.split("=")
        d[k] = int(v)
    return d.get(s, n_days)

days_off = {(r["staff_id"], int(r["day"])) for r in read_csv("days_off.csv")}
on_req = [(r["staff_id"], int(r["day"]), r["shift_id"], int(r["weight"]))
          for r in read_csv("shift_on_requests.csv")]
off_req = [(r["staff_id"], int(r["day"]), r["shift_id"], int(r["weight"]))
           for r in read_csv("shift_off_requests.csv")]

cover_rows = read_csv("cover.csv")
R = {(int(r["day"]), r["shift_id"]): int(r["requirement"]) for r in cover_rows}
w_under = {(int(r["day"]), r["shift_id"]): int(r["under_weight"]) for r in cover_rows}
w_over = {(int(r["day"]), r["shift_id"]): int(r["over_weight"]) for r in cover_rows}

weekends = [(5 + 7 * k, 6 + 7 * k) for k in range((n_days + 6) // 7)
            if 5 + 7 * k < n_days]

# =====================================================================
# 2. MODEL AND VARIABLES
# =====================================================================

model = cp_model.CpModel()

# CP-SAT has no continuous variables -- under/over/dev become bounded
# integers. len(staff) is a safe, generous upper bound for any of them.
UB = len(staff)

x = {(i, d, s): model.NewBoolVar(f"x_{i}_{d}_{s}")
     for i in staff for d in days for s in shifts}
under = {(d, s): model.NewIntVar(0, UB, f"under_{d}_{s}")
         for d in days for s in shifts}
over = {(d, s): model.NewIntVar(0, UB, f"over_{d}_{s}")
        for d in days for s in shifts}
Wk = {(i, k): model.NewBoolVar(f"wk_{i}_{k}")
      for i in staff for k in range(len(weekends))}

# work[i,d] as a linear expression (sum of bools) -- CP-SAT accepts linear
# expressions directly in Add(), no separate variable needed.
def work(i, d):
    return sum(x[i, d, s] for s in shifts)

# =====================================================================
# 3. HARD CONSTRAINTS   (same families, same order, as the other two files)
# =====================================================================

for i in staff:
    for d in days:
        model.Add(work(i, d) <= 1)

for (i, d) in days_off:
    model.Add(work(i, d) == 0)

for i in staff:
    for d in days[:-1]:
        for s in shifts:
            for t in cannot_follow[s]:
                model.Add(x[i, d, s] + x[i, d + 1, t] <= 1)

for i in staff:
    for s in shifts:
        model.Add(sum(x[i, d, s] for d in days) <= max_shifts_of(i, s))

for i in staff:
    model.Add(
        sum(length_min[s] * x[i, d, s] for d in days for s in shifts)
        >= int(S[i]["min_total_min"]))
    model.Add(
        sum(length_min[s] * x[i, d, s] for d in days for s in shifts)
        <= int(S[i]["max_total_min"]))

for i in staff:
    K = int(S[i]["max_consec_shifts"])
    for start in range(n_days - K):
        model.Add(sum(work(i, d) for d in range(start, start + K + 1)) <= K)

for i in staff:
    mn = int(S[i]["min_consec_shifts"])
    for j in range(1, mn):
        for d in range(n_days - j - 1):
            model.Add(
                sum(work(i, d + t) for t in range(1, j + 1))
                - work(i, d) - work(i, d + j + 1) <= j - 1)

for i in staff:
    mn = int(S[i]["min_consec_days_off"])
    for j in range(1, mn):
        for d in range(n_days - j - 1):
            model.Add(
                work(i, d) + work(i, d + j + 1)
                - sum(work(i, d + t) for t in range(1, j + 1)) <= 1)

for i in staff:
    for k, (sat, sun) in enumerate(weekends):
        model.Add(Wk[i, k] >= work(i, sat))
        if sun < n_days:
            model.Add(Wk[i, k] >= work(i, sun))
    model.Add(sum(Wk[i, k] for k in range(len(weekends))) <= int(S[i]["max_weekends"]))

for d in days:
    for s in shifts:
        model.Add(under[d, s] >= R[d, s] - sum(x[i, d, s] for i in staff))
        model.Add(over[d, s] >= sum(x[i, d, s] for i in staff) - R[d, s])

# =====================================================================
# 4. OBJECTIVES
# =====================================================================

pen_on = sum(wt * (1 - x[i, d, s]) for (i, d, s, wt) in on_req)
pen_off = sum(wt * x[i, d, s] for (i, d, s, wt) in off_req)
pen_under = sum(w_under[d, s] * under[d, s] for d in days for s in shifts)
pen_over = sum(w_over[d, s] * over[d, s] for d in days for s in shifts)

if MODE == "benchmark":
    model.Minimize(pen_on + pen_off + pen_under + pen_over)

elif MODE == "tbl":
    # CP-SAT is integer-only, and wage/km/emission_factor are floats in
    # the CSV -- scale by 100 and round so the objective stays integer
    # (CP-SAT requires integer coefficients). This changes nothing about
    # WHERE the optimum sits, only the units the objective is reported in.
    SCALE = 100
    wage = {i: round(float(S[i]["wage_per_shift"]) * SCALE) for i in staff}
    km = {i: float(S[i]["commute_km"]) for i in staff}
    ef = {i: float(S[i]["emission_factor"]) for i in staff}
    env_coef = {i: round(2 * km[i] * ef[i] * SCALE) for i in staff}
    e_anc = round(2.0 * SCALE)
    lam_fair = round(10.0 * SCALE)

    F_econ = (sum(wage[i] * x[i, d, s] for i in staff for d in days for s in shifts)
              + SCALE * pen_under)

    dev = {i: model.NewIntVar(0, SCALE * n_days, f"dev_{i}") for i in staff}
    tot = {i: sum(work(i, d) for d in days) for i in staff}
    # mean_tot * len(staff) kept as an integer numerator to avoid fractions
    sum_tot = sum(tot[i] for i in staff)
    for i in staff:
        # dev[i] >= tot[i] - mean  <=>  len(staff)*dev[i] >= len(staff)*tot[i] - sum_tot
        model.Add(len(staff) * dev[i] >= len(staff) * tot[i] - sum_tot)
        model.Add(len(staff) * dev[i] >= sum_tot - len(staff) * tot[i])
    F_soc = SCALE * (pen_on + pen_off) + lam_fair * sum(dev[i] for i in staff)
    F_env = (sum(env_coef[i] * work(i, d) for i in staff for d in days)
             + e_anc * sum(over[d, s] for d in days for s in shifts))

    model.Minimize(F_econ + F_soc + F_env)

# =====================================================================
# 5. SOLVE AND REPORT
# =====================================================================

solver = cp_model.CpSolver()
solver.parameters.max_time_in_seconds = 120   # equivalent of Params.TimeLimit
solver.parameters.num_search_workers = 8      # use multiple threads, like the MIP solvers

status = solver.Solve(model)

if status in (cp_model.OPTIMAL, cp_model.FEASIBLE):
    tag = "OPTIMAL" if status == cp_model.OPTIMAL else "FEASIBLE (time limit hit, not proven optimal)"
    print(f"\n=== MODE: {MODE} (OR-Tools CP-SAT) -- {tag} ===")
    print(f"Objective: {solver.ObjectiveValue():.1f}")
    if MODE == "benchmark":
        print(f"  on-request penalty  : {sum(wt * (1 - solver.Value(x[i, d, s])) for (i, d, s, wt) in on_req):.0f}")
        print(f"  off-request penalty : {sum(wt * solver.Value(x[i, d, s]) for (i, d, s, wt) in off_req):.0f}")
        print(f"  under-cover penalty : {sum(w_under[d, s] * solver.Value(under[d, s]) for d in days for s in shifts):.0f}")
        print(f"  over-cover penalty  : {sum(w_over[d, s] * solver.Value(over[d, s]) for d in days for s in shifts):.0f}")

    out = os.path.join(os.path.dirname(__file__), f"roster_bench_{MODE}_ortools.csv")
    with open(out, "w", newline="") as f:
        wtr = csv.writer(f)
        wtr.writerow(["staff_id"] + [f"day{d}" for d in days])
        for i in staff:
            row = [i]
            for d in days:
                cell = ""
                for s in shifts:
                    if solver.Value(x[i, d, s]) > 0.5:
                        cell = s
                row.append(cell)
            wtr.writerow(row)
    print(f"Roster grid written to {out}")

    staff_list, day_cols, grid = visualize_roster.load_roster(out)
    cover_rows_v = visualize_roster.load_cover(os.path.join(DATA_DIR, "cover.csv"))
    html = visualize_roster.build_html(staff_list, day_cols, grid, cover_rows_v)
    view_path = os.path.join(os.path.dirname(__file__), f"roster_view_{MODE}_ortools.html")
    with open(view_path, "w") as f:
        f.write(html)
    print(f"Visual timetable written to {view_path} -- opening it now...")
    webbrowser.open("file://" + os.path.abspath(view_path))
else:
    print("No solution found. Solver status:", solver.StatusName(status))
