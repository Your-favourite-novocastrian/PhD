"""
tbl_epsilon.py -- EPSILON-CONSTRAINT (AUGMECON2) frontier: monetary cost vs emissions.

    python tbl_epsilon.py --instance Instance1 --points 12
    python tbl_epsilon.py --instance Instance8 --points 20 --time 300
    python tbl_epsilon.py --instance Instance1 --points 12 --no-bypass

Writes results\\<Instance>_frontier.csv -- one row per efficient point.

WHY THIS EXISTS
---------------
The weighted sum in tbl_ortools.py minimises F_econ + F_soc + F_env. Every weighted sum,
whatever the weights, can only return SUPPORTED efficient solutions -- those on the convex
hull of the efficient set. Rostering is a binary problem, so its efficient set is not
convex, and there are genuinely efficient rosters that NO carbon price can ever produce.
Sweeping the carbon price from 0 to 5000 does not fix this; it just slides along the hull.

The epsilon-constraint method does not scalarise. It optimises ONE objective and turns the
others into constraints:

    min   F_econ + F_soc                 (money)
    s.t.  total kg CO2e  <=  epsilon      (the other objective, as a bound)
          ... every original rostering constraint ...

Solving this for a series of epsilon values recovers the WHOLE efficient set, unsupported
points included. Note what else changed: carbon is no longer priced at all. That is the
point. Instead of asserting A$36.28/t and seeing nothing move, we ask the question that
has an answer -- "what does it cost to cap emissions at X?" -- and read the implied
abatement cost (A$ per kg avoided) off the frontier instead of assuming it.

AUGMECON2 (Mavrotas & Florios, 2013) adds two things to the textbook method, both here:

  1. SURPLUS IN THE OBJECTIVE. The constraint is written as an equality with a surplus
     variable, kg + s = epsilon, and the objective becomes lexicographic: minimise cost
     first, then maximise s. Without this you get WEAKLY efficient points -- rosters that
     cost the same as an efficient one but emit more, because nothing rewarded using less
     than the cap. Implemented exactly in integers as  min COST*W - s  with W = range+1,
     so one cent of cost always outranks all of the surplus. No epsilon tuning, no
     floating-point tie-breaking.

  2. BYPASS. The surplus tells you how far below the cap the solution actually landed, so
     the next few epsilon values are already known to give the same roster and can be
     skipped outright. On a fine grid this removes most of the solves. --no-bypass turns
     it off if you want to see every grid point solved independently.

The feasible region comes from tbl_ortools.build_core -- the SAME builder the weighted-sum
runs use, so the frontier and the scalarised results are directly comparable.
"""
import argparse, csv, os, time
from ortools.sat.python import cp_model
import tbl_data as D
import tbl_ortools as ORT

CENTS = 100          # money -> integer cents
GRAMS = 1000         # kg CO2e -> integer grams


def _expressions(inst, C, core):
    """The two objectives as INTEGER CP-SAT expressions.

    Must mirror tbl_data.evaluate() exactly or the frontier would be drawn in different
    units than the table beside it. evaluate() is still what produces the reported
    numbers -- these expressions only steer the search.
    """
    P, x, a, o = C.P, core["x"], core["a"], core["o"]
    under, over, sen_short, work = core["under"], core["over"], core["sen_short"], core["work"]
    n = len(inst.staff)

    def c(v): return int(round(v * CENTS))
    def g(v): return int(round(v * GRAMS))

    # ---- fairness and fatigue, identical in form to the weighted-sum model ----
    m = core["m"]
    fair_s = inst.NIGHT or inst.shifts
    cnt = {i: sum(x[i, d, s] for d in inst.days for s in fair_s) for i in inst.staff}
    tot = sum(cnt.values())
    DEV = {i: m.NewIntVar(0, n * inst.n_days, f"E_DEV_{i}") for i in inst.staff}
    for i in inst.staff:
        m.Add(DEV[i] >= n * cnt[i] - tot)
        m.Add(DEV[i] >= tot - n * cnt[i])
    fat = {}
    if inst.NIGHT:
        for i in inst.staff:
            for d in range(inst.n_days - 2):
                fat[i, d] = m.NewBoolVar(f"E_g_{i}_{d}")
                m.Add(fat[i, d] >= sum(x[i, d+t, s] for t in range(3) for s in inst.NIGHT) - 2)

    F_econ = (sum(c(C.wage[i][s]) * x[i, d, s]
                  for i in inst.staff for d in inst.days for s in inst.shifts)
            + sum(c(C.ot_hourly[i] / 60.0) * o[i] for i in inst.staff)
            + sum(c(P["agency_cost_per_shift"]) * a[k] for k in inst.R)
            + sum(c(P["rho_understaffing"]) * under[k] for k in inst.R)
            + sum(c(P["rho_senior_shortfall"]) * t for t in sen_short.values()))
    F_soc = (sum(c(C.k_pref * wt) * (1 - x[i, d, s]) for (i, d, s, wt) in inst.on_req)
           + sum(c(C.k_pref * wt) * x[i, d, s]       for (i, d, s, wt) in inst.off_req)
           + sum(c(C.k_fair / n) * DEV[i] for i in inst.staff)
           + sum(c(P["kappa_fatigue"]) * t for t in fat.values()))

    # Emissions in GRAMS. No carbon price anywhere -- that is what makes this a
    # constraint on a physical quantity rather than another term in a money objective.
    KG = (sum(g(C.kg_day[i]) * work(i, d) for i in inst.staff for d in inst.days)
        + sum(g(P["agency_travel_kg"]) * a[k] for k in inst.R)
        + sum(g(P["e_anc_kg_per_over_shift"]) * over[k] for k in inst.R))
    return F_econ + F_soc, KG


def _run(m, time_limit, workers, quiet=True):
    sv = cp_model.CpSolver()
    sv.parameters.max_time_in_seconds = time_limit
    sv.parameters.num_search_workers = workers
    st = sv.Solve(m)
    return (sv, st) if st in (cp_model.OPTIMAL, cp_model.FEASIBLE) else (sv, None)


def _extract(inst, sv, core):
    x, a, o = core["x"], core["a"], core["o"]
    roster = {(i, d): next((s for s in inst.shifts if sv.Value(x[i, d, s])), "")
              for i in inst.staff for d in inst.days}
    return (roster, {k: sv.Value(a[k]) for k in inst.R},
            {i: sv.Value(o[i]) for i in inst.staff})


def frontier(inst, C, points=10, time_limit=120., workers=8, bypass=True, quiet=False):
    def say(*t):
        if not quiet: print(*t, flush=True)

    # ---------------------------------------------------------- 1. payoff table
    # Two lexicographic solves give the range of epsilon. Getting this wrong is the
    # classic epsilon-constraint mistake: too wide and most solves are infeasible or
    # duplicate, too narrow and you miss half the frontier.
    say("payoff table (lexicographic anchors)")

    core = ORT.build_core(inst, C, True)
    COST, KG = _expressions(inst, C, core)
    m = core["m"]; m.Minimize(COST)
    sv, st = _run(m, time_limit, workers)
    if st is None: raise SystemExit("could not solve the cost anchor -- raise --time")
    cost_min = sv.Value(COST)
    m.Add(COST <= cost_min); m.Minimize(KG)          # tie-break: cheapest, then cleanest
    sv, st = _run(m, time_limit, workers)
    kg_hi = sv.Value(KG)
    say(f"  cheapest roster : A${cost_min/CENTS:,.2f}   {kg_hi/GRAMS:,.1f} kg")

    core2 = ORT.build_core(inst, C, True)
    COST2, KG2 = _expressions(inst, C, core2)
    m2 = core2["m"]; m2.Minimize(KG2)
    sv2, st2 = _run(m2, time_limit, workers)
    if st2 is None: raise SystemExit("could not solve the emissions anchor -- raise --time")
    kg_min = sv2.Value(KG2)
    m2.Add(KG2 <= kg_min); m2.Minimize(COST2)
    sv2, st2 = _run(m2, time_limit, workers)
    cost_hi = sv2.Value(COST2)
    say(f"  cleanest roster : A${cost_hi/CENTS:,.2f}   {kg_min/GRAMS:,.1f} kg")

    rng = kg_hi - kg_min
    if rng <= 0:
        say("\n  the two anchors coincide: emissions cannot vary on this instance.")
        say("  Every feasible roster emits the same amount, so there is no trade-off")
        say("  to trace and no frontier to draw. That is a finding, not a failure.")
        return [], dict(cost_min=cost_min, cost_hi=cost_hi, kg_min=kg_min, kg_hi=kg_hi)

    # ---------------------------------------------------------- 2. sweep
    W = rng + 1                       # one cent of cost outranks all surplus -> exact lex
    if cost_hi * W > 4e18:
        raise SystemExit("objective would overflow int64 -- reduce CENTS or use fewer points")

    step = rng / (points - 1) if points > 1 else rng
    say(f"\nsweeping {points} epsilon value(s) over {rng/GRAMS:,.1f} kg "
        f"(step {step/GRAMS:,.1f} kg)"
        f"{'  [AUGMECON2 bypass on]' if bypass else ''}\n")
    hdr = (f"{'eps (kg)':>12}{'status':>12}{'solve s':>9}{'cost A$':>14}"
           f"{'kg':>12}{'surplus kg':>12}{'skipped':>9}")
    say(hdr); say("-" * len(hdr))

    pts, k, solves = [], 0, 0
    while k < points:
        eps = int(round(kg_hi - k * step))
        core3 = ORT.build_core(inst, C, True)
        COST3, KG3 = _expressions(inst, C, core3)
        m3 = core3["m"]
        s = m3.NewIntVar(0, rng, "surplus")
        m3.Add(KG3 + s == eps)                       # equality + surplus, not <=
        m3.Minimize(COST3 * W - s)                   # lexicographic: min cost, then max s
        t0 = time.time(); sv3, st3 = _run(m3, time_limit, workers)
        dt = time.time() - t0; solves += 1

        if st3 is None:
            say(f"{eps/GRAMS:>12,.1f}{'INFEASIBLE':>12}{dt:>9.1f}")
            k += 1
            continue

        roster, ag, ot = _extract(inst, sv3, core3)
        e = D.evaluate(inst, C, roster, ag, ot, None)
        surplus = sv3.Value(s)
        # BYPASS: the solution came in `surplus` grams under the cap, so every epsilon
        # down to (eps - surplus) yields this same roster. Skip them.
        jump = 1 + int(surplus // step) if (bypass and step > 0) else 1
        skipped = jump - 1
        say(f"{eps/GRAMS:>12,.1f}{'OPTIMAL':>12}{dt:>9.1f}"
            f"{e['F_econ']+e['F_soc']:>14,.2f}{e['kg']:>12,.1f}"
            f"{surplus/GRAMS:>12,.1f}{skipped:>9}")
        pts.append(dict(eps_kg=round(eps / GRAMS, 3), status="OPTIMAL", solve_s=round(dt, 2),
                        cost_aud=round(e["F_econ"] + e["F_soc"], 2),
                        F_econ=round(e["F_econ"], 2), F_soc=round(e["F_soc"], 2),
                        kg_co2e=round(e["kg"], 3), surplus_kg=round(surplus / GRAMS, 3),
                        penalty=round(e["penalty"]), shifts=e["shifts"],
                        agency=e["n_agency"], understaffed=e["n_under"]))
        k += jump

    say("-" * len(hdr))
    say(f"{solves} solve(s) for {points} grid point(s), {len(pts)} distinct efficient point(s)")
    mark_supported(pts)
    return pts, dict(cost_min=cost_min, cost_hi=cost_hi, kg_min=kg_min, kg_hi=kg_hi)


def mark_supported(pts):
    """Tag each efficient point supported / UNSUPPORTED, and say so out loud.

    A point is SUPPORTED iff it is a vertex of the lower-left convex hull of the
    efficient set -- equivalently, iff some weighting of the two objectives makes it
    optimal. Everything else is an efficient roster that NO weighted sum can return,
    at any carbon price. This is the concrete, instance-specific evidence for the
    methodological claim; it turns 'weighted sums miss unsupported solutions' from a
    citation into a count from your own data.
    """
    if not pts: return
    P = sorted({(p["kg_co2e"], p["cost_aud"]) for p in pts})
    h = []
    for q in P:
        while len(h) >= 2:
            (x1, y1), (x2, y2) = h[-2], h[-1]
            if (x2 - x1) * (q[1] - y1) - (y2 - y1) * (q[0] - x1) <= 0: h.pop()
            else: break
        h.append(q)
    H = set(h)
    for p in pts:
        p["supported"] = "yes" if (p["kg_co2e"], p["cost_aud"]) in H else "NO"
    n_un = sum(1 for p in pts if p["supported"] == "NO")
    print(f"\nSUPPORTED vs UNSUPPORTED")
    print(f"  {len(pts) - n_un} of {len(pts)} points lie on the convex hull (supported)")
    print(f"  {n_un} are UNSUPPORTED -- efficient rosters that no weighted sum can reach,")
    print(f"  at any carbon price. This is why the carbon sweep alone was not enough.")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--instance", default="Instance1")
    ap.add_argument("--points", type=int, default=10, help="grid points on the frontier")
    ap.add_argument("--time", type=float, default=120., help="seconds per solve")
    ap.add_argument("--workers", type=int, default=8)
    ap.add_argument("--no-bypass", action="store_true", help="solve every grid point")
    ap.add_argument("--price", action="append", default=[], metavar="KEY=VALUE")
    args = ap.parse_args()

    inst = D.load_instance(args.instance)
    T, P = D.load_tbl(inst)
    D.apply_price_overrides(P, args.price)
    C = D.Coef(inst, T, P)

    out = os.path.join(D.HERE, "results", f"{inst.name}_frontier.csv")
    os.makedirs(os.path.dirname(out), exist_ok=True)
    with D.Tee(os.path.join(D.HERE, "results", f"{inst.name}_frontier.log")):
        print(f"EPSILON-CONSTRAINT FRONTIER -- {inst.name}  "
              f"({len(inst.staff)} staff, {inst.n_days} days)")
        print("minimise F_econ + F_soc   subject to   kg CO2e <= epsilon\n")
        pts, anch = frontier(inst, C, args.points, args.time, args.workers,
                             bypass=not args.no_bypass)
        if len(pts) > 1:
            d_cost = pts[-1]["cost_aud"] - pts[0]["cost_aud"]
            d_kg = pts[0]["kg_co2e"] - pts[-1]["kg_co2e"]
            print(f"\nACROSS THE FRONTIER: {d_kg:,.1f} kg avoided for A${d_cost:,.2f}")
            if d_kg > 0:
                print(f"  implied abatement cost  A${d_cost/d_kg:,.2f} per kg "
                      f"= A${d_cost/d_kg*1000:,.0f} per tonne CO2e")
                print(f"  compare with the market price in prices.csv "
                      f"(A${P['carbon_price_aud_per_t']:,.2f}/t): if abatement here costs "
                      f"more\n  than the market price, buying offsets dominates "
                      f"re-rostering, and that is\n  the economically honest conclusion "
                      f"to report.")

    if pts:
        cols = list(pts[0].keys())
        def _w(p):
            with open(p, "w", newline="") as f:
                w = csv.DictWriter(f, fieldnames=cols); w.writeheader(); w.writerows(pts)
        out = D.safe_write(out, _w, "frontier") or out
        print(f"\nfrontier written to {out}")
