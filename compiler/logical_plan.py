"""
logical_plan.py  -  PHASE 4: INTERMEDIATE CODE GENERATION
=========================================================
Converts the resolved AST into a LOGICAL PLAN: a tree of relational algebra
operators. This tree is our compiler's Intermediate Representation (IR).

What is an Intermediate Representation?
    A form of the program that sits between the source language and the
    final output. A C compiler turns C into three-address code; we turn SQL
    into relational algebra. The IR is simple and uniform, which makes it
    easy for the optimizer (Phase 5) to analyze and rewrite.

The five operators (relational algebra symbol -> our class):

    Scan      R          read every row of table R              (leaf)
    σ  Filter            keep only rows where a condition holds
    ⋈  Join              combine rows of two inputs that match
    τ  Sort              order rows by a column
    π  Project           keep only the requested columns

Execution reads BOTTOM-UP: rows come out of the Scans at the leaves and
flow upward through each operator until they leave the Project at the top.

Example:
    SELECT name FROM employees WHERE salary > 50000 ORDER BY salary DESC

    π Project: employees.name
    └── τ Sort: employees.salary DESC
        └── σ Filter: employees.salary > 50000
            └── Scan: employees

    Relational algebra:
    π[employees.name](τ[employees.salary DESC](σ[employees.salary > 50000](employees)))

This phase builds the plan in a fixed, simple shape (it's "naive"). Making it
efficient is deliberately left to Phase 5, so each phase has one job.

Run this file directly to see it in action:
    python -m compiler.logical_plan
    python -m compiler.logical_plan "SELECT name FROM employees WHERE age < 30"
"""

import sys
from dataclasses import dataclass

from compiler.ast_nodes import ColumnRef, Comparison, Condition, LogicalOp, SelectQuery
from compiler.errors import CompilerError
from compiler.parser import parse
from compiler.schema import Schema
from compiler.semantic import analyze
from compiler.tree_printer import TreeNode, render_tree

# ---------------------------------------------------------------------------
# Helpers for working with WHERE conditions
# (Phase 5 relies heavily on condition_tables.)
# ---------------------------------------------------------------------------

def condition_columns(condition: Condition) -> list[ColumnRef]:
    """
    Return every column used anywhere inside a condition tree.

    Example: (salary > 5 AND name = 'A')  ->  [salary, name]
    """
    if isinstance(condition, Comparison):
        return [operand for operand in (condition.left, condition.right) if isinstance(operand, ColumnRef)]
    # A LogicalOp: collect from both sides.
    return condition_columns(condition.left) + condition_columns(condition.right)


def condition_tables(condition: Condition) -> set[str]:
    """
    Return the names of all tables a condition touches.

    Example: employees.salary > 5 AND departments.location = 'X'
             -> {"employees", "departments"}

    Columns must already be resolved (have their table filled in).
    """
    return {column.table for column in condition_columns(condition)}


def format_condition(condition: Condition) -> str:
    """
    Text for a condition without the redundant outer parentheses that
    LogicalOp.__str__ adds:  "(a AND b)"  ->  "a AND b".
    Inner parentheses are kept, because they show grouping.
    """
    text = str(condition)
    if isinstance(condition, LogicalOp):
        return text[1:-1]
    return text


# ---------------------------------------------------------------------------
# Logical plan nodes
#
# Every node class provides the same interface:
#     children      -> list of input nodes (empty for Scan)
#     tables        -> names of the tables whose rows flow out of this node
#     label()       -> one-line description for the tree view
#     to_algebra()  -> relational algebra text for this subtree
#
# Because they all share this interface, functions like plan_to_tree() work
# on ANY node without checking its type. This is called polymorphism.
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Scan:
    """
    Read all rows of one table. Always a leaf (it has no inputs).

    Attributes:
        table: Name of the table to read.
    """

    table: str

    @property
    def children(self) -> list["LogicalNode"]:
        """A Scan reads from storage, not from another operator."""
        return []

    @property
    def tables(self) -> frozenset[str]:
        """Rows coming out of a Scan belong to exactly one table."""
        return frozenset({self.table})

    def label(self) -> str:
        """Tree label, e.g. 'Scan: employees'."""
        return f"Scan: {self.table}"

    def to_algebra(self) -> str:
        """In relational algebra a table is written by its bare name."""
        return self.table


@dataclass(frozen=True)
class Filter:
    """
    σ (sigma), SELECTION in relational algebra: keep rows where `condition` is true.

    Careful: relational algebra's "selection" means FILTERING rows. It is the
    SQL WHERE clause, NOT the SQL SELECT keyword (that's projection, π).

    Attributes:
        condition: The (resolved) WHERE condition, or part of it.
        child:     The operator producing the input rows.
    """

    condition: Condition
    child: "LogicalNode"

    @property
    def children(self) -> list["LogicalNode"]:
        """One input."""
        return [self.child]

    @property
    def tables(self) -> frozenset[str]:
        """Filtering removes rows, not tables, so this is the child's set."""
        return self.child.tables

    def label(self) -> str:
        """Tree label, e.g. 'σ Filter: employees.salary > 50000'."""
        return f"σ Filter: {format_condition(self.condition)}"

    def to_algebra(self) -> str:
        """σ[condition](input)"""
        return f"σ[{format_condition(self.condition)}]({self.child.to_algebra()})"


@dataclass(frozen=True)
class Join:
    """
    ⋈ (bowtie), JOIN: pair up rows from two inputs where
    left_column = right_column.

    Attributes:
        left:         Input that produces the FROM table's rows.
        right:        Input that produces the JOIN table's rows.
        left_column:  Join column belonging to the left input.
        right_column: Join column belonging to the right input.
    """

    left: "LogicalNode"
    right: "LogicalNode"
    left_column: ColumnRef
    right_column: ColumnRef

    @property
    def children(self) -> list["LogicalNode"]:
        """Two inputs, left first."""
        return [self.left, self.right]

    @property
    def tables(self) -> frozenset[str]:
        """Joined rows contain columns from both sides. '|' is set union."""
        return self.left.tables | self.right.tables

    @property
    def condition_text(self) -> str:
        """The ON condition as text, e.g. 'employees.dept_id = departments.id'."""
        return f"{self.left_column} = {self.right_column}"

    def label(self) -> str:
        """Tree label, e.g. '⋈ Join: employees.dept_id = departments.id'."""
        return f"⋈ Join: {self.condition_text}"

    def to_algebra(self) -> str:
        """(left ⋈[condition] right)"""
        return f"({self.left.to_algebra()} ⋈[{self.condition_text}] {self.right.to_algebra()})"


@dataclass(frozen=True)
class Sort:
    """
    τ (tau), SORT: order rows by one column (from extended relational algebra).

    Attributes:
        column:     The column to sort by.
        descending: True for DESC, False for ASC.
        child:      The operator producing the input rows.
    """

    column: ColumnRef
    descending: bool
    child: "LogicalNode"

    @property
    def direction(self) -> str:
        """'DESC' or 'ASC'."""
        return "DESC" if self.descending else "ASC"

    @property
    def children(self) -> list["LogicalNode"]:
        """One input."""
        return [self.child]

    @property
    def tables(self) -> frozenset[str]:
        """Sorting reorders rows; the tables don't change."""
        return self.child.tables

    def label(self) -> str:
        """Tree label, e.g. 'τ Sort: employees.salary DESC'."""
        return f"τ Sort: {self.column} {self.direction}"

    def to_algebra(self) -> str:
        """τ[column direction](input)"""
        return f"τ[{self.column} {self.direction}]({self.child.to_algebra()})"


@dataclass(frozen=True)
class Project:
    """
    π (pi), PROJECTION: keep only the requested columns. This is SQL's SELECT list.

    Attributes:
        columns: The columns to output. An EMPTY tuple means "all columns" (SELECT *).
        child:   The operator producing the input rows.
    """

    columns: tuple[ColumnRef, ...]
    child: "LogicalNode"

    @property
    def is_select_all(self) -> bool:
        """True for SELECT *."""
        return not self.columns

    @property
    def columns_text(self) -> str:
        """'*' or a comma-separated column list."""
        if self.is_select_all:
            return "*"
        return ", ".join(str(column) for column in self.columns)

    @property
    def children(self) -> list["LogicalNode"]:
        """One input."""
        return [self.child]

    @property
    def tables(self) -> frozenset[str]:
        """The columns kept still come from the child's tables."""
        return self.child.tables

    def label(self) -> str:
        """Tree label, e.g. 'π Project: employees.name'."""
        return f"π Project: {self.columns_text}"

    def to_algebra(self) -> str:
        """π[columns](input)"""
        return f"π[{self.columns_text}]({self.child.to_algebra()})"


# Any logical plan node.
LogicalNode = Scan | Filter | Join | Sort | Project


# ---------------------------------------------------------------------------
# Building the plan
# ---------------------------------------------------------------------------

def _all_columns(query: SelectQuery) -> list[ColumnRef]:
    """Collect every ColumnRef that appears anywhere in the query."""
    columns = list(query.columns)
    if query.join is not None:
        columns += [query.join.left, query.join.right]
    if query.where is not None:
        columns += condition_columns(query.where)
    if query.order_by is not None:
        columns.append(query.order_by.column)
    return columns


def _require_resolved(query: SelectQuery) -> None:
    """
    Guard: the planner needs every column to know its table.

    If someone passes the raw parser output (skipping Phase 3), that's a
    PROGRAMMING mistake, not a user mistake, so we raise ValueError rather
    than a CompilerError.
    """
    for column in _all_columns(query):
        if column.table is None:
            raise ValueError(
                f"Column '{column}' has no table. build_logical_plan() needs the "
                "output of semantic analysis (Phase 3), not the raw parser output."
            )


def build_logical_plan(query: SelectQuery) -> LogicalNode:
    """
    Turn a resolved AST into a logical plan, built bottom-up.

    The shape is always:  Project( Sort( Filter( Join-or-Scan ) ) )
    with Sort and Filter included only if the query has ORDER BY / WHERE.

    Args:
        query: Output of semantic.analyze() (all columns resolved).

    Returns:
        The root node of the plan (always a Project).
    """
    _require_resolved(query)

    # Step 1, FROM / JOIN: where the rows come from (the leaves).
    plan: LogicalNode = Scan(query.from_table.name)

    if query.join is not None:
        # Normalise the ON condition so left_column belongs to the left (FROM) table.
        # "ON departments.id = employees.dept_id" is valid SQL, but the plan is
        # easier to read and execute if the sides always match the inputs.
        left_column, right_column = query.join.left, query.join.right
        if left_column.table != query.from_table.name:
            left_column, right_column = right_column, left_column

        plan = Join(
            left=plan,
            right=Scan(query.join.table.name),
            left_column=left_column,
            right_column=right_column,
        )

    # Step 2, WHERE: remove rows that don't match.
    if query.where is not None:
        plan = Filter(query.where, plan)

    # Step 3, ORDER BY: sort BEFORE projecting, because the sort column might
    # not be in the SELECT list (e.g. SELECT name ... ORDER BY salary).
    if query.order_by is not None:
        plan = Sort(query.order_by.column, query.order_by.descending, plan)

    # Step 4, SELECT: keep only the requested columns (always the root).
    return Project(query.columns, plan)


# ---------------------------------------------------------------------------
# Output helpers
# ---------------------------------------------------------------------------

def plan_to_tree(node: LogicalNode) -> TreeNode:
    """Convert a plan (recursively) into TreeNodes for printing."""
    return TreeNode(node.label(), [plan_to_tree(child) for child in node.children])


def format_logical_plan(node: LogicalNode) -> str:
    """Return the plan drawn as a text tree."""
    return render_tree(plan_to_tree(node))


def compile_to_logical_plan(source: str, schema: Schema | None = None) -> LogicalNode:
    """
    Run Phases 1-4 on a SQL string: lex, parse, analyze, build the logical plan.

    Raises:
        CompilerError (Lexer/Parser/Semantic/Schema) if the query is invalid.
    """
    return build_logical_plan(analyze(parse(source), schema))


# ---------------------------------------------------------------------------
# Demo: python -m compiler.logical_plan ["optional SQL here"]
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    DEFAULT_QUERY = (
        "SELECT name, dept_name FROM employees\n"
        "JOIN departments ON dept_id = departments.id\n"
        "WHERE salary > 50000 AND location = 'Hyderabad'\n"
        "ORDER BY salary DESC;"
    )
    query_text = " ".join(sys.argv[1:]) if len(sys.argv) > 1 else DEFAULT_QUERY

    print("Input SQL:")
    print(query_text)
    print()

    try:
        logical_plan = compile_to_logical_plan(query_text)
    except CompilerError as error:
        print(error.pretty(query_text))
        sys.exit(1)

    print("Logical plan (unoptimized; read bottom-up):")
    print(format_logical_plan(logical_plan))
    print()
    print("Relational algebra:")
    print(logical_plan.to_algebra())
