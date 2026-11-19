"""
Factory production LP — CPLEX (docplex) implementation.

Same model:

    maximize    30x + 20y            (profit)
    subject to  4x + 3y <= 240       (wood)
                2x +  y <= 100       (labor)
                x, y >= 0

Known optimum: x = 30 tables, y = 40 chairs, profit = 1700.

docplex is IBM's Python modeling layer; it hands the model to the
CPLEX engine underneath (pip install cplex docplex).
"""

from docplex.mp.model import Model

# ---------------------------------------------------------------
# 1. Create the model object
# ---------------------------------------------------------------
m = Model(name="factory")

# ---------------------------------------------------------------
# 2. Decision variables
#    continuous_var encodes lb=0 by default; integer_var and
#    binary_var are the discrete counterparts (binary_var_dict
#    is what you'd use for an NRP's x[i,d,s]).
# ---------------------------------------------------------------
x = m.continuous_var(name="tables")
y = m.continuous_var(name="chairs")

# ---------------------------------------------------------------
# 3. Objective function
# ---------------------------------------------------------------
m.maximize(30 * x + 20 * y)

# ---------------------------------------------------------------
# 4. Constraints
# ---------------------------------------------------------------
wood  = m.add_constraint(4 * x + 3 * y <= 240, ctname="wood")
labor = m.add_constraint(2 * x + 1 * y <= 100, ctname="labor")

# ---------------------------------------------------------------
# 5. Solve
# ---------------------------------------------------------------
solution = m.solve()

# ---------------------------------------------------------------
# 6. Read the solution
# ---------------------------------------------------------------
if solution:
    print(f"\nOptimal profit : {m.objective_value:.1f}")
    print(f"tables  x      : {x.solution_value:.1f}")
    print(f"chairs  y      : {y.solution_value:.1f}")
    # Shadow prices (LP duals), marginal value of each resource:
    wood_dual, labor_dual = m.dual_values([wood, labor])
    print(f"wood shadow price  : {wood_dual:.2f}")
    print(f"labor shadow price : {labor_dual:.2f}")
else:
    print("No solution found:", m.solve_details.status)

# ---------------------------------------------------------------
# The pattern that scales to your NRP: indexed variable families.
#
#   nurses, days, shifts = range(10), range(28), ["M", "E", "N"]
#   x = m.binary_var_dict(
#       ((i, d, s) for i in nurses for d in days for s in shifts),
#       name="x",
#   )
#
#   # e.g. single-assignment constraint, one per (nurse, day):
#   for i in nurses:
#       for d in days:
#           m.add_constraint(m.sum(x[i, d, s] for s in shifts) <= 1)
# ---------------------------------------------------------------
