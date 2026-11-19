"""
make_provenance.py -- document EXACTLY what came from the benchmark and what is yours.

    python make_provenance.py
    python make_provenance.py --out data_provenance.xlsx

Writes TBL_HWSP_data_provenance.xlsx -- the data-availability artefact. Its whole purpose
is that a future researcher citing this work can tell, feature by feature, which values are
the published Curtois & Qu benchmark and which were added by this thesis. Without that
separation nobody can reproduce or challenge the TBL layer, and the benchmark's own
validity claim (Instance1 = 607) cannot be checked.

SHEETS
  1 Read me            how to cite, and the one-line summary of what is original vs added
  2 Original features  every field the benchmark .txt defines, by section
  3 Added - per nurse  the sidecar schema, and per-instance summary statistics
  4 Added - prices     the global coefficient vector, with provenance per row
  5 Derived            quantities computed from the two layers, with their formulas
  6 Model extensions   decision variables and constraints this thesis adds
  7 Instance table     one row per instance: original characteristics + added-data summary

Sheets 2, 5 and 6 are a specification and are hardcoded here deliberately -- they describe
the FORMAT, not the data. Sheets 3, 4 and 7 are generated from prices.csv and data\\, so
they cannot drift from what the solver actually reads.
"""
import argparse, csv, glob, os, statistics
from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
from openpyxl.utils import get_column_letter
import tbl_data as D

F = "Arial"
NAVY = PatternFill("solid", fgColor="1F3864")
HF = Font(name=F, bold=True, color="FFFFFF", size=10)
ORIG = PatternFill("solid", fgColor="DDEBF7")     # blue  = from the benchmark
MINE = PatternFill("solid", fgColor="FFF2CC")     # amber = added by this thesis
DERV = PatternFill("solid", fgColor="E2EFDA")     # green = derived from the two
SEC = PatternFill("solid", fgColor="D9E1F2")
GREY = PatternFill("solid", fgColor="F2F2F2")
RED = PatternFill("solid", fgColor="FCE4D6")
BLK = Font(name=F, size=10)
BOLD = Font(name=F, bold=True, size=10)
IT = Font(name=F, italic=True, size=9, color="595959")
MONO = Font(name="Consolas", size=9)
TITLE = Font(name=F, bold=True, size=14, color="1F3864")
thin = Side(style="thin", color="BFBFBF")
BOX = Border(left=thin, right=thin, top=thin, bottom=thin)
WRAP = Alignment(wrap_text=True, vertical="top")


def inst_no(s):
    d = "".join(c for c in str(s) if c.isdigit())
    return int(d) if d else 0


def head(ws, row, hdrs, heights=32):
    for j, h in enumerate(hdrs, 1):
        c = ws.cell(row=row, column=j, value=h)
        c.fill = NAVY; c.font = HF; c.alignment = WRAP; c.border = BOX
    ws.row_dimensions[row].height = heights
    ws.freeze_panes = ws.cell(row=row + 1, column=1)


def put(ws, r, vals, fill=None, font=BLK):
    for j, v in enumerate(vals, 1):
        c = ws.cell(row=r, column=j, value=v)
        c.font = font; c.border = BOX; c.alignment = WRAP
        if fill: c.fill = fill
    return r + 1


def widths(ws, w):
    for j, x in enumerate(w, 1):
        ws.column_dimensions[get_column_letter(j)].width = x


def rd(path):
    with open(path) as f:
        return list(csv.DictReader(l for l in f if not l.lstrip().startswith("#")))


ap = argparse.ArgumentParser()
ap.add_argument("--out", default="TBL_HWSP_data_provenance.xlsx")
args = ap.parse_args()

wb = Workbook()

# ===================================================================== 1. Read me
ws = wb.active; ws.title = "Read me"
ws["A1"] = "TBL-HWSP — data provenance"; ws["A1"].font = TITLE
r = 3
ws.cell(row=r, column=1, value=(
    "This workbook separates, feature by feature, the published benchmark data from the "
    "data added by this thesis. Colour coding is used throughout:")).font = BLK
ws.cell(row=r, column=1).alignment = WRAP
ws.merge_cells(start_row=r, start_column=1, end_row=r + 1, end_column=6)
r += 3
for fill, label, txt in (
    (ORIG, "ORIGINAL", "Defined by the published benchmark instance file. NOT modified by "
                       "this thesis. The instance .txt files are reproduced byte for byte."),
    (MINE, "ADDED", "Introduced by this thesis. Synthetic unless a source is given."),
    (DERV, "DERIVED", "Computed at run time from the two layers above. Stored nowhere."),
):
    c = ws.cell(row=r, column=1, value=label); c.fill = fill; c.font = BOLD; c.border = BOX
    c2 = ws.cell(row=r, column=2, value=txt); c2.font = BLK; c2.alignment = WRAP
    ws.merge_cells(start_row=r, start_column=2, end_row=r, end_column=8)
    ws.row_dimensions[r].height = 30
    r += 1
c = ws.cell(row=r, column=2, value=(
    "One exception: on the 'Added - prices' sheet every row is ADDED, so the shading there "
    "means evidence status (SOURCED / DERIVED / ASSUMED) rather than layer. That sheet says "
    "so at the top."))
c.font = IT; c.alignment = WRAP
ws.merge_cells(start_row=r, start_column=2, end_row=r, end_column=8)
ws.row_dimensions[r].height = 28
r += 1

r += 1
for k, v in (
    ("Benchmark source",
     "Curtois, T. and Qu, R. — nurse rostering benchmark instances, "
     "schedulingbenchmarks.org/nrp. Described in Burke, E.K. and Curtois, T. (2014), "
     "'New approaches to nurse rostering benchmark instances', European Journal of "
     "Operational Research 237(1), 71-81, doi:10.1016/j.ejor.2014.01.039."),
    ("Instances used", "Instance1 .. Instance24 of that collection (8-150 employees, "
                       "14-364 days, 1-32 shift types)."),
    ("What is NOT modified",
     "Every field of every instance .txt. The files are read and never written. This is "
     "what allows the base model to reproduce the published optimum for Instance1 "
     "(penalty = 607) as a validity check on the TBL layer."),
    ("What IS added",
     "(a) a per-nurse sidecar table joined on staff ID, supplying pay, commute and "
     "seniority attributes; (b) a global price vector of 15 coefficients converting "
     "physical and social quantities into A$; (c) four new decision variables and one "
     "new soft constraint. See sheets 3, 4 and 6."),
    ("Reproducibility of the added data",
     "The per-nurse sidecar is generated by prep_all.py from a fixed per-instance seed "
     "(2026 + instance number), so it regenerates identically. check_setup.py prints a "
     "fingerprint of the generated data for cross-machine verification. NOTE: the same "
     "seed can yield a different draw on a different CPython version, so the fingerprint "
     "must be compared, not assumed."),
    ("How to cite the added data",
     "Cite this thesis for the TBL layer and the benchmark paper above for the instances. "
     "The two must be cited separately: the instances are not this thesis's contribution "
     "and the TBL attributes are not the benchmark's."),
    ("Status of the added values",
     "13 of the 15 price coefficients are ASSUMED rather than sourced (sheet 4). Every "
     "per-nurse attribute is synthetic. Results are therefore a demonstration of method, "
     "not an estimate of any real service's costs."),
):
    c = ws.cell(row=r, column=1, value=k); c.font = BOLD; c.fill = GREY; c.border = BOX
    c.alignment = WRAP
    c2 = ws.cell(row=r, column=2, value=v); c2.font = BLK; c2.alignment = WRAP; c2.border = BOX
    ws.merge_cells(start_row=r, start_column=2, end_row=r, end_column=8)
    ws.row_dimensions[r].height = 62
    r += 1
widths(ws, [22, 30, 18, 18, 18, 18, 18, 18])

# ============================================================ 2. Original features
ws = wb.create_sheet("Original features")
ws["A1"] = "ORIGINAL — every field defined by the benchmark instance file"; ws["A1"].font = TITLE
ws["A2"] = ("Reproduced from the instance .txt format. None of this is modified. Column "
            "'role in the TBL model' says whether the field drives a hard constraint, the "
            "benchmark objective, or the monetised objective.")
ws["A2"].font = IT; ws.merge_cells("A2:F2")
head(ws, 4, ["Section in the .txt", "Field", "Symbol", "Type / units",
             "Meaning", "Role in the TBL model"])
r = 5
ORIGINAL = [
 ("SECTION_HORIZON", "days", "|D|", "integer, days", "Length of the planning horizon. All instances start on a Monday.", "Index set for every variable"),
 ("SECTION_SHIFTS", "ShiftID", "s in S", "string", "Identifier of a shift type, e.g. D, E, N.", "Index set"),
 ("SECTION_SHIFTS", "Length in mins", "l_s", "integer, minutes", "Duration of one shift of type s.", "HARD: total-minutes bounds. Also the basis of the wage coefficient"),
 ("SECTION_SHIFTS", "Shifts which cannot follow", "cannot_follow(s)", "list of ShiftIDs", "Shift types forbidden on the day after s, e.g. an early after a night.", "HARD constraint"),
 ("SECTION_STAFF", "ID", "i in I", "string", "Employee identifier. THE JOIN KEY for the added per-nurse table.", "Index set"),
 ("SECTION_STAFF", "MaxShifts", "m_is", "ShiftID=count pairs", "Maximum number of shifts of each type this employee may work.", "HARD constraint"),
 ("SECTION_STAFF", "MaxTotalMinutes", "Mmax_i", "integer, minutes", "Contractual maximum working minutes over the horizon.", "HARD, but RELAXED by the added overtime variable (sheet 6)"),
 ("SECTION_STAFF", "MinTotalMinutes", "Mmin_i", "integer, minutes", "Contractual minimum working minutes over the horizon.", "HARD constraint"),
 ("SECTION_STAFF", "MaxConsecutiveShifts", "Kmax_i", "integer, days", "Longest permitted run of consecutive working days.", "HARD constraint"),
 ("SECTION_STAFF", "MinConsecutiveShifts", "Kmin_i", "integer, days", "Shortest permitted run of consecutive working days.", "HARD constraint"),
 ("SECTION_STAFF", "MinConsecutiveDaysOff", "Omin_i", "integer, days", "Shortest permitted run of consecutive days off.", "HARD constraint"),
 ("SECTION_STAFF", "MaxWeekends", "Wmax_i", "integer, weekends", "Maximum number of weekends worked (a weekend counts if either day is worked).", "HARD constraint"),
 ("SECTION_DAYS_OFF", "EmployeeID, DayIndexes", "(i,d) in O", "pairs, day index from 0", "Pre-assigned days off (leave, training).", "HARD constraint: no assignment permitted"),
 ("SECTION_SHIFT_ON_REQUESTS", "EmployeeID, Day, ShiftID, Weight", "w^on_ids", "integer weight", "Employee ASKED to work shift s on day d; the weight is the penalty if not granted.", "Benchmark objective, AND priced into F_soc"),
 ("SECTION_SHIFT_OFF_REQUESTS", "EmployeeID, Day, ShiftID, Weight", "w^off_ids", "integer weight", "Employee asked NOT to work shift s on day d; weight is the penalty if broken.", "Benchmark objective, AND priced into F_soc"),
 ("SECTION_COVER", "Day, ShiftID, Requirement", "R_ds", "integer, employees", "Number of employees required on day d for shift s.", "SOFT: shortfall is penalised, and in the TBL model is priced"),
 ("SECTION_COVER", "Weight for under", "wu_ds", "integer", "Penalty per employee below the requirement. 100 in every published instance.", "Benchmark objective only. The TBL model uses its own A$ shadow price instead"),
 ("SECTION_COVER", "Weight for over", "wo_ds", "integer", "Penalty per employee above the requirement. 1 in every published instance.", "Benchmark objective only"),
]
for row in ORIGINAL:
    r = put(ws, r, list(row), fill=ORIG)
widths(ws, [27, 34, 17, 22, 60, 46])

# ============================================================ 3. Added — per nurse
ws = wb.create_sheet("Added - per nurse")
ws["A1"] = "ADDED — per-nurse sidecar table (data\\<Instance>\\staff_tbl.csv)"
ws["A1"].font = TITLE
ws["A2"] = ("Joined to SECTION_STAFF on the employee ID. The instance files themselves are "
            "never written to. ALL VALUES SYNTHETIC, generated by prep_all.py from a fixed "
            "per-instance seed.")
ws["A2"].font = IT; ws.merge_cells("A2:G2")
head(ws, 4, ["Field", "Symbol", "Type / units", "Meaning", "How it is generated",
             "What it feeds"])
r = 5
ADDED_NURSE = [
 ("staff_id", "i", "string", "Join key. Copied from SECTION_STAFF, never invented.",
  "Read from the instance", "the join"),
 ("grade", "-", "string", "Pay step on an 8-step registered-nurse ladder.",
  "Uniform at random over the 8 steps", "hourly_rate_aud and the senior flag"),
 ("senior", "i in Sen", "0 or 1", "Whether this nurse counts as senior for skill mix.",
  "1 for the top 3 grades; topped up to reach min_senior_share of the establishment",
  "the added soft skill-mix constraint"),
 ("hourly_rate_aud", "h_i", "A$ per hour", "Base hourly pay.",
  "Determined by grade", "wage and overtime coefficients -> F_econ"),
 ("commute_km_one_way", "delta_i", "km", "One-way home-to-work distance.",
  "Lognormal, median 12 km, sigma 0.6, clipped to [1, 60]", "emissions -> F_env"),
 ("commute_mode", "-", "car / public_transport / active", "Usual travel mode.",
  "Random: car 0.75, public transport 0.15, active 0.10", "the emission factor"),
 ("ef_kg_per_km", "epsilon_i", "kg CO2e per km", "Emission factor for that mode.",
  "car 0.171, public transport 0.035, active 0.000", "emissions -> F_env"),
]
for row in ADDED_NURSE:
    r = put(ws, r, list(row), fill=MINE)
r += 1
c = ws.cell(row=r, column=1, value=(
    "LIMITATION TO STATE IN THE THESIS: every value above is synthetic. Replace with the "
    "NSW Nurses' and Midwives' (State) Award for pay, a staff travel survey for distance "
    "and mode, and the DCCEEW National Greenhouse Accounts Factors for emission factors "
    "before making any empirical claim."))
c.font = BOLD; c.fill = RED; c.alignment = WRAP; c.border = BOX
ws.merge_cells(start_row=r, start_column=1, end_row=r + 1, end_column=6)
widths(ws, [22, 12, 30, 52, 52, 40])

# ============================================================ 4. Added — prices
ws = wb.create_sheet("Added - prices")
ws["A1"] = "ADDED — global price vector (prices.csv)"; ws["A1"].font = TITLE
ws["A2"] = ("The conversion factors that put all three pillars in A$. Read live from "
            "prices.csv, so this sheet cannot drift from what the solver uses. Every row "
            "applies to every instance.")
ws["A2"].font = IT; ws.merge_cells("A2:E2")
ws["A3"] = ("NOTE ON THE SHADING ON THIS SHEET: every row here is ADDED. The colour shows "
            "EVIDENCE STATUS, not layer — green = SOURCED (a citable published figure), "
            "amber = DERIVED (computed from sourced figures), orange = ASSUMED (a placeholder "
            "this thesis has to justify or sweep).")
ws["A3"].font = IT; ws.merge_cells("A3:E3")
head(ws, 4, ["Key", "Value", "Unit", "Provenance", "Source or derivation chain"])
r = 5
pr = []
pp = os.path.join(D.HERE, "prices.csv")
if os.path.exists(pp):
    pr = rd(pp)
    for row in pr:
        st = row.get("status", "")
        fill = (DERV if st == "SOURCED" else MINE if st == "DERIVED" else RED)
        r = put(ws, r, [row.get("key"), float(row["value"]) if row.get("value") else None,
                        row.get("unit"), st, row.get("source_or_chain", "")], fill=fill)
    first, last = 5, r - 1
    cnt_row = r
    ws.cell(row=cnt_row, column=1, value=f"{len(pr)} coefficients").font = BOLD
    for j, st in ((2, "SOURCED"), (3, "DERIVED"), (4, "ASSUMED")):
        c = ws.cell(row=cnt_row, column=j,
                    value=f'=COUNTIF(D{first}:D{last},"{st}")&" {st}"')
        c.font = BOLD
    c = ws.cell(row=cnt_row, column=5,
                value="Every ASSUMED row needs either a source or a sensitivity sweep "
                      "before publication.")
    c.font = BOLD
    for j in range(1, 6):
        ws.cell(row=cnt_row, column=j).fill = SEC; ws.cell(row=cnt_row, column=j).border = BOX
else:
    ws.cell(row=5, column=1, value="prices.csv not found").font = IT
widths(ws, [30, 14, 26, 13, 88])

# ============================================================ 5. Derived
ws = wb.create_sheet("Derived")
ws["A1"] = "DERIVED — computed from the two layers, stored nowhere"; ws["A1"].font = TITLE
ws["A2"] = ("Built once per instance in tbl_data.py, class Coef. Listing these separately "
            "matters: they are not data anyone can edit, so a reader auditing the model "
            "needs to know they are functions of the rows in sheets 2, 3 and 4.")
ws["A2"].font = IT; ws.merge_cells("A2:E2")
head(ws, 4, ["Quantity", "Symbol", "Formula", "Units", "Inputs it combines"])
r = 5
DERIVED = [
 ("Wage of one shift", "c_is", "hourly_rate_aud[i] x (l_s / 60)", "A$ per shift",
  "ADDED pay x ORIGINAL shift length"),
 ("Overtime hourly rate", "-", "hourly_rate_aud[i] x overtime_multiplier", "A$ per hour",
  "ADDED pay x ADDED price"),
 ("Emissions per attendance day", "2.delta_i.epsilon_i",
  "2 x commute_km_one_way[i] x ef_kg_per_km[i]", "kg CO2e per day worked",
  "ADDED distance x ADDED emission factor. Charged per DAY, not per shift, because a "
  "commute is one return trip whichever shift is worked"),
 ("Carbon price per kg", "p_kg", "carbon_price_aud_per_t / 1000", "A$ per kg CO2e",
  "ADDED price. This single factor converts the whole environmental pillar to A$"),
 ("Request-violation price", "k_pref",
  "p_leave_per_pref_weight x turnover_cost_aud", "A$ per unit of request weight",
  "ADDED elasticity x ADDED turnover cost. Applied to the ORIGINAL request weights"),
 ("Fairness price", "k_fair",
  "p_leave_per_dev_shift x turnover_cost_aud", "A$ per shift of deviation",
  "ADDED elasticity x ADDED turnover cost"),
 ("Senior requirement", "Q_ds",
  "min(|Sen|, ceil(min_senior_share x R_ds))", "employees",
  "ADDED share x ORIGINAL cover requirement. Proportional, so it scales with instance "
  "size instead of being a flat count"),
 ("Mean wage per shift", "-", "mean over i of c_is for the first shift type", "A$",
  "Used only by the price-ordering sanity check"),
]
for row in DERIVED:
    r = put(ws, r, list(row), fill=DERV)
widths(ws, [30, 18, 46, 28, 66])

# ============================================================ 6. Model extensions
ws = wb.create_sheet("Model extensions")
ws["A1"] = "ADDED — decision variables and constraints introduced by this thesis"
ws["A1"].font = TITLE
ws["A2"] = ("Enabled by the --levers flag. Without it these variables exist but are pinned "
            "to zero, which is how the model reproduces the published benchmark exactly.")
ws["A2"].font = IT; ws.merge_cells("A2:E2")
head(ws, 4, ["Name", "Symbol", "Kind", "Bounds", "What it does and why"])
r = 5
EXT = [
 ("Agency staffing", "a_ds", "integer variable", "0 .. agency_max_per_shift",
  "Buy external staff for a shift. Priced in F_econ; also carries its own travel "
  "emissions in F_env. Enlarges the feasible region relative to the benchmark."),
 ("Overtime", "o_i", "continuous variable", "0 .. overtime_max_min",
  "RELAXES the ORIGINAL MaxTotalMinutes constraint: hours above contract are permitted "
  "but paid at the overtime multiplier."),
 ("Understaffing", "u_ds", "continuous variable", ">= 0",
  "Shortfall against the ORIGINAL cover requirement. Priced at a patient-safety shadow "
  "price which must exceed the agency cost, or the optimiser buys gaps instead of staff."),
 ("Overstaffing", "v_ds", "continuous variable", ">= 0",
  "Surplus above the requirement. Carries a small ancillary emissions term."),
 ("Senior shortfall", "t_ds", "continuous variable", "0 .. Q_ds",
  "NEW SOFT CONSTRAINT. Skill mix is priced, never impossible. A hard flat skill-mix rule "
  "made several instances infeasible; making it soft and proportional fixed that without "
  "touching any benchmark constraint."),
 ("Fairness deviation", "dev_i", "continuous variable", ">= 0",
  "Absolute deviation of nurse i's shift count from the mean, linearised by two "
  "inequalities. Priced in F_soc."),
 ("Fatigue indicator", "g_id", "binary variable", "0 or 1",
  "Flags three consecutive night shifts. Always zero on instances with no night shift."),
 ("Churn", "-", "expression", "-",
  "Changes against a previously published roster. Implemented but INACTIVE: it needs a "
  "prior roster to be supplied, which the benchmark does not provide."),
]
for row in EXT:
    r = put(ws, r, list(row), fill=MINE)
r += 1
c = ws.cell(row=r, column=1, value=(
    "PRINCIPLE OBSERVED THROUGHOUT: the only HARD constraints are the benchmark's own. "
    "Every constraint this thesis adds is soft and priced. That is what keeps the feasible "
    "region a superset of the benchmark's and lets Instance1 still reproduce 607."))
c.font = BOLD; c.fill = SEC; c.alignment = WRAP; c.border = BOX
ws.merge_cells(start_row=r, start_column=1, end_row=r + 1, end_column=5)
widths(ws, [22, 14, 20, 26, 86])

# ============================================================ 7. Instance table
ws = wb.create_sheet("Instance table")
ws["A1"] = "Per-instance characteristics — ORIGINAL (blue) and ADDED (amber)"
ws["A1"].font = TITLE
ws["A2"] = ("Generated from data\\, so it reflects exactly what the solver reads. The added "
            "columns are summary statistics of the synthetic sidecar, not new data.")
ws["A2"].font = IT; ws.merge_cells("A2:P2")
H = ["Instance", "Employees", "Days", "Shift types", "Shift IDs", "Total demand",
     "On-requests", "Off-requests", "Days off", "Seniors", "Senior %",
     "Mean pay (A$/h)", "Min pay", "Max pay", "Mean commute (km)", "Mean kg/day"]
head(ws, 4, H)
r = 5
names = sorted(D.list_instances(), key=inst_no) if os.path.isdir(os.path.join(D.HERE, "data")) else []
for nm in names:
    try:
        inst = D.load_instance(nm)
        T, P = D.load_tbl(inst)
    except BaseException as ex:
        r = put(ws, r, [nm, f"could not load: {str(ex).splitlines()[0][:50]}"])
        continue
    rates = [float(T[i]["hourly_rate_aud"]) for i in inst.staff]
    kms = [float(T[i]["commute_km_one_way"]) for i in inst.staff]
    kgd = [2 * float(T[i]["commute_km_one_way"]) * float(T[i]["ef_kg_per_km"])
           for i in inst.staff]
    nsen = sum(1 for i in inst.staff if T[i]["senior"] == "1")
    vals = [nm, len(inst.staff), inst.n_days, len(inst.shifts), "/".join(inst.shifts),
            sum(inst.R.values()), len(inst.on_req), len(inst.off_req), len(inst.days_off),
            nsen, None,
            round(statistics.fmean(rates), 2), min(rates), max(rates),
            round(statistics.fmean(kms), 1), round(statistics.fmean(kgd), 3)]
    for j, v in enumerate(vals, 1):
        c = ws.cell(row=r, column=j, value=v)
        c.font = BLK; c.border = BOX
        c.fill = ORIG if j <= 9 else MINE
    ws.cell(row=r, column=11, value=f"=IF(B{r}=0,\"\",J{r}/B{r})").number_format = '0.0%'
    ws.cell(row=r, column=11).fill = MINE; ws.cell(row=r, column=11).border = BOX
    for j in (12, 13, 14): ws.cell(row=r, column=j).number_format = '#,##0.00'
    ws.cell(row=r, column=15).number_format = '#,##0.0'
    ws.cell(row=r, column=16).number_format = '#,##0.000'
    r += 1
if r == 5:
    ws.cell(row=5, column=1, value="No prepared instances in data\\ — run prep_all.py "
                                   "first.").font = IT
else:
    last = r - 1
    ws.cell(row=r, column=1, value="TOTAL / MEAN").font = BOLD
    for j, col in ((2, "B"), (3, "C"), (6, "F"), (7, "G"), (8, "H"), (9, "I"), (10, "J")):
        c = ws.cell(row=r, column=j, value=f"=SUM({col}5:{col}{last})")
        c.font = BOLD; c.fill = SEC; c.border = BOX
    for j, col in ((12, "L"), (15, "O"), (16, "P")):
        c = ws.cell(row=r, column=j, value=f"=AVERAGE({col}5:{col}{last})")
        c.font = BOLD; c.fill = SEC; c.border = BOX; c.number_format = '#,##0.00'
    c = ws.cell(row=r, column=11, value=f"=IF(B{r}=0,\"\",J{r}/B{r})")
    c.font = BOLD; c.fill = SEC; c.border = BOX; c.number_format = '0.0%'
    for j in range(1, 17):
        if ws.cell(row=r, column=j).value is None:
            ws.cell(row=r, column=j).fill = SEC; ws.cell(row=r, column=j).border = BOX
widths(ws, [12, 11, 7, 11, 22, 13, 12, 12, 10, 9, 10, 15, 10, 10, 17, 13])

out = os.path.join(D.HERE, args.out)
try:
    wb.save(out)
except PermissionError:
    import time as _t
    out = os.path.join(D.HERE, f"{os.path.splitext(args.out)[0]}_{_t.strftime('%H%M%S')}.xlsx")
    wb.save(out)
    print("  [!] target was locked (open in Excel?) — saved under a new name")
print(f"provenance workbook written to {out}")
print(f"  {len(names)} instance(s) documented, {len(pr)} price coefficient(s)")
