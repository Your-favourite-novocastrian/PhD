"""
tbl_data.py -- data layer for TBL-HWSP. Nothing here builds a model.

FOLDER LAYOUT (one folder per instance -- nothing ever overwrites anything):

    tbl_bench\\
        instances\\Instance1.txt ... Instance24.txt     the originals, never edited
        data\\Instance1\\horizon.csv  shifts.csv  staff.csv  days_off.csv
                        shift_on_requests.csv  shift_off_requests.csv  cover.csv
                        staff_tbl.csv      <- the per-nurse sidecar, lives WITH its instance
        data\\Instance2\\...
        prices.csv                         <- ONE file shared by all 24
        results\\                           <- solver output

WHAT VARIES PER INSTANCE: the converted CSVs and staff_tbl.csv.
WHAT IS SHARED: prices.csv, this code, and the sidecar generation rules.
"""
import csv, math, os

HERE = os.path.dirname(os.path.abspath(__file__))
DATA_ROOT = os.path.join(HERE, "data")
INST_ROOT = os.path.join(HERE, "instances")

def _csv(path):
    with open(path) as f:
        return list(csv.DictReader(l for l in f if not l.lstrip().startswith("#")))

def list_instances():
    """Every instance that has been prepared, in natural numeric order."""
    if not os.path.isdir(DATA_ROOT): return []
    names = [d for d in os.listdir(DATA_ROOT)
             if os.path.exists(os.path.join(DATA_ROOT, d, "staff.csv"))]
    def key(n):
        digits = "".join(c for c in n if c.isdigit())
        return (int(digits) if digits else 0, n)
    return sorted(names, key=key)

class Instance:
    """One benchmark instance, loaded from data\\<name>\\."""
    def __init__(self, name, root=None):
        self.name = name
        self.dir = root or os.path.join(DATA_ROOT, name)
        if not os.path.isdir(self.dir):
            raise SystemExit(f"no prepared data for '{name}' at {self.dir}\n"
                             f"   run:  python prep_all.py        (prepares every instance)\n"
                             f"   or :  python prep_all.py --only {name}")
        g = lambda f: _csv(os.path.join(self.dir, f))

        self.n_days = int(g("horizon.csv")[0]["n_days"])
        self.days = list(range(self.n_days))
        sh = g("shifts.csv")
        self.shifts = [r["shift_id"] for r in sh]
        self.length_min = {r["shift_id"]: int(r["length_min"]) for r in sh}
        self.cannot_follow = {r["shift_id"]: [t for t in r["cannot_follow"].split("|") if t] for r in sh}
        self.NIGHT = [s for s in self.shifts if s.upper().startswith("N")]

        st = g("staff.csv")
        self.staff = [r["staff_id"] for r in st]
        self.S = {r["staff_id"]: r for r in st}
        self.days_off = {(r["staff_id"], int(r["day"])) for r in g("days_off.csv")}
        self.on_req = [(r["staff_id"], int(r["day"]), r["shift_id"], int(r["weight"]))
                       for r in g("shift_on_requests.csv")]
        self.off_req = [(r["staff_id"], int(r["day"]), r["shift_id"], int(r["weight"]))
                        for r in g("shift_off_requests.csv")]
        cv = g("cover.csv")
        self.R = {(int(r["day"]), r["shift_id"]): int(r["requirement"]) for r in cv}
        self.w_under = {(int(r["day"]), r["shift_id"]): int(r["under_weight"]) for r in cv}
        self.w_over  = {(int(r["day"]), r["shift_id"]): int(r["over_weight"])  for r in cv}
        self.weekends = [(5 + 7*k, 6 + 7*k) for k in range((self.n_days + 6)//7)
                         if 5 + 7*k < self.n_days]

    def max_shifts_of(self, i, s):
        return int(dict(p.split("=") for p in self.S[i]["max_shifts"].split("|")).get(s, self.n_days))

    def __str__(self):
        return (f"{self.name}: {len(self.staff)} staff, {self.n_days} days, "
                f"shifts {'/'.join(self.shifts)}"
                f"{'  (has night shift)' if self.NIGHT else ''}")

def load_instance(name):
    return Instance(name)

def load_tbl(inst, price_file="prices.csv"):
    """Per-nurse sidecar (lives with the instance) + the one shared price file."""
    sp = os.path.join(inst.dir, "staff_tbl.csv")
    if not os.path.exists(sp):
        raise SystemExit(f"{inst.name} has no staff_tbl.csv\n"
                         f"   run:  python prep_all.py --only {inst.name}")
    T = {r["staff_id"]: r for r in _csv(sp)}
    P = {r["key"]: float(r["value"]) for r in _csv(os.path.join(HERE, price_file))}
    missing = set(inst.staff) - set(T)
    if missing:
        raise SystemExit(f"{sp}\n   is missing rows for {sorted(missing)[:10]}"
                         f"{' ...' if len(missing) > 10 else ''}\n"
                         f"   regenerate it:  python prep_all.py --only {inst.name} --force")
    return T, P

def apply_price_overrides(P, overrides):
    """--price KEY=VALUE, checked against prices.csv so a typo is caught, not ignored."""
    for kv in overrides:
        if "=" not in kv:
            raise SystemExit(f"--price needs KEY=VALUE, got '{kv}'")
        k, v = kv.split("=", 1)
        k = k.strip()
        if k not in P:
            raise SystemExit(f"unknown price '{k}'.\n   valid keys: {', '.join(sorted(P))}")
        try: P[k] = float(v)
        except ValueError: raise SystemExit(f"--price {k} needs a number, got '{v}'")
        print(f"  price override: {k} = {P[k]:g}")
    return P

class Coef:
    """Everything the objective multiplies, built once from the sidecar + prices."""
    def __init__(self, inst, T, P):
        self.inst, self.T, self.P = inst, T, P
        h = {s: inst.length_min[s] / 60.0 for s in inst.shifts}
        self.wage      = {i: {s: float(T[i]["hourly_rate_aud"]) * h[s] for s in inst.shifts}
                          for i in inst.staff}
        self.ot_hourly = {i: float(T[i]["hourly_rate_aud"]) * P["overtime_multiplier"] for i in inst.staff}
        self.kg_day    = {i: 2 * float(T[i]["commute_km_one_way"]) * float(T[i]["ef_kg_per_km"])
                          for i in inst.staff}
        self.senior    = [i for i in inst.staff if T[i]["senior"] == "1"]
        self.p_kg      = P["carbon_price_aud_per_t"] / 1000.0
        self.k_pref    = P["p_leave_per_pref_weight"] * P["turnover_cost_aud"]
        self.k_fair    = P["p_leave_per_dev_shift"]  * P["turnover_cost_aud"]
        self.mean_wage = sum(self.wage[i][inst.shifts[0]] for i in inst.staff) / len(inst.staff)
        # Q_{d,s}: proportional to the requirement, capped by how many seniors exist
        self.senior_req = {k: min(len(self.senior), math.ceil(P["min_senior_share"] * inst.R[k]))
                           for k in inst.R}

    def check_price_ordering(self, verbose=True):
        rho, ag = self.P["rho_understaffing"], self.P["agency_cost_per_shift"]
        ok = rho > ag > self.mean_wage
        if verbose:
            print(f"  price ordering  rho {rho:,.0f} > agency {ag:,.0f} > mean wage "
                  f"{self.mean_wage:,.2f}   {'OK' if ok else '*** BROKEN ***'}")
        if not ok:
            raise SystemExit("Fix prices.csv: with this ordering the optimiser prefers "
                             "uncovered shifts to staffed ones.")

# ---------------------------------------------------------------- evaluation
def evaluate(inst, C, roster, agency=None, overtime_min=None, published=None):
    """Score ONE roster every way at once. Pure arithmetic -- works on a roster from
    any source (CP-SAT, Gurobi, a metaheuristic, a CSV)."""
    P = C.P
    agency = agency or {}; overtime_min = overtime_min or {}
    n = len(inst.staff)

    staffed = {(d, s): sum(1 for i in inst.staff if roster[(i, d)] == s) for (d, s) in inst.R}
    under = {k: max(0, inst.R[k] - staffed[k] - agency.get(k, 0)) for k in inst.R}
    over  = {k: max(0, staffed[k] + agency.get(k, 0) - inst.R[k]) for k in inst.R}

    q_on  = sum(wt for (i, d, s, wt) in inst.on_req  if roster[(i, d)] != s)
    q_off = sum(wt for (i, d, s, wt) in inst.off_req if roster[(i, d)] == s)
    # BENCHMARK PENALTY -- the schedulingbenchmarks.org score, four weighted counts:
    #   pen_on    = sum of the WEIGHTS of shift-on requests not granted
    #   pen_off   = sum of the WEIGHTS of shift-off requests broken
    #   pen_under = 100 x (shift-units below requirement)   <- weight from cover.csv
    #   pen_over  =   1 x (shift-units above requirement)   <- weight from cover.csv
    # The 100 on under-cover dominates everything: ONE uncovered shift = 100 points,
    # so a penalty of 5,500 is ~55 uncovered shifts, not thousands of unhappy nurses.
    pen_on, pen_off = q_on, q_off
    pen_under = sum(inst.w_under[k]*under[k] for k in inst.R)
    pen_over  = sum(inst.w_over[k]*over[k]  for k in inst.R)
    penalty = pen_on + pen_off + pen_under + pen_over

    fair_s = inst.NIGHT or inst.shifts
    cnt  = {i: sum(1 for d in inst.days if roster[(i, d)] in fair_s) for i in inst.staff}
    mean = sum(cnt.values()) / n
    dev  = sum(abs(cnt[i] - mean) for i in inst.staff)
    worked = {i: sum(1 for d in inst.days if roster[(i, d)]) for i in inst.staff}

    sen_on = {(d, s): sum(1 for i in C.senior if roster[(i, d)] == s) for (d, s) in inst.R}
    sen_short = {k: max(0, C.senior_req[k] - sen_on[k]) for k in inst.R}

    n_ag = sum(agency.values())
    wages   = sum(C.wage[i][roster[(i, d)]] for i in inst.staff for d in inst.days if roster[(i, d)])
    ot      = sum(overtime_min.get(i, 0) / 60.0 * C.ot_hourly[i] for i in inst.staff)
    ag_cost = n_ag * P["agency_cost_per_shift"]
    und_c   = sum(under.values()) * P["rho_understaffing"]
    sen_c   = sum(sen_short.values()) * P["rho_senior_shortfall"]

    kg_com = sum(C.kg_day[i] for i in inst.staff for d in inst.days if roster[(i, d)])
    kg_ag  = n_ag * P["agency_travel_kg"]
    kg_anc = sum(over.values()) * P["e_anc_kg_per_over_shift"]
    kg = kg_com + kg_ag + kg_anc

    n_fat = 0
    if inst.NIGHT:
        for i in inst.staff:
            for d in range(inst.n_days - 2):
                if all(roster[(i, d+t)] in inst.NIGHT for t in range(3)): n_fat += 1
    n_churn = 0
    if published:
        n_churn = sum(1 for i in inst.staff for d in inst.days
                      if published.get((i, d), roster[(i, d)]) != roster[(i, d)])

    F_econ = wages + ot + ag_cost + und_c + sen_c
    F_env  = kg * C.p_kg
    F_soc  = ((q_on + q_off) * C.k_pref + dev * C.k_fair
              + n_fat * P["kappa_fatigue"] + n_churn * P["kappa_churn"])
    return dict(penalty=penalty, q_on=q_on, q_off=q_off,
                pen_on=pen_on, pen_off=pen_off, pen_under=pen_under, pen_over=pen_over,
                n_under=sum(under.values()), n_over=sum(over.values()),
                shifts=sum(worked.values()), mean=mean, dev=dev,
                n_agency=n_ag, ot_hours=sum(overtime_min.values())/60.0,
                n_fatigue=n_fat, n_churn=n_churn,
                kg=kg, kg_com=kg_com, kg_ag=kg_ag, kg_anc=kg_anc,
                wages=wages, ot=ot, ag_cost=ag_cost, und_cost=und_c,
                n_senior_short=sum(sen_short.values()), senior_cost=sen_c,
                senior_req=sum(C.senior_req.values()),
                F_econ=F_econ, F_soc=F_soc, F_env=F_env, total=F_econ + F_soc + F_env)

# ---------------------------------------------------------------- reporting
class Tee:
    """Write to the console AND a log file at the same time, so a crashed or closed
    terminal never loses a run's report. Used by the single-instance runners."""
    def __init__(self, path):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        try:
            self.fh = open(path, "w", encoding="utf-8")
        except PermissionError:
            import time as _t
            path = f"{os.path.splitext(path)[0]}_{_t.strftime('%H%M%S')}.log"
            self.fh = open(path, "w", encoding="utf-8")
        self.path = path
    def __enter__(self):
        import sys; self._out = sys.stdout; sys.stdout = self; return self
    def __exit__(self, *a):
        import sys; sys.stdout = self._out; self.fh.close()
        print(f"  full report also saved to {self.path}")
    def write(self, d): self._out.write(d); self.fh.write(d); self.fh.flush()
    def flush(self): self._out.flush(); self.fh.flush()

def report(inst, C, e, solver_name, status, wall, gap=None, objective="penalty",
           build_s=None, solve_s=None):
    P = C.P; tot = e["total"]
    print()
    print("=" * 72)
    print(f"  {inst}")
    print(f"  {solver_name}   minimising: {objective.upper()}   status: {status}")
    print("=" * 72)
    print(f"  BENCHMARK PENALTY                                    {e['penalty']:>14,.0f}")
    print(f"     shift-on requests not granted {e['pen_on']:>10,.0f}   (sum of their weights)")
    print(f"     shift-off requests broken     {e['pen_off']:>10,.0f}   (sum of their weights)")
    print(f"     under-cover                   {e['pen_under']:>10,.0f}   ({e['n_under']} shift-units x weight 100)")
    print(f"     over-cover                    {e['pen_over']:>10,.0f}   ({e['n_over']} shift-units x weight 1)")
    if objective == "dollars":
        print(f"     [reported only -- the DOLLAR objective was minimised, not this]")
    print()
    print(f"  F_econ   economic       (A$)                         {e['F_econ']:>14,.2f}")
    print(f"     wages {e['wages']:>14,.2f}   ({e['shifts']} shifts)")
    print(f"     overtime {e['ot']:>11,.2f}   ({e['ot_hours']:.1f} h)")
    print(f"     agency {e['ag_cost']:>13,.2f}   ({e['n_agency']} @ {P['agency_cost_per_shift']:,.0f})")
    print(f"     understaffing {e['und_cost']:>6,.2f}   ({e['n_under']} @ {P['rho_understaffing']:,.0f})")
    print(f"     senior short {e['senior_cost']:>7,.2f}   ({e['n_senior_short']} of "
          f"{e['senior_req']} required senior-shifts @ {P['rho_senior_shortfall']:,.0f})")
    print()
    print(f"  F_soc    social         (A$)                         {e['F_soc']:>14,.2f}")
    print(f"     requests {(e['q_on']+e['q_off'])*C.k_pref:>11,.2f}   ({e['q_on']+e['q_off']} wt @ {C.k_pref:,.2f})")
    print(f"     fairness {e['dev']*C.k_fair:>11,.2f}   ({e['dev']:.2f} shifts dev @ {C.k_fair:,.2f})")
    print(f"     fatigue {e['n_fatigue']*P['kappa_fatigue']:>12,.2f}   ({e['n_fatigue']} blocks)"
          f"{'   [zero: no night shift in this instance]' if not inst.NIGHT else ''}")
    print(f"     churn {e['n_churn']*P['kappa_churn']:>14,.2f}   ({e['n_churn']} changed)")
    print()
    print(f"  F_env    environmental  (A$)                         {e['F_env']:>14,.2f}")
    print(f"     {e['kg']:,.2f} kg CO2e @ A${P['carbon_price_aud_per_t']:,.2f}/t"
          f"   (commute {e['kg_com']:,.1f} + agency {e['kg_ag']:,.1f} + anc {e['kg_anc']:,.1f})")
    print("  " + "-" * 68)
    print(f"  TOTAL    F$ = F_econ + F_soc + F_env                  {tot:>14,.2f}")
    print(f"     econ {e['F_econ']/tot*100:6.3f}%   soc {e['F_soc']/tot*100:6.3f}%   env {e['F_env']/tot*100:6.3f}%")
    print()
    if build_s is not None:
        print(f"  model build time     {build_s:>8.2f} s   (Python building the model)")
    print(f"  solver time          {(solve_s if solve_s is not None else wall):>8.2f} s"
          f"   (what the time limit bounds)")
    print(f"  total elapsed        {wall + (build_s or 0):>8.2f} s")
    if gap is not None and gap == gap: print(f"  relative MIP gap     {gap*100:>8.4f} %")
    print("=" * 72)

def print_roster(inst, roster):
    print("\n  roster   " + " ".join(f"{d:>2}" for d in inst.days))
    for i in inst.staff:
        print(f"  {i:<8} " + " ".join(f"{(roster[(i,d)] or '.'):>2}" for d in inst.days)
              + f"   {sum(1 for d in inst.days if roster[(i,d)])}")

def safe_write(path, write_fn, what="file"):
    """Write, but survive a locked file. On Windows, having a CSV open in Excel gives
    PermissionError -- which must NOT kill a multi-hour batch. Falls back to a
    timestamped name and tells you, rather than losing the run."""
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    try:
        write_fn(path); return path
    except PermissionError:
        import time as _t
        alt = f"{os.path.splitext(path)[0]}_{_t.strftime('%H%M%S')}.csv"
        try:
            write_fn(alt)
            print(f"  [!] {os.path.basename(path)} is locked (open in Excel?) -- "
                  f"wrote {os.path.basename(alt)} instead")
            return alt
        except Exception as ex:
            print(f"  [!] could not write {what}: {ex}"); return None

def write_roster_csv(inst, roster, path):
    def _w(p):
        with open(p, "w", newline="") as f:
            w = csv.writer(f); w.writerow(["staff_id"] + [f"day{d}" for d in inst.days])
            for i in inst.staff: w.writerow([i] + [roster[(i, d)] for d in inst.days])
    return safe_write(path, _w, "roster")

def load_published(inst, path):
    if not path or not os.path.exists(path): return None
    out = {}
    for r in _csv(path):
        for d in inst.days: out[(r["staff_id"], d)] = r[f"day{d}"]
    return out
