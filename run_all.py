"""
run_all.py -- solve every prepared instance and write one summary table.

    python run_all.py                                          stage 1 on all instances
    python run_all.py --objective dollars --levers             stage 3 on all
    python run_all.py --gap 0.01 --time 300                    1% gap, 5 min each
    python run_all.py --only Instance1 Instance2 Instance3
    python run_all.py --carbon-sweep 0 36.28 100 500 1000      one row per instance x price

Writes results\\summary_<tag>.csv with one row per run:
    instance, staff, days, shift_types, status, wall_s, gap,
    penalty, F_econ, F_soc, F_env, total, env_share_pct, kg_co2e,
    shifts, agency, understaffed, request_wt, dev_shifts, fatigue_blocks

That CSV is the thing you put in a results chapter -- and the thing to re-read when
you change a price, because every number in it moves together.
"""
import argparse, csv, os, time
import tbl_data as D
import tbl_ortools as ORT

ap = argparse.ArgumentParser()
ap.add_argument("--objective", choices=["penalty", "dollars"], default="penalty")
ap.add_argument("--levers", action="store_true")
ap.add_argument("--gap",  type=float, default=0.0,
                help="relative MIP gap, e.g. 0.01 for 1%%. Applies to BOTH solvers.")
ap.add_argument("--time", type=float, default=120.,
                help="time limit in seconds PER INSTANCE. Applies to BOTH solvers.")
ap.add_argument("--range", nargs=2, type=int, metavar=("FIRST", "LAST"), default=None,
                help="solve instances FIRST..LAST by number, e.g. --range 1 5")
ap.add_argument("--only", nargs="*", default=None,
                help="solve these instances by name, e.g. --only Instance1 Instance7")
ap.add_argument("--carbon-sweep", nargs="*", type=float, default=None,
                help="solve each instance once per carbon price")
ap.add_argument("--price", action="append", default=[], metavar="KEY=VALUE",
                help="override any row of prices.csv for this run only, e.g. "
                     "--price rho_understaffing=2000. Repeatable.")
ap.add_argument("--workers", type=int, default=8,
                help="CP-SAT search threads (ortools only). Use 1 for a reproducible search.")
ap.add_argument("--solver", choices=["ortools", "gurobi"], default="ortools")
ap.add_argument("--lowmem", action="store_true",
                help="gurobi only: 2 threads, spill the search tree to disk, dual simplex, "
                     "MIPFocus=1. Use when an instance dies with 'bad allocation'.")
ap.add_argument("--memlimit", type=float, default=0.0, metavar="GB",
                help="gurobi only: stop gracefully at this much RAM (status OUT_OF_MEMORY) "
                     "instead of crashing with 'bad allocation'. Set it a few GB below your "
                     "physical RAM, e.g. --memlimit 12 on a 16 GB machine.")
ap.add_argument("--no-workbook", action="store_true",
                help="skip rebuilding TBL_HWSP_results.xlsx at the end of the batch")
ap.add_argument("--verbose", "-v", action="store_true",
                help="show the solver's own log while it works (incumbent, bound, gap). "
                     "Use this when you cannot tell a long solve from a frozen one.")
args = ap.parse_args()

def inst_number(name):
    digits = "".join(c for c in name if c.isdigit())
    return int(digits) if digits else 0

names = D.list_instances()
if not names:
    raise SystemExit("no prepared instances found -- run:  python prep_all.py")
if args.only:
    # Accept "5", "Instance5" and "instance5" alike. Typing --only 5 7 11 and getting
    # "not prepared: ['5']" is a pointless papercut.
    want = []
    for tok in args.only:
        t = str(tok).strip()
        want.append(f"Instance{t}" if t.isdigit() else t)
    lower = {n.lower(): n for n in names}
    resolved, missing = [], []
    for w in want:
        (resolved if w.lower() in lower else missing).append(lower.get(w.lower(), w))
    if missing:
        raise SystemExit(f"not prepared: {missing}\n   available: {names}\n"
                         f"   run:  python prep_all.py")
    names = [n for n in names if n in resolved]
elif args.range:
    lo, hi = args.range
    names = [n for n in names if lo <= inst_number(n) <= hi]
    if not names:
        raise SystemExit(f"no prepared instances numbered {lo}..{hi}\n"
                         f"   available: {D.list_instances()}")

if args.solver == "gurobi":
    import tbl_gurobi as SOLVER
else:
    SOLVER = ORT

prices = args.carbon_sweep or [None]
# The tag carries the SETTINGS, not just the mode -- otherwise a second run with a
# different --gap or --time silently overwrites the first one's summary and you lose
# the earlier experiment. gap0.01_t30 and gap0.01_t60 are different experiments.
tag = (f"{args.objective}{'_levers' if args.levers else ''}"
       f"{'_sweep' if args.carbon_sweep else ''}"
       f"_gap{args.gap:g}_t{args.time:g}")
# A --only run must NOT share a filename with a --range run at the same settings, or
# "--only 5 7" silently overwrites the summary from "--range 1 12" and you lose ten
# instances. Name the subset in the tag so the two coexist.
if args.only:
    sel = "-".join(str(inst_number(n)) for n in names)
    tag += f"_i{sel}" if len(sel) <= 24 else f"_i{len(names)}sel"
os.makedirs(os.path.join(D.HERE, "results"), exist_ok=True)
out = os.path.join(D.HERE, "results", f"summary_{tag}_{args.solver}.csv")

cols = ["instance", "solver", "time_limit",
        "staff", "days", "shift_types", "carbon_price", "status", "wall_s", "gap",
        "build_s", "solve_s",
        "penalty", "pen_on", "pen_off", "pen_under", "pen_over",
        "F_econ", "F_soc", "F_env", "total", "env_share_pct", "kg_co2e",
        "shifts", "agency", "understaffed", "senior_short", "request_wt", "dev_shifts", "fatigue_blocks"]
rows = []
INTERRUPTED = False
t_start = time.time()

def _flush():
    """Rewrite the summary CSV after EVERY instance, not just at the end of the batch.

    A batch used to write its table only once the whole loop finished, so one instance
    that hung, blew up on memory, or was Ctrl-C'd took every earlier instance's results
    down with it. Now the table on disk is always current to the last completed run:
    kill the batch whenever you like and the rows you already paid for are there.
    """
    def _w(p):
        with open(p, "w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=cols); w.writeheader(); w.writerows(rows)
    return D.safe_write(out, _w, "summary")

n_runs = len(names) * len(prices)
print(f"solver {args.solver} | objective {args.objective}{' +levers' if args.levers else ''} | "
      f"{len(names)} instance(s) x {len(prices)} price(s) = {n_runs} run(s)")
print(f"limits: {args.time:g} s per run"
      f"{f', stop at {args.gap*100:g}% gap' if args.gap > 0 else ', run to proven optimal'}"
      f"   -> SOLVER time alone, worst case {n_runs * args.time / 60:.0f} min")
# --time bounds the solver, NOT the wall clock. Each run also pays a model-build cost
# (seconds on the small instances, 25-40 s at 100+ nurses x 364 days) and, if the solve
# returns UNKNOWN, a feasibility-only fallback of up to max(30, 0.5 x --time) more.
# Saying "24 x 30 s = 12 min" and then seeing an hour is this, not a broken time limit.
_fb = 0.0 if args.solver == "gurobi" else min(600.0, max(30.0, 0.5 * args.time))
print(f"        plus model build (see the 'build' column) and, on UNKNOWN, up to "
      f"{_fb:.0f} s fallback per run -- so expect longer than the line above")
print()
PROG = ""                        # the in-progress line currently on screen
def _clear():
    """Erase the '-> solving ...' line so the results row lands clean.

    Only the exact width is erased -- blanking a fixed 90 columns left a trail of
    whitespace when the line was longer. Under --verbose there is nothing to erase
    because the solver log has already scrolled past it."""
    global PROG
    if PROG and not args.verbose:
        print("\r" + " " * len(PROG) + "\r", end="", flush=True)
    PROG = ""

SWEEP = args.carbon_sweep is not None
def _pre(name, inst, P):
    """Left-hand identity columns, shared by every row type."""
    s = f"{name:<12}{len(inst.staff):>6}{inst.n_days:>5}"
    if SWEEP: s += f"{P['carbon_price_aud_per_t']:>9.2f}"
    return s
HDR = (f"{'instance':<12}{'staff':>6}{'days':>5}" + (f"{'pC':>9}" if SWEEP else "")
       + f"{'status':>10}{'build':>7}{'solve':>7}{'gap%':>7}{'penalty':>9}"
       + f"{'F_econ':>13}{'F_soc':>11}{'F_env':>10}{'TOTAL A$':>13}")
print(HDR)
print("-" * len(HDR))

for name in names:
    if INTERRUPTED: break
    inst = D.load_instance(name)
    for pc in prices:
        T, P = D.load_tbl(inst)
        if args.price: D.apply_price_overrides(P, args.price)
        if pc is not None: P["carbon_price_aud_per_t"] = pc
        C = D.Coef(inst, T, P); C.check_price_ordering(verbose=False)
        PROG = (f"  -> solving {name} ({len(inst.staff)} staff x {inst.n_days} days x "
                f"{len(inst.shifts)} shifts), limit {args.time:g}s ... ")
        print(PROG, end="" if not args.verbose else "\n", flush=True)
        try:
            if args.solver == "ortools":
                kw = {"workers": args.workers}
            else:
                kw = {"lowmem": args.lowmem, "memlimit": args.memlimit}
            roster, info = SOLVER.solve(inst, C, args.objective, args.levers,
                                        args.gap, args.time, None,
                                        quiet=not args.verbose, **kw)
        except KeyboardInterrupt:
            # Ctrl-C / an interrupt from the host. STOP THE WHOLE BATCH -- carrying on to
            # the next instance is what made an interrupt look like it was ignored.
            print(f"\n[interrupted] stopping the batch at {name}.")
            print(f"              {len(rows)} completed run(s) are already saved.")
            _flush()
            INTERRUPTED = True
            break
        except Exception as ex:
            # One instance failing (licence limit, memory, whatever) must not kill a
            # 24-instance batch. Record it and carry on.
            _clear()
            msg = str(ex).split("\n")[0][:60]
            print(_pre(name, inst, P) + f"{'ERROR':>10}   {msg}")
            rows.append(dict(instance=name, solver=args.solver, time_limit=args.time,
                             staff=len(inst.staff), days=inst.n_days,
                             shift_types="/".join(inst.shifts),
                             carbon_price=P["carbon_price_aud_per_t"],
                             status=f"ERROR: {msg}"))
            _flush()
            continue
        _clear()
        # SUSPEND DETECTOR. A solver cannot overrun its own time limit -- both check it
        # between nodes and stop within a fraction of a second. A solve_s far above
        # --time means the WALL CLOCK advanced while nothing was computing, i.e. the
        # machine slept or hibernated mid-solve. The roster is valid but the run got
        # less computation than it claims, and solve_s is not a reportable time.
        _s = float(info.get("solve_s") or 0)
        if _s > args.time * 1.10 + 5:
            print(f"  [!] {name}: solve_s {_s:.1f}s exceeds the {args.time:g}s limit.")
            print(f"      The machine almost certainly SLEPT mid-solve. This row's timing")
            print(f"      is not reportable and the search was cut short -- re-run it after:")
            print(f"        powercfg /change standby-timeout-ac 0")

        if str(info.get("status", "")).startswith("INTERRUPTED"):
            print(_pre(name, inst, P) + f"{'INTERRUPTED':>10}")
            print(f"\n[interrupted] {name} was stopped mid-solve, so its numbers are NOT a "
                  f"result.\n              Not recorded. {len(rows)} earlier run(s) are saved.")
            _flush()
            INTERRUPTED = True
            break

        if roster is None:
            print(_pre(name, inst, P) + f"{info['status']:>10}"
                  + f"{info.get('build_s',0):>7.1f}{info.get('solve_s',0):>7.1f}"
                  + f"{'':>7}{'':>9}{'NO SOLUTION':>13}")
            rows.append(dict(instance=name, solver=args.solver, time_limit=args.time,
                             staff=len(inst.staff), days=inst.n_days,
                             shift_types="/".join(inst.shifts),
                             carbon_price=P["carbon_price_aud_per_t"],
                             status=info["status"], wall_s=round(info["wall"], 2),
                             build_s=round(info.get("build_s", 0), 2),
                             solve_s=round(info.get("solve_s", 0), 2)))
            _flush()
            continue
        e = D.evaluate(inst, C, roster, info["agency"], info["ot_min"], None)
        rows.append(dict(
            instance=name, solver=args.solver, time_limit=args.time,
            staff=len(inst.staff), days=inst.n_days,
            shift_types="/".join(inst.shifts),
            carbon_price=P["carbon_price_aud_per_t"],
            status=info["status"], wall_s=round(info["wall"], 2),
            build_s=round(info.get("build_s", 0), 2), solve_s=round(info.get("solve_s", 0), 2),
            gap=round(info["gap"], 6),
            penalty=round(e["penalty"]), pen_on=round(e["pen_on"]), pen_off=round(e["pen_off"]),
            pen_under=round(e["pen_under"]), pen_over=round(e["pen_over"]),
            F_econ=round(e["F_econ"], 2),
            F_soc=round(e["F_soc"], 2), F_env=round(e["F_env"], 4),
            total=round(e["total"], 2),
            env_share_pct=round(e["F_env"] / e["total"] * 100, 5),
            kg_co2e=round(e["kg"], 2), shifts=e["shifts"], agency=e["n_agency"],
            understaffed=e["n_under"], senior_short=e["n_senior_short"],
            request_wt=e["q_on"] + e["q_off"],
            dev_shifts=round(e["dev"], 2), fatigue_blocks=e["n_fatigue"]))
        _flush()
        D.write_roster_csv(inst, roster, os.path.join(
            D.HERE, "results", f"{name}_{tag}_{args.solver}.csv"))
        print(_pre(name, inst, P)
              + f"{info['status']:>10}{info.get('build_s',0):>7.1f}{info.get('solve_s',0):>7.1f}"
              + f"{info['gap']*100:>7.2f}"
              + f"{e['penalty']:>9,.0f}{e['F_econ']:>13,.2f}{e['F_soc']:>11,.2f}"
              + f"{e['F_env']:>10,.2f}{e['total']:>13,.2f}")

out = _flush() or out

print("-" * len(HDR))
if INTERRUPTED:
    print(f"BATCH INTERRUPTED after {len(rows)} run(s) in {time.time()-t_start:.1f} s")
    done = {r["instance"] for r in rows}
    left = [n for n in names if n not in done]
    print(f"  {len(done)} of {len(names)} instance(s) recorded.")
    if left:
        print(f"  NOT attempted: {', '.join(left)}")
        print(f"  finish them with:  --only {' '.join(str(inst_number(n)) for n in left)}")
else:
    print(f"{len(rows)} run(s) in {time.time()-t_start:.1f} s")
print(f"summary written to {out}")

# Rebuild the Excel workbook from EVERY summary file, so it always reflects the full
# picture across both solvers rather than just this batch. Wrapped in try/except and run
# as a subprocess: a workbook problem must never lose a batch that already succeeded.
if not args.no_workbook:
    import subprocess, sys as _sys
    try:
        p = subprocess.run([_sys.executable, os.path.join(D.HERE, "make_workbook.py"), "--quiet"],
                           cwd=D.HERE, capture_output=True, text=True, timeout=180)
        if p.returncode == 0:
            print(f"workbook updated  -> {os.path.join(D.HERE, 'TBL_HWSP_results.xlsx')}")
        else:
            print(f"[!] workbook not updated: {(p.stderr or p.stdout).strip().splitlines()[-1][:90]}")
            print(f"    your results are safe in {os.path.basename(out)} -- "
                  f"run 'python make_workbook.py' once Excel is closed")
    except Exception as ex:
        print(f"[!] workbook not updated ({type(ex).__name__}) -- results are safe in "
              f"{os.path.basename(out)}")
