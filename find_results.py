"""
find_results.py -- WHERE ARE MY NUMBERS? Inventory everything in results\\.

    python find_results.py                 what you have, and what is missing
    python find_results.py --instance 7    everything ever recorded for one instance

Results end up spread across several summary files because the filename carries the
settings (objective, levers, gap, time, solver, instance subset). That is deliberate --
it stops one run overwriting another -- but it does mean "I ran instance 7 somewhere"
is not the same as "I can find instance 7". This prints the coverage matrix so you can
see at a glance which instances have a usable result and which still need running.

A result is USABLE if it has a status of OPTIMAL, WITHIN-GAP or FEASIBLE. Anything else
(INTERRUPTED, UNKNOWN, ERROR, out of memory) is shown but not counted.
"""
import argparse, csv, glob, os, sys
import tbl_data as D

RESULTS = os.path.join(D.HERE, "results")
GOOD = ("OPTIMAL", "WITHIN-GAP", "FEASIBLE")

ap = argparse.ArgumentParser()
ap.add_argument("--instance", default=None, help="show every record for this instance")
a = ap.parse_args()

def inst_no(s):
    d = "".join(c for c in str(s) if c.isdigit())
    return int(d) if d else 0

if not os.path.isdir(RESULTS):
    raise SystemExit(f"there is no results\\ folder yet at {RESULTS}")

files = sorted(glob.glob(os.path.join(RESULTS, "summary_*.csv")))
if not files:
    print("NO summary_*.csv files at all in results\\.")
    print("\nIf you ran a batch and it was killed or crashed partway, and you were using")
    print("the OLD run_all.py, the summary was only written when the WHOLE batch finished")
    print("-- so an interrupted batch wrote nothing. The current run_all.py rewrites the")
    print("file after every instance. Check you are running the updated copy.")
    sys.exit(1)

records = {}          # instance -> list of (settings, solver, status, total, solve_s, file)
for p in files:
    base = os.path.basename(p)[len("summary_"):-len(".csv")]
    solver = base.rsplit("_", 1)[-1]
    settings = base[:-(len(solver) + 1)]
    try:
        with open(p) as f:
            rows = list(csv.DictReader(l for l in f if not l.lstrip().startswith("#")))
    except Exception as ex:
        print(f"  [!] could not read {os.path.basename(p)}: {ex}")
        continue
    for r in rows:
        records.setdefault(r.get("instance", "?"), []).append(
            dict(settings=settings, solver=solver, status=r.get("status", ""),
                 total=r.get("total", ""), solve_s=r.get("solve_s", ""),
                 gap=r.get("gap", ""), penalty=r.get("penalty", ""),
                 file=os.path.basename(p)))

# ---------------------------------------------------------------- single instance
if a.instance:
    key = a.instance if not str(a.instance).isdigit() else f"Instance{a.instance}"
    recs = records.get(key, [])
    print(f"=== every record for {key} ===")
    if not recs:
        print(f"  NOTHING recorded for {key} in any summary file.")
        print(f"  It has not been solved, or the batch that solved it never wrote its file.")
        sys.exit(0)
    for r in recs:
        print(f"  {r['status']:<22} total {r['total']:>14}  penalty {r['penalty']:>8}  "
              f"solve {r['solve_s']:>8}s")
        print(f"     {r['solver']} / {r['settings']}")
        print(f"     {r['file']}")
    sys.exit(0)

# ---------------------------------------------------------------- coverage matrix
print(f"{len(files)} summary file(s) in results\\\n")
for p in files:
    base = os.path.basename(p)
    with open(p) as f:
        rows = [r for r in csv.DictReader(l for l in f if not l.lstrip().startswith("#"))]
    got = sorted({inst_no(r.get("instance", "")) for r in rows})
    ok = sum(1 for r in rows if any(g in str(r.get("status", "")) for g in GOOD))
    print(f"  {base}")
    print(f"      {len(rows)} row(s), {ok} usable | instances: "
          f"{', '.join(str(g) for g in got) if got else '(none)'}")

print("\n" + "=" * 72)
print("  COVERAGE -- does each instance have at least one usable result?")
print("=" * 72)
prepared = D.list_instances()
missing_any, only_penalty = [], []
for name in sorted(prepared, key=inst_no):
    recs = records.get(name, [])
    usable = [r for r in recs if any(g in r["status"] for g in GOOD)]
    dollars = [r for r in usable if "dollars" in r["settings"] and "sweep" not in r["settings"]]
    penalty = [r for r in usable if r["settings"].startswith("penalty")]
    if not usable:
        mark, note = "MISSING", "no usable result anywhere"
        missing_any.append(inst_no(name))
    elif not dollars:
        mark, note = "part", "benchmark objective only -- no monetised run"
        only_penalty.append(inst_no(name))
    else:
        best = min(dollars, key=lambda r: float(r["total"] or 1e18))
        mark = "ok"
        note = (f"best total A${float(best['total']):,.0f} "
                f"({best['solver']}, {best['status']})")
    print(f"  {name:<12} {mark:<8} {note}")
    if recs and not usable:
        for r in recs[:3]:
            print(f"               ^ recorded as {r['status']} in {r['file']}")

print("-" * 72)
if missing_any:
    print(f"  NOTHING usable for: {missing_any}")
    print(f"    run:  python run_all.py --only {' '.join(map(str, missing_any))} "
          f"--objective dollars --levers --gap 0.01 --time 1800 --verbose")
if only_penalty:
    print(f"  benchmark objective only (need the monetised run): {only_penalty}")
    print(f"    run:  python run_all.py --only {' '.join(map(str, only_penalty))} "
          f"--objective dollars --levers --gap 0.01 --time 1800 --verbose")
if not missing_any and not only_penalty:
    print("  every prepared instance has a usable monetised result.")
print("=" * 72)
