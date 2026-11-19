# tbl_bench — what's here and what each script does

TBL-HWSP: tri-objective nurse rostering (F_econ, F_soc, F_env, all in A$) on the
schedulingbenchmarks.org NRP instances (Instance1–24).

## The three solution methods

| Method | What it does | Script(s) | Output |
|---|---|---|---|
| **Weighted sum** | One solve per instance: minimise F_econ + F_soc + F_env (pillars converted to A$). Gives ONE roster. A carbon-price sweep re-solves at different carbon prices. | `tbl_gurobi.py`, `tbl_ortools.py`, batched by `run_all.py` | `results\summary_*.csv` → **`TBL_HWSP_results.xlsx`** (via `make_workbook.py`) |
| **ε-constraint, 2 objectives** | min (F_econ + F_soc) s.t. kg CO2e ≤ ε, swept over ε (AUGMECON2). Cost-vs-emissions frontier. | `tbl_epsilon.py` | `results\<Inst>_frontier.csv`, `.log` |
| **ε-constraint, 3 objectives** | min F_econ s.t. F_soc ≤ ε₁, F_env ≤ ε₂ over a K × K grid (AUGMECON2). Exact points on the 3-D Pareto front. | `tbl_epsilon3.py` | `results\<Inst>_front3.csv`, `.log` |
| **NSGA-II / NSGA-III** | Evolutionary multi-objective search. Approximates the whole Pareto front in one run; repeated over seeds. | `tbl_nsga.py` | everything in `results_nsga\` |

Shared by all of them: **`tbl_data.py`** — loads instances and prices, `Coef` (wages, carbon,
conversion factors), and `evaluate()`, which scores any roster. Every reported number in every
method comes from `evaluate()`, so the methods are directly comparable.

## Other scripts

| Script | Purpose |
|---|---|
| `run_everything.py` | Whole weighted-sum pipeline end to end (prep → run_all batches → carbon sweep → tables → workbook). `--plan` shows what it would run. Does **not** run ε-constraint or NSGA. |
| `run_all.py` | Weighted-sum batch over many instances (`--solver gurobi|ortools`, `--objective penalty|dollars`, `--levers`, `--carbon-sweep`). |
| `make_workbook.py` | Rebuilds `TBL_HWSP_results.xlsx` from `results\summary_*.csv`. |
| `plot_fronts.py` | Plots NSGA fronts + ε-constraint front + weighted-sum points on the same axes (interactive HTML + PNG). |

## Folders

| Folder | Written by | Notes |
|---|---|---|
| `results\` | weighted sum, ε-constraint | NSGA and the plot script only ever **read** this folder |
| `results_nsga\` | `tbl_nsga.py`, `plot_fronts.py` | One file set per instance + algorithm; re-running replaces it. `--out NAME` for a separate experiment. |
| `results_nsga\plots\` | `plot_fronts.py` | `.html` (interactive) and `.png` (for the thesis) |
| `results_nsga\cache\` | `tbl_nsga.py` | Starting rows per instance; safe to delete (rebuilt on next run) |

## Status (as of 7 Oct 2026)

| Method | Done | To do |
|---|---|---|
| Weighted sum (Gurobi, dollars + levers) | Instances 1–23 (Instance24: out of memory) | — |
| Weighted sum (OR-Tools) | 1–21, 23 (22 UNKNOWN, 24 ERROR) | — |
| ε-constraint, 3 objectives | Instances 1, 2 | 3–5 next; 6+ after confirmation (Instance9 was started on 23 Sep and stopped in the payoff table) |
| ε-constraint, 2 objectives | none found in `results\` | optional |
| NSGA-II | Instances 1–5, 5 seeds | 10+ seeds; larger instances |
| NSGA-III | — | same runs as NSGA-II |

## Commands I actually use

```
:: weighted sum, one instance
python tbl_gurobi.py --instance Instance1 --objective dollars --levers --gap 0.01 --time 600

:: 3-objective epsilon-constraint, small instances
python tbl_epsilon3.py --range 3 5 --K 6 --time 120

:: NSGA-II, instances 1-5, 10 seeds, 8 at a time
python tbl_nsga.py --range 1 5 --levers --seeds 10 --algo nsga2 --jobs 8

:: same for NSGA-III
python tbl_nsga.py --range 1 5 --levers --seeds 10 --algo nsga3 --jobs 8

:: rebuild the NSGA workbook without re-running
python tbl_nsga.py --report

:: plots (opens the interactive version in the browser)
python plot_fronts.py --instance 1
python plot_fronts.py --range 1 5 --no-show
```

## How to read the outputs

- **`TBL_HWSP_results.xlsx`** (weighted sum): cite *Corrected status*, not *Reported status*.
  Only Gap = 0 is proven optimal.
- **`results_nsga\TBL_NSGA_results.xlsx`** (NSGA): *Summary* sheet has median hypervolume
  (HV), Mann–Whitney p-value NSGA-II vs NSGA-III, and coverage against the ε-constraint front.
  HV is comparable within an instance only.
- **Best total** in the NSGA workbook = lowest F_econ + F_soc + F_env on the front. Compare it
  with the weighted-sum Total for the same instance.
- **Coverage**: *Covered by ε* = % of NSGA rosters matched or beaten by an exact ε roster
  (lower is better for NSGA); *Covers ε* = % of ε rosters NSGA matches or beats.

## Requirements

Python with `numpy`, `ortools`, `openpyxl`, `scipy`, `matplotlib`, `plotly`; `gurobipy` + a
licence for the Gurobi runs only. NSGA uses OR-Tools CP-SAT (free) for its starting rows and
feasibility checks — it does not need Gurobi. Nothing needs internet once installed.
