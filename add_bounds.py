"""
add_bounds.py -- get the MIP GAP for rosters you already have, without re-solving blind.

    python add_bounds.py --time 300                 every recovered roster, 5 min each
    python add_bounds.py --only 7 8 9 --time 600
    python add_bounds.py --solver gurobi --time 300

Writes results\\summary_BOUNDED_<tag>_<solver>.csv -- the full 28 columns, with a real
status, gap, bound and solve time.

THE PROBLEM THIS SOLVES
-----------------------
A roster file contains the SOLUTION, so recover_rosters.py can rebuild every objective
value exactly. It cannot rebuild the MIP gap, because a gap is a statement about the
SEARCH -- how close the solver got to proving nothing better exists. That lives in the
solver and nowhere else.

Re-solving from scratch would work but throws away the solution you already have.
Instead this hands the existing roster to the solver as a MIP START. The solver begins
with a good incumbent already in hand and spends the whole time budget on the lower
bound, which is precisely the missing piece. Typically far more productive per second
than a cold run on the same instance.

WHAT YOU GET
  bound_aud   the best lower bound proved in this run
  gap         (incumbent - bound) / incumbent -- a REAL, defensible gap
  status      OPTIMAL / WITHIN-GAP / FEASIBLE, meaning the same as anywhere else
  solve_s     seconds spent in this bounding run

If the solver finds something BETTER than your roster it keeps the better one and says
so -- which is useful information in itself: it means the original run stopped early.

FOR THE WRITE-UP
  Say that a MIP start was supplied. Warm starting is standard practice and does not
  weaken the result -- the bound is proved by the solver either way, and the reported
  gap is valid for the solution reported beside it. Do not present solve_s here as the
  time taken to FIND the solution; it is the time taken to bound it.
"""
import argparse, csv, glob, os, re
import tbl_data as D

RESULTS = os.path.join(D.HERE, "results")

ap = argparse.ArgumentParser()
ap.add_argument("--only", nargs="*", default=None, help="instance numbers or names")
ap.add_argument("--time", type=float, default=300., help="seconds per instance")
ap.add_argument("--gap", type=float, default=0.0, help="stop early at this gap")
ap.add_argument("--solver", choices=["gurobi", "ortools"], default="gurobi",
                help="gurobi is much better at raising bounds; that is the whole job here")
ap.add_argument("--workers", type=int, default=8)
a = ap.parse_args()

if a.solver == "gurobi":
    import tbl_gurobi as SOLVER
else:
    import tbl_ortools as SOLVER

def inst_no(s):
    d = "".join(c for c in str(s) if c.isdigit())
    return int(d) if d else 0

def load_roster(path, inst):
    roster = {}
    with open(path) as f:
        for r in csv.DictReader(l for l in f if not l.lstrip().startswith("#")):
            sid = r.get("staff_id")
            if sid not in inst.S: return None
            for d in inst.days:
                roster[(sid, d)] = (r.get(f"day{d}") or "").strip()
    return roster if all((i, 0) in roster for i in inst.staff) else None

want = {inst_no(t) for t in a.only} if a.only else None
jobs = []
for p in sorted(glob.glob(os.path.join(RESULTS, "*.csv"))):
    b = os.path.basename(p)
    if b.startswith("summary_") or b.endswith("_frontier.csv"): continue
    m = re.match(r"(Instance\d+)_(.+)_(ortools|gurobi)\.csv$", b)
    if not m: continue
    if want and inst_no(m.group(1)) not in want: continue
    if m.group(3) != a.solver: continue          # bound the rosters from this solver
    jobs.append((p, m.group(1), m.group(2)))
jobs.sort(key=lambda j: inst_no(j[1]))

if not jobs:
    raise SystemExit(f"no {a.solver} roster files found in results\\ "
                     f"(looking for Instance<N>_<tag>_{a.solver}.csv)")

print(f"bounding {len(jobs)} roster(s) with {a.solver}, {a.time:g}s each "
      f"-> worst case {len(jobs)*a.time/60:.0f} min\n")
hdr = (f"{'instance':<12}{'status':>12}{'solve':>8}{'gap%':>8}"
       f"{'objective A$':>16}{'bound A$':>16}{'note':>12}")
print(hdr); print("-" * len(hdr))

cols = ["instance", "staff", "days", "shift_types", "carbon_price", "status", "wall_s", "gap",
        "build_s", "solve_s", "bound_aud", "penalty", "pen_on", "pen_off", "pen_under",
        "pen_over", "F_econ", "F_soc", "F_env", "total", "env_share_pct", "kg_co2e",
        "shifts", "agency", "understaffed", "senior_short", "request_wt",
        "dev_shifts", "fatigue_blocks"]
by_out = {}

def flush():
    for (tag, solver), rows in by_out.items():
        out = os.path.join(RESULTS, f"summary_BOUNDED_{tag}_{solver}.csv")
        try:
            with open(out, "w", newline="") as f:
                w = csv.DictWriter(f, fieldnames=cols); w.writeheader(); w.writerows(rows)
        except PermissionError:
            import time as _t
            out = out[:-4] + f"_{_t.strftime('%H%M%S')}.csv"
            with open(out, "w", newline="") as f:
                w = csv.DictWriter(f, fieldnames=cols); w.writeheader(); w.writerows(rows)

for path, name, tag in jobs:
    try:
        inst = D.load_instance(name)
        T, P = D.load_tbl(inst)
        C = D.Coef(inst, T, P)
    except BaseException as ex:
        print(f"{name:<12}{'CANNOT LOAD':>12}   {str(ex).splitlines()[0][:50]}")
        continue
    start = load_roster(path, inst)
    if start is None:
        print(f"{name:<12}{'BAD ROSTER':>12}")
        continue

    objective = "dollars" if "dollars" in tag else "penalty"
    levers = "levers" in tag
    kw = {"start": start} if a.solver == "gurobi" else {}
    if a.solver == "ortools": kw = {"workers": a.workers}
    try:
        roster, info = SOLVER.solve(inst, C, objective, levers, a.gap, a.time,
                                    None, quiet=True, **kw)
    except KeyboardInterrupt:
        print(f"\n[interrupted] stopping. {sum(len(v) for v in by_out.values())} row(s) saved.")
        flush(); break
    except BaseException as ex:
        print(f"{name:<12}{'ERROR':>12}   {str(ex).splitlines()[0][:50]}")
        continue
    if roster is None:
        print(f"{name:<12}{info.get('status','?'):>12}   no solution returned")
        continue

    e = D.evaluate(inst, C, roster, info["agency"], info["ot_min"], None)
    g = float(info.get("gap") or 0.0)
    bound = e["total"] * (1.0 - g)
    note = ""
    # Did the warm start get beaten? That means the ORIGINAL run stopped early, and this
    # roster is now the better one -- worth knowing, and worth using.
    try:
        # The start roster must be costed the SAME WAY recover_rosters.py costs it --
        # agency reconstructed, overtime derived -- or the comparison is meaningless.
        # Evaluating it with zero agency inflates its cost and flags every instance
        # as "improved".
        cap = int(P["agency_max_per_shift"]) if levers else 0
        ag0 = {(d, s): int(min(cap, max(0, inst.R[d, s]
                  - sum(1 for i in inst.staff if start[(i, d)] == s))))
               for (d, s) in inst.R}
        ot0 = {i: max(0.0, sum(inst.length_min[start[(i, d)]]
                               for d in inst.days if start[(i, d)])
                      - float(inst.S[i]["max_total_min"])) for i in inst.staff}
        old = D.evaluate(inst, C, start, ag0, ot0, None)
        if e["total"] < old["total"] - 0.01:
            note = f"-{(old['total']-e['total'])/old['total']*100:.1f}%"
    except BaseException:
        pass
    print(f"{name:<12}{info['status']:>12}{info.get('solve_s',0):>8.1f}{g*100:>8.2f}"
          f"{e['total']:>16,.2f}{bound:>16,.2f}{note:>12}")

    by_out.setdefault((tag, a.solver), []).append(dict(
        instance=name, staff=len(inst.staff), days=inst.n_days,
        shift_types="/".join(inst.shifts), carbon_price=P["carbon_price_aud_per_t"],
        status=info["status"], wall_s=round(info.get("wall", 0), 2), gap=round(g, 6),
        build_s=round(info.get("build_s", 0), 2), solve_s=round(info.get("solve_s", 0), 2),
        bound_aud=round(bound, 2),
        penalty=round(e["penalty"]), pen_on=round(e["pen_on"]), pen_off=round(e["pen_off"]),
        pen_under=round(e["pen_under"]), pen_over=round(e["pen_over"]),
        F_econ=round(e["F_econ"], 2), F_soc=round(e["F_soc"], 2), F_env=round(e["F_env"], 4),
        total=round(e["total"], 2),
        env_share_pct=round(e["F_env"] / e["total"] * 100, 5) if e["total"] else "",
        kg_co2e=round(e["kg"], 2), shifts=e["shifts"], agency=e["n_agency"],
        understaffed=e["n_under"], senior_short=e["n_senior_short"],
        request_wt=e["q_on"] + e["q_off"], dev_shifts=round(e["dev"], 2),
        fatigue_blocks=e["n_fatigue"]))
    flush()          # written after every instance -- a crash costs you one, not all

print("-" * len(hdr))
n = sum(len(v) for v in by_out.values())
for (tag, solver) in by_out:
    print(f"  -> results\\summary_BOUNDED_{tag}_{solver}.csv")
print(f"{n} row(s) with real gaps and bounds")
