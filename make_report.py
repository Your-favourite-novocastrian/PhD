"""
make_report.py -- turn everything in results\\ into ONE formatted Excel workbook.

    python make_report.py                      build TBL_results.xlsx from all results
    python make_report.py --out thesis.xlsx    choose the filename

Sheets produced:
  1. All runs        every row of every summary_*.csv, with the settings that produced it
  2. Prices          prices.csv with provenance colouring (green=sourced, amber=derived, red=assumed)
  3. Nurse data      every instance's staff_tbl.csv stacked, with derived c_i and kg/day
  4. Rosters         index of every roster CSV written, so you can find them
  5. Read me         what each column means

Nothing here re-solves anything. It only reads what the solver runs already wrote,
so it is safe to run any time, including while a batch is still going.
"""
import argparse, csv, glob, os, re
from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
from openpyxl.utils import get_column_letter

HERE = os.path.dirname(os.path.abspath(__file__))
RESULTS = os.path.join(HERE, "results")
DATA = os.path.join(HERE, "data")

F = "Arial"
HDR = PatternFill("solid", fgColor="1F3864"); HF = Font(name=F, bold=True, color="FFFFFF", size=10)
GREEN = PatternFill("solid", fgColor="E2EFDA"); AMB = PatternFill("solid", fgColor="FFF2CC")
RED = PatternFill("solid", fgColor="FCE4D6"); SEC = PatternFill("solid", fgColor="D9E1F2")
BLK = Font(name=F, size=10); BOLD = Font(name=F, bold=True, size=10); IT = Font(name=F, italic=True, size=9)
thin = Side(style="thin", color="BFBFBF"); BOX = Border(left=thin, right=thin, top=thin, bottom=thin)
WRAP = Alignment(wrap_text=True, vertical="top")

def read_csv(path):
    with open(path) as f:
        return list(csv.DictReader(l for l in f if not l.lstrip().startswith("#")))

def head(ws, row, cols):
    for j, c in enumerate(cols, 1):
        x = ws.cell(row=row, column=j, value=c); x.fill = HDR; x.font = HF
        x.alignment = WRAP; x.border = BOX
    ws.freeze_panes = ws.cell(row=row + 1, column=1)

def put(ws, row, vals, font=BLK, fill=None):
    for j, v in enumerate(vals, 1):
        c = ws.cell(row=row, column=j, value=v); c.font = font; c.border = BOX
        if fill: c.fill = fill

def widths(ws, w):
    for j, x in enumerate(w, 1): ws.column_dimensions[get_column_letter(j)].width = x

def num(v):
    """CSV values arrive as strings -- make them real numbers so Excel can chart/sort them."""
    if v is None or v == "": return None
    try: return int(v)
    except ValueError:
        try: return float(v)
        except ValueError: return v

ap = argparse.ArgumentParser()
ap.add_argument("--out", default="TBL_results.xlsx")
args = ap.parse_args()

wb = Workbook()

# ======================================================= 1. All runs
ws = wb.active; ws.title = "All runs"
ws["A1"] = "Every solver run found in results\\ — one row per instance per experiment"
ws["A1"].font = Font(name=F, bold=True, size=13)
ws["A2"] = ("Built by make_report.py from the summary_*.csv files. 'settings' and 'solver' are "
            "parsed from each filename, so runs with different --gap / --time stay separate.")
ws["A2"].font = IT; ws.merge_cells("A2:H2")

summaries = sorted(glob.glob(os.path.join(RESULTS, "summary_*.csv")))
COLS = ["settings", "solver", "instance", "staff", "days", "shift_types", "carbon_price",
        "status", "build_s", "solve_s", "wall_s", "gap",
        "penalty", "pen_on", "pen_off", "pen_under", "pen_over",
        "F_econ", "F_soc", "F_env", "total", "env_share_pct", "kg_co2e",
        "shifts", "agency", "understaffed", "senior_short", "request_wt",
        "dev_shifts", "fatigue_blocks"]
head(ws, 4, COLS)

r = 5
for path in summaries:
    base = os.path.basename(path)[len("summary_"):-len(".csv")]
    solver = base.rsplit("_", 1)[-1]
    settings = base[:-(len(solver) + 1)]
    for row in read_csv(path):
        vals = [settings, solver] + [num(row.get(c)) for c in COLS[2:]]
        put(ws, r, vals)
        st = ws.cell(row=r, column=COLS.index("status") + 1)
        v = str(st.value or "")
        st.fill = GREEN if v == "OPTIMAL" else AMB if v in ("WITHIN-GAP", "FEASIBLE") else RED
        for c in ("F_econ", "F_soc", "total"):
            ws.cell(row=r, column=COLS.index(c) + 1).number_format = '#,##0.00'
        ws.cell(row=r, column=COLS.index("F_env") + 1).number_format = '#,##0.0000'
        ws.cell(row=r, column=COLS.index("gap") + 1).number_format = '0.0000%'
        ws.cell(row=r, column=COLS.index("env_share_pct") + 1).number_format = '0.0000'
        r += 1

if r == 5:
    ws.cell(row=5, column=1, value="No summary_*.csv found in results\\ — run run_all.py first.").font = IT
widths(ws, [26, 9, 12, 7, 6, 11, 12, 16, 8, 8, 8, 10,
            9, 8, 8, 10, 9, 13, 12, 11, 13, 12, 11, 8, 8, 12, 12, 11, 11, 13])

# ======================================================= 2. Prices
ws = wb.create_sheet("Prices")
ws["A1"] = "prices.csv — the price vector, with provenance"; ws["A1"].font = Font(name=F, bold=True, size=13)
ws["A2"] = "Green = SOURCED (citation or public dataset) · Amber = DERIVED · Red = ASSUMED (no source yet)"
ws["A2"].font = IT; ws.merge_cells("A2:E2")
pr = read_csv(os.path.join(HERE, "prices.csv"))
head(ws, 4, ["key", "value", "unit", "status", "source / chain"])
for k, row in enumerate(pr):
    rr = 5 + k
    put(ws, rr, [row["key"], num(row["value"]), row["unit"], row["status"],
                 row.get("source_or_chain", "")])
    s = row["status"]
    ws.cell(row=rr, column=4).fill = GREEN if s == "SOURCED" else AMB if s == "DERIVED" else RED
    ws.row_dimensions[rr].height = 28
n = 5 + len(pr) + 1
cnt = {s: sum(1 for x in pr if x["status"] == s) for s in ("SOURCED", "DERIVED", "ASSUMED")}
put(ws, n, [f"{len(pr)} coefficients", f"SOURCED {cnt['SOURCED']}", f"DERIVED {cnt['DERIVED']}",
            f"ASSUMED {cnt['ASSUMED']}", "Every ASSUMED row needs a sensitivity sweep before publication"],
    font=BOLD, fill=SEC)
widths(ws, [28, 12, 24, 12, 70])

# ======================================================= 3. Nurse data
ws = wb.create_sheet("Nurse data")
ws["A1"] = "staff_tbl.csv for every prepared instance — THE SYNTHETIC DATA"
ws["A1"].font = Font(name=F, bold=True, size=13)
ws["A2"] = ("This is the per-nurse data added to the benchmark. The instance .txt files themselves "
            "are never modified. c_i and kg/day are derived, not stored.")
ws["A2"].font = IT; ws.merge_cells("A2:J2")
head(ws, 4, ["instance", "staff_id", "grade", "senior", "hourly_rate_aud", "commute_km_one_way",
             "commute_mode", "ef_kg_per_km", "DERIVED c_i @8h (A$/shift)",
             "DERIVED 2·δ·ε (kg CO2e per attendance day)"])
r = 5
for d in sorted(os.listdir(DATA) if os.path.isdir(DATA) else [],
                key=lambda n: (int("".join(c for c in n if c.isdigit()) or 0), n)):
    sp = os.path.join(DATA, d, "staff_tbl.csv")
    if not os.path.exists(sp): continue
    for row in read_csv(sp):
        rate = float(row["hourly_rate_aud"]); km = float(row["commute_km_one_way"])
        ef = float(row["ef_kg_per_km"])
        put(ws, r, [d, row["staff_id"], row["grade"], int(row["senior"]), rate, km,
                    row["commute_mode"], ef, round(rate * 8, 2), round(2 * km * ef, 3)])
        if int(row["senior"]): ws.cell(row=r, column=4).fill = SEC
        r += 1
if r == 5:
    ws.cell(row=5, column=1, value="No data\\*\\staff_tbl.csv found — run prep_all.py first.").font = IT
widths(ws, [12, 10, 10, 8, 15, 18, 18, 13, 22, 26])

# ======================================================= 4. Rosters
ws = wb.create_sheet("Rosters")
ws["A1"] = "Every roster file written — open these to see the actual schedules"
ws["A1"].font = Font(name=F, bold=True, size=13)
head(ws, 3, ["file", "instance", "settings", "solver", "size (bytes)"])
r = 4
for p in sorted(glob.glob(os.path.join(RESULTS, "*.csv"))):
    b = os.path.basename(p)
    if b.startswith("summary_"): continue
    m = re.match(r"(Instance\d+)_(.+)_(ortools|gurobi)", b)
    put(ws, r, [b, m.group(1) if m else "", m.group(2) if m else "", m.group(3) if m else "",
                os.path.getsize(p)])
    r += 1
if r == 4:
    ws.cell(row=4, column=1, value="No roster CSVs yet.").font = IT
widths(ws, [52, 12, 30, 10, 14])

# ======================================================= 5. Read me
ws = wb.create_sheet("Read me")
ws["A1"] = "What each column means"; ws["A1"].font = Font(name=F, bold=True, size=13)
head(ws, 3, ["column", "meaning"])
GLOSS = [
 ("settings", "objective / levers / gap / time, parsed from the summary filename"),
 ("status", "OPTIMAL = proven optimal. WITHIN-GAP = stopped because the gap tolerance was met, NOT proven. "
            "FEASIBLE = hit the time limit. FEASIBLE-NOT-OPTIMISED = fallback roster, objective never minimised. "
            "UNKNOWN = no solution found in time (NOT infeasible)."),
 ("build_s", "seconds Python spent building the model — the time limit does NOT cover this"),
 ("solve_s", "seconds the solver spent — what --time bounds"),
 ("gap", "relative MIP gap achieved. 0 means proven optimal"),
 ("penalty", "benchmark score = pen_on + pen_off + 100×under + 1×over. Reported even when dollars were minimised"),
 ("pen_under", "100 × uncovered shift-units — this term dominates the penalty"),
 ("F_econ", "wages + overtime + agency + understaffing + senior shortfall (A$)"),
 ("F_soc", "requests + fairness + fatigue + churn (A$)"),
 ("F_env", "carbon price × total kg CO2e (A$)"),
 ("total", "F_econ + F_soc + F_env — what the dollars objective minimises"),
 ("env_share_pct", "F_env as a percentage of total"),
 ("kg_co2e", "commuting + agency travel + ancillary emissions"),
 ("senior_short", "senior-shifts short of the skill-mix requirement (soft, priced)"),
 ("dev_shifts", "sum over nurses of |shifts − mean|"),
 ("fatigue_blocks", "three-consecutive-night blocks. Always 0 on instances with no night shift"),
]
for k, (a, b) in enumerate(GLOSS):
    put(ws, 4 + k, [a, b]); ws.cell(row=4 + k, column=2).alignment = WRAP
    ws.row_dimensions[4 + k].height = 30
widths(ws, [18, 110])

out = os.path.join(HERE, args.out)
try:
    wb.save(out)
except PermissionError:
    import time as _t
    out = os.path.join(HERE, f"{os.path.splitext(args.out)[0]}_{_t.strftime('%H%M%S')}.xlsx")
    wb.save(out)
    print("  [!] target was locked (open in Excel?) — saved under a new name instead")
print(f"workbook written to {out}")
print(f"  {len(summaries)} summary file(s), {r-4} roster file(s), {len(pr)} prices")
