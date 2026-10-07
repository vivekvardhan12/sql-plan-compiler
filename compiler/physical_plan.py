"""
physical_plan.py  -  PHASE 6: TARGET CODE GENERATION
====================================================
Turns the optimized LOGICAL plan (what to compute) into a PHYSICAL plan
(exactly HOW to compute it): a concrete algorithm for every operator, plus
an estimate of how many rows each step produces and how much work it costs.
This is our compiler's final output, the "execution plan".

Logical operator  ->  Physical operator (algorithm)
    Scan          ->  Seq Scan          read the table row by row, start to end
    σ Filter      ->  Filter            test each incoming row, pass the matches
    ⋈ Join        ->  Nested Loop Join  OR  Hash Join   (chosen by estimated cost)
    τ Sort        ->  Sort              sort all incoming rows
    π Project     ->  Project           keep only the requested columns

Like PostgreSQL's EXPLAIN, every node shows estimated rows and cost:

    Project: employees.name  (rows=111 cost=111)
    └── Filter: employees.salary > 50000  (rows=333 cost=1,000)
        └── Seq Scan on employees  (rows=1,000 cost=1,000)

The cost model (deliberately simple; 1 unit = touching 1 row once)
------------------------------------------------------------------
    Seq Scan          table rows
    Filter            input rows                      (each row is tested once)
    Nested Loop Join  left rows x right rows          (every pair is compared)
    Hash Join         2 x build rows + probe rows     (build hash table, then look up)
    Sort              n x log2(n)                     (comparison sort)
    Project           input rows

Row estimates use the textbook "default selectivity" rules (from IBM's
System R, the first SQL optimizer), because we have no real data statistics.

Run this file directly to see it in action:
    python -m compiler.physical_plan
    python -m compiler.physical_plan "SELECT * FROM employees WHERE age < 30"
"""

import math
import sys
from dataclasses import dataclass

from compiler.ast_nodes import ColumnRef, Comparison, Condition
from compiler.errors import CompilerError
from compiler.logical_plan import (
    Filter,
    Join,
    LogicalNode,
    Project,
    Scan,
    Sort,
    compile_to_logical_plan,
    format_condition,
)
from compiler.optimizer import fold_constants, optimize
from compiler.schema import Schema, load_schema
from compiler.tree_printer import TreeNode, render_tree

# ---------------------------------------------------------------------------
# Selectivity: the fraction of rows (0.0 to 1.0) expected to pass a condition
# ---------------------------------------------------------------------------

EQUALITY_SELECTIVITY = 0.1        # col = value    keeps ~1 row in 10
NOT_EQUAL_SELECTIVITY = 0.9       # col != value   keeps ~9 rows in 10
RANGE_SELECTIVITY = 1 / 3         # col < value    keeps ~1 row in 3

# Building a hash table entry (hash + store) costs more than one lookup.
HASH_BUILD_COST_PER_ROW = 2


def estimate_selectivity(condition: Condition) -> float:
    """
    Estimate what fraction of rows will satisfy `condition`.

    Rules:
        literal vs literal   -> exactly 1.0 (always true) or 0.0 (always false)
        col = x              -> 0.1
        col != x             -> 0.9
        col <, <=, >, >= x   -> 1/3
        A AND B              -> sel(A) x sel(B)               (assumes independence)
        A OR B               -> sel(A) + sel(B) - sel(A)xsel(B)  (inclusion-exclusion)
    """
    if isinstance(condition, Comparison):
        constant_value = fold_constants(condition)   # reuse Phase 5's evaluator
        if constant_value is True:
            return 1.0
        if constant_value is False:
            return 0.0
        if condition.operator == "=":
            return EQUALITY_SELECTIVITY
        if condition.operator == "!=":
            return NOT_EQUAL_SELECTIVITY
        return RANGE_SELECTIVITY

    left = estimate_selectivity(condition.left)
    right = estimate_selectivity(condition.right)
    if condition.operator == "AND":
        return left * right
    return left + right - left * right   # OR


def sort_cost(row_count: float) -> float:
    """Cost of sorting n rows: n x log2(n) comparisons (just n when n <= 2)."""
    if row_count <= 2:
        return row_count
    return row_count * math.log2(row_count)


def format_estimate(value: float) -> str:
    """
    Show an estimate as a whole number with thousands separators.

    Like PostgreSQL, anything above zero is shown as at least 1 (a plan saying
    "0 rows" would wrongly suggest certainty). A true 0.0 (always-false
    condition) stays 0.
    """
    if value == 0:
        return "0"
    return f"{max(1, round(value)):,}"


# ---------------------------------------------------------------------------
# Physical plan nodes
#
# Every node stores:
#     rows  -> estimated number of rows it OUTPUTS
#     cost  -> estimated work done by THIS operator alone (not its children)
# and provides `children` and `label()`, like the logical nodes.
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class SeqScan:
    """Sequential scan: read every row of a table from start to end."""

    table: str
    rows: float
    cost: float

    @property
    def children(self) -> list["PhysicalNode"]:
        """A scan reads from storage, so it has no input operators."""
        return []

    def label(self) -> str:
        """Tree label, e.g. 'Seq Scan on employees'."""
        return f"Seq Scan on {self.table}"


@dataclass(frozen=True)
class FilterOp:
    """Test each incoming row against `condition`; pass only matching rows."""

    condition: Condition
    child: "PhysicalNode"
    rows: float
    cost: float

    @property
    def children(self) -> list["PhysicalNode"]:
        """One input."""
        return [self.child]

    def label(self) -> str:
        """Tree label, e.g. 'Filter: employees.salary > 50000'."""
        return f"Filter: {format_condition(self.condition)}"


@dataclass(frozen=True)
class NestedLoopJoin:
    """
    For EVERY left row, loop over EVERY right row and keep matching pairs.

        for left_row in left:
            for right_row in right:
                if left_row[left_column] == right_row[right_column]:
                    output(left_row + right_row)

    Simple, needs no extra memory, but costs left x right comparisons.
    Best when at least one input is tiny.
    """

    left: "PhysicalNode"
    right: "PhysicalNode"
    left_column: ColumnRef
    right_column: ColumnRef
    rows: float
    cost: float

    @property
    def children(self) -> list["PhysicalNode"]:
        """Two inputs."""
        return [self.left, self.right]

    def label(self) -> str:
        """Tree label, e.g. 'Nested Loop Join: employees.dept_id = departments.id'."""
        return f"Nested Loop Join: {self.left_column} = {self.right_column}"


@dataclass(frozen=True)
class HashJoin:
    """
    Two phases:
        1. BUILD: put every row of the smaller input into a hash table,
                  keyed by its join column.
        2. PROBE: for each row of the other input, look up matches in the
                  hash table, which takes O(1) on average.

    Costs roughly (build + probe) instead of (left x right), at the price of
    memory for the hash table. Best when both inputs are large.

    Attributes:
        build_side: "left" or "right", whichever input fills the hash table.
    """

    left: "PhysicalNode"
    right: "PhysicalNode"
    left_column: ColumnRef
    right_column: ColumnRef
    build_side: str
    rows: float
    cost: float

    @property
    def children(self) -> list["PhysicalNode"]:
        """Two inputs."""
        return [self.left, self.right]

    @property
    def build_table_column(self) -> ColumnRef:
        """The join column of the build side (its table holds the hash table)."""
        return self.left_column if self.build_side == "left" else self.right_column

    def label(self) -> str:
        """Tree label, naming the table whose rows go into the hash table."""
        return (
            f"Hash Join: {self.left_column} = {self.right_column} "
            f"(hash table on {self.build_table_column.table})"
        )


@dataclass(frozen=True)
class SortOp:
    """Collect all incoming rows and sort them by one column."""

    column: ColumnRef
    descending: bool
    child: "PhysicalNode"
    rows: float
    cost: float

    @property
    def children(self) -> list["PhysicalNode"]:
        """One input."""
        return [self.child]

    def label(self) -> str:
        """Tree label, e.g. 'Sort: employees.salary DESC'."""
        direction = "DESC" if self.descending else "ASC"
        return f"Sort: {self.column} {direction}"


@dataclass(frozen=True)
class ProjectOp:
    """Output only the requested columns of each row (empty tuple = all columns)."""

    columns: tuple[ColumnRef, ...]
    child: "PhysicalNode"
    rows: float
    cost: float

    @property
    def children(self) -> list["PhysicalNode"]:
        """One input."""
        return [self.child]

    def label(self) -> str:
        """Tree label, e.g. 'Project: employees.name' or 'Project: *'."""
        columns_text = ", ".join(str(column) for column in self.columns) if self.columns else "*"
        return f"Project: {columns_text}"


# Any physical plan node.
PhysicalNode = SeqScan | FilterOp | NestedLoopJoin | HashJoin | SortOp | ProjectOp


def total_cost(node: PhysicalNode) -> float:
    """Cost of the whole subtree: this operator plus everything below it."""
    return node.cost + sum(total_cost(child) for child in node.children)


# ---------------------------------------------------------------------------
# The planner
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class PhysicalPlanResult:
    """
    What the physical planner returns.

    Attributes:
        plan:      Root of the physical plan.
        decisions: One readable sentence per algorithm choice made (e.g. joins).
    """

    plan: PhysicalNode
    decisions: tuple[str, ...]

    @property
    def total_cost(self) -> float:
        """Estimated cost of running the whole plan."""
        return total_cost(self.plan)


class PhysicalPlanner:
    """
    Converts a logical plan into a physical plan, bottom-up.

    Children are converted first, because a node's estimates depend on how
    many rows its inputs produce.
    """

    def __init__(self, schema: Schema):
        """
        Args:
            schema: Provides each table's row_count, the starting point
                    for every estimate.
        """
        self.schema = schema
        self.decisions: list[str] = []

    def build(self, logical_plan: LogicalNode) -> PhysicalPlanResult:
        """Convert the whole plan and return it with the decision log."""
        self.decisions = []
        physical_plan = self._convert(logical_plan)
        return PhysicalPlanResult(physical_plan, tuple(self.decisions))

    def _convert(self, node: LogicalNode) -> PhysicalNode:
        """Pick the right conversion for each logical node type."""
        if isinstance(node, Scan):
            row_count = self.schema.get_table(node.table).row_count
            return SeqScan(node.table, rows=row_count, cost=row_count)

        if isinstance(node, Filter):
            child = self._convert(node.child)
            rows = child.rows * estimate_selectivity(node.condition)
            return FilterOp(node.condition, child, rows=rows, cost=child.rows)

        if isinstance(node, Join):
            return self._convert_join(node)

        if isinstance(node, Sort):
            child = self._convert(node.child)
            return SortOp(node.column, node.descending, child, rows=child.rows, cost=sort_cost(child.rows))

        if isinstance(node, Project):
            child = self._convert(node.child)
            return ProjectOp(node.columns, child, rows=child.rows, cost=child.rows)

        raise TypeError(f"Unknown logical node: {node!r}")   # programming error guard

    def _join_selectivity(self, join: Join) -> float:
        """
        Fraction of all (left, right) row pairs expected to match.

        Textbook formula: 1 / (number of distinct values of the join key).
        Without real statistics we assume a foreign-key join, where the key
        has about as many distinct values as the SMALLER table has rows
        (e.g. dept_id -> departments.id: about 10 distinct departments).
        """
        left_table_rows = self.schema.get_table(join.left_column.table).row_count
        right_table_rows = self.schema.get_table(join.right_column.table).row_count
        distinct_keys = max(1, min(left_table_rows, right_table_rows))
        return 1 / distinct_keys

    def _convert_join(self, join: Join) -> PhysicalNode:
        """
        Estimate both join algorithms' costs and pick the cheaper one.
        This is a tiny piece of COST-BASED optimization.
        """
        left = self._convert(join.left)
        right = self._convert(join.right)
        rows = left.rows * right.rows * self._join_selectivity(join)

        nested_loop_cost = left.rows * right.rows

        # The smaller input is the cheaper one to load into a hash table.
        if left.rows <= right.rows:
            build_side, build_rows, probe_rows = "left", left.rows, right.rows
        else:
            build_side, build_rows, probe_rows = "right", right.rows, left.rows
        hash_join_cost = HASH_BUILD_COST_PER_ROW * build_rows + probe_rows

        comparison = (
            f"Nested Loop Join cost {format_estimate(nested_loop_cost)} vs "
            f"Hash Join cost {format_estimate(hash_join_cost)}"
        )

        if nested_loop_cost <= hash_join_cost:
            self.decisions.append(f"Chose Nested Loop Join ({comparison}): one input is small")
            return NestedLoopJoin(
                left, right, join.left_column, join.right_column, rows=rows, cost=nested_loop_cost
            )

        self.decisions.append(f"Chose Hash Join ({comparison}): avoids comparing every pair")
        return HashJoin(
            left, right, join.left_column, join.right_column,
            build_side=build_side, rows=rows, cost=hash_join_cost,
        )


# ---------------------------------------------------------------------------
# Output and convenience functions
# ---------------------------------------------------------------------------

def plan_to_tree(node: PhysicalNode) -> TreeNode:
    """Convert a physical plan into TreeNodes, with estimates on every line."""
    label = f"{node.label()}  (rows={format_estimate(node.rows)} cost={format_estimate(node.cost)})"
    return TreeNode(label, [plan_to_tree(child) for child in node.children])


def format_physical_plan(node: PhysicalNode) -> str:
    """Return the physical plan drawn as a text tree."""
    return render_tree(plan_to_tree(node))


def build_physical_plan(logical_plan: LogicalNode, schema: Schema) -> PhysicalPlanResult:
    """Shortcut: convert a logical plan with a fresh PhysicalPlanner."""
    return PhysicalPlanner(schema).build(logical_plan)


def compile_to_physical_plan(source: str, schema: Schema | None = None) -> PhysicalPlanResult:
    """
    Run ALL six phases on a SQL string and return the execution plan.

    The schema is loaded once here and shared by Phase 3 (checking names)
    and Phase 6 (row counts).

    Raises:
        CompilerError (Lexer/Parser/Semantic/Schema) if the query is invalid.
    """
    if schema is None:
        schema = load_schema()
    optimized = optimize(compile_to_logical_plan(source, schema))
    return build_physical_plan(optimized.plan, schema)


# ---------------------------------------------------------------------------
# Demo: python -m compiler.physical_plan ["optional SQL here"]
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    DEFAULT_QUERY = (
        "SELECT name, dept_name FROM employees\n"
        "JOIN departments ON dept_id = departments.id\n"
        "WHERE salary > 50000 AND location = 'Hyderabad' AND age < 40\n"
        "ORDER BY salary DESC;"
    )
    query_text = " ".join(sys.argv[1:]) if len(sys.argv) > 1 else DEFAULT_QUERY

    print("Input SQL:")
    print(query_text)
    print()

    try:
        demo_schema = load_schema()
        logical_plan = compile_to_logical_plan(query_text, demo_schema)
    except CompilerError as error:
        print(error.pretty(query_text))
        sys.exit(1)

    # Build a physical plan for BOTH versions, to show what optimization saved.
    unoptimized = build_physical_plan(logical_plan, demo_schema)
    optimized = build_physical_plan(optimize(logical_plan).plan, demo_schema)

    print("Execution plan (read bottom-up):")
    print(format_physical_plan(optimized.plan))
    print()
    print("Planner decisions:")
    if optimized.decisions:
        for number, decision in enumerate(optimized.decisions, start=1):
            print(f"  {number}. {decision}")
    else:
        print("  None: no join in this query.")
    print()
    print(f"Estimated total cost (optimized):   {format_estimate(optimized.total_cost)}")
    print(f"Estimated total cost (unoptimized): {format_estimate(unoptimized.total_cost)}")
    if optimized.total_cost > 0 and unoptimized.total_cost > optimized.total_cost:
        saving = (1 - optimized.total_cost / unoptimized.total_cost) * 100
        print(f"Optimization saved about {saving:.0f}% of the estimated work.")
