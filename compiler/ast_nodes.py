"""
ast_nodes.py
============
The classes that make up the Abstract Syntax Tree (AST), the output of Phase 2.

What is an AST?
    A tree that captures the STRUCTURE and MEANING of the query, while dropping
    pure syntax noise like commas, the word "BY", or the semicolon.

    "SELECT name FROM employees WHERE salary > 50000"
    becomes
    SelectQuery(
        columns    = [ColumnRef(name)],
        from_table = TableRef(employees),
        where      = Comparison(ColumnRef(salary), ">", Literal(50000)),
    )

Design notes:
    * Every node is a frozen dataclass: read-only once built, so later phases
      can't accidentally change the tree.
    * line/column fields remember where each piece came from (for Phase 3's
      error messages). They use compare=False, so two nodes are "equal" when
      their CONTENT matches, regardless of position. That makes tests simple.
    * Every node has a to_tree() method that turns it into a printable
      TreeNode (see tree_printer.py).
"""

from dataclasses import dataclass, field

from compiler.tree_printer import TreeNode, render_tree


def position_field():
    """
    Create a dataclass field for line/column numbers.

    compare=False -> ignored by == (equality checks only look at content)
    repr=False    -> hidden when printing the node, to keep output short
    default=0     -> optional, so tests can build nodes without positions
    """
    return field(default=0, compare=False, repr=False)


# ---------------------------------------------------------------------------
# Leaf nodes: the smallest pieces
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class ColumnRef:
    """
    A reference to a column, either plain ("salary") or
    qualified with its table ("employees.salary").

    Attributes:
        table: The table prefix, or None if the user didn't write one.
        name:  The column name (always lowercase, from the lexer).
    """

    table: str | None
    name: str
    line: int = position_field()
    column: int = position_field()

    def __str__(self) -> str:
        """Display as 'table.column' or just 'column'."""
        return f"{self.table}.{self.name}" if self.table else self.name


@dataclass(frozen=True)
class Literal:
    """
    A constant value written directly in the query: 50000, 3.5, or 'Sales'.

    Attributes:
        value: An int, float, or str (strings are stored without quotes).
    """

    value: int | float | str
    line: int = position_field()
    column: int = position_field()

    def __str__(self) -> str:
        """Strings get their quotes back for display; numbers are shown as-is."""
        if isinstance(self.value, str):
            return f"'{self.value}'"
        return str(self.value)


# Either side of a comparison can be a column or a constant.
Operand = ColumnRef | Literal


@dataclass(frozen=True)
class TableRef:
    """A table name used after FROM or JOIN."""

    name: str
    line: int = position_field()
    column: int = position_field()

    def __str__(self) -> str:
        """Display the table name."""
        return self.name


# ---------------------------------------------------------------------------
# Condition nodes: used in WHERE
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Comparison:
    """
    A single comparison such as  salary > 50000  or  dept = 'Sales'.

    Attributes:
        left:     Left operand.
        operator: One of "=", "!=", "<", "<=", ">", ">=".
                  ("<>" from the input is normalised to "!=" by the parser.)
        right:    Right operand.
    """

    left: Operand
    operator: str
    right: Operand

    def __str__(self) -> str:
        """Display as 'left op right'."""
        return f"{self.left} {self.operator} {self.right}"

    def to_tree(self) -> TreeNode:
        """A comparison is a leaf in the printed tree."""
        return TreeNode(str(self))


@dataclass(frozen=True)
class LogicalOp:
    """
    Two conditions joined by AND or OR.

    Example: salary > 50000 AND dept = 'Sales'
        -> LogicalOp("AND", Comparison(...), Comparison(...))

    Longer chains nest: a AND b AND c -> LogicalOp(AND, LogicalOp(AND, a, b), c)

    Attributes:
        operator: "AND" or "OR".
        left:     Left condition (a Comparison or another LogicalOp).
        right:    Right condition.
    """

    operator: str
    left: "Condition"
    right: "Condition"

    def __str__(self) -> str:
        """Parentheses make the grouping unambiguous when printed."""
        return f"({self.left} {self.operator} {self.right})"

    def to_tree(self) -> TreeNode:
        """The operator becomes a parent node with the two conditions as children."""
        return TreeNode(self.operator, [self.left.to_tree(), self.right.to_tree()])


# A WHERE condition is either a single comparison or a combination of them.
Condition = Comparison | LogicalOp


# ---------------------------------------------------------------------------
# Clause nodes
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class JoinClause:
    """
    JOIN <table> ON <left column> = <right column>

    Only equality joins are supported (an "equi-join"), the most common kind.
    """

    table: TableRef
    left: ColumnRef
    right: ColumnRef

    def to_tree(self) -> TreeNode:
        """Show the joined table and the ON condition."""
        return TreeNode(
            "JOIN",
            [TreeNode(f"Table: {self.table}"), TreeNode(f"ON: {self.left} = {self.right}")],
        )


@dataclass(frozen=True)
class OrderBy:
    """
    ORDER BY <column> [ASC | DESC]

    Attributes:
        column:     The column to sort by.
        descending: True for DESC, False for ASC (the SQL default).
    """

    column: ColumnRef
    descending: bool = False

    @property
    def direction(self) -> str:
        """Return 'DESC' or 'ASC' as text."""
        return "DESC" if self.descending else "ASC"

    def to_tree(self) -> TreeNode:
        """Show the sort column and direction."""
        return TreeNode("ORDER BY", [TreeNode(f"{self.column} {self.direction}")])


# ---------------------------------------------------------------------------
# The root node
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class SelectQuery:
    """
    The root of the AST: one complete SELECT statement.

    Attributes:
        select_all: True for SELECT *, False when specific columns are listed.
        columns:    The listed columns (empty tuple when select_all is True).
                    A tuple (not a list) so the frozen node is fully read-only.
        from_table: The table after FROM.
        join:       The JOIN clause, or None.
        where:      The WHERE condition, or None.
        order_by:   The ORDER BY clause, or None.
    """

    select_all: bool
    columns: tuple[ColumnRef, ...]
    from_table: TableRef
    join: JoinClause | None = None
    where: Condition | None = None
    order_by: OrderBy | None = None

    def to_tree(self) -> TreeNode:
        """Build the full display tree; optional clauses appear only if present."""
        if self.select_all:
            select_children = [TreeNode("Column: *")]
        else:
            select_children = [TreeNode(f"Column: {col}") for col in self.columns]

        children = [
            TreeNode("SELECT", select_children),
            TreeNode("FROM", [TreeNode(f"Table: {self.from_table}")]),
        ]
        if self.join is not None:
            children.append(self.join.to_tree())
        if self.where is not None:
            children.append(TreeNode("WHERE", [self.where.to_tree()]))
        if self.order_by is not None:
            children.append(self.order_by.to_tree())

        return TreeNode("SelectQuery", children)


def format_ast(query: SelectQuery) -> str:
    """Return the AST drawn as a text tree (used by demos and the CLI)."""
    return render_tree(query.to_tree())
