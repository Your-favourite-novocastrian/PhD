"""
make_tables.py -- turn the raw summary CSVs into THESIS-READY tables.

    python make_tables.py

Writes tables\\ as both .csv (open in Excel) and .tex (booktabs, paste into LaTeX):

  T1_instances        instance characteristics: staff, days, shift types, total demand
  T2_validation       benchmark objective vs published optimum -- the correctness table
  T3_main_results     THE RESULTS TABLE: one row per instance, dollars + levers
  T4_carbon_sweep     instance x carbon price -> F_env and total
  T5_pillar_split     economic / social / environmental as A$ and as % of total

The summary CSVs in results\\ are the archive: every run of every experiment, stacked.
These tables are the SELECTION: one row per instance for one experiment, which is what
a results chapter actually prints. Nothing is recomputed here -- it only reads and
reshapes what the solver already wrote, so these can never disagree with the raw data.

Re-run it any time; it is instant and overwrites cleanly.
"""
import csv, glob, os, re
import tbl_data as D

HERE = D.HERE
RESULTS = os.path.join(HERE, "results")
OUT = os.path.join(HERE, "tables")

# Best known solution values for the BENCHMARK PENALTY OBJECTIVE, as published at
# schedulingbenchmarks.org/nrp (Curtois & Qu). Instance1 = 607 is independently
# reproduced by this codebase, which is the consistency check on the rest of the list.
#
# 23 of the 24 are proven optimal: best known lower bound == best known solution.
# Instance24 is the sole exception, LB 40,438 vs best 40,439 -- a gap of ONE.
# The site notes that solutions are verified but lower bounds cannot be.
#
# THESE APPLY ONLY TO --objective penalty. They are NOT bounds on the monetised
# objective, which is a different objective over a larger feasible region.
PUBLISHED = {
    "Instance1":   607, "Instance2":   828, "Instance3":  1001, "Instance4":  1716,
    "Instance5":  1143, "Instance6":  1950, "Instance7":  1056, "Instance8":  1300,
    "Instance9":   439, "Instance10": 4631, "Instance11": 3443, "Instance12": 4040,
    "Instance13": 1348, "Instance14": 1278, "Instance15": 3828, "Instance16": 3225,
    "Instance17": 5746, "Instance18": 4459, "Instance19": 3149, "Instance20": 4769,
    "Instance21": 21133, "Instance22": 30241, "Instance23": 17285, "Instance24": 40439,
}
NOT_PROVEN = {"Instance24": 40438}      # best known LOWER BOUND where it differs

def rd(p):
    with open(p) as f:
        return list(csv.DictReader(l for l in f if not l.lstrip().startswith("#")))

def n(v, cast=float):
    try: return cast(v)
    except (TypeError, ValueError): return None

def inst_no(s):
    d = "".join(c for c in s if c.isdigit())
    return int(d) if d else 0

def load_runs():
    """Every row of every summary file, tagged with the settings that produced it."""
    out = []
    for p in sorted(glob.glob(os.path.join(RESULTS, "summary_*.csv"))):
        base = os.path.basename(p)[len("summary_"):-len(".csv")]
        solver = base.rsplit("_", 1)[-1]
        settings = base[:-(len(solver) + 1)]
        for r in rd(p):
            r["_settings"], r["_solver"] = settings, solver
            out.append(r)
    return out

def best_per_instance(rows, key="total"):
    """When an instance was run more than once under the same experiment, keep the best
    (lowest objective). A later run with a longer time limit should win, and comparing
    by objective rather than by filename date makes that automatic."""
    # A run you killed mid-solve is not a result. Gurobi returns INTERRUPTED with a valid
    # but arbitrary incumbent and a huge gap; letting that into a thesis table would
    # report "the answer" for an instance that was never actually solved.
    BAD = ("NO SOLUTION", "INTERRUPTED", "ERROR", "UNKNOWN", "INFEASIBLE",
           "NUMERIC_TROUBLE", "OUT_OF_MEMORY", "GUROBI_STATUS_")
    keep = {}
    for r in rows:
        st = str(r.get("status") or "")
        if not st or any(b in st for b in BAD): continue
        v = n(r.get(key))
        if v is None: continue
        k = r["instance"]
        if k not in keep or v < n(keep[k].get(key), float):
            keep[k] = r
    return [keep[k] for k in sorted(keep, key=inst_no)]

TEX_ESC = {"\\": r"\textbackslash{}", "_": r"\_", "%": r"\%",
           "&": r"\&", "#": r"\#", "$": r"\$"}
def tex(s):
    s = str(s)
    for a, b in TEX_ESC.items(): s = s.replace(a, b)
    return s

def write(name, header, rows, caption, align=None):
    os.makedirs(OUT, exist_ok=True)
    cp = os.path.join(OUT, f"{name}.csv")
    def _wc(p):
        with open(p, "w", newline="") as f:
            w = csv.writer(f); w.writerow(header); w.writerows(rows)
    D.safe_write(cp, _wc, name)

    al = align or ("l" + "r" * (len(header) - 1))
    L = [r"\begin{table}[htbp]", r"\centering", f"\\caption{{{tex(caption)}}}",
         f"\\label{{tab:{name.lower()}}}", f"\\begin{{tabular}}{{{al}}}", r"\toprule",
         " & ".join(tex(h) for h in header) + r" \\", r"\midrule"]
    L += [" & ".join("" if c is None else tex(c) for c in r) + r" \\" for r in rows]
    L += [r"\bottomrule", r"\end{tabular}", r"\end{table}"]
    def _wt(p):
        with open(p, "w", encoding="utf-8") as f: f.write("\n".join(L) + "\n")
    D.safe_write(os.path.join(OUT, f"{name}.tex"), _wt, name)
    print(f"  {name:<18} {len(rows):>3} rows  ->  tables\\{name}.csv  +  .tex")

runs = load_runs()
if not runs:
    raise SystemExit("no summary_*.csv in results\\ -- run run_all.py or run_everything.py first")
os.makedirs(OUT, exist_ok=True)
print(f"reading {len(runs)} run row(s) from results\\")

# ------------------------------------------------------------------ T1 instances
rows = []
for name in sorted(D.list_instances(), key=inst_no):
    inst = D.load_instance(name)
    demand = sum(inst.R.values())
    rows.append([name, len(inst.staff), inst.n_days, len(inst.shifts),
                 "/".join(inst.shifts), demand,
                 len(inst.on_req), len(inst.off_req), len(inst.days_off)])
write("T1_instances",
      ["Instance", "Staff", "Days", "Shift types", "Shifts", "Total demand",
       "On-requests", "Off-requests", "Days off"], rows,
      "Benchmark instance characteristics (Curtois and Qu nurse rostering set).")

# ------------------------------------------------------------------ T2 validation
val = best_per_instance([r for r in runs if r["_settings"].startswith("penalty")
                         and "sweep" not in r["_settings"]], key="penalty")
rows = []
for r in val:
    pen, pub = n(r.get("penalty"), int), PUBLISHED.get(r["instance"])
    lb = NOT_PROVEN.get(r["instance"])
    if pub is None or pen is None:
        verdict, excess = "--", ""
    elif pen == pub:
        verdict, excess = "MATCH", "0.00"
    else:
        # How far above the published best. This is the number that says whether a
        # non-match is a real modelling problem or just an unfinished search.
        verdict, excess = ("above" if pen > pub else "BELOW*"), f"{(pen - pub) / pub * 100:+.2f}"
    rows.append([r["instance"], r.get("status", ""),
                 f"{n(r.get('solve_s')):.1f}" if n(r.get("solve_s")) is not None else "",
                 f"{n(r.get('gap'))*100:.2f}" if n(r.get("gap")) is not None else "",
                 pen, pub if pub is not None else "--",
                 lb if lb is not None else "= best", verdict, excess])
write("T2_validation",
      ["Instance", "Status", "Solve (s)", "Gap (%)", "Penalty", "Published best",
       "Published LB", "Verdict", "Excess (%)"],
      rows,
      "Validation of the base model against the published benchmark objective "
      "(schedulingbenchmarks.org, Curtois and Qu). 23 of 24 published values are proven "
      "optimal, the lower bound equalling the best known solution; Instance24 is the "
      "exception, with a lower bound of 40,438 against a best known 40,439. A value "
      "above the published best means the search was not run long enough, not that the "
      "model is wrong. A value BELOW the published best would indicate a missing "
      "constraint and must be investigated.")

# ------------------------------------------------------------------ T3 main results
main = best_per_instance([r for r in runs if "dollars" in r["_settings"]
                          and "sweep" not in r["_settings"]])
rows = []
for r in main:
    g = n(r.get("gap"))
    rows.append([r["instance"], r.get("staff", ""), r.get("days", ""), r.get("status", ""),
                 f"{n(r.get('build_s')):.1f}" if n(r.get("build_s")) is not None else "",
                 f"{n(r.get('solve_s')):.1f}" if n(r.get("solve_s")) is not None else "",
                 f"{g*100:.2f}" if g is not None else "",
                 f"{n(r.get('F_econ')):,.0f}" if n(r.get("F_econ")) is not None else "",
                 f"{n(r.get('F_soc')):,.0f}" if n(r.get("F_soc")) is not None else "",
                 f"{n(r.get('F_env')):,.2f}" if n(r.get("F_env")) is not None else "",
                 f"{n(r.get('total')):,.0f}" if n(r.get("total")) is not None else "",
                 r.get("agency", ""), r.get("understaffed", ""), r.get("senior_short", "")])
write("T3_main_results",
      ["Instance", "Staff", "Days", "Status", "Build (s)", "Solve (s)", "Gap (%)",
       "F econ (A$)", "F soc (A$)", "F env (A$)", "Total (A$)",
       "Agency", "Under", "Sen. short"], rows,
      "Monetised objective, all three pillars, with agency and overtime levers enabled. "
      "Status WITHIN-GAP indicates the gap tolerance was met but optimality not proven.")

# ------------------------------------------------------------------ T4 carbon sweep
sw = [r for r in runs if "sweep" in r["_settings"]]
if sw:
    prices = sorted({n(r.get("carbon_price")) for r in sw if n(r.get("carbon_price")) is not None})
    insts = sorted({r["instance"] for r in sw}, key=inst_no)
    look = {(r["instance"], n(r.get("carbon_price"))): r for r in sw}
    rows = []
    for i in insts:
        env = [look.get((i, p)) for p in prices]
        rows.append([i] + [f"{n(e.get('F_env')):,.2f}" if e and n(e.get("F_env")) is not None
                           else "" for e in env]
                        + [f"{n(e.get('total')):,.0f}" if e and n(e.get("total")) is not None
                           else "" for e in env])
    write("T4_carbon_sweep",
          ["Instance"] + [f"F env @ {p:g}" for p in prices]
                       + [f"Total @ {p:g}" for p in prices], rows,
          "Carbon price sensitivity (A$ per tonne CO2e). F env scales linearly with the "
          "price by construction; the question is whether the total changes, that is, "
          "whether any assignment is altered.")
else:
    print("  T4_carbon_sweep    skipped -- no --carbon-sweep runs found")

# ------------------------------------------------------------------ T5 pillar split
rows = []
for r in main:
    e, s, v, t = (n(r.get(k)) for k in ("F_econ", "F_soc", "F_env", "total"))
    if None in (e, s, v, t) or t == 0: continue
    rows.append([r["instance"], f"{e:,.0f}", f"{e/t*100:.2f}", f"{s:,.0f}", f"{s/t*100:.2f}",
                 f"{v:,.2f}", f"{v/t*100:.4f}", f"{t:,.0f}",
                 f"{n(r.get('kg_co2e')):,.0f}" if n(r.get("kg_co2e")) is not None else ""])
write("T5_pillar_split",
      ["Instance", "F econ (A$)", "% econ", "F soc (A$)", "% soc", "F env (A$)",
       "% env", "Total (A$)", "kg CO2e"], rows,
      "Triple bottom line split by pillar at the market carbon price. The environmental "
      "share is reported to four decimal places because it is a fraction of a percent.")

print(f"\ntables written to {OUT}")
print("  .csv opens in Excel; .tex is booktabs -- \\usepackage{booktabs} in your preamble")
