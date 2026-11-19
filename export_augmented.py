"""
export_augmented.py -- write the AUGMENTED instance files: one self-contained .txt per
instance, in the benchmark's own section format, carrying the original instance verbatim
PLUS the synthetic TBL data that the objective function needs.

    python export_augmented.py                 every prepared instance
    python export_augmented.py --only Instance1
    python export_augmented.py --verify        re-check that originals are byte-identical

Writes augmented\\<Instance>_TBL.txt

WHY THIS EXISTS, AND WHAT IT IS NOT
-----------------------------------
The solver does NOT read these files. It reads instances\\*.txt plus the sidecar
data\\<Instance>\\staff_tbl.csv plus prices.csv. That separation is deliberate: it is what
lets check_setup.py keep proving Instance1 = 607 against the published benchmark, because
the base instance is provably untouched.

These augmented files are the DOCUMENTATION artefact for "modify the relevant instances
with synthetic data" -- one file per instance that a supervisor, examiner or reader can
open and see the whole modified instance in one place, with every added value marked as
synthetic and every price marked SOURCED / DERIVED / ASSUMED.

The original sections are copied through BYTE FOR BYTE. --verify re-reads each output and
asserts that the original instance text appears in it unchanged, so the claim "we did not
modify the benchmark" is checkable rather than asserted.
"""
import argparse, csv, os, sys
import tbl_data as D

HERE = D.HERE
OUT = os.path.join(HERE, "augmented")

ap = argparse.ArgumentParser()
ap.add_argument("--only", nargs="*", default=None)
ap.add_argument("--verify", action="store_true",
                help="check existing outputs still contain the original instance verbatim")
a = ap.parse_args()

def read_rows(path):
    with open(path) as f:
        return list(csv.DictReader(l for l in f if not l.lstrip().startswith("#")))

def original_text(name):
    p = os.path.join(HERE, "instances", f"{name}.txt")
    if not os.path.exists(p): return None
    with open(p, encoding="utf-8", errors="replace") as f:
        return f.read()

names = D.list_instances()
if a.only:
    missing = [n for n in a.only if n not in names]
    if missing: raise SystemExit(f"not prepared: {missing}\n   run: python prep_all.py")
    names = [n for n in names if n in a.only]
if not names:
    raise SystemExit("no prepared instances -- run:  python prep_all.py")

# ------------------------------------------------------------------ verify mode
if a.verify:
    bad = 0
    for n in names:
        src, dst = original_text(n), os.path.join(OUT, f"{n}_TBL.txt")
        if src is None: print(f"  [skip] {n}: no original .txt to compare against"); continue
        if not os.path.exists(dst): print(f"  [FAIL] {n}: {n}_TBL.txt not written yet"); bad += 1; continue
        with open(dst, encoding="utf-8", errors="replace") as f: aug = f.read()
        if src.strip() in aug:
            print(f"  [ok]   {n}: original instance present byte-for-byte")
        else:
            print(f"  [FAIL] {n}: original text is NOT intact in the augmented file"); bad += 1
    print(f"\n{'all originals intact' if not bad else f'{bad} problem(s)'}")
    sys.exit(0 if not bad else 1)

# ------------------------------------------------------------------ write mode
os.makedirs(OUT, exist_ok=True)
prices = read_rows(os.path.join(HERE, "prices.csv"))

print(f"writing {len(names)} augmented instance file(s) to augmented\\")
for n in names:
    src = original_text(n)
    if src is None:
        print(f"  [skip] {n}: instances\\{n}.txt not found"); continue
    tbl = read_rows(os.path.join(HERE, "data", n, "staff_tbl.csv"))
    dst = os.path.join(OUT, f"{n}_TBL.txt")

    L = []
    L.append(f"# {n}_TBL.txt -- AUGMENTED INSTANCE for the Triple Bottom Line HWSP")
    L.append("#")
    L.append("# Everything from SECTION_HORIZON to SECTION_COVER below is the published")
    L.append(f"# benchmark instance {n} (Curtois & Qu), reproduced BYTE FOR BYTE and not")
    L.append("# modified in any way. The benchmark optimum is therefore still the benchmark")
    L.append("# optimum, and the model is validated against it independently of this file.")
    L.append("#")
    L.append("# The two sections AFTER SECTION_COVER are the additions this thesis makes, so")
    L.append("# that the economic / social / environmental objectives can be expressed in")
    L.append("# A$. SECTION_STAFF_TBL is per-nurse and SYNTHETIC. SECTION_TBL_PRICES is")
    L.append("# global, with each coefficient marked SOURCED, DERIVED or ASSUMED.")
    L.append("#")
    L.append("# This file is documentation, not solver input: the pipeline reads")
    L.append(f"# instances\\{n}.txt + data\\{n}\\staff_tbl.csv + prices.csv.")
    L.append("")
    L.append(src.strip())
    L.append("")
    L.append("")
    L.append("# ==================================================================")
    L.append("# ===================  TBL ADDITIONS START HERE  ===================")
    L.append("# ==================================================================")
    L.append("")
    L.append("SECTION_STAFF_TBL")
    L.append("# SYNTHETIC per-nurse data. Joined to SECTION_STAFF on ID. Generated by")
    L.append("# prep_all.py from a fixed per-instance seed, so it is reproducible.")
    L.append("# Grade ladder: 8-step RN scale, top 3 steps flagged senior.")
    L.append("# Commute km: lognormal(median 12, sigma 0.6) clipped to [1,60].")
    L.append("# Mode: car 0.75 / public transport 0.15 / active 0.10.")
    L.append("# Emission factor kg CO2e per km: car 0.171, PT 0.035, active 0.000.")
    L.append("# ALL VALUES ASSUMED -- replace with the NSW Nurses' & Midwives' Award,")
    L.append("# a staff travel survey, and DCCEEW National Greenhouse Accounts Factors.")
    L.append("#")
    L.append("# ID, Grade, Senior, HourlyRateAUD, CommuteKmOneWay, CommuteMode, EmissionFactorKgPerKm")
    for r in tbl:
        L.append(f"{r['staff_id']},{r['grade']},{r['senior']},{r['hourly_rate_aud']},"
                 f"{r['commute_km_one_way']},{r['commute_mode']},{r['ef_kg_per_km']}")
    L.append("")
    L.append("SECTION_TBL_PRICES")
    L.append("# Global conversion factors -- the 'common language of cost'. Identical across")
    L.append("# all instances. Provenance is stated per row so a reader can see exactly which")
    L.append("# numbers are defensible and which still need a source.")
    L.append("#")
    L.append("# Key, Value, Unit, Provenance, SourceOrDerivationChain")
    for r in prices:
        chain = (r.get("source_or_chain", "") or "").replace("\n", " ")
        L.append(f"{r['key']},{r['value']},{r['unit']},{r['status']},{chain}")
    L.append("")
    L.append("# ---------------- how these feed the objective ----------------")
    L.append("# F_econ  wages  c_i,s = HourlyRateAUD_i x (shift length min / 60)")
    L.append("#         + overtime at overtime_multiplier x rate")
    L.append("#         + agency_cost_per_shift x agency shifts used")
    L.append("#         + rho_understaffing x uncovered shift-units")
    L.append("#         + rho_senior_shortfall x senior-shifts short of min_senior_share")
    L.append("# F_soc   k_pref x request weight, k_pref = p_leave_per_pref_weight x turnover_cost_aud")
    L.append("#         + k_fair x |shifts_i - mean|, k_fair = p_leave_per_dev_shift x turnover_cost_aud")
    L.append("#         + kappa_fatigue x three-consecutive-night blocks")
    L.append("#         + kappa_churn  x changes against a published roster")
    L.append("# F_env   (carbon_price_aud_per_t / 1000) x total kg CO2e, where kg per")
    L.append("#         attendance day = 2 x CommuteKmOneWay_i x EmissionFactorKgPerKm_i,")
    L.append("#         plus agency_travel_kg per agency shift and")
    L.append("#         e_anc_kg_per_over_shift per overstaffed shift-unit.")
    L.append("")

    text = "\n".join(L)
    assert src.strip() in text, f"internal error: original text lost for {n}"
    def _w(p):
        with open(p, "w", encoding="utf-8", newline="\n") as f: f.write(text)
    written = D.safe_write(dst, _w, "augmented instance") or dst
    print(f"  {n:<12} {len(tbl):>4} nurses, {len(prices):>3} prices  ->  "
          f"{os.path.basename(written)}  ({len(text):,} bytes)")

print(f"\ndone. Verify the originals are intact with:  python export_augmented.py --verify")
