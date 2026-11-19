"""
Turn a roster grid CSV (staff_id, day0, day1, ..., dayN-1 -- exactly what
tbl_hwsp_bench_gurobi.py writes) into an actual visual timetable: a
color-coded HTML page you open in a browser, not a spreadsheet of letters.

Usage:
    python visualize_roster.py roster_bench_benchmark.csv
    python visualize_roster.py roster_bench_benchmark.csv --cover data_bench/cover.csv

Output: roster_view.html, next to wherever you ran this from. Double-click
it (or drag it into a browser) to view.

Design notes (why it looks the way it does):
  - Shift type is an IDENTITY (nominal category), not a magnitude, so it
    gets categorical color -- NOT the sequential light-to-dark ramp you'd
    use for a heatmap of numbers.
  - Any two shift types can end up in neighboring cells, so this is
    validated as an "all-pairs" categorical case. That caps SAFE distinct
    colors at 3 (see the dataviz palette rules) -- a 4th+ shift type
    still renders, but falls back to a neutral background. The shift
    LETTER inside every cell is the real identity carrier regardless of
    color, so nothing depends on color alone.
  - Weekends (Saturday/Sunday, assuming the horizon starts on a Monday --
    true for this whole benchmark family) get a subtle marker, since
    max-weekends is one of the constraints you're checking visually.
"""

import argparse
import csv
import os

# Validated categorical palette (first 3 slots -- pass all-pairs CVD checks
# in both light and dark mode; see dataviz skill palette.md)
SHIFT_COLORS_LIGHT = ["#2a78d6", "#eb6834", "#1baf7a"]
SHIFT_COLORS_DARK = ["#3987e5", "#d95926", "#199e70"]
OVERFLOW_LIGHT = "#c3c2b7"   # 4th+ shift type: neutral, letter still carries identity
OVERFLOW_DARK = "#383835"


def load_roster(path):
    with open(path) as f:
        rows = list(csv.DictReader(f))
    day_cols = [c for c in rows[0].keys() if c != "staff_id"]
    staff = [r["staff_id"] for r in rows]
    grid = {r["staff_id"]: [r[c] for c in day_cols] for r in rows}
    return staff, day_cols, grid


def load_cover(path):
    if not path or not os.path.exists(path):
        return None
    with open(path) as f:
        rows = list(csv.DictReader(f))
    return rows


def build_html(staff, day_cols, grid, cover_rows):
    n_days = len(day_cols)
    shift_types = sorted({s for row in grid.values() for s in row if s})
    color_map_light = {s: SHIFT_COLORS_LIGHT[i] if i < 3 else OVERFLOW_LIGHT
                       for i, s in enumerate(shift_types)}
    color_map_dark = {s: SHIFT_COLORS_DARK[i] if i < 3 else OVERFLOW_DARK
                      for i, s in enumerate(shift_types)}

    def is_weekend(d):
        return d % 7 in (5, 6)   # horizon starts Monday=day0

    # Header row
    header_cells = []
    for d in range(n_days):
        wk = ' class="weekend"' if is_weekend(d) else ""
        header_cells.append(f'<th{wk}>{d}</th>')
    header_html = "<tr><th class='corner'>Staff</th>" + "".join(header_cells) + "</tr>"

    # Body rows
    body_html = ""
    for i in staff:
        cells = [f"<td class='staffcol'>{i}</td>"]
        for d, val in enumerate(grid[i]):
            wk = " weekend" if is_weekend(d) else ""
            if val:
                cells.append(
                    f'<td class="shift{wk}" style="--cell-light:{color_map_light[val]};'
                    f'--cell-dark:{color_map_dark[val]}" title="Staff {i}, day {d}: shift {val}">{val}</td>')
            else:
                cells.append(f'<td class="off{wk}" title="Staff {i}, day {d}: off">&middot;</td>')
        body_html += "<tr>" + "".join(cells) + "</tr>\n"

    legend_items = "".join(
        f'<div class="legend-item"><span class="swatch" style="--cell-light:{color_map_light[s]};'
        f'--cell-dark:{color_map_dark[s]}"></span>Shift {s}</div>'
        for s in shift_types
    )

    # Optional coverage table
    cover_html = ""
    if cover_rows:
        actual = {}
        for i in staff:
            for d, val in enumerate(grid[i]):
                if val:
                    actual[(d, val)] = actual.get((d, val), 0) + 1
        rows_html = ""
        for r in cover_rows:
            d, s, req = int(r["day"]), r["shift_id"], int(r["requirement"])
            got = actual.get((d, s), 0)
            status = "ok" if got == req else ("under" if got < req else "over")
            rows_html += (f"<tr><td>{d}</td><td>{s}</td><td>{req}</td><td>{got}</td>"
                         f'<td class="status-{status}">{status}</td></tr>\n')
        cover_html = f"""
        <details class="tableview">
          <summary>Coverage check (required vs. actually assigned, per day/shift)</summary>
          <table class="data-table">
            <thead><tr><th>Day</th><th>Shift</th><th>Required</th><th>Assigned</th><th>Status</th></tr></thead>
            <tbody>{rows_html}</tbody>
          </table>
        </details>"""

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<title>Roster — Visual Timetable</title>
<style>
  .viz-root {{
    color-scheme: light;
    --surface-1: #fcfcfb; --page: #f9f9f7;
    --text-primary: #0b0b0b; --text-secondary: #52514e; --text-muted: #898781;
    --gridline: #e1e0d9; --border: rgba(11,11,11,0.10);
    --weekend-tint: rgba(11,11,11,0.035);
  }}
  @media (prefers-color-scheme: dark) {{
    :root:where(:not([data-theme="light"])) .viz-root {{
      color-scheme: dark;
      --surface-1: #1a1a19; --page: #0d0d0d;
      --text-primary: #ffffff; --text-secondary: #c3c2b7; --text-muted: #898781;
      --gridline: #2c2c2a; --border: rgba(255,255,255,0.10);
      --weekend-tint: rgba(255,255,255,0.045);
    }}
  }}
  :root[data-theme="dark"] .viz-root {{
    color-scheme: dark;
    --surface-1: #1a1a19; --page: #0d0d0d;
    --text-primary: #ffffff; --text-secondary: #c3c2b7; --text-muted: #898781;
    --gridline: #2c2c2a; --border: rgba(255,255,255,0.10);
    --weekend-tint: rgba(255,255,255,0.045);
  }}
  * {{ box-sizing: border-box; }}
  html, body {{ margin: 0; font-family: system-ui, -apple-system, "Segoe UI", sans-serif; }}
  .viz-root {{ padding: 32px 20px 60px; background: var(--page); min-height: 100vh; }}
  .wrap {{ max-width: 1100px; margin: 0 auto; }}
  .topbar {{ display: flex; justify-content: space-between; align-items: flex-start; gap: 16px; margin-bottom: 16px; }}
  h1 {{ font-size: 1.3rem; font-weight: 700; color: var(--text-primary); margin: 0 0 4px; }}
  .subtitle {{ font-size: 0.88rem; color: var(--text-secondary); margin: 0; }}
  .theme-toggle {{ flex-shrink: 0; border: 1px solid var(--border); background: var(--surface-1);
    color: var(--text-secondary); border-radius: 7px; padding: 6px 12px; font-size: 0.78rem; cursor: pointer; }}
  .card {{ background: var(--surface-1); border: 1px solid var(--border); border-radius: 12px; padding: 18px 20px; overflow-x: auto; }}
  .legend {{ display: flex; flex-wrap: wrap; gap: 16px; margin-bottom: 14px; font-size: 0.82rem; color: var(--text-secondary); }}
  .legend-item {{ display: flex; align-items: center; gap: 7px; }}
  .swatch {{ width: 14px; height: 14px; border-radius: 4px; background: var(--cell-light); flex-shrink: 0; }}
  :root[data-theme="dark"] .swatch {{ background: var(--cell-dark); }}
  @media (prefers-color-scheme: dark) {{ :root:not([data-theme="light"]) .swatch {{ background: var(--cell-dark); }} }}
  table.roster {{ border-collapse: collapse; font-size: 0.78rem; }}
  table.roster th, table.roster td {{ text-align: center; padding: 6px 8px; border: 1px solid var(--gridline); min-width: 26px; }}
  table.roster th {{ color: var(--text-muted); font-weight: 600; font-size: 0.7rem; }}
  th.corner, td.staffcol {{ text-align: left; color: var(--text-primary); font-weight: 600; position: sticky; left: 0; background: var(--surface-1); }}
  th.weekend, td.weekend {{ background: var(--weekend-tint); }}
  td.off {{ color: var(--text-muted); }}
  td.shift {{ background: var(--cell-light); color: #fff; font-weight: 700; }}
  :root[data-theme="dark"] td.shift {{ background: var(--cell-dark); }}
  @media (prefers-color-scheme: dark) {{ :root:not([data-theme="light"]) td.shift {{ background: var(--cell-dark); }} }}
  .caption {{ font-size: 0.82rem; color: var(--text-secondary); margin-top: 14px; line-height: 1.5; }}
  details.tableview {{ margin-top: 20px; }}
  details.tableview summary {{ cursor: pointer; font-size: 0.85rem; color: var(--text-secondary); }}
  table.data-table {{ width: 100%; border-collapse: collapse; margin-top: 10px; font-size: 0.82rem; }}
  table.data-table th, table.data-table td {{ text-align: left; padding: 6px 10px; border-bottom: 1px solid var(--gridline); color: var(--text-secondary); }}
  .status-ok {{ color: var(--text-muted); }}
  .status-under {{ color: #d03b3b; font-weight: 600; }}
  .status-over {{ color: #eda100; font-weight: 600; }}
</style>
</head>
<body>
<div class="viz-root">
  <div class="wrap">
    <div class="topbar">
      <div>
        <h1>Optimized roster — visual timetable</h1>
        <p class="subtitle">{len(staff)} staff &times; {n_days} days. Shaded columns are Saturday/Sunday.</p>
      </div>
      <button class="theme-toggle" id="themeToggle" type="button">Dark mode</button>
    </div>
    <div class="card">
      <div class="legend">{legend_items}<div class="legend-item"><span class="swatch" style="background:transparent;border:1px solid var(--gridline)"></span>&middot; = day off</div></div>
      <table class="roster">
        <thead>{header_html}</thead>
        <tbody>{body_html}</tbody>
      </table>
      <p class="caption">Each cell shows the shift letter a nurse is assigned that day; a dot means a day off. Hover any cell for a plain-language readout.</p>
      {cover_html}
    </div>
  </div>
</div>
<script>
  var btn = document.getElementById('themeToggle');
  var root = document.documentElement;
  btn.addEventListener('click', function() {{
    var isDark = root.getAttribute('data-theme') === 'dark';
    root.setAttribute('data-theme', isDark ? 'light' : 'dark');
    btn.textContent = isDark ? 'Dark mode' : 'Light mode';
  }});
</script>
</body>
</html>"""


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("roster_csv")
    parser.add_argument("--cover", default=None,
                       help="path to cover.csv (e.g. data_bench/cover.csv) for a coverage check table")
    args = parser.parse_args()

    staff, day_cols, grid = load_roster(args.roster_csv)
    cover_rows = load_cover(args.cover)
    html = build_html(staff, day_cols, grid, cover_rows)

    out_path = os.path.join(os.path.dirname(os.path.abspath(args.roster_csv)) or ".", "roster_view.html")
    with open(out_path, "w") as f:
        f.write(html)
    print(f"Visual timetable written to {out_path} -- open it in a browser.")
