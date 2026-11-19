"""
check_setup.py -- verify a fresh machine has EVERYTHING before you start a long run.

    python check_setup.py

Checks, in order, and tells you exactly what to do about each failure:
  1. Python version
  2. the 17 code files
  3. prices.csv, and that it parses with the expected keys
  4. instances\\*.txt -- how many, which numbers are missing
  5. ortools and openpyxl importable (gurobipy checked but NOT required)
  6. prep_all can parse every instance and every sidecar matches its staff list
  7. THE VALIDATION: Instance1 under the benchmark objective must give penalty 607

Exit code 0 means go. Anything else means stop and fix what it printed.
Nothing here writes to results\\ and nothing takes more than a few seconds.
"""
import glob, importlib, os, sys, traceback

HERE = os.path.dirname(os.path.abspath(__file__))
ok = True          # everything passed
runnable = True    # code + packages are fine, so steps 6-7 are worth attempting
def good(m): print(f"  [ok]   {m}")
def bad(m, fix="", fatal=True):
    """fatal=False for things that do not stop the model itself from working --
    a missing Instance17 is a real problem for the batch but Instance1 still validates,
    and you want to SEE that validation pass rather than have it skipped."""
    global ok, runnable
    ok = False
    if fatal: runnable = False
    print(f"  [FAIL] {m}")
    if fix: print(f"         -> {fix}")
def warn(m): print(f"  [note] {m}")

print("=" * 74)
print("  TBL-HWSP setup check")
print(f"  folder: {HERE}")
print("=" * 74)

# ---------------------------------------------------------------- 1. python
print("\n1. Python")
v = sys.version_info
if v >= (3, 9): good(f"Python {v.major}.{v.minor}.{v.micro}")
else: bad(f"Python {v.major}.{v.minor} is too old", "install Python 3.9 or newer")

# ---------------------------------------------------------------- 2. code files
print("\n2. Code files")
NEEDED = ["tbl_data.py", "tbl_ortools.py", "tbl_gurobi.py", "prep_all.py",
          "run_all.py", "run_everything.py", "make_report.py", "check_setup.py",
          "export_augmented.py", "make_tables.py", "tbl_epsilon.py",
          "find_results.py", "recover_rosters.py", "add_bounds.py",
          "make_workbook.py", "import_console_log.py"]
for f in NEEDED:
    p = os.path.join(HERE, f)
    if os.path.exists(p): good(f"{f}  ({os.path.getsize(p):,} bytes)")
    else: bad(f"{f} is MISSING", "ask Claude to re-send this file")

# ---------------------------------------------------------------- 3. prices
print("\n3. prices.csv")
EXPECT_KEYS = {"carbon_price_aud_per_t", "agency_cost_per_shift", "agency_max_per_shift",
               "overtime_multiplier", "overtime_max_min", "rho_understaffing",
               "min_senior_share", "rho_senior_shortfall", "agency_travel_kg",
               "e_anc_kg_per_over_shift", "turnover_cost_aud", "p_leave_per_pref_weight",
               "p_leave_per_dev_shift", "kappa_fatigue", "kappa_churn"}
pp = os.path.join(HERE, "prices.csv")
if not os.path.exists(pp):
    bad("prices.csv is MISSING", "ask Claude to re-send it -- every A$ value lives here")
else:
    import csv as _csv
    with open(pp) as f:
        got = {r["key"] for r in _csv.DictReader(l for l in f if not l.lstrip().startswith("#"))}
    miss = EXPECT_KEYS - got
    if miss: bad(f"prices.csv is missing keys: {sorted(miss)}", "re-send prices.csv")
    else: good(f"prices.csv has all {len(EXPECT_KEYS)} expected keys ({len(got)} rows total)")

# ---------------------------------------------------------------- 4. instances
print("\n4. Benchmark instances")
idir = os.path.join(HERE, "instances")
if not os.path.isdir(idir):
    bad("there is no instances\\ folder",
        "create it and put the 24 Instance*.txt files in it -- these are YOUR input "
        "data and must come across from the other machine")
else:
    found = {}
    for p in glob.glob(os.path.join(idir, "*.txt")):
        d = "".join(c for c in os.path.basename(p) if c.isdigit())
        if d: found[int(d)] = os.path.basename(p)
    if not found:
        bad("instances\\ exists but contains no Instance*.txt files",
            "copy the .txt files across from the other machine")
    else:
        missing = [k for k in range(1, 25) if k not in found]
        good(f"{len(found)} instance file(s) present: {min(found)}..{max(found)}")
        if missing:
            bad(f"missing instance numbers: {missing}",
                "copy these .txt files across -- run_all will silently skip them otherwise",
                fatal=False)
        else:
            good("all 24 instances present")

# ---------------------------------------------------------------- 5. packages
print("\n5. Python packages")
for mod, why, fix in [
    ("ortools", "the CP-SAT solver -- REQUIRED", "pip install ortools"),
    ("openpyxl", "writes the Excel workbook -- REQUIRED", "pip install openpyxl"),
]:
    try:
        importlib.import_module(mod); good(f"{mod} -- {why}")
    except Exception as e:
        bad(f"{mod} will not import ({type(e).__name__}) -- {why}", fix)
try:
    import gurobipy
    warn(f"gurobipy {'.'.join(map(str, gurobipy.gurobi.version()))} is installed -- "
         f"the pip licence caps models at 2000 vars/constraints, so only Instance1-2 "
         f"will run. Use --no-gurobi for the batch.")
except Exception:
    warn("gurobipy not installed -- fine, use --no-gurobi. Nothing else is affected.")

# ---------------------------------------------------------------- 6. parse + sidecars
print("\n6. Parse every instance and check its sidecar")
if not runnable:
    warn("skipping -- fix the failures above first")
else:
    try:
        import tbl_data as D
        prepared = D.list_instances()
        if not prepared:
            warn("nothing prepared yet -- that is expected on a new machine. "
                 "prep_all.py runs first inside run_everything.py.")
        else:
            bad_sc = []
            for n in prepared:
                inst = D.load_instance(n)
                T, _ = D.load_tbl(inst)
                miss = [i for i in inst.staff if i not in T]
                if miss: bad_sc.append((n, miss[:5]))
            if bad_sc:
                for n, m in bad_sc:
                    bad(f"{n}: sidecar has no row for staff {m}",
                        f"python prep_all.py --only {n} --force", fatal=False)
            else:
                good(f"{len(prepared)} prepared instance(s), every staff member has a "
                     f"sidecar row")
            # FINGERPRINT of the synthetic data. prep_all seeds its RNG per instance, so
            # the same seed SHOULD give the same nurses everywhere -- but random.choices
            # and normalvariate internals have changed between CPython releases, so
            # "should" is not "does". Compare this number across machines: identical means
            # your results are directly comparable; different means the synthetic draw
            # differs and you must copy data\\ across instead of regenerating it.
            import hashlib
            h = hashlib.sha256()
            for n in sorted(prepared):
                p = os.path.join(D.HERE, "data", n, "staff_tbl.csv")
                if not os.path.exists(p): continue
                # Normalise before hashing. Windows writes CRLF and Linux writes LF for
                # the same data, so hashing raw bytes flags a line-ending difference as
                # a data difference -- a false alarm that sends you hunting a non-problem.
                # Only the VALUES should move this number.
                with open(p, "rb") as f:
                    for line in f:
                        line = line.strip()
                        if line and not line.startswith(b"#"): h.update(line + b"\n")
            print(f"         synthetic-data fingerprint: {h.hexdigest()[:16]}")
            print(f"         (compare this with the other machine -- must match)")
    except Exception:
        bad("tbl_data.py could not be imported or used:")
        traceback.print_exc()

# ---------------------------------------------------------------- 7. the validation
print("\n7. VALIDATION -- Instance1, benchmark objective, must be penalty 607")
if not runnable:
    warn("skipping -- fix the failures above first")
else:
    try:
        import tbl_data as D
        if "Instance1" not in D.list_instances():
            warn("Instance1 not prepared yet. After prep_all.py runs, re-run this check "
                 "to confirm 607.")
        else:
            import tbl_ortools as ORT
            inst = D.load_instance("Instance1")
            T, P = D.load_tbl(inst)
            C = D.Coef(inst, T, P)
            roster, info = ORT.solve(inst, C, "penalty", False, 0.0, 120., None, quiet=True)
            if roster is None:
                bad(f"Instance1 returned {info['status']} -- no solution at all")
            else:
                e = D.evaluate(inst, C, roster, info["agency"], info["ot_min"], None)
                pen = round(e["penalty"])
                if pen == 607 and info["status"] == "OPTIMAL":
                    good(f"penalty {pen}, status OPTIMAL -- matches the published optimum. "
                         f"The model is correct on this machine.")
                else:
                    bad(f"got penalty {pen}, status {info['status']} -- expected 607/OPTIMAL",
                        "the instance file or a code file differs from the working set; "
                        "re-send tbl_data.py, tbl_ortools.py and Instance1.txt")
    except Exception:
        bad("the validation solve raised:")
        traceback.print_exc()

print("\n" + "=" * 74)
if ok:
    print("  ALL CHECKS PASSED -- safe to start.")
    print("  next:  python run_everything.py --no-gurobi --plan     (see the time estimate)")
    print("  then:  python run_everything.py --no-gurobi")
else:
    print("  SOMETHING IS MISSING -- fix the [FAIL] lines above before running a batch.")
print("=" * 74)
sys.exit(0 if ok else 1)
