"""
import_console_log.py -- turn a pasted run_all.py console log back into summary CSVs.

    python import_console_log.py mylog.txt
    python import_console_log.py mylog.txt --tag dollars_levers

Writes results\\summary_IMPORTED_<tag>_t<limit>_<solver>.csv

WHY THIS EXISTS
---------------
Older versions of run_all.py wrote every run to the same summary filename, so a later
batch silently replaced an earlier one. If you still have the terminal output, nothing
is actually lost: the console table carries every column the summary CSV does. This
parses it back.

It also means a log pasted into an email or a lab notebook is a recoverable artefact.

PARSING NOTES
  The console format is fixed-width, and a value wider than its column pushes the rest
  of the line right -- so 'F_econ 4,960,287.00' and 'F_soc 1,106,183.12' print with no
  space between them. Splitting on whitespace therefore fails on exactly the rows you
  care about most (the largest instances). Instead each field is consumed left to right
  with a pattern that knows its shape: money is '\\d{1,3}(,\\d{3})*\\.\\d{2}' -- exactly
  two decimals -- so it cannot run past its own end into the next number.

  Every parsed row is checked: F_econ + F_soc + F_env must equal the printed total.
  Rows that fail are reported and NOT written.
"""
import argparse, csv, os, re, sys
import tbl_data as D

RESULTS = os.path.join(D.HERE, "results")

STATUSES = ["FEASIBLE-NOT-OPTIMISED", "OUT_OF_MEMORY", "NUMERIC_TROUBLE", "WITHIN-GAP",
            "INTERRUPTED", "INFEASIBLE", "OPTIMAL", "FEASIBLE", "UNKNOWN", "ERROR"]
ST_RE = re.compile("(" + "|".join(STATUSES) + ")")
MONEY = r"\d{1,3}(?:,\d{3})*\.\d{2}"
RUN_RE = re.compile(r"solver\s+(\w+)\s*\|\s*objective\s+(\w+)((?:\s*\+levers)?)")
LIM_RE = re.compile(r"limits:\s*([\d.]+)\s*s per run")

COLS = ["instance", "solver", "time_limit", "staff", "days", "shift_types", "carbon_price",
        "status", "wall_s", "gap", "build_s", "solve_s",
        "penalty", "pen_on", "pen_off", "pen_under", "pen_over",
        "F_econ", "F_soc", "F_env", "total", "env_share_pct", "kg_co2e",
        "shifts", "agency", "understaffed", "senior_short", "request_wt",
        "dev_shifts", "fatigue_blocks"]


def take(s, pat):
    m = re.match(r"\s*(" + pat + r")", s)
    return (None, s) if not m else (m.group(1), s[m.end():])


def num(v):
    if v is None or v == "nan": return None
    try: return float(str(v).replace(",", ""))
    except ValueError: return None


def parse(path):
    """-> list of (solver, tag, limit, row-dict). Also returns the rows it could not use."""
    out, rejected = [], []
    solver, tag, limit = "unknown", "unknown", None
    with open(path, encoding="utf-8", errors="replace") as f:
        for line in f:
            line = line.rstrip("\n")

            rm = RUN_RE.search(line)
            if rm:
                solver = rm.group(1)
                tag = rm.group(2) + ("_levers" if rm.group(3).strip() else "")
                continue
            lm = LIM_RE.search(line)
            if lm:
                limit = float(lm.group(1))
                continue
            if not line.startswith("Instance"):
                continue

            sm = ST_RE.search(line)
            if not sm:
                rejected.append((line[:60], "no status keyword")); continue
            head, status, rest = line[:sm.start()], sm.group(1), line[sm.end():]
            hm = re.match(r"(Instance\d+)\s+(\d+)\s+(\d+)", head)
            if not hm:
                rejected.append((line[:60], "could not read instance/staff/days")); continue
            inst, staff, days = hm.group(1), int(hm.group(2)), int(hm.group(3))

            build, rest = take(rest, r"\d+\.\d+")
            solve, rest = take(rest, r"\d+\.\d+")
            gap,   rest = take(rest, r"\d+\.\d+|nan")
            pen,   rest = take(rest, r"\d{1,3}(?:,\d{3})*")
            fe,    rest = take(rest, MONEY)
            fs,    rest = take(rest, MONEY)
            fv,    rest = take(rest, MONEY)
            tot,   rest = take(rest, MONEY)

            fe_, fs_, fv_, tot_ = num(fe), num(fs), num(fv), num(tot)
            if tot_ is not None:
                # The checksum. A transcription or parsing slip cannot survive this.
                if abs((fe_ or 0) + (fs_ or 0) + (fv_ or 0) - tot_) > 0.02:
                    rejected.append((f"{inst} {status}", "pillars do not sum to the total"))
                    continue

            row = {c: "" for c in COLS}
            row.update(instance=inst, solver=solver, time_limit=limit if limit else "",
                       staff=staff, days=days, status=status,
                       build_s=num(build) if build else "",
                       solve_s=num(solve) if solve else "",
                       wall_s=num(solve) if solve else "",
                       gap=(num(gap) / 100.0) if num(gap) is not None else "",
                       penalty=int(num(pen)) if num(pen) is not None else "",
                       F_econ=fe_ if fe_ is not None else "",
                       F_soc=fs_ if fs_ is not None else "",
                       F_env=fv_ if fv_ is not None else "",
                       total=tot_ if tot_ is not None else "")
            if tot_:
                row["env_share_pct"] = round((fv_ or 0) / tot_ * 100, 5)
            out.append((solver, tag, limit, row))
    return out, rejected


ap = argparse.ArgumentParser()
ap.add_argument("logfile", help="the saved terminal output")
ap.add_argument("--tag", default=None, help="override the settings tag in the filename")
a = ap.parse_args()

if not os.path.exists(a.logfile):
    raise SystemExit(f"no such file: {a.logfile}")

parsed, rejected = parse(a.logfile)
if not parsed:
    print("Nothing usable found. Lines that were skipped:")
    for t, why in rejected[:20]: print(f"    {t}  -> {why}")
    raise SystemExit(1)

print(f"parsed {len(parsed)} run row(s) from {os.path.basename(a.logfile)}")
if rejected:
    print(f"{len(rejected)} line(s) skipped:")
    for t, why in rejected[:10]: print(f"    {t}  -> {why}")

groups = {}
for solver, tag, limit, row in parsed:
    groups.setdefault((solver, a.tag or tag, limit), []).append(row)

os.makedirs(RESULTS, exist_ok=True)
hdr = f"{'file':<52}{'rows':>6}{'usable':>8}"
print(); print(hdr); print("-" * len(hdr))
GOOD = ("OPTIMAL", "WITHIN-GAP", "FEASIBLE")
for (solver, tag, limit), rows in sorted(groups.items(), key=lambda k: str(k[0])):
    rows.sort(key=lambda r: int("".join(c for c in r["instance"] if c.isdigit()) or 0))
    lim = f"_t{limit:g}" if limit else ""
    name = f"summary_IMPORTED_{tag}{lim}_{solver}.csv"
    path = os.path.join(RESULTS, name)

    def _w(p, rows=rows):
        with open(p, "w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=COLS); w.writeheader(); w.writerows(rows)
    sw = getattr(D, "safe_write", None)
    if sw is not None:
        path = sw(path, _w, "imported summary") or path
    else:
        _w(path)
    usable = sum(1 for r in rows if r["status"] in GOOD)
    print(f"{os.path.basename(path):<52}{len(rows):>6}{usable:>8}")

print(f"\nnext:  python make_workbook.py")
