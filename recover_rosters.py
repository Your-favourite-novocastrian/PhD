"""
recover_rosters.py -- rebuild summary rows from the ROSTER files when a summary was lost.

    python recover_rosters.py                     recover everything recoverable
    python recover_rosters.py --only 7 8 9 10     just these

Writes results\\summary_RECOVERED_<tag>_<solver>.csv

WHY THIS WORKS
--------------
run_all.py writes TWO kinds of file. The summary is one row per instance and older
versions used a filename that did not carry the settings, so a later run overwrote it.
The ROSTERS are one file per instance -- Instance7_dollars_levers_ortools.csv -- and each
has a distinct name, so they survived.

A roster is the solution. Every term of the objective is a deterministic function of it:
wages from who worked which shift, emissions from their commutes, requests and fairness
from the assignment. So the objective values are not lost, they just need recomputing.
This script does exactly that, through the SAME tbl_data.evaluate() the solver runs use,
so the recovered numbers are identical to what the original run reported.

WHAT IS RECOVERED EXACTLY
  F_econ, F_soc, F_env, total, penalty and its four parts, kg CO2e, shifts,
  understaffing, senior shortfall, request weight, fairness deviation, fatigue blocks.

WHAT IS RECONSTRUCTED (stated, not guessed)
  overtime  -- exactly: minutes worked above each nurse's max_total_min IS the overtime.
  agency    -- inferred. The roster records who worked, not how many agency staff were
               bought. Where staffing falls short of the requirement the optimiser would
               always buy agency before leaving a gap, because agency_cost_per_shift
               (605) is cheaper than rho_understaffing (1200), and the emissions
               difference is cents. So shortfalls are filled with agency up to
               agency_max_per_shift and any remainder counted as understaffing.
               That is what an optimal solution did; it is still a reconstruction.

WHAT IS GONE FOR GOOD
  status, MIP gap, solve_s, build_s. Solver metadata is not in the roster. Rows are
  marked status=RECOVERED with blank timings. If you need computation time and gap for
  the thesis -- and for Task 3 you do -- those instances must be re-run. But you get the
  objective values back now, and can re-run for timings without racing the deadline.
"""
import argparse, csv, glob, math, os, re
import tbl_data as D

RESULTS = os.path.join(D.HERE, "results")

ap = argparse.ArgumentParser()
ap.add_argument("--only", nargs="*", default=None, help="instance numbers or names")
args = ap.parse_args()

def inst_no(s):
    d = "".join(c for c in str(s) if c.isdigit())
    return int(d) if d else 0

def load_roster(path, inst):
    """Read the wide staff x day CSV back into the {(staff, day): shift} dict."""
    roster = {}
    with open(path) as f:
        for r in csv.DictReader(l for l in f if not l.lstrip().startswith("#")):
            sid = r.get("staff_id")
            if sid not in inst.S: return None          # roster is for a different instance
            for d in inst.days:
                roster[(sid, d)] = (r.get(f"day{d}") or "").strip()
    for i in inst.staff:                                # every nurse must be present
        if (i, 0) not in roster: return None
    return roster

want = None
if args.only:
    want = {inst_no(t) for t in args.only}

found, rejected = [], []
all_csv = sorted(glob.glob(os.path.join(RESULTS, "*.csv")))
for p in all_csv:
    b = os.path.basename(p)
    if b.startswith("summary_"): rejected.append((b, "is a summary, not a roster")); continue
    if b.endswith("_frontier.csv"): rejected.append((b, "epsilon frontier, not a roster")); continue
    m = re.match(r"(Instance\d+)_(.+)_(ortools|gurobi)\.csv$", b)
    if not m:
        rejected.append((b, "name does not look like Instance<N>_<tag>_<solver>.csv"))
        continue
    if want and inst_no(m.group(1)) not in want:
        rejected.append((b, "not in --only")); continue
    found.append((p, m.group(1), m.group(2), m.group(3)))

if not found:
    # Say exactly what WAS seen. An empty table with no explanation is useless when you
    # are trying to work out whether your results still exist.
    print("NO ROSTER FILES COULD BE USED.\n")
    print(f"results\\ contains {len(all_csv)} .csv file(s):")
    for b, why in rejected[:40]:
        print(f"    {b}\n        -> skipped: {why}")
    if len(rejected) > 40: print(f"    ... and {len(rejected)-40} more")
    if not all_csv:
        print("    (the folder is empty)")
    print("\nWHAT THIS MEANS")
    print("  A roster file is named like:  Instance7_dollars_levers_ortools.csv")
    print("  If you only see summary_*.csv, the version of run_all.py that produced those")
    print("  runs did not write per-instance rosters, and the objective values genuinely")
    print("  cannot be rebuilt -- those instances must be re-run.")
    print("  Also check results\\ is the right folder: this looked in")
    print(f"    {RESULTS}")
    raise SystemExit(1)

print(f"found {len(found)} roster file(s)\n")
hdr = f"{'instance':<12}{'tag':<34}{'solver':>8}{'F_econ':>13}{'F_soc':>11}{'F_env':>9}{'TOTAL A$':>14}"
print(hdr); print("-" * len(hdr))

by_out, n_ok, n_skip = {}, 0, 0
for path, name, tag, solver in found:
    # BaseException, not Exception: tbl_data raises SystemExit when an instance is not
    # prepared, and SystemExit does NOT inherit from Exception. Catching only Exception
    # let one bad instance kill the whole recovery right after the header printed --
    # which looked exactly like "found nothing".
    try:
        inst = D.load_instance(name)
        T, P = D.load_tbl(inst)
        C = D.Coef(inst, T, P)
    except BaseException as ex:
        why = str(ex).split("\n")[0][:70] or type(ex).__name__
        print(f"{name:<12} {tag:<34}{solver:>8}   CANNOT LOAD: {why}")
        n_skip += 1
        continue
    try:
        roster = load_roster(path, inst)
    except BaseException as ex:
        print(f"{name:<12} {tag:<34}{solver:>8}   unreadable roster: "
              f"{str(ex).split(chr(10))[0][:60]}")
        n_skip += 1
        continue
    if roster is None:
        print(f"{name:<12} {tag:<34}{solver:>8}   roster does not match this instance -- skipped")
        n_skip += 1
        continue

    # overtime: EXACT. minutes above the contractual maximum are, by definition, overtime.
    ot = {}
    for i in inst.staff:
        mins = sum(inst.length_min[roster[(i, d)]] for d in inst.days if roster[(i, d)])
        ot[i] = max(0.0, mins - float(inst.S[i]["max_total_min"]))

    # agency: INFERRED -- see the module docstring. Agency is cheaper than a gap, so the
    # optimiser fills shortfalls with agency up to the cap before accepting understaffing.
    levers = "levers" in tag
    cap = int(P["agency_max_per_shift"]) if levers else 0
    ag = {}
    for (d, s) in inst.R:
        staffed = sum(1 for i in inst.staff if roster[(i, d)] == s)
        ag[d, s] = int(min(cap, max(0, inst.R[d, s] - staffed)))

    try:
        e = D.evaluate(inst, C, roster, ag, ot, None)
    except BaseException as ex:
        print(f"{name:<12} {tag:<34}{solver:>8}   evaluate failed: "
              f"{str(ex).split(chr(10))[0][:55]}")
        n_skip += 1
        continue
    print(f"{name:<12} {tag:<34}{solver:>8}{e['F_econ']:>13,.2f}{e['F_soc']:>11,.2f}"
          f"{e['F_env']:>9,.2f}{e['total']:>14,.2f}")
    by_out.setdefault((tag, solver), []).append(dict(
        instance=name, staff=len(inst.staff), days=inst.n_days,
        shift_types="/".join(inst.shifts), carbon_price=P["carbon_price_aud_per_t"],
        status="RECOVERED", wall_s="", gap="", build_s="", solve_s="",
        penalty=round(e["penalty"]), pen_on=round(e["pen_on"]), pen_off=round(e["pen_off"]),
        pen_under=round(e["pen_under"]), pen_over=round(e["pen_over"]),
        F_econ=round(e["F_econ"], 2), F_soc=round(e["F_soc"], 2), F_env=round(e["F_env"], 4),
        total=round(e["total"], 2),
        env_share_pct=round(e["F_env"] / e["total"] * 100, 5) if e["total"] else "",
        kg_co2e=round(e["kg"], 2), shifts=e["shifts"], agency=e["n_agency"],
        understaffed=e["n_under"], senior_short=e["n_senior_short"],
        request_wt=e["q_on"] + e["q_off"], dev_shifts=round(e["dev"], 2),
        fatigue_blocks=e["n_fatigue"]))
    n_ok += 1

cols = ["instance", "staff", "days", "shift_types", "carbon_price", "status", "wall_s", "gap",
        "build_s", "solve_s", "penalty", "pen_on", "pen_off", "pen_under", "pen_over",
        "F_econ", "F_soc", "F_env", "total", "env_share_pct", "kg_co2e",
        "shifts", "agency", "understaffed", "senior_short", "request_wt",
        "dev_shifts", "fatigue_blocks"]
print("-" * len(hdr))
for (tag, solver), rows in sorted(by_out.items()):
    rows.sort(key=lambda r: inst_no(r["instance"]))
    out = os.path.join(RESULTS, f"summary_RECOVERED_{tag}_{solver}.csv")
    def _w(p, rows=rows):
        with open(p, "w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=cols); w.writeheader(); w.writerows(rows)
    # safe_write only exists in newer tbl_data.py. This tool must work against an OLD
    # tbl_data too -- recovering results is exactly the situation where you have old
    # files lying around -- so fall back to a plain write, with the same lock handling.
    _sw = getattr(D, "safe_write", None)
    if _sw is not None:
        out = _sw(out, _w, "recovered summary") or out
    else:
        try:
            _w(out)
        except PermissionError:
            import time as _t
            out = out[:-4] + f"_{_t.strftime('%H%M%S')}.csv"
            _w(out)
            print("      [!] target was locked (open in Excel?) -- saved under a new name")
    print(f"  {len(rows):>3} row(s) -> {os.path.basename(out)}")

print(f"\n{n_ok} recovered, {n_skip} skipped")
print("\nstatus is RECOVERED and gap / solve_s / build_s are BLANK -- those live only in the")
print("solver, not in the roster. Objective values are exact. Re-run these instances when")
print("you need computation times and gaps for the results chapter.")
