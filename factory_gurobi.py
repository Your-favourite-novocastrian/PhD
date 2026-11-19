"""
Factory production LP — Gurobi (gurobipy) implementation.

The model (from our very first example):

    maximize    30x + 20y            (profit)
    subject to  4x + 3y <= 240       (wood)
                2x +  y <= 100       (labor)
                x, y >= 0

Known optimum: x = 30 tables, y = 40 chairs, profit = 1700.
"""

import gurobipy as gp
from gurobipy import GRB

# ---------------------------------------------------------------
# 1. Create the model object — the container everything lives in
# ---------------------------------------------------------------
m = gp.Model("factory")

# ---------------------------------------------------------------
# 2. Decision variables
#    lb=0 encodes the x,y >= 0 constraints (default vtype is
#    continuous; for an integer program use vtype=GRB.INTEGER,
#    for binary assignment vars like your x[i,d,s] use GRB.BINARY)
# ---------------------------------------------------------------
x = m.addVar(lb=0, name="tables")
y = m.addVar(lb=0, name="chairs")

# ---------------------------------------------------------------
# 3. Objective function
# ---------------------------------------------------------------
m.setObjective(30 * x + 20 * y, GRB.MAXIMIZE)

# ---------------------------------------------------------------
# 4. Constraints (kept in variables so we can query duals later)
# ---------------------------------------------------------------
wood  = m.addConstr(4 * x + 3 * y <= 240, name="wood")
labor = m.addConstr(2 * x + 1 * y <= 100, name="labor")

# ---------------------------------------------------------------
# 5. Solve
# ---------------------------------------------------------------
m.optimize()

# ---------------------------------------------------------------
# 6. Read the solution
# ---------------------------------------------------------------
if m.Status == GRB.OPTIMAL:
    print(f"\nOptimal profit : {m.ObjVal:.1f}")
    print(f"tables  x      : {x.X:.1f}")
    print(f"chairs  y      : {y.X:.1f}")
    # Shadow prices (LP duals): the marginal value of one extra
    # unit of each resource — only defined for continuous models.
    print(f"wood shadow price  : {wood.Pi:.2f}")
    print(f"labor shadow price : {labor.Pi:.2f}")
else:
    print(f"No optimal solution found (status {m.Status}).")

# ---------------------------------------------------------------
# The pattern that scales to your NRP: indexed variable families.
# Instead of one addVar per variable, build dict-indexed blocks:
#
#   nurses, days, shifts = range(10), range(28), ["M", "E", "N"]
#   x = m.addVars(nurses, days, shifts, vtype=GRB.BINARY, name="x")
#
#   # e.g. single-assignment constraint, one per (nurse, day):
#   m.addConstrs(
#       (x.sum(i, d, "*") <= 1 for i in nurses for d in days),
#       name="one_shift_per_day",
#   )
# ---------------------------------------------------------------
