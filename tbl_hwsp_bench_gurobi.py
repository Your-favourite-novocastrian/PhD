"""
TBL-HWSP benchmark runner -- solves converted schedulingbenchmarks.org
instances (see bench_convert.py) in one of two modes:

  MODE = "benchmark"
      Faithful reproduction of the benchmark's own objective: minimize
      weighted on/off-request violations + weighted under/over-coverage,
      with every structural rule hard. Use this FIRST: if the optimal
      value matches the published optimum for the instance, the
      constraint encoding is proven correct.

  MODE = "tbl"
      The same hard feasibility rules, but the three TBL pillar
      objectives with the uniform placeholder data added by the
      converter. This is the testing scaffold for the real model --
      placeholders make F_econ/F_env structurally present but not yet
      meaningful.

Constraint families implemented (all hard):
  - at most one shift per day
  - fixed days off (SECTION_DAYS_OFF)
  - forbidden shift successions (cannot-follow lists)
  - max shifts per shift type
  - workload band: min/max total minutes
  - max consecutive working days
  - MIN consecutive working days   (no isolated short work blocks)
  - MIN consecutive days off       (no isolated short off blocks)
  - max working weekends           (horizon starts on a Monday)
Min-consecutive rules are not applied to blocks touching the horizon
boundary (they may continue outside the horizon), per the benchmark
definition.

Usage:  python bench_convert.py Instance1.txt   (once)
        python tbl_hwsp_bench_gurobi.py
"""

import csv
import os
import gurobipy as gp
from gurobipy import GRB, quicksum

MODE = "benchmark"          # "benchmark"  or  "tbl"

DATA_DIR = os.path.join(os.path.dirname(__file__), "data_bench")

# =====================================================================
# 1. LOAD THE CONVERTED INSTANCE
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
    # "D=14|E=5" -> {"D":14, "E":5}
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

# Weekends: horizon starts on a Monday, so 0-indexed Sat=5+7k, Sun=6+7k
weekends = [(5 + 7 * k, 6 + 7 * k) for k in range((n_days + 6) // 7)
            if 5 + 7 * k < n_days]

# =====================================================================
# 2. MODEL AND VARIABLES
# =====================================================================

m = gp.Model("bench")

x = m.addVars(staff, days, shifts, vtype=GRB.BINARY, name="x")
under = m.addVars(days, shifts, lb=0, name="under")
over = m.addVars(days, shifts, lb=0, name="over")
Wk = m.addVars(staff, range(len(weekends)), vtype=GRB.BINARY, name="weekend")

# "worked day d" as a linear expression (single assignment makes it 0/1)
work = {(i, d): quicksum(x[i, d, s] for s in shifts) for i in staff for d in days}

# =====================================================================
# 3. HARD CONSTRAINTS
# =====================================================================

# At most one shift per day
m.addConstrs((work[i, d] <= 1 for i in staff for d in days), name="one_per_day")

# Fixed days off
for (i, d) in days_off:
    m.addConstr(work[i, d] == 0, name=f"dayoff_{i}_{d}")

# Forbidden successions
m.addConstrs(
    (x[i, d, s] + x[i, d + 1, t] <= 1
     for i in staff for d in days[:-1]
     for s in shifts for t in cannot_follow[s]),
    name="succession")

# Max shifts per type
m.addConstrs(
    (quicksum(x[i, d, s] for d in days) <= max_shifts_of(i, s)
     for i in staff for s in shifts),
    name="max_shifts")

# Workload band (minutes)
m.addConstrs(
    (quicksum(length_min[s] * x[i, d, s] for d in days for s in shifts)
     >= int(S[i]["min_total_min"]) for i in staff),
    name="min_minutes")
m.addConstrs(
    (quicksum(length_min[s] * x[i, d, s] for d in days for s in shifts)
     <= int(S[i]["max_total_min"]) for i in staff),
    name="max_minutes")

# Max consecutive working days (window of K+1 days)
for i in staff:
    K = int(S[i]["max_consec_shifts"])
    for start in range(n_days - K):
        m.addConstr(
            quicksum(work[i, d] for d in range(start, start + K + 1)) <= K,
            name=f"maxconsec_{i}_{start}")

# MIN consecutive working days: forbid off, j worked, off  (j < min)
for i in staff:
    mn = int(S[i]["min_consec_shifts"])
    for j in range(1, mn):
        for d in range(n_days - j - 1):
            m.addConstr(
                quicksum(work[i, d + t] for t in range(1, j + 1))
                - work[i, d] - work[i, d + j + 1] <= j - 1,
                name=f"minconsec_{i}_{j}_{d}")

# MIN consecutive days off: forbid worked, j off, worked  (j < min)
for i in staff:
    mn = int(S[i]["min_consec_days_off"])
    for j in range(1, mn):
        for d in range(n_days - j - 1):
            m.addConstr(
                work[i, d] + work[i, d + j + 1]
                - quicksum(work[i, d + t] for t in range(1, j + 1)) <= 1,
                name=f"minoff_{i}_{j}_{d}")

# Max working weekends
for i in staff:
    for k, (sat, sun) in enumerate(weekends):
        m.addConstr(Wk[i, k] >= work[i, sat], name=f"wk_sat_{i}_{k}")
        if sun < n_days:
            m.addConstr(Wk[i, k] >= work[i, sun], name=f"wk_sun_{i}_{k}")
    m.addConstr(
        quicksum(Wk[i, k] for k in range(len(weekends)))
        <= int(S[i]["max_weekends"]),
        name=f"max_weekends_{i}")

# Coverage bookkeeping (soft both sides, per the benchmark)
m.addConstrs(
    (under[d, s] >= R[d, s] - quicksum(x[i, d, s] for i in staff)
     for d in days for s in shifts), name="under_link")
m.addConstrs(
    (over[d, s] >= quicksum(x[i, d, s] for i in staff) - R[d, s]
     for d in days for s in shifts), name="over_link")

# =====================================================================
# 4. OBJECTIVES
# =====================================================================

pen_on = quicksum(wt * (1 - x[i, d, s]) for (i, d, s, wt) in on_req)
pen_off = quicksum(wt * x[i, d, s] for (i, d, s, wt) in off_req)
pen_under = quicksum(w_under[d, s] * under[d, s] for d in days for s in shifts)
pen_over = quicksum(w_over[d, s] * over[d, s] for d in days for s in shifts)

if MODE == "benchmark":
    m.setObjective(pen_on + pen_off + pen_under + pen_over, GRB.MINIMIZE)

elif MODE == "tbl":
    # Placeholder TBL pillars over the same hard-feasible set.
    wage = {i: float(S[i]["wage_per_shift"]) for i in staff}
    km = {i: float(S[i]["commute_km"]) for i in staff}
    ef = {i: float(S[i]["emission_factor"]) for i in staff}
    e_anc = 2.0
    lam_fair = 10.0

    F_econ = (quicksum(wage[i] * x[i, d, s]
                       for i in staff for d in days for s in shifts)
              + pen_under)          # keep the benchmark's rho=100 as-is
    # fairness on total shifts (this instance has no night shift, so the
    # night-balance term generalizes to total-workload balance)
    dev = m.addVars(staff, lb=0, name="dev")
    tot = {i: quicksum(work[i, d] for d in days) for i in staff}
    mean_tot = quicksum(tot[i] for i in staff) / len(staff)
    m.addConstrs((dev[i] >= tot[i] - mean_tot for i in staff), name="dev_p")
    m.addConstrs((dev[i] >= mean_tot - tot[i] for i in staff), name="dev_n")
    F_soc = pen_on + pen_off + lam_fair * quicksum(dev[i] for i in staff)
    F_env = (quicksum(2 * km[i] * ef[i] * work[i, d]
                      for i in staff for d in days)
             + e_anc * quicksum(over[d, s] for d in days for s in shifts))

    m.setObjective(F_econ + F_soc + F_env, GRB.MINIMIZE)

# =====================================================================
# 5. SOLVE AND REPORT
# =====================================================================

m.optimize()

if m.Status == GRB.OPTIMAL:
    print(f"\n=== MODE: {MODE} ===")
    print(f"Optimal objective: {m.ObjVal:.1f}")
    if MODE == "benchmark":
        print(f"  on-request penalty  : {pen_on.getValue():.0f}")
        print(f"  off-request penalty : {pen_off.getValue():.0f}")
        print(f"  under-cover penalty : {pen_under.getValue():.0f}")
        print(f"  over-cover penalty  : {pen_over.getValue():.0f}")

    out = os.path.join(os.path.dirname(__file__), f"roster_bench_{MODE}.csv")
    with open(out, "w", newline="") as f:
        wtr = csv.writer(f)
        wtr.writerow(["staff_id"] + [f"day{d}" for d in days])
        for i in staff:
            row = [i]
            for d in days:
                cell = ""
                for s in shifts:
                    if x[i, d, s].X > 0.5:
                        cell = s
                row.append(cell)
            wtr.writerow(row)
    print(f"Roster grid written to {out}")
else:
    print(f"Not optimal (status {m.Status}).")
