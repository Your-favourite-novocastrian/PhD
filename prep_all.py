"""
prep_all.py -- ONE command that prepares every instance you have.

    python prep_all.py                     prepare all instances\\*.txt
    python prep_all.py --only Instance3    just one
    python prep_all.py --force             regenerate sidecars that already exist
    python prep_all.py --seed 7            a different synthetic draw

For each instances\\<Name>.txt it writes data\\<Name>\\ containing
    horizon.csv shifts.csv staff.csv days_off.csv
    shift_on_requests.csv shift_off_requests.csv cover.csv    (the converted benchmark)
    staff_tbl.csv                                             (your synthetic TBL sidecar)

Existing staff_tbl.csv files are NEVER overwritten without --force, so any row you
hand-edit survives re-preparation. Nothing here touches instances\\*.txt.

SIDECAR GENERATION RULES (this is your data provenance -- put it in the thesis):
  grade         uniform over an 8-step RN ladder; top 3 grades are senior;
                at least --senior-share of staff forced senior so skill mix is feasible
  hourly rate   fixed by grade (RN Yr1 36.00 ... CNS 50.00)
  commute km    lognormal(median 12, sigma 0.6), clipped to [1, 60] -- right-skewed,
                because a few long commuters dominate ward emissions
  mode          car 75% / public transport 15% / active 10%
  emission fac  car 0.171, PT 0.035, active 0.0 kg CO2e per km
  seed          per instance = --seed + instance number, so every instance gets a
                different but REPRODUCIBLE draw

REPLACE BEFORE REPORTING: award rate table (rates), staff travel survey or ABS
journey-to-work (distances), DCCEEW National Greenhouse Accounts Factors (factors).
"""
import argparse, csv, math, os, random

HERE = os.path.dirname(os.path.abspath(__file__))
INST = os.path.join(HERE, "instances")
DATA = os.path.join(HERE, "data")

GRADES = [("RN Yr1", 36.00), ("RN Yr2", 37.50), ("RN Yr3", 39.00), ("RN Yr4", 40.50),
          ("RN Yr5", 42.00), ("RN Yr6", 44.00), ("RN Yr8", 47.50), ("CNS", 50.00)]
SENIOR = {"RN Yr6", "RN Yr8", "CNS"}
MODES = [("car", 0.75, 0.171), ("public_transport", 0.15, 0.035), ("active", 0.10, 0.000)]

# ------------------------------------------------------------------ converter
def parse_instance(path):
    sec, d = None, {}
    for line in open(path):
        line = line.strip()
        if not line or line.startswith("#"): continue
        if line.startswith("SECTION_"): sec = line; d[sec] = []
        else: d[sec].append(line)
    return d

def convert(src, outdir):
    d = parse_instance(src)
    os.makedirs(outdir, exist_ok=True)
    def w(name, header, rows):
        with open(os.path.join(outdir, name), "w", newline="") as f:
            c = csv.writer(f); c.writerow(header); c.writerows(rows)

    w("horizon.csv", ["n_days"], [[int(d["SECTION_HORIZON"][0])]])
    w("shifts.csv", ["shift_id", "length_min", "cannot_follow"],
      [[(p + ["", ""])[0], int((p + ["", ""])[1]), (p + ["", ""])[2]]
       for ln in d["SECTION_SHIFTS"] for p in [ln.split(",")]])
    w("staff.csv", ["staff_id", "max_shifts", "max_total_min", "min_total_min",
                    "max_consec_shifts", "min_consec_shifts", "min_consec_days_off", "max_weekends"],
      [ln.split(",")[:8] for ln in d["SECTION_STAFF"]])
    w("days_off.csv", ["staff_id", "day"],
      [[p[0], int(x)] for ln in d.get("SECTION_DAYS_OFF", []) for p in [ln.split(",")] for x in p[1:]])
    for sec, name in [("SECTION_SHIFT_ON_REQUESTS", "shift_on_requests.csv"),
                      ("SECTION_SHIFT_OFF_REQUESTS", "shift_off_requests.csv")]:
        w(name, ["staff_id", "day", "shift_id", "weight"], [ln.split(",") for ln in d.get(sec, [])])
    w("cover.csv", ["day", "shift_id", "requirement", "under_weight", "over_weight"],
      [ln.split(",") for ln in d["SECTION_COVER"]])

# ------------------------------------------------------------------ sidecar
def make_sidecar(outdir, name, seed, senior_share):
    def rd(f):
        with open(os.path.join(outdir, f)) as fh:
            return list(csv.DictReader(l for l in fh if not l.lstrip().startswith("#")))
    staff = [r["staff_id"] for r in rd("staff.csv")]
    shifts = [r["shift_id"] for r in rd("shifts.csv")]
    n_days = int(rd("horizon.csv")[0]["n_days"])
    rng = random.Random(seed)

    rows = []
    for sid in staff:
        grade, rate = rng.choice(GRADES)
        mode, _, ef = rng.choices(MODES, weights=[m[1] for m in MODES])[0]
        km = min(60.0, max(1.0, rng.lognormvariate(math.log(12.0), 0.6)))
        rows.append(dict(staff_id=sid, grade=grade, senior=int(grade in SENIOR),
                         hourly_rate_aud=f"{rate:.2f}", commute_km_one_way=f"{km:.1f}",
                         commute_mode=mode, ef_kg_per_km=f"{ef:.3f}"))
    need = max(2, math.ceil(senior_share * len(staff)))
    have = sum(r["senior"] for r in rows)
    for r in rows:
        if have >= need: break
        if not r["senior"]:
            r.update(grade="RN Yr8", senior=1, hourly_rate_aud="47.50"); have += 1

    path = os.path.join(outdir, "staff_tbl.csv")
    with open(path, "w", newline="") as f:
        f.write(f"# staff_tbl.csv for {name} -- SYNTHETIC, generated by prep_all.py\n")
        f.write(f"# seed={seed}  staff={len(staff)}  days={n_days}  shifts={','.join(shifts)}\n")
        f.write("# Rules: grade uniform over 8-step RN ladder (top 3 senior); km lognormal(median 12,\n")
        f.write("# sigma 0.6) clipped [1,60]; mode car .75/PT .15/active .10; ef 0.171/0.035/0.0 kg per km.\n")
        f.write("# ALL VALUES ASSUMED. Replace with award rates, travel survey, DCCEEW NGA Factors.\n")
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys())); w.writeheader(); w.writerows(rows)

    kg = [2*float(r["commute_km_one_way"])*float(r["ef_kg_per_km"]) for r in rows]
    night = [s for s in shifts if s.upper().startswith("N")]
    return dict(staff=len(staff), days=n_days, shifts=shifts, night=bool(night),
                seniors=have, kg_min=min(kg), kg_mean=sum(kg)/len(kg), kg_max=max(kg))

# ------------------------------------------------------------------ main
ap = argparse.ArgumentParser()
ap.add_argument("--only", default=None, help="prepare just this instance, e.g. Instance3")
ap.add_argument("--range", nargs=2, type=int, metavar=("FIRST", "LAST"), default=None,
                help="prepare instances FIRST..LAST by number, e.g. --range 1 5")
ap.add_argument("--force", action="store_true", help="overwrite existing staff_tbl.csv")
ap.add_argument("--seed", type=int, default=2026)
ap.add_argument("--senior-share", type=float, default=0.30)
a = ap.parse_args()

if not os.path.isdir(INST):
    raise SystemExit(f"no instances folder at {INST}\n"
                     f"   make it and put Instance1.txt ... Instance24.txt inside")

files = sorted([f for f in os.listdir(INST) if f.lower().endswith(".txt")],
               key=lambda f: (int("".join(c for c in f if c.isdigit()) or 0), f))
if a.only:
    files = [f for f in files if os.path.splitext(f)[0] == a.only]
    if not files: raise SystemExit(f"{a.only}.txt not found in {INST}")
elif a.range:
    lo, hi = a.range
    files = [f for f in files if lo <= int("".join(c for c in f if c.isdigit()) or 0) <= hi]
    if not files: raise SystemExit(f"no instance .txt numbered {lo}..{hi} in {INST}")

print(f"{'instance':<14}{'staff':>6}{'days':>6}  {'shifts':<10}{'night':>6}{'seniors':>8}"
      f"{'kg/day min':>11}{'mean':>7}{'max':>7}   sidecar")
print("-" * 92)
for f in files:
    name = os.path.splitext(f)[0]
    outdir = os.path.join(DATA, name)
    convert(os.path.join(INST, f), outdir)
    num = int("".join(c for c in name if c.isdigit()) or 0)
    sp = os.path.join(outdir, "staff_tbl.csv")
    if os.path.exists(sp) and not a.force:
        import csv as _c
        with open(sp) as fh:
            rows = list(_c.DictReader(l for l in fh if not l.lstrip().startswith("#")))
        kg = [2*float(r["commute_km_one_way"])*float(r["ef_kg_per_km"]) for r in rows]
        with open(os.path.join(outdir, "shifts.csv")) as fh:
            sh = [r["shift_id"] for r in _c.DictReader(fh)]
        with open(os.path.join(outdir, "horizon.csv")) as fh:
            nd = int(list(_c.DictReader(fh))[0]["n_days"])
        info = dict(staff=len(rows), days=nd, shifts=sh,
                    night=any(s.upper().startswith("N") for s in sh),
                    seniors=sum(int(r["senior"]) for r in rows),
                    kg_min=min(kg), kg_mean=sum(kg)/len(kg), kg_max=max(kg))
        note = "kept (use --force to regenerate)"
    else:
        info = make_sidecar(outdir, name, a.seed + num, a.senior_share)
        note = "generated"
    print(f"{name:<14}{info['staff']:>6}{info['days']:>6}  {'/'.join(info['shifts']):<10}"
          f"{('yes' if info['night'] else '-'):>6}{info['seniors']:>8}"
          f"{info['kg_min']:>11.2f}{info['kg_mean']:>7.2f}{info['kg_max']:>7.2f}   {note}")

print(f"\nprepared {len(files)} instance(s) into {DATA}")
print("next:  python tbl_ortools.py --instance Instance1")
print("  or:  python run_all.py --objective dollars --levers --gap 0.01 --time 300")
