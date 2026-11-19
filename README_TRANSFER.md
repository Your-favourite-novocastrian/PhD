# TBL-HWSP — setting up on a new machine (OR-Tools only, no command prompt)

Everything is driven through Claude. You never type a command yourself; you tell Claude
what you want and it runs it.

## What's in this package

**9 code files + prices.csv** — the complete working set. Nothing else is needed to run.

| file | what it is |
|---|---|
| `tbl_data.py` | the data layer: instance parsing, the `Coef` price vector, `evaluate()`, `report()` |
| `tbl_ortools.py` | the CP-SAT solver runner — **this is the one you'll use** |
| `tbl_gurobi.py` | the Gurobi runner (same model, same flags). Harmless if Gurobi isn't installed |
| `prep_all.py` | converts `instances\*.txt` → `data\<Instance>\` and generates the synthetic nurse data |
| `run_all.py` | solves a batch and writes one summary table |
| `run_everything.py` | one command: prep → all batches → Excel workbook |
| `make_report.py` | builds `TBL_results.xlsx` from whatever is in `results\` |
| `check_setup.py` | **run this first.** Verifies nothing is missing, then proves the model gives 607 |
| `prices.csv` | every A$ coefficient. The only file you edit to change a price |

## What is NOT in this package, and why

**The 24 benchmark instance `.txt` files.** They are the published Curtois & Qu benchmark
data, not my code, and I can't reach that host from here. Get them either way:

- **Easiest:** in a browser on the new machine, download
  `https://www.schedulingbenchmarks.org/nrp/data/instances1_24.zip`,
  then attach the zip to Claude and ask it to extract into `instances\`.
  This is also the *better* option for the thesis — your pipeline then starts from the
  canonical published benchmark rather than a copy of a copy.
- **Or:** zip the `instances` folder from the old machine (right-click → Send to →
  Compressed folder) and attach that.

**`data\` — the converted instances and the synthetic nurse data.** `prep_all.py`
regenerates these from the `.txt` files, seeded per instance (`seed = 2026 + N`), so in
principle you get byte-identical nurses on any machine.

⚠️ In *principle*. `random.choices` and `normalvariate` internals have changed between
CPython versions, so a different Python could produce a different synthetic draw — and
then your two machines' cost numbers aren't comparable. `check_setup.py` prints a
**synthetic-data fingerprint**. Run it on both machines and compare. If the fingerprints
differ, copy `data\` across from the old machine instead of regenerating, and don't pass
`--force` to `prep_all`.

## Folder layout it expects

```
tbl_bench\
├── tbl_data.py, tbl_ortools.py, tbl_gurobi.py
├── prep_all.py, run_all.py, run_everything.py, make_report.py, check_setup.py
├── prices.csv
├── instances\          <- the 24 .txt files go here
├── data\               <- created by prep_all
├── results\            <- created by run_all
└── logs\               <- created by run_everything
```

## Order of operations

1. `check_setup.py` — must end with **ALL CHECKS PASSED** and **penalty 607, OPTIMAL**
2. `run_everything.py --no-gurobi --plan` — read the time estimate before committing
3. `run_everything.py --no-gurobi` — the actual run

Step 1 is not optional. It's the difference between finding a missing file in 5 seconds
and finding it 40 minutes into a batch.

## Two things to know before you start

**`--time` bounds the solver, not the wall clock.** Per instance you also pay model-build
time (0.1 s on Instance1, 25–40 s on the 100+ nurse instances) and, when a solve returns
UNKNOWN, a feasibility fallback of up to `min(600, max(30, 0.5 × --time))` more seconds.
`run_everything --plan` now accounts for all three, which is why its estimate is larger
than `24 × --time`.

**Gurobi.** `pip install gurobipy` gives a licence capped at 2000 variables and 2000
constraints — enough for Instance1–2 only (Instance1's model is 200 vars / 506
constraints; Instance3 is roughly 1500 / 3800). So use `--no-gurobi` for the batch. If you
want the cross-solver validation point, run instances 1–2 under Gurobi: both solvers agree
at **A$28,371.46** on Instance1 stage 3, which is worth one sentence in the methodology.
