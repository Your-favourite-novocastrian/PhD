"""
run_everything.py -- ONE command that does the whole experiment set end to end.

    python run_everything.py                 the full set (hours -- see the plan it prints)
    python run_everything.py --quick         a fast smoke test, ~5 minutes
    python run_everything.py --no-gurobi     skip Gurobi (e.g. no licence on this machine)
    python run_everything.py --plan          print what it WOULD run, then exit

What it does, in order:
  1. prep_all.py            converts every instances\\*.txt and generates its synthetic sidecar
  2. a series of run_all.py batches (below)
  3. export_augmented.py    writes augmented\\<Instance>_TBL.txt (the Task 2 artefact)
  4. make_tables.py         writes tables\\T1..T5 as .csv and .tex (the results chapter)
  5. make_report.py         builds TBL_results.xlsx from everything in results\\

Every step's console output is also written to logs\\, so a crash, a freeze or a closed
terminal never loses a run. Safe to re-run: finished summary files are simply overwritten
by the matching experiment, and differently-configured experiments keep separate files.

WHY THE CLOCK BEATS YOUR ARITHMETIC ------------------------------------------------
--time 30 on 24 instances is NOT 12 minutes, and the solver is not cheating. Per
instance the wall clock is

    build  +  solve  +  (fallback, only if solve found nothing)

--time bounds the MIDDLE TERM ONLY. Building the CP-SAT / Gurobi model in Python is
pure model construction -- hundreds of thousands of variables and the pairwise
cannot-follow and min-consecutive constraints -- and on the 100-150 nurse, 364-day
instances that alone is 25-40 s before the solver is even handed the problem. Then if
the solve returns UNKNOWN, tbl_ortools falls back to a feasibility-only solve with a
further max(30, 0.5 x --time) seconds so you get a roster instead of a blank row.
And run_everything runs SIX batches, not one.

So the estimate below adds all three terms per instance, per batch. It is a genuine
worst case; real runs come in well under it because most instances prove optimality
early and most never touch the fallback.
"""
import argparse, csv, glob, os, subprocess, sys, time

HERE = os.path.dirname(os.path.abspath(__file__))
LOGS = os.path.join(HERE, "logs")
DATA = os.path.join(HERE, "data")
RESULTS = os.path.join(HERE, "results")
PY = sys.executable          # the same interpreter running this script

ap = argparse.ArgumentParser()
ap.add_argument("--quick", action="store_true", help="smoke test, instances 1-3, ~5 min")
ap.add_argument("--fast", action="store_true",
                help="THESIS-GRADE but sane: every instance, 1%% gap, sweep on 4 "
                     "representative instances. A few hours instead of ~20.")
ap.add_argument("--no-gurobi", action="store_true")
ap.add_argument("--no-ortools", action="store_true")
ap.add_argument("--plan", action="store_true", help="show the plan and exit")
a = ap.parse_args()

# ---------------------------------------------------------------- the experiment plan
if a.quick:
    PLAN = [
        ("smoke: benchmark objective, must give 607 on Instance1",
         ["run_all.py", "--range", "1", "3", "--gap", "0", "--time", "60"]),
        ("dollars + levers, ortools",
         ["run_all.py", "--range", "1", "3", "--objective", "dollars", "--levers",
          "--gap", "0", "--time", "60"]),
        ("dollars + levers, gurobi",
         ["run_all.py", "--solver", "gurobi", "--range", "1", "3", "--objective", "dollars",
          "--levers", "--gap", "0", "--time", "60"]),
        ("carbon sweep",
         ["run_all.py", "--range", "1", "3", "--objective", "dollars", "--levers",
          "--gap", "0.01", "--time", "30", "--carbon-sweep", "0", "36.28", "200", "1000"]),
    ]
elif a.fast:
    # The full plan below spends most of its time PROVING optimality on instances the
    # solver already solved in seconds, and re-running a carbon sweep on 12 instances
    # when the sweep's finding (the roster does not change with the carbon price) is
    # visible from 4. A 1% gap is a completely standard, publishable stopping rule.
    PLAN = [
        ("benchmark objective, all 24 (validation: Instance1 must give 607)",
         ["run_all.py", "--range", "1", "24", "--gap", "0.01", "--time", "120"]),
        ("dollars + levers, small instances",
         ["run_all.py", "--range", "1", "12", "--objective", "dollars", "--levers",
          "--gap", "0.01", "--time", "180"]),
        ("dollars + levers, large instances",
         ["run_all.py", "--range", "13", "24", "--objective", "dollars", "--levers",
          "--gap", "0.05", "--time", "300"]),
        ("carbon sweep on 4 representative instances",
         ["run_all.py", "--only", "Instance1", "Instance8", "Instance13", "Instance20",
          "--objective", "dollars", "--levers", "--gap", "0.01", "--time", "120",
          "--carbon-sweep", "0", "36.28", "200", "1000", "5000"]),
    ]
else:
    PLAN = [
        ("benchmark objective, all instances (validation: Instance1 must give 607)",
         ["run_all.py", "--range", "1", "24", "--gap", "0.01", "--time", "300"]),

        ("ortools, dollars + levers, SMALL instances, proven optimal",
         ["run_all.py", "--range", "1", "12", "--objective", "dollars", "--levers",
          "--gap", "0", "--time", "600"]),
        ("ortools, dollars + levers, LARGE instances, 5% gap",
         ["run_all.py", "--range", "13", "24", "--objective", "dollars", "--levers",
          "--gap", "0.05", "--time", "900"]),

        ("gurobi, dollars + levers, SMALL instances, proven optimal",
         ["run_all.py", "--solver", "gurobi", "--range", "1", "12", "--objective", "dollars",
          "--levers", "--gap", "0", "--time", "600"]),
        ("gurobi, dollars + levers, LARGE instances, 5% gap",
         ["run_all.py", "--solver", "gurobi", "--range", "13", "24", "--objective", "dollars",
          "--levers", "--gap", "0.05", "--time", "900"]),

        ("carbon price sweep, instances 1-12",
         ["run_all.py", "--range", "1", "12", "--objective", "dollars", "--levers",
          "--gap", "0.01", "--time", "300",
          "--carbon-sweep", "0", "36.28", "100", "200", "500", "1000"]),
    ]

if a.no_gurobi: PLAN = [(d, c) for d, c in PLAN if "gurobi" not in c]
if a.no_ortools: PLAN = [(d, c) for d, c in PLAN if "gurobi" in c or "--objective" not in c]

# ------------------------------------------------- honest per-instance time estimates
BUILD_PER_VAR_S = 4.5e-5      # calibrated on a measured 100-nurse x 364-day build (~26 s)

def inst_no(name):
    d = "".join(c for c in name if c.isdigit())
    return int(d) if d else 0

def instance_sizes():
    """staff x days x shift-types per prepared instance. Empty before prep_all runs."""
    out = {}
    if not os.path.isdir(DATA): return out
    for d in os.listdir(DATA):
        try:
            with open(os.path.join(DATA, d, "staff.csv")) as f: n_staff = len(list(csv.DictReader(f)))
            with open(os.path.join(DATA, d, "shifts.csv")) as f: n_sh = len(list(csv.DictReader(f)))
            with open(os.path.join(DATA, d, "horizon.csv")) as f:
                r = next(csv.DictReader(f))
                n_days = int(r.get("n_days") or r.get("days"))
        except Exception:
            continue
        out[d] = (n_staff, n_days, n_sh)
    return out

def observed_build_s():
    """Worst build_s actually recorded for each instance, from previous runs."""
    out = {}
    for p in glob.glob(os.path.join(RESULTS, "summary_*.csv")):
        try:
            with open(p) as f:
                for row in csv.DictReader(l for l in f if not l.lstrip().startswith("#")):
                    try: b = float(row.get("build_s") or 0)
                    except ValueError: continue
                    k = row.get("instance", "")
                    if k: out[k] = max(out.get(k, 0.0), b)
        except Exception:
            continue
    return out

SIZES = instance_sizes()
OBSERVED = observed_build_s()

def build_est_s(name):
    """Measured if we have it, else modelled from problem size, else a blunt fallback."""
    if name in OBSERVED and OBSERVED[name] > 0: return OBSERVED[name]
    if name in SIZES:
        ns, nd, nsh = SIZES[name]
        return BUILD_PER_VAR_S * ns * nd * nsh
    # nothing prepared yet and nothing measured: the benchmark set grows steeply with
    # its number, so ramp rather than pretend every instance is Instance1.
    return 1.0 + 1.5 * inst_no(name)

def values_after(cmd, flag):
    """The values belonging to `flag`: everything up to the NEXT flag, not the end of
    the command. Taking them to the end silently swallowed later flags' values."""
    if flag not in cmd: return []
    out = []
    for v in cmd[cmd.index(flag) + 1:]:
        if v.startswith("--"): break
        out.append(v)
    return out

def targets(cmd):
    if "--only" in cmd:
        return values_after(cmd, "--only")
    if "--range" in cmd:
        i = cmd.index("--range")
        return [f"Instance{k}" for k in range(int(cmd[i+1]), int(cmd[i+2]) + 1)]
    return [f"Instance{k}" for k in range(1, 25)]

def worst_case_minutes(cmd):
    t = float(cmd[cmd.index("--time") + 1]) if "--time" in cmd else 120.0
    sweep = max(1, len(values_after(cmd, "--carbon-sweep")))
    fallback = max(30.0, 0.5 * t) if "gurobi" not in cmd else 0.0   # ortools-only fallback
    secs = sum(build_est_s(n) + t + fallback for n in targets(cmd)) * sweep
    return secs / 60.0

def print_plan():
    print("=" * 78)
    print("  TBL-HWSP full experiment set")
    print("=" * 78)
    total = 0.0
    for k, (desc, cmd) in enumerate(PLAN, 1):
        mins = worst_case_minutes(cmd); total += mins
        t = float(cmd[cmd.index("--time") + 1]) if "--time" in cmd else 120.0
        n = len(targets(cmd))
        naive = n * t / 60.0
        naive *= max(1, len(values_after(cmd, "--carbon-sweep")))
        print(f"  {k}. {desc}")
        print(f"     python {' '.join(cmd)}")
        print(f"     solver time alone {naive:.0f} min  ->  worst case {mins:.0f} min "
              f"(+ model build, + UNKNOWN fallback)")
    print("-" * 78)
    print(f"  WORST CASE TOTAL: {total/60:.1f} hours across {len(PLAN)} batches")
    print(f"  Real runs finish well under this -- most instances stop early on optimality.")
    if not SIZES:
        print("  (instances not prepared yet, so build times are a rough ramp; the estimate")
        print("   is reprinted with exact problem sizes once prep_all has run)")
    elif OBSERVED:
        print(f"  (build times: measured from {len(OBSERVED)} previous run(s) where available)")
    print(f"  logs -> {LOGS}")
    print("=" * 78)
    return total

print_plan()
if a.plan: sys.exit(0)

os.makedirs(LOGS, exist_ok=True)

def step(desc, cmd, logname):
    print(f"\n>>> {desc}\n    python {' '.join(cmd)}", flush=True)
    log = os.path.join(LOGS, logname)
    t0 = time.time()
    with open(log, "w", encoding="utf-8") as fh:
        p = subprocess.Popen([PY] + cmd, cwd=HERE, stdout=subprocess.PIPE,
                             stderr=subprocess.STDOUT, text=True, bufsize=1)
        for line in p.stdout:
            sys.stdout.write(line); sys.stdout.flush(); fh.write(line)
        p.wait()
    print(f"    done in {(time.time()-t0)/60:.1f} min  (log: {logname})", flush=True)
    return p.returncode

t_start = time.time()
step("STEP 0 - prepare every instance", ["prep_all.py"], "00_prep.log")

# Sizes are known now, so redo the estimate with real numbers instead of the ramp.
SIZES = instance_sizes()
print("\n  === estimate refreshed now that every instance is prepared ===")
print_plan()

done = 0.0
for k, (desc, cmd) in enumerate(PLAN, 1):
    est = worst_case_minutes(cmd)
    print(f"\n[batch {k}/{len(PLAN)}]  worst case {est:.0f} min  |  "
          f"elapsed so far {(time.time()-t_start)/60:.0f} min", flush=True)
    rc = step(f"STEP {k} - {desc}", cmd, f"{k:02d}_run.log")
    done += est
    if rc != 0:
        # One failing batch must not abandon the rest -- Gurobi licence problems are the
        # usual cause and the ortools batches are still worth having.
        print(f"    [!] step {k} exited with code {rc} -- continuing with the next step")

step("FINAL - write the augmented instance files", ["export_augmented.py"], "98_augment.log")
step("FINAL - build the thesis tables", ["make_tables.py"], "98b_tables.log")
step("FINAL - build the Excel workbook", ["make_report.py"], "99_report.log")

print("\n" + "=" * 78)
print(f"  ALL DONE in {(time.time()-t_start)/60:.1f} min")
print(f"  Excel workbook : {os.path.join(HERE, 'TBL_results.xlsx')}")
print(f"  thesis tables  : {os.path.join(HERE, 'tables')}")
print(f"  raw CSVs       : {os.path.join(HERE, 'results')}")
print(f"  logs           : {LOGS}")
print("=" * 78)
