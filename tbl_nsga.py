"""
tbl_nsga.py -- NSGA-II and NSGA-III on the TBL-HWSP tri-objective (F_econ, F_soc, F_env).

    python tbl_nsga.py --instance Instance1 --levers                  both algorithms, 5 seeds
    python tbl_nsga.py --range 1 5 --levers --seeds 10 --jobs 4       4 runs at a time
    python tbl_nsga.py --only 1 8 13 --levers --algo nsga3 --time 600 time-limited runs
    python tbl_nsga.py --instance 24 --levers --pilot                 timing estimate only
    python tbl_nsga.py --report                                       rebuild the workbook only
    python tbl_nsga.py --instance 1 --levers --out nsga_trial         a separate experiment

EVERYTHING goes to its own folder, results_nsga\\ (or --out NAME). results\\ -- your Gurobi,
OR-Tools and epsilon-constraint outputs -- is only ever READ (for the epsilon front).

ONE SET OF FILES PER INSTANCE + ALGORITHM, holding all of its seeds. Re-running an instance
and algorithm REPLACES that set (and its rows in nsga_runs.csv); other instances, the other
algorithm, and base/levers runs are left alone. The settings each seed was run with are
recorded in nsga_runs.csv, the workbook, and the top of the log.

    results_nsga\\nsga_runs.csv                     one row per seed run (the statistics source)
    results_nsga\\TBL_NSGA_results.xlsx             rebuilt after every instance, and by --report
    results_nsga\\<Inst>_<tag>_<algo>.log           settings + every seed's console output
    results_nsga\\<Inst>_<tag>_<algo>_best.csv      lowest-total roster over ALL seeds
    results_nsga\\fronts\\<Inst>_<tag>_<algo>.csv    every seed's Pareto front, with a seed column
    results_nsga\\fronts\\<Inst>_<tag>_<algo>.npz    the front rosters (X_s1, F_s1, X_s2, ...)
    results_nsga\\cache\\                           starting rows per instance (safe to delete)
<tag> is 'levers' or 'base'.

WHAT IS OPTIMISED
    The same three pillars as tbl_gurobi.py --objective dollars, kept SEPARATE instead of
    summed: minimise (F_econ, F_soc, F_env), all in A$. Published-roster churn is not used
    (no --published), exactly as in the epsilon-constraint runs.

ENCODING AND WHY EVERY ROSTER STAYS FEASIBLE
    A roster is an integer matrix X[staff, day]: 0 = off, k = the k-th shift type.
    Every HARD constraint in the model is per-employee (one shift a day, fixed days off,
    forbidden successions, max shifts per type, min/max consecutive shifts, min consecutive
    days off, max weekends, min minutes, max minutes + overtime cap). Cover and skill mix
    are soft. So a roster is feasible exactly when each ROW is feasible on its own, and:
      * initial rows come from small one-employee CP-SAT models (always feasible),
      * crossover swaps whole rows between parents (cannot break anything),
      * every mutation is checked row by row and undone if it breaks a row.
    No penalty for infeasibility and no repair heuristics are needed.

AGENCY AND OVERTIME ARE NOT GENES
    Given a roster, the optimal lever values are a closed-form choice, the same one Gurobi
    makes inside the dollars model: overtime = minutes above the contract maximum; agency
    fills a shortfall only when (agency cost + carbon cost of agency travel) is less than
    the understaffing penalty, up to agency_max_per_shift. Without --levers both are zero.

THREE CONSISTENCY CHECKS (printed, and recorded in the workbook)
    1. The fast numpy evaluator steers the search; tbl_data.evaluate scores every reported
       number. The two are compared before the first run and on every front.
    2. Every CP-SAT row in the initial pool is re-checked by the numpy row checker.
    3. Rows on the final front are re-checked by an independent CP-SAT feasibility model
       (all of them, or a 300-row sample on large instances). --verify-core also re-checks
       the best roster with YOUR tbl_ortools.build_core model.

HYPERVOLUME
    Computed in the workbook, not during the run: for each instance every objective is
    scaled to [0, 1] between the best and worst value found in ANY front on record for that
    instance (plus the epsilon-constraint front, results\\<Inst>_front3.csv, if present),
    with the reference point at 1.1 on each axis. HV values are therefore comparable WITHIN an instance and within one
    workbook build. Adding runs can move them slightly; never mix HVs from two builds.
"""
import argparse, contextlib, csv, math, os, sys, time
from concurrent.futures import ProcessPoolExecutor, as_completed
import numpy as np
import tbl_data as D

HERE = getattr(D, "HERE", os.path.dirname(os.path.abspath(__file__)))
EXACT = os.path.join(HERE, "results")        # Gurobi / epsilon results: READ ONLY here


def set_out(folder):
    """Every file this script writes goes under HERE\\<folder>. Nothing is written to
    results\\, so Gurobi, OR-Tools and epsilon-constraint outputs cannot be touched."""
    global OUT, RESULTS, FRONTS, CACHE, RUNS_CSV, WORKBOOK
    OUT = folder if os.path.isabs(folder) else os.path.join(HERE, folder)
    if os.path.normcase(os.path.abspath(OUT)) == os.path.normcase(os.path.abspath(EXACT)):
        raise SystemExit("--out must not be results\\ -- that folder belongs to the exact runs")
    RESULTS = OUT
    FRONTS = os.path.join(OUT, "fronts")
    CACHE = os.path.join(OUT, "cache")
    RUNS_CSV = os.path.join(OUT, "nsga_runs.csv")
    WORKBOOK = os.path.join(OUT, "TBL_NSGA_results.xlsx")


set_out("results_nsga")
ALGOS = ("nsga2", "nsga3")
NICE = {"nsga2": "NSGA-II", "nsga3": "NSGA-III"}


# =====================================================================================
#  THE PROBLEM: arrays built once from inst and C, a fast evaluator, a row checker
# =====================================================================================
def _num(v):
    return int(round(float(v)))


class Problem:
    def __init__(self, inst, C, levers):
        self.inst, self.C, self.levers = inst, C, levers
        P = C.P
        self.name = inst.name
        self.staff = list(inst.staff)
        self.n = n = len(self.staff)
        self.T = T = int(inst.n_days)
        self.shifts = list(inst.shifts)
        self.S = S = len(self.shifts)
        if S > 120:
            raise SystemExit("more than 120 shift types -- the int8 encoding would overflow")
        self.code = {s: k + 1 for k, s in enumerate(self.shifts)}
        self.lens = np.array([0.0] + [float(inst.length_min[s]) for s in self.shifts])
        self.capot = float(P["overtime_max_min"]) if levers else 0.0
        self.cap_ag = int(P["agency_max_per_shift"]) if levers else 0

        def g(i, key): return _num(inst.S[i][key])
        self.K = np.array([g(i, "max_consec_shifts") for i in self.staff])
        self.mn_sh = np.array([g(i, "min_consec_shifts") for i in self.staff])
        self.mn_off = np.array([g(i, "min_consec_days_off") for i in self.staff])
        self.maxwk = np.array([g(i, "max_weekends") for i in self.staff])
        self.minmin = np.array([float(inst.S[i]["min_total_min"]) for i in self.staff])
        self.maxmin = np.array([float(inst.S[i]["max_total_min"]) for i in self.staff])
        self.maxsh = np.array([[_num(inst.max_shifts_of(i, s)) for s in self.shifts]
                               for i in self.staff]).reshape(n, S)

        idx = {i: k for k, i in enumerate(self.staff)}
        self.idx = idx
        off = [[] for _ in range(n)]
        for (i, d) in inst.days_off:
            off[idx[i]].append(int(d))
        self.fixed_off = [np.array(sorted(set(o)), dtype=int) for o in off]
        self.locked = np.zeros((n, T), bool)
        for k, o in enumerate(self.fixed_off):
            self.locked[k, o] = True

        self.CF = np.zeros((S + 1, S + 1), bool)          # CF[a, b]: code a cannot precede b
        for s in self.shifts:
            for t in inst.cannot_follow[s]:
                self.CF[self.code[s], self.code[t]] = True
        self.has_cf = bool(self.CF.any())

        wk = list(inst.weekends)
        self.sat = np.array([int(a) for a, b in wk], dtype=int)
        sun = np.array([int(b) for a, b in wk], dtype=int)
        self.sun_ok = sun < T
        self.sun = np.where(self.sun_ok, sun, 0)

        # ---- cover ----
        self.req = np.zeros((T, S))
        self.Rmask = np.zeros((T, S), bool)
        for (d, s), r in inst.R.items():
            self.req[int(d), self.code[s] - 1] = float(r)
            self.Rmask[int(d), self.code[s] - 1] = True

        # ---- money ----
        self.wage_code = np.array([[0.0] + [float(C.wage[i][s]) for s in self.shifts]
                                   for i in self.staff])
        self.ot_rate = np.array([float(C.ot_hourly[i]) / 60.0 for i in self.staff])
        self.kg_day = np.array([float(C.kg_day[i]) for i in self.staff])
        self.p_kg = float(C.p_kg)
        self.ag_cost = float(P["agency_cost_per_shift"])
        self.rho_under = float(P["rho_understaffing"])
        self.rho_sen = float(P.get("rho_senior_shortfall", 0.0))
        self.ag_kg = float(P["agency_travel_kg"])
        self.anc_kg = float(P["e_anc_kg_per_over_shift"])
        self.k_pref = float(C.k_pref)
        self.k_fair = float(C.k_fair)
        self.k_fat = float(P["kappa_fatigue"])
        # the agency decision Gurobi makes for a fixed roster (see module docstring)
        self.agency_pays = levers and self.cap_ag > 0 and \
            (self.ag_cost + self.p_kg * self.ag_kg) < self.rho_under

        # Skill mix: in tbl_gurobi.py the senior-shortfall term exists only with --levers.
        # tbl_data.evaluate has no levers argument, so it may charge it either way; sen_on
        # starts as the Gurobi convention and consistency_check() switches it if evaluate()
        # turns out to disagree.
        senior = set(getattr(C, "senior", []) or [])
        self.senior = np.array([i in senior for i in self.staff], bool)
        self.sen_req = np.zeros((T, S))
        sreq = getattr(C, "senior_req", None)
        if sreq is not None and self.senior.any():
            for (d, s) in inst.R:
                try:
                    self.sen_req[int(d), self.code[s] - 1] = float(sreq[d, s])
                except (KeyError, TypeError):
                    pass
        self.sen_on = bool(levers)

        def req_arrays(lst):
            if not lst:
                z = np.zeros(0, int)
                return z, z, z, np.zeros(0)
            a = np.array([(idx[i], int(d), self.code[s]) for (i, d, s, w) in lst], dtype=int)
            return a[:, 0], a[:, 1], a[:, 2], np.array([float(w) for *_, w in lst])
        self.on_i, self.on_d, self.on_c, self.on_w = req_arrays(list(inst.on_req))
        self.off_i, self.off_d, self.off_c, self.off_w = req_arrays(list(inst.off_req))

        self.fair_lut = np.zeros(S + 1, bool)
        for s in (inst.NIGHT or inst.shifts):
            self.fair_lut[self.code[s]] = True
        self.night_lut = np.zeros(S + 1, bool)
        for s in (inst.NIGHT or []):
            self.night_lut[self.code[s]] = True
        self.has_night = bool(self.night_lut.any())

        self._rows = np.arange(n)[:, None]
        self._dayoff = (S + 1) * np.arange(T)[None, :]

    # -------------------------------------------------------------- evaluation
    def _counts(self, X):
        flat = (X.astype(np.int64) + self._dayoff).ravel()
        return np.bincount(flat, minlength=self.T * (self.S + 1)) \
                 .reshape(self.T, self.S + 1)[:, 1:].astype(float)

    def levers_for(self, X):
        """(staffed, agency, overtime) -- the lever values Gurobi would choose for X."""
        staffed = self._counts(X)
        mins = self.lens[X].sum(1)
        ot = np.maximum(0.0, mins - self.maxmin)
        if self.agency_pays:
            a = np.where(self.Rmask, np.minimum(np.maximum(0.0, self.req - staffed),
                                                self.cap_ag), 0.0)
        else:
            a = np.zeros_like(staffed)
        return staffed, a, ot

    def objectives(self, X):
        """(F_econ, F_soc, F_env) in A$, mirroring tbl_gurobi.py --objective dollars."""
        staffed, a, ot = self.levers_for(X)
        work = X > 0
        under = np.where(self.Rmask, np.maximum(0.0, self.req - staffed - a), 0.0)
        over = np.where(self.Rmask, np.maximum(0.0, staffed + a - self.req), 0.0)

        econ = (self.wage_code[self._rows, X].sum() + (self.ot_rate * ot).sum()
                + self.ag_cost * a.sum() + self.rho_under * under.sum())
        if self.sen_on and self.rho_sen and self.senior.any():
            sen = self._counts(X[self.senior])
            econ += self.rho_sen * np.where(
                self.Rmask, np.maximum(0.0, self.sen_req - sen), 0.0).sum()

        env = self.p_kg * ((self.kg_day * work.sum(1)).sum()
                           + self.ag_kg * a.sum() + self.anc_kg * over.sum())

        soc = 0.0
        if self.on_w.size:
            soc += self.k_pref * (self.on_w * (X[self.on_i, self.on_d] != self.on_c)).sum()
        if self.off_w.size:
            soc += self.k_pref * (self.off_w * (X[self.off_i, self.off_d] == self.off_c)).sum()
        cnt = self.fair_lut[X].sum(1).astype(float)
        soc += self.k_fair * np.abs(cnt - cnt.mean()).sum()
        if self.has_night and self.T >= 3:
            nm = self.night_lut[X]
            soc += self.k_fat * (nm[:, :-2] & nm[:, 1:-1] & nm[:, 2:]).sum()
        return np.array([econ, soc, env])

    def to_tbl(self, X):
        """X -> (roster, agency, ot_min) in the dict form tbl_data.evaluate expects."""
        _, a, ot = self.levers_for(X)
        roster = {(i, d): (self.shifts[X[k, d] - 1] if X[k, d] else "")
                  for k, i in enumerate(self.staff) for d in range(self.T)}
        agency = {(d, s): int(round(a[int(d), self.code[s] - 1])) for (d, s) in self.inst.R}
        ot_min = {i: float(ot[k]) for k, i in enumerate(self.staff)}
        return roster, agency, ot_min

    def score(self, X):
        roster, agency, ot = self.to_tbl(X)
        return roster, agency, ot, D.evaluate(self.inst, self.C, roster, agency, ot, None)

    # -------------------------------------------------------------- feasibility
    def check_row(self, k, row):
        """True iff employee k's row satisfies every hard constraint of the model."""
        T = self.T
        w = row > 0
        fo = self.fixed_off[k]
        if fo.size and w[fo].any():
            return False
        if self.has_cf and self.CF[row[:-1], row[1:]].any():
            return False
        if (np.bincount(row, minlength=self.S + 1)[1:] > self.maxsh[k]).any():
            return False
        mins = self.lens[row].sum()
        if mins < self.minmin[k] - 1e-9 or mins > self.maxmin[k] + self.capot + 1e-9:
            return False
        if self.sat.size:
            if (w[self.sat] | (w[self.sun] & self.sun_ok)).sum() > self.maxwk[k]:
                return False
        wi = w.astype(np.int8)
        cut = np.flatnonzero(np.diff(wi)) + 1
        st = np.concatenate(([0], cut))
        en = np.concatenate((cut, [T]))
        ln = en - st
        val = wi[st]
        if (ln[val == 1] > self.K[k]).any():                       # max consecutive shifts
            return False
        inner = (st > 0) & (en < T)                                 # runs with both neighbours
        if (inner & (val == 1) & (ln < self.mn_sh[k])).any():       # min consecutive shifts
            return False
        if (inner & (val == 0) & (ln < self.mn_off[k])).any():      # min consecutive days off
            return False
        return True

    def row_spec(self, k):
        """Plain, picklable description of employee k's hard constraints (for CP-SAT)."""
        return dict(T=self.T, S=self.S, CF=self.CF, maxsh=self.maxsh[k], K=int(self.K[k]),
                    mn_sh=int(self.mn_sh[k]), mn_off=int(self.mn_off[k]),
                    maxwk=int(self.maxwk[k]), sat=self.sat, sun=self.sun, sun_ok=self.sun_ok,
                    lens=self.lens, minmin=self.minmin[k], maxmin=self.maxmin[k],
                    capot=self.capot, fixed_off=self.fixed_off[k])

    def row_weights(self, k, p, rng):
        """CP-SAT objective for pool row p of employee k. Four flavours for diversity."""
        T, S = self.T, self.S
        demand = self.req / max(1, self.n)
        pref = np.zeros((T, S))
        m = self.on_i == k
        pref[self.on_d[m], self.on_c[m] - 1] += self.on_w[m]
        m = self.off_i == k
        pref[self.off_d[m], self.off_c[m] - 1] -= self.off_w[m]
        if pref.any():
            pref /= np.abs(pref).max()
        noise = rng.normal(size=(T, S))
        kind = p % 4
        if kind == 0:   w = 3 * demand + 0.5 * noise                 # cover-led
        elif kind == 1: w = 2 * pref + demand + 0.3 * noise         # preference-led
        elif kind == 2: w = noise                                   # pure random
        else:           w = -1.0 + 0.3 * noise + pref               # lean (few days)
        return w


# =====================================================================================
#  CP-SAT: one-employee models for the initial pool and for independent verification
# =====================================================================================
def _row_model(spec):
    from ortools.sat.python import cp_model
    T, S = spec["T"], spec["S"]
    m = cp_model.CpModel()
    x = [[m.NewBoolVar(f"x{d}_{a}") for a in range(S)] for d in range(T)]
    work = [cp_model.LinearExpr.Sum(x[d]) for d in range(T)]
    for d in range(T):
        m.Add(work[d] <= 1)
    for d in spec["fixed_off"]:
        m.Add(work[int(d)] == 0)
    CF = spec["CF"]
    for d in range(T - 1):
        for a in range(S):
            for b in range(S):
                if CF[a + 1, b + 1]:
                    m.AddBoolOr([x[d][a].Not(), x[d + 1][b].Not()])
    for a in range(S):
        m.Add(cp_model.LinearExpr.Sum([x[d][a] for d in range(T)]) <= int(spec["maxsh"][a]))
    K = spec["K"]
    for st in range(T - K):
        m.Add(cp_model.LinearExpr.Sum(work[st:st + K + 1]) <= K)
    for j in range(1, spec["mn_sh"]):
        for d in range(T - j - 1):
            m.Add(cp_model.LinearExpr.Sum(work[d + 1:d + j + 1]) - work[d] - work[d + j + 1]
                  <= j - 1)
    for j in range(1, spec["mn_off"]):
        for d in range(T - j - 1):
            m.Add(work[d] + work[d + j + 1] - cp_model.LinearExpr.Sum(work[d + 1:d + j + 1])
                  <= 1)
    wk = []
    for q, (sa, su) in enumerate(zip(spec["sat"], spec["sun"])):
        v = m.NewBoolVar(f"wk{q}")
        m.Add(v >= work[int(sa)])
        if spec["sun_ok"][q]:
            m.Add(v >= work[int(su)])
        wk.append(v)
    if wk:
        m.Add(cp_model.LinearExpr.Sum(wk) <= spec["maxwk"])
    lens = spec["lens"]
    mins = cp_model.LinearExpr.WeightedSum(
        [x[d][a] for d in range(T) for a in range(S)],
        [int(round(lens[a + 1])) for d in range(T) for a in range(S)])
    m.Add(mins >= int(math.ceil(spec["minmin"] - 1e-9)))
    m.Add(mins <= int(math.floor(spec["maxmin"] + spec["capot"] + 1e-9)))
    return m, x


def _solve(m, workers, tl):
    """-> (solver, found_a_solution, status_name)."""
    from ortools.sat.python import cp_model
    sv = cp_model.CpSolver()
    sv.parameters.max_time_in_seconds = tl
    sv.parameters.num_search_workers = max(1, workers)
    st = sv.Solve(m)
    name = {cp_model.OPTIMAL: "OPTIMAL", cp_model.FEASIBLE: "FEASIBLE",
            cp_model.INFEASIBLE: "INFEASIBLE", cp_model.MODEL_INVALID: "MODEL_INVALID"}.get(
        st, "UNKNOWN")
    return sv, st in (cp_model.OPTIMAL, cp_model.FEASIBLE), name


def _read_row(sv, x, T, S):
    row = np.zeros(T, np.int8)
    for d in range(T):
        for a in range(S):
            if sv.Value(x[d][a]):
                row[d] = a + 1
    return row


def _pool_for_employee(args):
    """Worker: `size` feasible rows for one employee. Returns (rows, error-or-None).

    Long horizons (182 / 364 days, many shift types) can need far more than the per-row
    time just to find ANY feasible row. So:
      1. find one feasible row, escalating the time (10 s, 60 s, 300 s) and the threads,
         and telling apart 'proven infeasible' from 'ran out of time';
      2. every further row is solved with that row as a HINT, so CP-SAT starts from a
         feasible point and only has to move it toward the row's objective -- it cannot
         come back empty.
    """
    spec, weights, workers, tl = args
    T, S = spec["T"], spec["S"]
    m, x = _row_model(spec)
    flat = [x[d][a] for d in range(T) for a in range(S)]

    def set_objective(w):
        coefs = np.round(w * 1000).astype(int)
        m.Maximize(sum(int(coefs[d, a]) * x[d][a] for d in range(T) for a in range(S)
                       if coefs[d, a] != 0))

    # ---- 1. the first feasible row
    set_objective(weights[0])
    sv, ok, st = _solve(m, workers, tl)
    if ok:
        base = _read_row(sv, x, T, S)
    else:
        m0, x0 = _row_model(spec)               # same constraints, no objective
        base, tried = None, []
        for t_lim in (max(10.0, 5 * tl), 60.0, 300.0):
            sv, ok, st = _solve(m0, max(workers, 8), t_lim)
            tried.append(f"{t_lim:g}s")
            if ok:
                base = _read_row(sv, x0, T, S)
                break
            if st in ("INFEASIBLE", "MODEL_INVALID"):
                return None, (f"CP-SAT PROVES no feasible row exists ({st}). The hard "
                              f"constraints in tbl_nsga.py disagree with the instance -- "
                              f"send Claude this message and the instance name.")
        if base is None:
            return None, (f"no feasible row found within {' + '.join(tried)} (status "
                          f"{st}: ran out of time, NOT shown infeasible). Re-run with a "
                          f"larger --pool-time, e.g. --pool-time 5.")
    rows = [base]

    # ---- 2. the rest, each hinted from a feasible row so a solution always exists
    for w in weights[1:]:
        set_objective(w)
        m.ClearHints()
        for v, val in zip(flat, ((base[d] == a + 1) for d in range(T) for a in range(S))):
            m.AddHint(v, bool(val))
        sv, ok, st = _solve(m, workers, tl)
        rows.append(_read_row(sv, x, T, S) if ok else base.copy())
    return rows, None


def _verify_row(args):
    """Worker: is this row feasible in the independent CP-SAT model?"""
    spec, row = args
    m, x = _row_model(spec)
    for d in range(spec["T"]):
        for a in range(spec["S"]):
            m.Add(x[d][a] == (1 if row[d] == a + 1 else 0))
    _, ok, _ = _solve(m, 1, 60.0)
    return ok


def build_pool(prob, size, seed, workers, tl, jobs, say):
    """Feasible rows per employee, cached on disk so a pool is only ever paid for once."""
    os.makedirs(CACHE, exist_ok=True)
    tag = "levers" if prob.levers else "base"
    path = os.path.join(CACHE, f"{prob.name}_{tag}_pool{size}_seed{seed}.npz")
    if os.path.exists(path):
        try:
            z = np.load(path)
            pools = [z[f"e{k}"] for k in range(prob.n)]
            bad = sum(not prob.check_row(k, r) for k, P in enumerate(pools) for r in P)
            if bad == 0 and int(z["T"]) == prob.T:
                say(f"  row pool  : loaded from cache ({sum(len(p) for p in pools)} rows)")
                return pools, 0.0
            say(f"  row pool  : cache is stale ({bad} rows fail today's checks) -- rebuilding")
        except Exception:
            say("  row pool  : cache unreadable -- rebuilding")

    t0 = time.time()
    rng = np.random.default_rng(seed)
    tasks = []
    for k in range(prob.n):
        ws = [prob.row_weights(k, p, rng) for p in range(size)]
        tasks.append((prob.row_spec(k), ws, 1 if jobs > 1 else workers, tl))
    est = prob.n * size * tl / max(1, jobs) / 60
    say(f"  row pool  : solving {size} one-employee CP-SAT model(s) for each of "
        f"{prob.n} staff -- at most ~{est:.0f} min, once per instance (then cached)")
    results = [None] * prob.n
    if jobs > 1:
        with ProcessPoolExecutor(max_workers=jobs) as ex:
            futs = {ex.submit(_pool_for_employee, t): k for k, t in enumerate(tasks)}
            for f in as_completed(futs):
                results[futs[f]] = f.result()
    else:
        for k, t in enumerate(tasks):
            results[k] = _pool_for_employee(t)
            if prob.n >= 20 and (k + 1) % max(1, prob.n // 10) == 0:
                say(f"              {k+1}/{prob.n} staff done  ({time.time()-t0:.0f} s)")
    pools, mismatch = [], 0
    for k, (rows, err) in enumerate(results):
        if err:
            raise SystemExit(f"  employee {prob.staff[k]}: {err}")
        uniq = {r.tobytes(): r for r in rows}
        rows = list(uniq.values())
        for r in rows:
            if not prob.check_row(k, r):
                mismatch += 1
        pools.append(np.array(rows, np.int8))
    if mismatch:
        raise SystemExit(f"  [!] {mismatch} CP-SAT row(s) FAIL the numpy row checker. The two "
                         f"encodings of the hard constraints disagree -- do not trust results "
                         f"until this is fixed.")
    np.savez_compressed(path, T=prob.T, **{f"e{k}": p for k, p in enumerate(pools)})
    dt = time.time() - t0
    say(f"  row pool  : {sum(len(p) for p in pools)} distinct rows in {dt:.1f} s, "
        f"all pass the row checker (cached)")
    return pools, dt


# =====================================================================================
#  NSGA machinery (generic: works on any objective matrix F, minimisation)
# =====================================================================================
def nd_sort(F):
    """Fast non-dominated sort -> list of index arrays, best front first."""
    le = (F[:, None, :] <= F[None, :, :]).all(2)
    lt = (F[:, None, :] < F[None, :, :]).any(2)
    dom = le & lt                                  # dom[p, q]: p dominates q
    cnt = dom.sum(0).astype(int)
    done = np.zeros(len(F), bool)
    fronts, cur = [], np.flatnonzero(cnt == 0)
    while cur.size:
        fronts.append(cur)
        done[cur] = True
        cnt = cnt - dom[cur].sum(0)
        cnt[done] = -1
        cur = np.flatnonzero(cnt == 0)
    return fronts


def crowding(F):
    n, M = F.shape
    d = np.zeros(n)
    if n <= 2:
        d[:] = np.inf
        return d
    for m in range(M):
        o = np.argsort(F[:, m], kind="stable")
        span = F[o[-1], m] - F[o[0], m]
        d[o[0]] = d[o[-1]] = np.inf
        if span > 0:
            d[o[1:-1]] += (F[o[2:], m] - F[o[:-2], m]) / span
    return d


def rank_and_crowd(F):
    rank = np.empty(len(F), int)
    crowd = np.empty(len(F))
    for r, f in enumerate(nd_sort(F)):
        rank[f] = r
        crowd[f] = crowding(F[f])
    return rank, crowd


def survive_nsga2(F, N):
    chosen = []
    for f in nd_sort(F):
        if len(chosen) + len(f) <= N:
            chosen.extend(f.tolist())
            if len(chosen) == N:
                break
        else:
            cd = crowding(F[f])
            chosen.extend(f[np.argsort(-cd, kind="stable")[:N - len(chosen)]].tolist())
            break
    return np.array(chosen, int)


def das_dennis(M, p):
    out = []

    def rec(prefix, left, depth):
        if depth == M - 1:
            out.append(prefix + [left])
            return
        for k in range(left + 1):
            rec(prefix + [k], left - k, depth + 1)
    rec([], p, 0)
    return np.array(out, float) / p


def _intercepts(Ft, first):
    """Hyperplane intercepts through the extreme points (Deb & Jain 2014), with the
    usual fallback to the nadir of the first front when the plane is degenerate."""
    M = Ft.shape[1]
    W = np.full((M, M), 1e-6)
    np.fill_diagonal(W, 1.0)
    asf = (Ft[:, None, :] / W[None, :, :]).max(2)
    E = Ft[asf.argmin(0)]
    a = None
    with np.errstate(all="ignore"):
        try:
            b = np.linalg.solve(E, np.ones(M))
            a = 1.0 / b
            if not np.all(np.isfinite(a)) or np.any(a <= 1e-9):
                a = None
        except np.linalg.LinAlgError:
            a = None
    if a is None:
        a = Ft[first].max(0)
    worst = Ft.max(0)
    a = np.where(a <= 1e-9, worst, a)
    return np.where(a <= 1e-9, 1.0, a)


def survive_nsga3(F, N, refs, rng):
    fronts = nd_sort(F)
    chosen, last = [], None
    for f in fronts:
        if len(chosen) + len(f) <= N:
            chosen.extend(f.tolist())
            if len(chosen) == N:
                return np.array(chosen, int)
        else:
            last = f
            break
    if last is None:
        return np.array(chosen, int)
    St = np.concatenate([np.array(chosen, int), last])
    Fs = F[St]
    Ft = Fs - Fs.min(0)
    first = np.isin(St, fronts[0])
    Fn = Ft / _intercepts(Ft, first)
    # association: perpendicular distance to each reference line
    rn = refs / np.linalg.norm(refs, axis=1, keepdims=True)
    proj = Fn @ rn.T
    dist = np.sqrt(np.maximum(0.0, (Fn ** 2).sum(1)[:, None] - proj ** 2))
    near = dist.argmin(1)
    dnear = dist[np.arange(len(St)), near]
    nP = len(chosen)
    rho = np.bincount(near[:nP], minlength=len(refs))
    cand = np.arange(nP, len(St))
    picked = np.zeros(len(St), bool)
    active = np.ones(len(refs), bool)
    sel = []
    while len(sel) < N - nP:
        rmin = rho[active].min()
        j = rng.choice(np.flatnonzero(active & (rho == rmin)))
        I = cand[(near[cand] == j) & ~picked[cand]]
        if I.size == 0:
            active[j] = False
            continue
        c = I[dnear[I].argmin()] if rho[j] == 0 else rng.choice(I)
        picked[c] = True
        sel.append(c)
        rho[j] += 1
    return np.concatenate([np.array(chosen, int), St[np.array(sel, int)]])


def hv3(P, ref):
    """Exact hypervolume of a 3-objective minimisation set (slicing along objective 3)."""
    P = np.asarray(P, float)
    P = P[(P < ref).all(1)]
    if len(P) == 0:
        return 0.0
    P = P[nd_sort(P)[0]]
    P = P[np.argsort(P[:, 2], kind="stable")]
    vol = 0.0
    for k in range(len(P)):
        z_next = P[k + 1, 2] if k + 1 < len(P) else ref[2]
        if z_next <= P[k, 2]:
            continue
        Q = P[:k + 1, :2]
        Q = Q[np.argsort(Q[:, 0], kind="stable")]
        area, best_y = 0.0, ref[1]
        for t in range(len(Q)):
            best_y = min(best_y, Q[t, 1])
            x_next = Q[t + 1, 0] if t + 1 < len(Q) else ref[0]
            area += (x_next - Q[t, 0]) * (ref[1] - best_y)
        vol += area * (z_next - P[k, 2])
    return vol


# =====================================================================================
#  VARIATION: row-wise crossover and feasibility-preserving mutation
# =====================================================================================
class Variation:
    OPS = ("swap", "shift", "block", "resample", "cover_add", "cover_drop")
    WEIGHTS = np.array([0.20, 0.10, 0.20, 0.10, 0.20, 0.20])

    def __init__(self, prob, pools, rng, pc, mut):
        self.p, self.pools, self.rng, self.pc, self.mut = prob, pools, rng, pc, mut
        self.tried = np.zeros(len(self.OPS), int)
        self.accepted = np.zeros(len(self.OPS), int)

    def crossover(self, A, B):
        if self.rng.random() >= self.pc:
            return A.copy(), B.copy()
        mask = self.rng.random(self.p.n) < 0.5
        return np.where(mask[:, None], A, B), np.where(mask[:, None], B, A)

    def mutate(self, X, events=None):
        rng = self.rng
        k = events if events is not None else max(1, rng.poisson(self.mut))
        for _ in range(k):
            for _attempt in range(6):
                op = rng.choice(len(self.OPS), p=self.WEIGHTS)
                self.tried[op] += 1
                if getattr(self, "_" + self.OPS[op])(X):
                    self.accepted[op] += 1
                    break
        return X

    def _try(self, X, k, new):
        if np.array_equal(new, X[k]) or not self.p.check_row(k, new):
            return False
        X[k] = new
        return True

    def _swap(self, X):
        p, rng = self.p, self.rng
        if p.n < 2:
            return False
        d = rng.integers(p.T)
        i, j = rng.choice(p.n, 2, replace=False)
        if X[i, d] == X[j, d]:
            return False
        ri, rj = X[i].copy(), X[j].copy()
        ri[d], rj[d] = rj[d], ri[d]
        if p.check_row(i, ri) and p.check_row(j, rj):
            X[i], X[j] = ri, rj
            return True
        return False

    def _shift(self, X):
        p, rng = self.p, self.rng
        if p.S < 2:
            return False
        k = rng.integers(p.n)
        days = np.flatnonzero(X[k] > 0)
        if days.size == 0:
            return False
        d = rng.choice(days)
        new = X[k].copy()
        c = rng.integers(1, p.S)
        new[d] = c if c < X[k, d] else c + 1          # any code but the current one
        return self._try(X, k, new)

    def _block(self, X):
        p, rng = self.p, self.rng
        k = rng.integers(p.n)
        L = int(min(p.T, rng.integers(2, 8)))
        st = rng.integers(0, p.T - L + 1)
        donor = self.pools[k][rng.integers(len(self.pools[k]))]
        new = X[k].copy()
        new[st:st + L] = donor[st:st + L]
        return self._try(X, k, new)

    def _resample(self, X):
        k = self.rng.integers(self.p.n)
        pool = self.pools[k]
        if len(pool) < 2:
            return False
        return self._try(X, k, pool[self.rng.integers(len(pool))].copy())

    def _cover_add(self, X):
        """Put someone on an understaffed (day, shift)."""
        p, rng = self.p, self.rng
        staffed = p._counts(X)
        gap = np.argwhere(p.Rmask & (staffed < p.req))
        if gap.size == 0:
            return False
        d, a = gap[rng.integers(len(gap))]
        who = np.flatnonzero((X[:, d] != a + 1) & ~p.locked[:, d])
        if who.size == 0:
            return False
        k = rng.choice(who)
        new = X[k].copy()
        new[d] = a + 1
        return self._try(X, k, new)

    def _cover_drop(self, X):
        """Take someone off an overstaffed (day, shift)."""
        p, rng = self.p, self.rng
        staffed = p._counts(X)
        surplus = np.argwhere(staffed > np.where(p.Rmask, p.req, 0))
        if surplus.size == 0:
            return False
        d, a = surplus[rng.integers(len(surplus))]
        who = np.flatnonzero(X[:, d] == a + 1)
        if who.size == 0:
            return False
        k = rng.choice(who)
        new = X[k].copy()
        new[d] = 0
        return self._try(X, k, new)


# =====================================================================================
#  ONE RUN
# =====================================================================================
def run_ga(prob, pools, algo, seed, pop, evals, time_limit, partitions, pc, mut,
           print_every, say):
    rng = np.random.default_rng(seed)
    var = Variation(prob, pools, rng, pc, mut)
    n, T = prob.n, prob.T
    refs = das_dennis(3, partitions) if algo == "nsga3" else None
    t0 = time.time()

    X = np.empty((pop, n, T), np.int8)
    for q in range(pop):
        for k in range(n):
            X[q, k] = pools[k][rng.integers(len(pools[k]))]
        var.mutate(X[q], events=int(rng.integers(0, 5)))
    F = np.array([prob.objectives(X[q]) for q in range(pop)])
    used, gen = pop, 0

    hdr = (f"{'gen':>6}{'evals':>9}{'sec':>8}{'|F1|':>6}{'min F_econ':>17}{'min F_soc':>14}"
           f"{'min F_env':>13}{'min total':>17}")
    say(hdr)
    say("-" * len(hdr))

    def progress():
        f1 = nd_sort(F)[0]
        tot = F.sum(1)
        say(f"{gen:>6}{used:>9}{time.time()-t0:>8.1f}{len(f1):>6}{F[:,0].min():>17,.2f}"
            f"{F[:,1].min():>14,.2f}{F[:,2].min():>13,.4f}{tot.min():>17,.2f}")
    progress()

    while True:
        if evals and used >= evals:
            break
        if time_limit and time.time() - t0 >= time_limit:
            break
        if algo == "nsga2":
            rank, crowd = rank_and_crowd(F)
            a, b = rng.integers(pop, size=pop), rng.integers(pop, size=pop)
            ka, kb = (rank[a], -crowd[a]), (rank[b], -crowd[b])
            a_wins = (ka[0] < kb[0]) | ((ka[0] == kb[0]) & (ka[1] < kb[1]))
            tie = (ka[0] == kb[0]) & (ka[1] == kb[1])
            a_wins = np.where(tie, rng.random(pop) < 0.5, a_wins)
            parents = np.where(a_wins, a, b)
        else:
            parents = rng.integers(pop, size=pop)    # NSGA-III: random mating (Deb & Jain)

        seen = {X[q].tobytes() for q in range(pop)}
        kids = []
        for q in range(0, pop - 1, 2):
            for c in var.crossover(X[parents[q]], X[parents[q + 1]]):
                var.mutate(c)
                for _ in range(5):                          # avoid clones of what we have
                    if c.tobytes() not in seen:
                        break
                    var.mutate(c, events=1)
                seen.add(c.tobytes())
                kids.append(c)
        C = np.array(kids, np.int8)
        FC = np.array([prob.objectives(c) for c in C])
        used += len(C)
        XX = np.concatenate([X, C])
        FF = np.concatenate([F, FC])
        keep = survive_nsga2(FF, pop) if algo == "nsga2" else survive_nsga3(FF, pop, refs, rng)
        X, F = XX[keep], FF[keep]
        gen += 1
        if print_every and gen % print_every == 0:
            progress()
    if not print_every or gen % print_every:
        progress()
    say("-" * len(hdr))
    rate = var.accepted / np.maximum(1, var.tried)
    say("  mutation acceptance: " + "  ".join(f"{o} {r:.0%}" for o, r in zip(var.OPS, rate)))

    f1 = nd_sort(F)[0]
    seen, front = set(), []
    for q in f1[np.argsort(F[f1, 0], kind="stable")]:
        key = tuple(np.round(F[q], 6))
        if key not in seen:
            seen.add(key)
            front.append(q)
    return X[front], F[front], dict(gens=gen, evals=used, run_s=time.time() - t0)


# =====================================================================================
#  VERIFICATION, REPORTING, FILES
# =====================================================================================
def verify_rows(prob, Xf, cap, jobs, rng):
    rows = {}
    for X in Xf:
        for k in range(prob.n):
            rows.setdefault((k, X[k].tobytes()), X[k])
    keys = list(rows)
    sample = len(keys) > cap
    if sample:
        keys = [keys[i] for i in rng.choice(len(keys), cap, replace=False)]
    tasks = [(prob.row_spec(k), rows[(k, b)]) for (k, b) in keys]
    if jobs > 1:
        with ProcessPoolExecutor(max_workers=jobs) as ex:
            ok = list(ex.map(_verify_row, tasks, chunksize=8))
    else:
        ok = [_verify_row(t) for t in tasks]
    good = sum(ok)
    return f"{good}/{len(ok)} ok" + (" (sample)" if sample else ""), good == len(ok)


def verify_core(prob, X):
    """Re-check one roster in YOUR tbl_ortools.build_core model, every lever free."""
    try:
        import tbl_ortools as ORT
        from ortools.sat.python import cp_model
        core = ORT.build_core(prob.inst, prob.C, prob.levers)
        m, x = core["m"], core["x"]
        for k, i in enumerate(prob.staff):
            for d in range(prob.T):
                for s in prob.shifts:
                    m.Add(x[i, d, s] == (1 if X[k, d] == prob.code[s] else 0))
        sv = cp_model.CpSolver()
        sv.parameters.max_time_in_seconds = 120
        st = sv.Solve(m)
        if st in (cp_model.OPTIMAL, cp_model.FEASIBLE):
            return "ok"
        try:
            return sv.status_name(st)
        except Exception:
            return str(st)
    except Exception as ex:
        return f"skipped ({str(ex).splitlines()[0][:50]})"


RUN_COLS = ["stamp", "run_id", "algorithm", "instance", "staff", "days", "levers", "seed", "pop",
            "partitions", "evals_budget", "time_limit", "pc", "mut", "pool", "generations",
            "evals", "pool_s",
            "run_s", "front_size", "best_total", "best_econ", "best_soc", "best_env",
            "best_kg", "min_econ", "min_soc", "min_env", "eval_diff", "row_check",
            "core_check", "carbon_price", "front_file"]


def append_run(row):
    os.makedirs(RESULTS, exist_ok=True)
    new = not os.path.exists(RUNS_CSV)
    for attempt in range(5):
        try:
            with open(RUNS_CSV, "a", newline="") as f:
                w = csv.DictWriter(f, fieldnames=RUN_COLS)
                if new:
                    w.writeheader()
                w.writerow({c: row.get(c, "") for c in RUN_COLS})
            return
        except PermissionError:
            time.sleep(2)
    alt = RUNS_CSV.replace(".csv", f"_{int(time.time())}.csv")
    with open(alt, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=RUN_COLS)
        w.writeheader()
        w.writerow({c: row.get(c, "") for c in RUN_COLS})
    print(f"  [!] {RUNS_CSV} is locked (open in Excel?) -- this run went to {alt}")


def _write_runs(rows):
    """Rewrite nsga_runs.csv in full (used when a group of runs is replaced)."""
    os.makedirs(RESULTS, exist_ok=True)
    for attempt in range(5):
        try:
            with open(RUNS_CSV, "w", newline="") as f:
                w = csv.DictWriter(f, fieldnames=RUN_COLS)
                w.writeheader()
                for r in rows:
                    w.writerow({c: r.get(c, "") for c in RUN_COLS})
            return
        except PermissionError:
            print(f"  [!] {RUNS_CSV} is open in another program -- close it (retrying)")
            time.sleep(5)
    raise SystemExit(f"cannot write {RUNS_CSV}: close it in Excel and run again")


def group_name(inst_name, levers, algo):
    return f"{inst_name}_{'levers' if levers else 'base'}_{algo}"


def settings_line(a, algo):
    return (f"settings: {NICE[algo]}  pop {a.pop}"
            + (f"  partitions {a.partitions}" if algo == "nsga3" else "")
            + (f"  evals {a.evals:,}" if a.evals else "") + (f"  time {a.time:g}s" if a.time else "")
            + f"  pc {a.pc}  mut {a.mut}  pool {a.pool}  levers {'on' if a.levers else 'off'}"
            + f"  seeds {a.seed_start}..{a.seed_start + a.seeds - 1}"
            + (f"  carbon {a.carbon:g}" if a.carbon is not None else "")
            + ("  prices " + " ".join(a.price) if a.price else ""))


class _Tee:
    """Print to the console AND a file."""
    def __init__(self, path):
        self.path = path

    def __enter__(self):
        self.f = open(self.path, "w", encoding="utf-8")
        self.old, sys.stdout = sys.stdout, self
        return self

    def write(self, t):
        self.old.write(t)
        self.f.write(t)

    def flush(self):
        self.old.flush()
        self.f.flush()

    def __exit__(self, *exc):
        sys.stdout = self.old
        self.f.close()


class Group:
    """All seeds of one instance + algorithm. Starting a Group wipes that pair's previous
    files and nsga_runs.csv rows; every finished seed then rewrites the group's files, so a
    crash part-way still leaves the seeds that did finish."""

    def __init__(self, prob, algo, a):
        self.prob, self.algo, self.a = prob, algo, a
        self.name = group_name(prob.name, prob.levers, algo)
        self.csv = os.path.join(FRONTS, self.name + ".csv")
        self.npz = os.path.join(FRONTS, self.name + ".npz")
        self.best = os.path.join(RESULTS, self.name + "_best.csv")
        self.log = os.path.join(RESULTS, self.name + ".log")
        self.seeds = {}
        os.makedirs(FRONTS, exist_ok=True)
        rows = _read_runs()
        keep = [r for r in rows if not (r["instance"] == prob.name and r["algorithm"] == algo
                                        and r["levers"] == str(int(prob.levers)))]
        replaced = len(rows) - len(keep)
        if replaced:
            _write_runs(keep)
        for p in (self.csv, self.npz, self.best, self.log):
            if os.path.exists(p):
                os.remove(p)
        if replaced:
            print(f"  replacing {replaced} earlier {NICE[algo]} run(s) of {prob.name}")

    def part(self, seed):
        return os.path.join(RESULTS, f"{self.name}_s{seed}.log.part")

    def add(self, seed, Xf, Ff, front_rows, row):
        self.seeds[seed] = (Xf, Ff, front_rows)
        append_run(row)
        self.write()

    def write(self):
        seeds = sorted(self.seeds)
        cols = ["seed"] + list(self.seeds[seeds[0]][2][0].keys())
        with open(self.csv, "w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=cols)
            w.writeheader()
            for sd in seeds:
                for r in self.seeds[sd][2]:
                    w.writerow(dict(seed=sd, **r))
        arrays = {}
        for sd in seeds:
            arrays[f"X_s{sd}"], arrays[f"F_s{sd}"] = self.seeds[sd][0], self.seeds[sd][1]
        np.savez_compressed(self.npz, staff=np.array([str(i) for i in self.prob.staff]),
                            shifts=np.array([str(x) for x in self.prob.shifts]), **arrays)
        best = min(((self.seeds[sd][2][q]["total"], sd, q) for sd in seeds
                    for q in range(len(self.seeds[sd][2]))))
        wr = getattr(D, "write_roster_csv", None)
        if wr:
            try:
                roster, _, _ = self.prob.to_tbl(self.seeds[best[1]][0][best[2]])
                wr(self.prob.inst, roster, self.best)
            except Exception as ex:
                print(f"  (best roster not written: {ex})")

    def finish(self):
        parts = sorted(glob_parts(RESULTS, self.name), key=lambda p: inst_no(p.split("_s")[-1]))
        if not parts and not self.seeds:
            return
        with open(self.log, "w", encoding="utf-8") as out:
            out.write(f"{self.name}  --  {time.strftime('%Y-%m-%d %H:%M')}\n")
            out.write(settings_line(self.a, self.algo) + "\n")
            for p in parts:
                with open(p, encoding="utf-8", errors="replace") as f:
                    out.write("\n" + f.read())
                os.remove(p)


def glob_parts(folder, name):
    pre = name + "_s"
    return [os.path.join(folder, f) for f in os.listdir(folder)
            if f.startswith(pre) and f.endswith(".log.part")]


def report_run(prob, a, algo, seed, Xf, Ff, info, pool_s, say):
    """Score the front with tbl_data.evaluate and print it.
    -> (nsga_runs.csv row, front rows). Files are written by Group."""
    rng = np.random.default_rng(seed + 7919)
    gname = group_name(prob.name, prob.levers, algo)

    rows, diff = [], 0.0
    for X, f in zip(Xf, Ff):
        roster, ag, ot, e = prob.score(X)
        diff = max(diff, abs(e["F_econ"] - f[0]), abs(e["F_soc"] - f[1]), abs(e["F_env"] - f[2]))
        rows.append(dict(F_econ=round(e["F_econ"], 2), F_soc=round(e["F_soc"], 2),
                         F_env=round(e["F_env"], 4), total=round(e["total"], 2),
                         penalty=round(e.get("penalty", 0)), kg_co2e=round(e.get("kg", 0), 3),
                         shifts=e.get("shifts", ""), agency=e.get("n_agency", ""),
                         understaffed=e.get("n_under", ""), _roster=roster))

    say(f"\nPARETO FRONT  {NICE[algo]}  seed {seed}  -- {len(rows)} non-dominated roster(s), "
        f"scored by tbl_data.evaluate")
    hdr = (f"{'#':>4}{'F_econ':>17}{'F_soc':>14}{'F_env':>13}{'total':>17}{'penalty':>9}"
           f"{'kg_co2e':>13}{'sh':>7}{'ag':>6}{'un':>6}")
    say(hdr)
    say("-" * len(hdr))
    best = min(range(len(rows)), key=lambda q: rows[q]["total"])
    for q, r in enumerate(rows):
        say(f"{q+1:>4}{r['F_econ']:>17,.2f}{r['F_soc']:>14,.2f}{r['F_env']:>13,.4f}"
            f"{r['total']:>17,.2f}{r['penalty']:>9}{r['kg_co2e']:>13,.2f}{r['shifts']:>7}"
            f"{r['agency']:>6}{r['understaffed']:>6}" + ("   <- lowest total" if q == best else ""))
    say("-" * len(hdr))
    B = rows[best]
    say(f"  lowest total on the front  A${B['total']:,.2f}   (compare: Gurobi weighted-sum "
        f"total for {prob.name})")
    for j, nm in enumerate(("F_econ", "F_soc", "F_env")):
        v = [r[nm] for r in rows]
        say(f"  {nm:<7} spans {min(v):>14,.4f} .. {max(v):,.4f}")

    flag = "ok" if diff < 0.05 else "MISMATCH"
    say(f"  evaluator check: max |fast - tbl_data.evaluate| = {diff:.6f}  [{flag}]")
    if flag != "ok":
        say("  [!] the search optimised different numbers from the ones reported. The reported")
        say("      front is still correctly SCORED by evaluate(), but fix this before citing it.")
    row_check, row_ok = verify_rows(prob, Xf, a.verify_max, a.jobs if a.jobs > 1 and
                                    not a._in_worker else 1, rng)
    say(f"  hard-constraint check (independent CP-SAT, per row): {row_check}"
        + ("" if row_ok else "   <-- INFEASIBLE ROWS: do not use this run"))
    core = verify_core(prob, Xf[best]) if a.verify_core else "not run"
    if a.verify_core:
        say(f"  best roster in tbl_ortools.build_core: {core}")

    front_rows = [{k: v for k, v in r.items() if not k.startswith("_")} for r in rows]
    return dict(stamp=time.strftime("%Y-%m-%d %H:%M"), run_id=f"{gname}_s{seed}",
                algorithm=algo, instance=prob.name,
                staff=prob.n, days=prob.T, levers=int(prob.levers), seed=seed, pop=a.pop,
                partitions=a.partitions if algo == "nsga3" else "", evals_budget=a.evals,
                time_limit=a.time, pc=a.pc, mut=a.mut, pool=a.pool,
                generations=info["gens"], evals=info["evals"],
                pool_s=round(pool_s, 1), run_s=round(info["run_s"], 1), front_size=len(rows),
                best_total=B["total"], best_econ=B["F_econ"], best_soc=B["F_soc"],
                best_env=B["F_env"], best_kg=B["kg_co2e"],
                min_econ=min(r["F_econ"] for r in rows), min_soc=min(r["F_soc"] for r in rows),
                min_env=min(r["F_env"] for r in rows), eval_diff=round(diff, 6),
                row_check=row_check, core_check=core,
                carbon_price=prob.C.P.get("carbon_price_aud_per_t", ""),
                front_file=os.path.relpath(os.path.join(FRONTS, gname + ".csv"), HERE)), \
        front_rows


# =====================================================================================
#  WORKBOOK
# =====================================================================================
def _read_runs():
    if not os.path.exists(RUNS_CSV):
        return []
    with open(RUNS_CSV, newline="") as f:
        return list(csv.DictReader(f))


def _front_of(path, seed=None):
    """One run's front. Group files hold every seed, so filter on the seed column."""
    full = os.path.join(HERE, path)
    if not os.path.exists(full):
        return None
    with open(full, newline="") as f:
        rows = list(csv.DictReader(f))
    if seed is not None and rows and "seed" in rows[0]:
        rows = [r for r in rows if str(r["seed"]) == str(seed)]
    if not rows:
        return None
    return np.array([[float(r["F_econ"]), float(r["F_soc"]), float(r["F_env"])] for r in rows])


def _eps_front(inst, carbon=None):
    """Exact reference front: results\\<Inst>_front3.csv (tbl_epsilon3.py), else the
    2-objective results\\<Inst>_frontier.csv (tbl_epsilon.py) with F_env = kg x carbon price.
    Points efficient for (F_econ + F_soc, kg) are non-dominated on the three pillars too."""
    pts = []
    p = os.path.join(EXACT, f"{inst}_front3.csv")
    if os.path.exists(p):
        with open(p, newline="") as f:
            for r in csv.DictReader(f):
                if r.get("status") in ("OPTIMAL", "FEASIBLE") and r.get("F_econ"):
                    pts.append([float(r["F_econ"]), float(r["F_soc"]), float(r["F_env"])])
    p = os.path.join(EXACT, f"{inst}_frontier.csv")
    if not pts and carbon and os.path.exists(p):
        with open(p, newline="") as f:
            for r in csv.DictReader(f):
                if r.get("status", "OPTIMAL") in ("OPTIMAL", "FEASIBLE") and r.get("F_econ"):
                    pts.append([float(r["F_econ"]), float(r["F_soc"]),
                                float(r["kg_co2e"]) * float(carbon) / 1000.0])
    return np.array(pts) if pts else None


def coverage(A, B, tol=1e-6):
    """Zitzler-Thiele coverage C(A, B): the share of B's points that some point of A is at
    least as good as on every objective (weak dominance). C(eps, run) = 0.3 means 30 % of
    the run's rosters are matched or beaten by an exact epsilon-constraint roster."""
    if A is None or B is None or len(A) == 0 or len(B) == 0:
        return None
    return float(np.mean([(A <= b + tol).all(1).any() for b in B]))


def hypervolumes(runs):
    """HV per run, normalised over everything on record for the same (instance, levers),
    plus coverage against the epsilon-constraint front:
        cov[id(run)] = (C(eps, run), C(run, eps))   -- None where there is no eps front."""
    groups = {}
    for r in runs:
        groups.setdefault((r["instance"], r["levers"]), []).append(r)
    hv, eps_hv, cov = {}, {}, {}
    for (inst, lev), rs in groups.items():
        fronts = {id(r): _front_of(r["front_file"], r.get("seed")) for r in rs}
        try:
            carbon = float(rs[0].get("carbon_price") or 0) or None
        except ValueError:
            carbon = None
        eps = _eps_front(inst, carbon) if lev == "1" else None
        pool = [f for f in fronts.values() if f is not None and len(f)]
        if eps is not None:
            pool.append(eps)
        if not pool:
            continue
        U = np.concatenate(pool)
        lo, hi = U.min(0), U.max(0)          # box around EVERY point on record, so no run
                                             # falls outside the reference point
        span = np.where(hi - lo > 1e-12, hi - lo, 1.0)
        ref = np.full(3, 1.1)
        for r in rs:
            f = fronts[id(r)]
            hv[id(r)] = hv3((f - lo) / span, ref) if f is not None and len(f) else None
            cov[id(r)] = (coverage(eps, f), coverage(f, eps))
        if eps is not None:
            eps_hv[inst] = hv3((eps - lo) / span, ref)
    return hv, eps_hv, cov


def _med(v):
    v = [x for x in v if x is not None]
    return float(np.median(v)) if v else None


def build_workbook(say=print):
    runs = _read_runs()
    if not runs:
        say("  no runs on record yet -- nothing to put in the workbook")
        return None
    try:
        from openpyxl import Workbook
        from openpyxl.styles import Font, PatternFill, Alignment
        from openpyxl.utils import get_column_letter
    except ImportError:
        say("  openpyxl not installed -- pip install openpyxl ; the CSV is still complete")
        return None
    hv, eps_hv, cov = hypervolumes(runs)

    def inum(s):
        d = "".join(c for c in s if c.isdigit())
        return int(d) if d else 0
    runs.sort(key=lambda r: (inum(r["instance"]), r["levers"], r["algorithm"], int(r["seed"])))

    wb = Workbook()
    bold, hfill = Font(bold=True), PatternFill("solid", fgColor="DDE4EE")
    title_font = Font(bold=True, size=14)

    def header(ws, title, note, cols, widths):
        ws["A1"] = title
        ws["A1"].font = title_font
        ws["A2"] = note
        ws["A2"].alignment = Alignment(wrap_text=False)
        for j, c in enumerate(cols, 1):
            cell = ws.cell(row=4, column=j, value=c)
            cell.font, cell.fill = bold, hfill
            ws.column_dimensions[get_column_letter(j)].width = widths[j - 1]
        ws.freeze_panes = "A5"

    # ---------------------------------------------------------------- All runs
    ws = wb.active
    ws.title = "All runs"
    cols = ["Algorithm", "Instance", "Staff", "Days", "Levers", "Seed", "Pop", "Gens", "Evals",
            "Run (s)", "Front size", "HV (norm.)", "Best total (A$)", "F_econ @best",
            "F_soc @best", "F_env @best", "Check", "kg @best", "Min F_econ", "Min F_soc",
            "Min F_env", "Eval diff", "Row check", "Core check", "Carbon A$/t", "Front file",
            "Settings", "Covered by e", "Covers e"]
    widths = [10, 11, 6, 6, 7, 6, 6, 7, 9, 9, 10, 11, 15, 14, 12, 12, 9, 10, 14, 12, 11, 10,
              17, 12, 11, 40, 46, 13, 10]
    header(ws, "TBL-HWSP -- every NSGA run on record",
           "Built from results\\nsga_runs.csv and results\\fronts\\. HV is normalised per "
           "instance over every front on record (see Read me). 'Best total' is the "
           "lowest F_econ + F_soc + F_env on the front -- the number to set beside the "
           "Gurobi weighted-sum total.", cols, widths)
    rr = 5
    for r in runs:
        h = hv.get(id(r))
        vals = [NICE.get(r["algorithm"], r["algorithm"]), r["instance"], int(r["staff"]),
                int(r["days"]), "yes" if r["levers"] == "1" else "no", int(r["seed"]),
                int(r["pop"]), int(r["generations"]), int(r["evals"]), float(r["run_s"]),
                int(r["front_size"]), None if h is None else round(h, 6),
                float(r["best_total"]), float(r["best_econ"]), float(r["best_soc"]),
                float(r["best_env"]), None, float(r["best_kg"]), float(r["min_econ"]),
                float(r["min_soc"]), float(r["min_env"]), float(r["eval_diff"]),
                r["row_check"], r["core_check"],
                float(r["carbon_price"]) if r["carbon_price"] not in ("", None) else None,
                r["front_file"],
                " ".join(x for x in (
                    f"evals {r['evals_budget']}" if r.get("evals_budget") not in ("", "0") else "",
                    f"time {r['time_limit']}s" if r.get("time_limit") not in ("", "0", "0.0") else "",
                    f"pc {r['pc']}" if r.get("pc") else "", f"mut {r['mut']}" if r.get("mut") else "",
                    f"pool {r['pool']}" if r.get("pool") else "",
                    f"partitions {r['partitions']}" if r.get("partitions") else "") if x),
                (cov.get(id(r)) or (None, None))[0], (cov.get(id(r)) or (None, None))[1]]
        for j, v in enumerate(vals, 1):
            ws.cell(row=rr, column=j, value=v)
        ws.cell(row=rr, column=17,
                value=f'=IF(ABS(N{rr}+O{rr}+P{rr}-M{rr})<0.02,"ok","MISMATCH")')
        for j, fmt in ((28, "0%"), (29, "0%"), (10, "0.0"), (12, "0.0000"), (13, "#,##0.00"), (14, "#,##0.00"),
                       (15, "#,##0.00"), (16, "#,##0.0000"), (18, "#,##0.00"),
                       (19, "#,##0.00"), (20, "#,##0.00"), (21, "#,##0.0000")):
            ws.cell(row=rr, column=j).number_format = fmt
        rr += 1

    # ---------------------------------------------------------------- Summary
    ws2 = wb.create_sheet("Summary")
    cols2 = ["Instance", "Levers", "Algorithm", "Runs", "HV median", "HV Q1", "HV Q3",
             "HV best", "e-constraint HV", "p (Mann-Whitney)", "Best total (A$)",
             "Median best total", "Median run (s)", "Median front size",
             "Covered by e (median)", "Covers e (median)"]
    header(ws2, "NSGA-II vs NSGA-III, per instance",
           "Median and inter-quartile range over seeds. p compares the HV samples of the two "
           "algorithms on the same instance (two-sided Mann-Whitney U); p < 0.05 means the "
           "difference is unlikely to be seed noise. e-constraint HV is the AUGMECON grid "
           "front from tbl_epsilon3.py, where available.", cols2,
           [11, 7, 10, 6, 10, 9, 9, 9, 14, 15, 15, 17, 14, 16, 19, 16])
    try:
        from scipy.stats import mannwhitneyu
    except ImportError:
        mannwhitneyu = None
    groups = {}
    for r in runs:
        groups.setdefault((r["instance"], r["levers"]), {}).setdefault(r["algorithm"], []).append(r)
    rr = 5
    for (inst, lev) in sorted(groups, key=lambda k: (inum(k[0]), k[1])):
        g = groups[(inst, lev)]
        samples = {al: [hv[id(r)] for r in rs if hv.get(id(r)) is not None]
                   for al, rs in g.items()}
        p = None
        if mannwhitneyu and all(len(samples.get(al, [])) >= 2 for al in ALGOS):
            try:
                p = float(mannwhitneyu(samples["nsga2"], samples["nsga3"],
                                       alternative="two-sided").pvalue)
            except ValueError:
                p = None
        for al in [x for x in ALGOS if x in g]:
            rs, hs = g[al], samples.get(al, [])
            tots = [float(r["best_total"]) for r in rs]
            vals = [inst, "yes" if lev == "1" else "no", NICE[al], len(rs),
                    float(np.median(hs)) if hs else None,
                    float(np.percentile(hs, 25)) if hs else None,
                    float(np.percentile(hs, 75)) if hs else None,
                    float(max(hs)) if hs else None,
                    eps_hv.get(inst) if lev == "1" else None,
                    p if al == "nsga3" else None, min(tots), float(np.median(tots)),
                    float(np.median([float(r["run_s"]) for r in rs])),
                    float(np.median([int(r["front_size"]) for r in rs])),
                    _med([cov[id(r)][0] for r in rs if cov.get(id(r))]),
                    _med([cov[id(r)][1] for r in rs if cov.get(id(r))])]
            for j, v in enumerate(vals, 1):
                c = ws2.cell(row=rr, column=j, value=v)
                if j in (5, 6, 7, 8, 9):
                    c.number_format = "0.0000"
                elif j == 10:
                    c.number_format = "0.0000"
                elif j in (11, 12):
                    c.number_format = "#,##0.00"
                elif j == 13:
                    c.number_format = "0.0"
                elif j in (15, 16):
                    c.number_format = "0%"
            rr += 1
        rr += 1

    # ---------------------------------------------------------------- Read me
    ws3 = wb.create_sheet("Read me")
    ws3["A1"] = "How to read this workbook"
    ws3["A1"].font = title_font
    notes = [
        ("Generated", "By tbl_nsga.py after every instance, or any time with "
                      "python tbl_nsga.py --report. Source of truth: results\\nsga_runs.csv."),
        ("Objectives", "Minimise F_econ, F_soc, F_env separately (A$), the same pillars as "
                       "tbl_gurobi.py --objective dollars. Agency and overtime are set to the "
                       "values Gurobi would choose for each roster (see tbl_nsga.py docstring)."),
        ("HV (norm.)", "Hypervolume after scaling each objective to [0,1] between the best "
                       "and worst value in any front on record for that instance (and the "
                       "e-constraint front, if any); reference point 1.1. Larger is better. "
                       "Comparable within an instance, in this build only."),
        ("Best total", "The lowest F_econ + F_soc + F_env among the front's rosters. Set it "
                       "beside the Gurobi weighted-sum total for the same instance: the gap "
                       "is how far the metaheuristic is from the exact single-objective "
                       "optimum."),
        ("Check", "F_econ + F_soc + F_env at the best roster equals Best total. MISMATCH "
                  "means a data problem, not rounding."),
        ("Eval diff", "Largest difference between the fast evaluator that steered the "
                      "search and tbl_data.evaluate, which scored everything reported. "
                      "Should be ~0."),
        ("Row check", "Front rows re-checked against every hard constraint by an independent "
                      "CP-SAT model. Anything below n/n means an infeasible roster -- "
                      "discard the run."),
        ("Core check", "Best roster re-checked in tbl_ortools.build_core (only with "
                       "--verify-core)."),
        ("Run (s)", "GA time only. The row pool (one-off, cached per instance) is excluded; "
                    "its time is in nsga_runs.csv as pool_s."),
        ("Settings", "Evals, time limit, pc, mut and pool for every seed are in "
                     "nsga_runs.csv and at the top of each <Inst>_<tag>_<algo>.log."),
        ("Covered by e", "Coverage C(e, run): the share of the run's rosters that an exact "
                         "e-constraint roster matches or beats on all three pillars "
                         "(Zitzler & Thiele 1999). 0% = no NSGA roster is beaten by the exact "
                         "front; 100% = every one is. Lower is better for NSGA."),
        ("Covers e", "Coverage C(run, e): the share of e-constraint rosters the run matches or "
                     "beats. Above 0% means NSGA found rosters at least as good as exact grid "
                     "points. Higher is better for NSGA. Read both together with HV."),
        ("Replacing", "Re-running an instance + algorithm replaces all of its earlier "
                      "seeds. Use --out NAME for a run you want kept separately."),
    ]
    for j, (a_, b_) in enumerate(notes, 3):
        ws3.cell(row=j, column=1, value=a_).font = bold
        ws3.cell(row=j, column=2, value=b_)
    ws3.column_dimensions["A"].width = 14
    ws3.column_dimensions["B"].width = 120

    out = WORKBOOK
    try:
        wb.save(out)
    except PermissionError:
        out = WORKBOOK.replace(".xlsx", f"_{time.strftime('%H%M%S')}.xlsx")
        wb.save(out)
        say(f"  [!] {WORKBOOK} is open in Excel -- saved to {out} instead")
    say(f"  workbook  : {out}")
    return out


# =====================================================================================
#  DRIVER
# =====================================================================================
def inst_no(t):
    d = "".join(c for c in str(t) if c.isdigit())
    return int(d) if d else 0


def load(name, a):
    inst = D.load_instance(name)
    T, P = D.load_tbl(inst)
    if a.carbon is not None:
        P["carbon_price_aud_per_t"] = a.carbon
    if hasattr(D, "apply_price_overrides"):
        D.apply_price_overrides(P, a.price)
    C = D.Coef(inst, T, P)
    if hasattr(C, "check_price_ordering"):
        C.check_price_ordering()
    return inst, C


def consistency_check(prob, pools, say):
    """Fast evaluator vs tbl_data.evaluate on a few random rosters, before any run."""
    def worst_diff():
        rng = np.random.default_rng(0)
        w = np.zeros(3)
        for _ in range(4):
            X = np.array([pl[rng.integers(len(pl))] for pl in pools], np.int8)
            f = prob.objectives(X)
            _, _, _, e = prob.score(X)
            w = np.maximum(w, np.abs(f - [e["F_econ"], e["F_soc"], e["F_env"]]))
        return w
    worst = worst_diff()
    if worst[0] >= 0.05 and prob.senior.any() and prob.rho_sen:
        prob.sen_on = not prob.sen_on              # try the other skill-mix convention
        w2 = worst_diff()
        if w2[0] < 0.05:
            say(f"  evaluator : tbl_data.evaluate {'charges' if prob.sen_on else 'does not charge'}"
                f" the senior shortfall {'even without' if prob.sen_on else 'even with'} "
                f"--levers; matched it")
            worst = w2
        else:
            prob.sen_on = not prob.sen_on
    ok = (worst < 0.05).all()
    say(f"  evaluator : fast vs tbl_data.evaluate, max |diff| econ {worst[0]:.4f}  "
        f"soc {worst[1]:.4f}  env {worst[2]:.6f}  [{'ok' if ok else 'MISMATCH'}]")
    if not ok:
        bad = [nm for nm, w in zip(("F_econ", "F_soc", "F_env"), worst) if w >= 0.05]
        say(f"  [!] {', '.join(bad)} computed differently from tbl_data.evaluate. Reported "
            f"numbers still come from evaluate(), but the search is steering by different "
            f"numbers. Send Claude tbl_data.py so the fast version can be matched exactly.")
    return ok


def _worker(name, algo, seed, a, log):
    """One run in a separate process: quiet console, its output to a .log.part file that
    Group.finish() folds into the instance's single log."""
    a._in_worker = True
    set_out(a.out)                      # a fresh process starts from the module default
    inst, C = load(name, a)
    prob = Problem(inst, C, a.levers)
    with open(log, "w", encoding="utf-8") as fh, contextlib.redirect_stdout(fh):
        say = lambda *t: print(*t, flush=True)
        pools, _ = build_pool(prob, a.pool, a.pool_seed, a.workers, a.pool_time, 1, say)
        consistency_check(prob, pools, say)
        say("=" * 60)
        say(f"{NICE[algo]} -- {prob.name}  seed {seed}")
        Xf, Ff, info = run_ga(prob, pools, algo, seed, a.pop, a.evals, a.time, a.partitions,
                              a.pc, a.mut, a.print_every, say)
        row, front_rows = report_run(prob, a, algo, seed, Xf, Ff, info, 0.0, say)
    return row, Xf, Ff, front_rows


def run_instance(name, a):
    tag = "levers" if a.levers else "base"
    inst, C = load(name, a)
    prob = Problem(inst, C, a.levers)
    os.makedirs(RESULTS, exist_ok=True)
    print("=" * 92)
    print(f"  NSGA -- {prob.name}  ({prob.n} staff, {prob.T} days, {prob.S} shift types)  "
          f"levers {'ON' if a.levers else 'off'}")
    print("=" * 92)
    pools, pool_s = build_pool(prob, a.pool, a.pool_seed, a.workers, a.pool_time, a.jobs, print)
    consistency_check(prob, pools, print)

    if a.pilot:
        rng = np.random.default_rng(1)
        var = Variation(prob, pools, rng, a.pc, a.mut)
        X = np.array([pl[rng.integers(len(pl))] for pl in pools], np.int8)
        t0 = time.time()
        for _ in range(30):
            Y = X.copy()
            var.mutate(Y)
            prob.objectives(Y)
        per = (time.time() - t0) / 30
        evals = a.evals or 20000
        one = per * evals * 1.15                    # +15 % for sorting and selection
        n_runs = len(a.algos) * a.seeds
        print(f"\n  TIMING PILOT  {per*1000:.2f} ms per offspring (mutation + evaluation)")
        print(f"  one run of {evals:,} evaluations   ~ {one/60:.1f} min")
        print(f"  {n_runs} runs ({'+'.join(a.algos)} x {a.seeds} seeds)  ~ {n_runs*one/3600:.1f} h "
              f"sequential, ~ {n_runs*one/3600/max(1,a.jobs):.1f} h with --jobs {max(1,a.jobs)}")
        print("  (nothing recorded)")
        return

    seeds = range(a.seed_start, a.seed_start + a.seeds)
    tasks = [(algo, seed) for algo in a.algos for seed in seeds]
    groups = {algo: Group(prob, algo, a) for algo in a.algos}
    for algo in a.algos:
        print(f"  {settings_line(a, algo)}")
    try:
        if a.jobs > 1 and len(tasks) > 1:
            print(f"\n  {len(tasks)} runs, {a.jobs} at a time. Full output of every seed goes "
                  f"to {prob.name}_{tag}_<algo>.log; one line here per finished run.\n")
            with ProcessPoolExecutor(max_workers=a.jobs) as ex:
                futs = {ex.submit(_worker, name, algo, seed, a, groups[algo].part(seed)):
                        (algo, seed) for algo, seed in tasks}
                for f in as_completed(futs):
                    algo, seed = futs[f]
                    try:
                        row, Xf, Ff, front_rows = f.result()
                    except Exception as ex_:
                        print(f"  [!] {NICE[algo]} seed {seed} failed: {ex_}")
                        continue
                    row["pool_s"] = round(pool_s, 1)
                    groups[algo].add(seed, Xf, Ff, front_rows, row)
                    print(f"  {NICE[algo]:<9} seed {seed:<3} {row['run_s']:>8.1f} s  "
                          f"front {row['front_size']:>3}  best total A${row['best_total']:>14,.2f}"
                          f"  rows {row['row_check']}")
        else:
            for algo, seed in tasks:
                with _Tee(groups[algo].part(seed)):
                    print("\n" + "=" * 60)
                    print(f"{NICE[algo]} -- {prob.name}  seed {seed}  pop {a.pop}  "
                          + (f"budget {a.evals:,} evals" if a.evals else "")
                          + (f"  limit {a.time:g} s" if a.time else "")
                          + (f"  ({len(das_dennis(3, a.partitions))} reference points)"
                             if algo == "nsga3" else ""))
                    Xf, Ff, info = run_ga(prob, pools, algo, seed, a.pop, a.evals, a.time,
                                          a.partitions, a.pc, a.mut, a.print_every, print)
                    row, front_rows = report_run(prob, a, algo, seed, Xf, Ff, info, pool_s,
                                                 print)
                groups[algo].add(seed, Xf, Ff, front_rows, row)
    finally:
        for algo, g in groups.items():
            g.finish()
            if g.seeds:
                print(f"  {NICE[algo]}: {len(g.seeds)} seed(s) -> {os.path.relpath(g.csv, HERE)}, "
                      f"{os.path.basename(g.log)}, {os.path.basename(g.best)}")

    # ---------------------------------------------------- instance summary
    runs = [r for r in _read_runs() if r["instance"] == prob.name
            and r["levers"] == str(int(a.levers))]
    hv, eps_hv, cov = hypervolumes(runs)
    print(f"\n  {prob.name} -- everything on record ({tag}):")
    print(f"  {'algorithm':<10}{'runs':>5}{'HV median':>11}{'HV IQR':>17}{'best total':>16}"
          f"{'median run s':>14}" + (f"{'covered by e':>14}{'covers e':>10}"
                                     if prob.name in eps_hv else ""))
    for algo in ALGOS:
        rs = [r for r in runs if r["algorithm"] == algo]
        if not rs:
            continue
        hs = [hv[id(r)] for r in rs if hv.get(id(r)) is not None]
        q1, q3 = (np.percentile(hs, 25), np.percentile(hs, 75)) if hs else (0, 0)
        c1 = [cov[id(r)][0] for r in rs if cov.get(id(r)) and cov[id(r)][0] is not None]
        c2 = [cov[id(r)][1] for r in rs if cov.get(id(r)) and cov[id(r)][1] is not None]
        print(f"  {NICE[algo]:<10}{len(rs):>5}{np.median(hs) if hs else 0:>11.4f}"
              f"{f'{q1:.4f}-{q3:.4f}':>17}{min(float(r['best_total']) for r in rs):>16,.2f}"
              f"{np.median([float(r['run_s']) for r in rs]):>14.1f}"
              + (f"{np.median(c1):>13.0%}{np.median(c2):>10.0%}" if c1 else ""))
    if prob.name in eps_hv:
        print(f"  {'e-constr.':<10}{'':>5}{eps_hv[prob.name]:>11.4f}   "
              f"(AUGMECON grid front, same normalisation)")
        print("  covered by e = share of a run's rosters matched or beaten on all three pillars")
        print("  by an exact e-constraint roster (lower is better for NSGA); covers e = share of")
        print("  the e-constraint rosters the run matches or beats (higher is better for NSGA).")


def main():
    ap = argparse.ArgumentParser(description="NSGA-II / NSGA-III for TBL-HWSP")
    ap.add_argument("--instance", default=None, help="one instance, e.g. Instance1 or 1")
    ap.add_argument("--range", nargs=2, type=int, metavar=("FIRST", "LAST"))
    ap.add_argument("--only", nargs="*", help="e.g. --only 1 5 9")
    ap.add_argument("--algo", choices=["nsga2", "nsga3", "both"], default="both")
    ap.add_argument("--seeds", type=int, default=5, help="independent runs per algorithm")
    ap.add_argument("--seed-start", type=int, default=1)
    ap.add_argument("--levers", action="store_true", help="agency, overtime, skill mix -- "
                    "as in tbl_gurobi.py --levers")
    ap.add_argument("--evals", type=int, default=20000, help="evaluation budget per run "
                    "(0 = no budget, use --time)")
    ap.add_argument("--time", type=float, default=0, help="seconds per run (0 = no limit)")
    ap.add_argument("--pop", type=int, default=92, help="population (92 matches the 91 "
                    "reference points of NSGA-III with 12 partitions)")
    ap.add_argument("--partitions", type=int, default=12, help="NSGA-III Das-Dennis divisions")
    ap.add_argument("--pc", type=float, default=0.9, help="crossover probability")
    ap.add_argument("--mut", type=float, default=2.0, help="mean mutation moves per child")
    ap.add_argument("--pool", type=int, default=8, help="CP-SAT rows per employee")
    ap.add_argument("--pool-seed", type=int, default=12345)
    ap.add_argument("--pool-time", type=float, default=0.5, help="seconds per pool row (rows "
                    "only need to be feasible and varied, not optimal)")
    ap.add_argument("--workers", type=int, default=8, help="CP-SAT threads (pool build)")
    ap.add_argument("--jobs", type=int, default=1, help="runs in parallel (one per core)")
    ap.add_argument("--print-every", type=int, default=0, help="progress line every N "
                    "generations (0 = about 15 lines per run)")
    ap.add_argument("--verify-max", type=int, default=300, help="front rows re-checked by CP-SAT")
    ap.add_argument("--verify-core", action="store_true", help="also check the best roster "
                    "in tbl_ortools.build_core")
    ap.add_argument("--pilot", action="store_true", help="time one instance, record nothing")
    ap.add_argument("--report", action="store_true", help="rebuild the workbook and exit")
    ap.add_argument("--out", default="results_nsga", help="folder for everything this script "
                    "writes (default results_nsga). Use a new name per experiment, e.g. "
                    "--out nsga_trial, to keep its workbook and summary separate.")
    ap.add_argument("--carbon", type=float, default=None)
    ap.add_argument("--price", action="append", default=[], metavar="KEY=VALUE")
    a = ap.parse_args()
    a._in_worker = False
    set_out(a.out)
    a.algos = list(ALGOS) if a.algo == "both" else [a.algo]

    if a.report:
        build_workbook()
        return
    if not a.evals and not a.time:
        raise SystemExit("give --evals, --time, or both -- otherwise a run never stops")
    if a.pop % 2:
        a.pop += 1
    H = len(das_dennis(3, a.partitions))
    if "nsga3" in a.algos and a.pop < H:
        print(f"  [note] pop {a.pop} < {H} reference points: NSGA-III cannot fill every "
              f"niche. Deb & Jain use pop = refs rounded up to a multiple of 4.")
    if a.print_every == 0:
        per_gen = a.pop
        gens = (a.evals // per_gen) if a.evals else 300
        a.print_every = max(1, gens // 15)

    lister = getattr(D, "list_instances", None)
    prepared = lister() if lister else []
    if a.only:
        want = {inst_no(t) for t in a.only}
        names = [n for n in prepared if inst_no(n) in want] or [f"Instance{k}" for k in sorted(want)]
    elif a.range:
        names = [n for n in prepared if a.range[0] <= inst_no(n) <= a.range[1]] or \
                [f"Instance{k}" for k in range(a.range[0], a.range[1] + 1)]
    else:
        key = a.instance or "Instance1"
        key = f"Instance{key}" if str(key).isdigit() else key
        names = [n for n in prepared if n.lower() == key.lower()] or [key]
    names.sort(key=inst_no)

    n_runs = len(names) * len(a.algos) * a.seeds
    print(f"NSGA: {len(names)} instance(s) x {len(a.algos)} algorithm(s) x {a.seeds} seed(s) "
          f"= {n_runs} run(s)   " + (f"{a.evals:,} evals" if a.evals else "")
          + (f"  {a.time:g} s" if a.time else "") + " per run"
          + (f"   {a.jobs} in parallel" if a.jobs > 1 else ""))
    print(f"  {', '.join(names)}")
    print(f"  output -> {OUT}   (results\\ is never written to)\n")

    t0 = time.time()
    for nm in names:
        try:
            run_instance(nm, a)
        except KeyboardInterrupt:
            print("\n[interrupted] finished runs are already in results\\nsga_runs.csv")
            break
        except SystemExit as ex:
            print(f"\n[!] {nm} stopped: {ex}")
        except Exception as ex:
            print(f"\n[!] {nm} failed: {type(ex).__name__}: {str(ex).splitlines()[0][:100]}")
        if not a.pilot:
            build_workbook()
    print(f"\nALL DONE in {(time.time()-t0)/60:.1f} min")


if __name__ == "__main__":
    main()
