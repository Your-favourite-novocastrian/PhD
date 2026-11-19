"""
make_workbook.py -- build the formatted Excel workbook from everything in results\\.

    python make_workbook.py                      -> TBL_HWSP_results.xlsx
    python make_workbook.py --out thesis.xlsx

run_all.py calls this automatically at the end of every batch, so the workbook is always
current. Nothing here re-solves anything: it only reads and reshapes the summary CSVs, so
the workbook can never disagree with the raw data.

SHEETS
  All runs          every row of every summary_*.csv, banded by solver
  Main results      one row per instance -- the best USABLE run, whichever solver
  Solver comparison Gurobi vs OR-Tools side by side, with the objective and time deltas
  Read me           what each column means and which numbers to cite

USABLE means status OPTIMAL, WITHIN-GAP or FEASIBLE. Excluded from 'Main results':
  UNKNOWN                 no solution found at all
  ERROR                   crashed (e.g. 'bad allocation' = out of memory)
  FEASIBLE-NOT-OPTIMISED  a fallback feasibility roster; the objective was never
                          minimised, so it is not a result
  INTERRUPTED             a solve that was killed mid-search

CORRECTED STATUS is a live formula, and it is the column to cite. Both solvers report
OPTIMAL when they merely MET the gap tolerance; only gap = 0 is proven optimality.
"""
import argparse, csv, glob, os, re
from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
from openpyxl.utils import get_column_letter
import tbl_data as D

RESULTS = os.path.join(D.HERE, "results")

F = "Arial"
NAVY = PatternFill("solid", fgColor="1F3864")
HF = Font(name=F, bold=True, color="FFFFFF", size=10)
SEC = PatternFill("solid", fgColor="D9E1F2")
GREEN = PatternFill("solid", fgColor="C6EFCE")
AMBER = PatternFill("solid", fgColor="FFEB9C")
RED = PatternFill("solid", fgColor="FFC7CE")
GREY = PatternFill("solid", fgColor="F2F2F2")
BLK = Font(name=F, size=10)
BOLD = Font(name=F, bold=True, size=10)
IT = Font(name=F, italic=True, size=9, color="595959")
TITLE = Font(name=F, bold=True, size=14, color="1F3864")
thin = Side(style="thin", color="BFBFBF")
BOX = Border(left=thin, right=thin, top=thin, bottom=thin)
WRAP = Alignment(wrap_text=True, vertical="top")
AUD, AUD0, PCT2, PCT4, N1 = '#,##0.00', '#,##0', '0.00%', '0.0000%', '0.0'
GOOD = ("OPTIMAL", "WITHIN-GAP", "FEASIBLE")


def inst_no(s):
    d = "".join(c for c in str(s) if c.isdigit())
    return int(d) if d else 0


def n(v, cast=float):
    if v is None or v == "": return None
    try: return cast(v)
    except (TypeError, ValueError): return None


def load():
    """Every row of every summary CSV, with solver and time limit resolved."""
    out = []
    for p in sorted(glob.glob(os.path.join(RESULTS, "summary_*.csv"))):
        base = os.path.basename(p)[len("summary_"):-len(".csv")]
        # Prefer the columns in the file. Fall back to the filename for CSVs written by
        # older versions of run_all.py, which recorded neither solver nor limit.
        fn_solver = base.rsplit("_", 1)[-1]
        if fn_solver not in ("gurobi", "ortools"): fn_solver = ""
        fn_tag = base[:-(len(fn_solver) + 1)] if fn_solver else base
        m = re.search(r"_t(\d+(?:\.\d+)?)", fn_tag)
        fn_limit = float(m.group(1)) if m else None
        try:
            with open(p) as f:
                rows = list(csv.DictReader(l for l in f if not l.lstrip().startswith("#")))
        except Exception as ex:
            print(f"  [!] could not read {os.path.basename(p)}: {ex}")
            continue
        for r in rows:
            r["_solver"] = (r.get("solver") or fn_solver or "?").strip()
            r["_limit"] = n(r.get("time_limit")) or fn_limit
            r["_tag"] = re.sub(r"_t\d+(?:\.\d+)?$", "", fn_tag)
            r["_file"] = os.path.basename(p)
            out.append(r)
    return out


def head(ws, row, hdrs, freeze=None):
    for j, h in enumerate(hdrs, 1):
        c = ws.cell(row=row, column=j, value=h)
        c.fill = NAVY; c.font = HF; c.alignment = WRAP; c.border = BOX
    ws.row_dimensions[row].height = 30
    if freeze: ws.freeze_panes = freeze


def widths(ws, ws_widths):
    for j, w in enumerate(ws_widths, 1):
        ws.column_dimensions[get_column_letter(j)].width = w


ap = argparse.ArgumentParser()
ap.add_argument("--out", default="TBL_HWSP_results.xlsx")
ap.add_argument("--quiet", action="store_true")
a = ap.parse_args()

runs = load()
if not runs:
    raise SystemExit("no summary_*.csv in results\\ -- run run_all.py first")

wb = Workbook()

# ===================================================================== 1. All runs
ws = wb.active; ws.title = "All runs"
ws["A1"] = "TBL-HWSP — every solver run on record"; ws["A1"].font = TITLE
ws["A2"] = ("Built from every summary_*.csv in results\\. 'Corrected status' is derived from "
            "the gap and is the column to cite — a solver reports OPTIMAL when it merely met "
            "the gap tolerance.")
ws["A2"].font = IT; ws.merge_cells("A2:S2")

H = ["Solver", "Limit (s)", "Instance", "Staff", "Days", "Reported status",
     "Corrected status", "Build (s)", "Solve (s)", "Gap", "Penalty",
     "F_econ (A$)", "F_soc (A$)", "F_env (A$)", "Total (A$)", "Check",
     "% econ", "% soc", "% env"]
head(ws, 4, H, "C5")

runs.sort(key=lambda r: (r["_solver"], inst_no(r["instance"]), r["_limit"] or 0))
r = 5
cur = None
row_of = {}
for rec in runs:
    if rec["_solver"] != cur:
        cur = rec["_solver"]
        for j in range(1, len(H) + 1):
            c = ws.cell(row=r, column=j); c.fill = SEC; c.border = BOX
        ws.cell(row=r, column=1, value=f"{cur.upper()}").font = BOLD
        r += 1
    tot = n(rec.get("total"))
    vals = [rec["_solver"], rec["_limit"], rec["instance"], n(rec.get("staff"), int),
            n(rec.get("days"), int), rec.get("status", ""), None,
            n(rec.get("build_s")), n(rec.get("solve_s")), n(rec.get("gap")),
            n(rec.get("penalty"), int), n(rec.get("F_econ")), n(rec.get("F_soc")),
            n(rec.get("F_env")), None, None, None, None, None]
    for j, v in enumerate(vals, 1):
        c = ws.cell(row=r, column=j, value=v); c.font = BLK; c.border = BOX
    ws.cell(row=r, column=7,
            value=f'=IF(J{r}="","",IF(J{r}=0,"OPTIMAL",'
                  f'IF(F{r}="OPTIMAL","WITHIN-GAP",F{r})))').font = BOLD
    if tot is not None:
        ws.cell(row=r, column=15, value=f"=L{r}+M{r}+N{r}").font = BOLD
        ws.cell(row=r, column=16, value=f'=IF(ABS(O{r}-{tot})<0.02,"ok","MISMATCH")')
        for j, src in ((17, "L"), (18, "M"), (19, "N")):
            ws.cell(row=r, column=j, value=f"=IF($O{r}=0,\"\",{src}{r}/$O{r})")
    for j in (8, 9): ws.cell(row=r, column=j).number_format = N1
    ws.cell(row=r, column=10).number_format = PCT2
    ws.cell(row=r, column=11).number_format = AUD0
    for j in (12, 13, 14, 15): ws.cell(row=r, column=j).number_format = AUD
    for j in (17, 18): ws.cell(row=r, column=j).number_format = PCT2
    ws.cell(row=r, column=19).number_format = PCT4
    st = rec.get("status", "")
    ws.cell(row=r, column=6).fill = (GREEN if st == "OPTIMAL"
                                     else AMBER if st in ("FEASIBLE", "WITHIN-GAP") else RED)
    g = n(rec.get("gap"))
    if g is not None:
        if g >= 0.10: ws.cell(row=r, column=10).fill = RED
        elif g > 0.015: ws.cell(row=r, column=10).fill = AMBER
    row_of[(rec["_solver"], rec["instance"], rec["_limit"])] = r
    rec["_row"] = r
    r += 1
widths(ws, [10, 9, 12, 7, 6, 23, 16, 9, 9, 9, 10, 14, 13, 12, 15, 9, 9, 9, 10])

# ===================================================================== 2. Main results
ws2 = wb.create_sheet("Main results")
ws2["A1"] = "Results table — one row per instance"; ws2["A1"].font = TITLE
ws2["A2"] = ("The best USABLE run per instance, by lowest total. UNKNOWN, ERROR, INTERRUPTED "
             "and FEASIBLE-NOT-OPTIMISED are excluded: a fallback roster was never optimised, "
             "so it is not a result. Values are live references to 'All runs'.")
ws2["A2"].font = IT; ws2.merge_cells("A2:M2")

H2 = ["Instance", "Staff", "Days", "Solver", "Limit (s)", "Status", "Solve (s)", "Gap",
      "F_econ (A$)", "F_soc (A$)", "F_env (A$)", "Total (A$)", "% env"]
head(ws2, 4, H2, "A5")

best = {}
for rec in runs:
    if rec.get("status") not in GOOD: continue
    t = n(rec.get("total"))
    if t is None: continue
    k = inst_no(rec["instance"])
    if k not in best or t < n(best[k].get("total")):
        best[k] = rec

r2 = 5
for k in sorted(best):
    src = best[k]["_row"]
    for j, col in ((1, "C"), (2, "D"), (3, "E"), (4, "A"), (5, "B"), (6, "G"),
                   (7, "I"), (8, "J"), (9, "L"), (10, "M"), (11, "N"), (12, "O")):
        c = ws2.cell(row=r2, column=j, value=f"='All runs'!{col}{src}")
        c.font = BOLD if j == 6 else BLK; c.border = BOX
    c = ws2.cell(row=r2, column=13, value=f'=IF(L{r2}=0,"",K{r2}/L{r2})')
    c.font = BLK; c.border = BOX; c.number_format = PCT4
    ws2.cell(row=r2, column=7).number_format = N1
    ws2.cell(row=r2, column=8).number_format = PCT2
    for j in (9, 10, 11, 12): ws2.cell(row=r2, column=j).number_format = AUD
    r2 += 1

if r2 > 5:
    tr = r2
    ws2.cell(row=tr, column=1, value="TOTAL / MEAN").font = BOLD
    for j, col in ((9, "I"), (10, "J"), (11, "K"), (12, "L")):
        c = ws2.cell(row=tr, column=j, value=f"=SUM({col}5:{col}{r2-1})")
        c.font = BOLD; c.number_format = AUD; c.fill = SEC
    c = ws2.cell(row=tr, column=8, value=f"=AVERAGE(H5:H{r2-1})")
    c.font = BOLD; c.number_format = PCT2; c.fill = SEC
    c = ws2.cell(row=tr, column=13, value=f'=IF(L{tr}=0,"",K{tr}/L{tr})')
    c.font = BOLD; c.number_format = PCT4; c.fill = SEC
    for j in range(1, 14):
        cc = ws2.cell(row=tr, column=j); cc.border = BOX
        if cc.value is None: cc.fill = SEC
    r2 = tr + 2

excluded = [rec for rec in runs if rec.get("status") not in GOOD]
if excluded:
    ws2.cell(row=r2, column=1, value="Excluded from this table").font = BOLD
    r2 += 1
    for rec in sorted(excluded, key=lambda x: (x["_solver"], inst_no(x["instance"]))):
        ws2.cell(row=r2, column=1, value=f"{rec['instance']} ({rec['_solver']})").font = BLK
        c = ws2.cell(row=r2, column=3, value=str(rec.get("status", "")))
        c.font = IT
        ws2.merge_cells(start_row=r2, start_column=3, end_row=r2, end_column=13)
        r2 += 1
widths(ws2, [12, 7, 6, 10, 9, 23, 9, 9, 14, 13, 12, 15, 10])

# ===================================================================== 3. Comparison
ws3 = wb.create_sheet("Solver comparison")
ws3["A1"] = "Gurobi vs OR-Tools CP-SAT"; ws3["A1"].font = TITLE
ws3["A2"] = ("Only instances where BOTH solvers produced a usable result at the same time "
             "limit. 'OR-Tools excess' positive means CP-SAT returned a more expensive roster.")
ws3["A2"].font = IT; ws3.merge_cells("A2:I2")
H3 = ["Instance", "Limit (s)", "Gurobi total (A$)", "Gurobi gap", "Gurobi solve (s)",
      "OR-Tools total (A$)", "OR-Tools gap", "OR-Tools solve (s)", "OR-Tools excess"]
head(ws3, 4, H3, "A5")

pair = {}
for rec in runs:
    if rec.get("status") not in GOOD or n(rec.get("total")) is None: continue
    pair.setdefault((inst_no(rec["instance"]), rec["_limit"]), {})[rec["_solver"]] = rec
r3 = 5
# Sort with an explicit key: summary CSVs written before time_limit became a column
# have lim=None, and None cannot be compared to a float.
for (k, lim) in sorted(pair, key=lambda t: (t[0], -1.0 if t[1] is None else float(t[1]))):
    p = pair[(k, lim)]
    if "gurobi" not in p or "ortools" not in p: continue
    gr, orr = p["gurobi"]["_row"], p["ortools"]["_row"]
    ws3.cell(row=r3, column=1, value=f"='All runs'!C{gr}").font = BLK
    ws3.cell(row=r3, column=2, value=lim).font = BLK
    for j, (src, col) in enumerate([(gr, "O"), (gr, "J"), (gr, "I"),
                                    (orr, "O"), (orr, "J"), (orr, "I")], 3):
        ws3.cell(row=r3, column=j, value=f"='All runs'!{col}{src}").font = BLK
    ws3.cell(row=r3, column=9, value=f'=IF(C{r3}=0,"",(F{r3}-C{r3})/C{r3})').font = BOLD
    for j in range(1, 10): ws3.cell(row=r3, column=j).border = BOX
    for j in (3, 6): ws3.cell(row=r3, column=j).number_format = AUD
    for j in (4, 7, 9): ws3.cell(row=r3, column=j).number_format = PCT2
    for j in (5, 8): ws3.cell(row=r3, column=j).number_format = N1
    r3 += 1
if r3 > 5:
    ws3.cell(row=r3, column=1, value="MEAN").font = BOLD
    for j, col in ((3, "C"), (4, "D"), (5, "E"), (6, "F"), (7, "G"), (8, "H"), (9, "I")):
        c = ws3.cell(row=r3, column=j, value=f"=AVERAGE({col}5:{col}{r3-1})")
        c.font = BOLD; c.fill = SEC; c.border = BOX
        c.number_format = AUD if j in (3, 6) else (N1 if j in (5, 8) else PCT2)
    for j in (1, 2):
        ws3.cell(row=r3, column=j).fill = SEC; ws3.cell(row=r3, column=j).border = BOX
else:
    ws3.cell(row=5, column=1, value="No instance has a usable result from BOTH solvers at "
                                    "the same time limit yet.").font = IT
widths(ws3, [12, 9, 18, 12, 15, 19, 13, 17, 15])

# ===================================================================== 4. Read me
ws4 = wb.create_sheet("Read me")
ws4["A1"] = "How to read this workbook"; ws4["A1"].font = TITLE
head(ws4, 3, ["Topic", "Detail"])
NOTES = [
    ("Generated", "By make_workbook.py from every summary_*.csv in results\\. run_all.py "
                  "calls it at the end of each batch, so this file is always current. "
                  "Re-run it any time: python make_workbook.py"),
    ("Corrected status", "CITE THIS, not 'Reported status'. Both solvers return OPTIMAL when "
                         "they merely MET the gap tolerance. Only Gap = 0.00% is proven "
                         "optimality. Report the gap beside every non-zero row."),
    ("Check", "Verifies F_econ + F_soc + F_env equals the total. Any MISMATCH means a "
              "data problem, not a rounding one."),
    ("Build vs Solve", "Build is Python constructing the model and is NOT bounded by the time "
                       "limit. Report Solve (s) as computational time."),
    ("Solve over the limit", "A solve_s well above the limit means either the machine slept "
                             "mid-run, or (OR-Tools) the UNKNOWN feasibility fallback ran "
                             "afterwards and its time is included. Neither is a solver fault, "
                             "but such a timing is not reportable."),
    ("Excluded rows", "FEASIBLE-NOT-OPTIMISED is a fallback roster whose objective was never "
                      "minimised. UNKNOWN found nothing. ERROR crashed — 'bad allocation' is "
                      "out of memory. All are listed at the foot of 'Main results' but kept "
                      "out of the table."),
    ("Environmental pillar", "% env is a fraction of a percent throughout. That is the finding, "
                             "not an error: at any defensible carbon price the environmental "
                             "term cannot influence the roster. Where the gap exceeds the "
                             "pillar, no environmental claim can be made from that row — use "
                             "the epsilon-constraint results instead."),
]
for k, (aa, bb) in enumerate(NOTES):
    c1 = ws4.cell(row=4 + k, column=1, value=aa); c1.font = BOLD; c1.fill = GREY; c1.border = BOX
    c2 = ws4.cell(row=4 + k, column=2, value=bb); c2.font = BLK; c2.alignment = WRAP
    c2.border = BOX
    ws4.row_dimensions[4 + k].height = 58
widths(ws4, [22, 105])

out = os.path.join(D.HERE, a.out)
try:
    wb.save(out)
except PermissionError:
    import time as _t
    out = os.path.join(D.HERE, f"{os.path.splitext(a.out)[0]}_{_t.strftime('%H%M%S')}.xlsx")
    wb.save(out)
    print(f"  [!] {a.out} was locked (open in Excel?) — saved as {os.path.basename(out)}")
if not a.quiet:
    print(f"workbook written to {out}")
    print(f"  {len(runs)} run(s) from {len(set(x['_file'] for x in runs))} summary file(s), "
          f"{len(best)} instance(s) in the results table")
