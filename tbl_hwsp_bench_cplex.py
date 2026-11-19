"""
TBL-HWSP benchmark runner -- CPLEX (docplex) version.

Line-for-line equivalent of tbl_hwsp_bench_gurobi.py -- same data,
same constraint families, same two MODEs, same auto-generated visual
timetable. Put the two files side by side to see the API mapping:

    gurobipy                          docplex
    ------------------------------    ------------------------------
    gp.Model("name")                  Model(name="name")
    m.addVars(keys, vtype=BINARY)     m.binary_var_dict(keys)
    quicksum(...)                     m.sum(...)
    m.addConstrs(gen)                 m.add_constraints(gen)
    m.addConstr(expr)                 m.add_constraint(expr)
    m.setObjective(e, MINIMIZE)       m.minimize(e)
    m.optimize()                      solution = m.solve()
    var.X                             var.solution_value
    m.ObjVal                          m.objective_value
    m.Status == GRB.OPTIMAL           solution is not None

Usage:  python bench_convert.py Instance1.txt   (once per instance)
        python tbl_hwsp_bench_cplex.py

Community-edition CPLEX caps at 1000 variables/constraints -- fine for
small instances (Instance1-10ish), not for the 150-staff/364-day ones.
Register for IBM's free academic license once you outgrow it.
"""

import csv
import os
import webbrowser
from docplex.mp.model import Model
import visualize_roster

MODE = "benchmark"          # "benchmark"  or  "tbl"

DATA_DIR = os.path.join(os.path.dirname(__file__), "data_bench")

# =====================================================================
# 1. LOAD THE CONVERTED INSTANCE   (identical to the Gurobi version)
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

m = Model(name="bench_cplex")

ids_x = [(i, d, s) for i in staff for d in days for s in shifts]
ids_ds = [(d, s) for d in days for s in shifts]
ids_wk = [(i, k) for i in staff for k in range(len(weekends))]

x = m.binary_var_dict(ids_x, name="x")
under = m.continuous_var_dict(ids_ds, lb=0, name="under")
over = m.continuous_var_dict(ids_ds, lb=0, name="over")
Wk = m.binary_var_dict(ids_wk, name="weekend")

work = {(i, d): m.sum(x[i, d, s] for s in shifts) for i in staff for d in days}

# =====================================================================
# 3. HARD CONSTRAINTS   (same families, same order, as the Gurobi file)
# =====================================================================

m.add_constraints((work[i, d] <= 1 for i in staff for d in days))

for (i, d) in days_off:
    m.add_constraint(work[i, d] == 0)

m.add_constraints(
    x[i, d, s] + x[i, d + 1, t] <= 1
    for i in staff for d in days[:-1]
    for s in shifts for t in cannot_follow[s])

m.add_constraints(
    m.sum(x[i, d, s] for d in days) <= max_shifts_of(i, s)
    for i in staff for s in shifts)

m.add_constraints(
    m.sum(length_min[s] * x[i, d, s] for d in days for s in shifts)
    >= int(S[i]["min_total_min"]) for i in staff)
m.add_constraints(
    m.sum(length_min[s] * x[i, d, s] for d in days for s in shifts)
    <= int(S[i]["max_total_min"]) for i in staff)

for i in staff:
    K = int(S[i]["max_consec_shifts"])
    for start in range(n_days - K):
        m.add_constraint(
            m.sum(work[i, d] for d in range(start, start + K + 1)) <= K)

for i in staff:
    mn = int(S[i]["min_consec_shifts"])
    for j in range(1, mn):
        for d in range(n_days - j - 1):
            m.add_constraint(
                m.sum(work[i, d + t] for t in range(1, j + 1))
                - work[i, d] - work[i, d + j + 1] <= j - 1)

for i in staff:
    mn = int(S[i]["min_consec_days_off"])
    for j in range(1, mn):
        for d in range(n_days - j - 1):
            m.add_constraint(
                work[i, d] + work[i, d + j + 1]
                - m.sum(work[i, d + t] for t in range(1, j + 1)) <= 1)

for i in staff:
    for k, (sat, sun) in enumerate(weekends):
        m.add_constraint(Wk[i, k] >= work[i, sat])
        if sun < n_days:
            m.add_constraint(Wk[i, k] >= work[i, sun])
    m.add_constraint(
        m.sum(Wk[i, k] for k in range(len(weekends))) <= int(S[i]["max_weekends"]))

m.add_constraints(
    under[d, s] >= R[d, s] - m.sum(x[i, d, s] for i in staff)
    for d in days for s in shifts)
m.add_constraints(
    over[d, s] >= m.sum(x[i, d, s] for i in staff) - R[d, s]
    for d in days for s in shifts)

# =====================================================================
# 4. OBJECTIVES
# =====================================================================

pen_on = m.sum(wt * (1 - x[i, d, s]) for (i, d, s, wt) in on_req)
pen_off = m.sum(wt * x[i, d, s] for (i, d, s, wt) in off_req)
pen_under = m.sum(w_under[d, s] * under[d, s] for d in days for s in shifts)
pen_over = m.sum(w_over[d, s] * over[d, s] for d in days for s in shifts)

if MODE == "benchmark":
    m.minimize(pen_on + pen_off + pen_under + pen_over)

elif MODE == "tbl":
    wage = {i: float(S[i]["wage_per_shift"]) for i in staff}
    km = {i: float(S[i]["commute_km"]) for i in staff}
    ef = {i: float(S[i]["emission_factor"]) for i in staff}
    e_anc = 2.0
    lam_fair = 10.0

    F_econ = (m.sum(wage[i] * x[i, d, s] for i in staff for d in days for s in shifts)
              + pen_under)
    dev = m.continuous_var_dict(staff, lb=0, name="dev")
    tot = {i: m.sum(work[i, d] for d in days) for i in staff}
    mean_tot = m.sum(tot[i] for i in staff) / len(staff)
    m.add_constraints(dev[i] >= tot[i] - mean_tot for i in staff)
    m.add_constraints(dev[i] >= mean_tot - tot[i] for i in staff)
    F_soc = pen_on + pen_off + lam_fair * m.sum(dev[i] for i in staff)
    F_env = (m.sum(2 * km[i] * ef[i] * work[i, d] for i in staff for d in days)
             + e_anc * m.sum(over[d, s] for d in days for s in shifts))

    m.minimize(F_econ + F_soc + F_env)

# =====================================================================
# 5. SOLVE AND REPORT
# =====================================================================

solution = m.solve(log_output=True)

if solution:
    print(f"\n=== MODE: {MODE} (CPLEX) ===")
    print(f"Optimal objective: {m.objective_value:.1f}")
    if MODE == "benchmark":
        print(f"  on-request penalty  : {solution.get_value(pen_on):.0f}")
        print(f"  off-request penalty : {solution.get_value(pen_off):.0f}")
        print(f"  under-cover penalty : {solution.get_value(pen_under):.0f}")
        print(f"  over-cover penalty  : {solution.get_value(pen_over):.0f}")

    out = os.path.join(os.path.dirname(__file__), f"roster_bench_{MODE}_cplex.csv")
    with open(out, "w", newline="") as f:
        wtr = csv.writer(f)
        wtr.writerow(["staff_id"] + [f"day{d}" for d in days])
        for i in staff:
            row = [i]
            for d in days:
                cell = ""
                for s in shifts:
                    if x[i, d, s].solution_value > 0.5:
                        cell = s
                row.append(cell)
            wtr.writerow(row)
    print(f"Roster grid written to {out}")

    staff_list, day_cols, grid = visualize_roster.load_roster(out)
    cover_rows_v = visualize_roster.load_cover(os.path.join(DATA_DIR, "cover.csv"))
    html = visualize_roster.build_html(staff_list, day_cols, grid, cover_rows_v)
    view_path = os.path.join(os.path.dirname(__file__), f"roster_view_{MODE}_cplex.html")
    with open(view_path, "w") as f:
        f.write(html)
    print(f"Visual timetable written to {view_path} -- opening it now...")
    webbrowser.open("file://" + os.path.abspath(view_path))
else:
    print("No solution found:", m.solve_details.status)
