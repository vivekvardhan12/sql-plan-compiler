"""
semantic.py  -  PHASE 3: SEMANTIC ANALYSIS
==========================================
Checks that a grammatically correct query actually MAKES SENSE, using the
schema (our symbol table), and fills in missing information.

The parser only checks FORM. This query is perfectly grammatical:
    SELECT salry FROM employes WHERE salary > 'abc'
...but it is meaningless: 'employes' doesn't exist, 'salry' doesn't exist,
and a number can't be compared with text. Catching that is this phase's job.

Checks performed:
    1. Every table in FROM / JOIN exists.
    2. A table is not joined with itself (that would need aliases).
    3. Every column exists in a table that is part of the query.
    4. Unqualified columns are not ambiguous (e.g. 'id' in two joined tables).
    5. The JOIN condition links one column from EACH table.
    6. Both sides of every comparison have compatible types.

Resolution (the "fill in" part):
    Every ColumnRef in the output has its table filled in:
        salary  ->  employees.salary
    Phase 4 needs this to know which table each condition belongs to.

Order of checking follows SQL's LOGICAL processing order:
    FROM -> JOIN -> WHERE -> SELECT -> ORDER BY
FROM/JOIN go first because they decide which columns are visible to the rest.

Run this file directly to see it in action:
    python -m compiler.semantic
    python -m compiler.semantic "SELECT salry FROM employees"
"""

import sys
from dataclasses import replace
from difflib import get_close_matches

from compiler.ast_nodes import (
    ColumnRef,
    Comparison,
    Condition,
    JoinClause,
    Literal,
    LogicalOp,
    Operand,
    SelectQuery,
    TableRef,
    format_ast,
)
from compiler.errors import CompilerError, SemanticError
from compiler.parser import parse
from compiler.schema import DataType, Schema, Table, format_schema, load_schema


def did_you_mean(name: str, candidates: list[str]) -> str:
    """
    Suggest the closest valid name for a typo, e.g. "salry" -> " (did you mean 'salary'?)".

    Uses difflib (Python standard library), which scores how similar two
    strings are; cutoff=0.6 means "at least 60% similar".

    Returns:
        The suggestion text, or "" if nothing is close enough.
    """
    matches = get_close_matches(name, candidates, n=1, cutoff=0.6)
    return f" (did you mean '{matches[0]}'?)" if matches else ""


def literal_type(literal: Literal) -> DataType:
    """Return the DataType of a constant written in the query."""
    if isinstance(literal.value, str):
        return DataType.TEXT
    if isinstance(literal.value, float):
        return DataType.FLOAT
    return DataType.INT


class SemanticAnalyzer:
    """
    Validates a SelectQuery against a Schema and returns a resolved copy.

    The input AST is never modified (its nodes are frozen). Instead,
    dataclasses.replace() builds new nodes with the updated fields.
    """

    def __init__(self, schema: Schema):
        """
        Args:
            schema: The tables and columns that queries may use.
        """
        self.schema = schema
        # The tables named in FROM and JOIN of the query being analyzed.
        # Only columns of these tables are visible ("in scope").
        self.tables_in_scope: list[Table] = []

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def analyze(self, query: SelectQuery) -> SelectQuery:
        """
        Check the query and return a new AST where every column is qualified.

        Raises:
            SemanticError: on the first problem found.
        """
        self.tables_in_scope = []   # reset, so one analyzer can check many queries

        # 1. FROM (decides what is visible to everything else)
        from_table = self._require_table(query.from_table)
        self.tables_in_scope.append(from_table)

        # 2. JOIN (adds a second table to the scope)
        join = self._analyze_join(query.join) if query.join is not None else None

        # 3. WHERE
        where = self._analyze_condition(query.where) if query.where is not None else None

        # 4. SELECT
        columns = tuple(self._resolve_column(column) for column in query.columns)

        # 5. ORDER BY
        order_by = None
        if query.order_by is not None:
            order_by = replace(query.order_by, column=self._resolve_column(query.order_by.column))

        return replace(query, columns=columns, join=join, where=where, order_by=order_by)

    # ------------------------------------------------------------------
    # Tables
    # ------------------------------------------------------------------

    def _require_table(self, table_ref: TableRef) -> Table:
        """Return the schema Table for `table_ref`, or raise if it doesn't exist."""
        table = self.schema.get_table(table_ref.name)
        if table is None:
            raise SemanticError(
                f"Table '{table_ref.name}' does not exist"
                + did_you_mean(table_ref.name, self.schema.table_names),
                table_ref.line,
                table_ref.column,
            )
        return table

    def _find_table_in_scope(self, table_name: str) -> Table | None:
        """Return the in-scope Table with this name, or None."""
        for table in self.tables_in_scope:
            if table.name == table_name:
                return table
        return None

    def _scope_description(self) -> str:
        """Human-friendly list of in-scope tables, e.g. "tables 'employees' and 'departments'"."""
        names = [f"'{table.name}'" for table in self.tables_in_scope]
        if len(names) == 1:
            return f"table {names[0]}"
        return f"tables {' and '.join(names)}"

    # ------------------------------------------------------------------
    # JOIN
    # ------------------------------------------------------------------

    def _analyze_join(self, join: JoinClause) -> JoinClause:
        """Check the joined table and the ON condition; return the resolved JoinClause."""
        from_table = self.tables_in_scope[0]
        join_table = self._require_table(join.table)

        if join_table.name == from_table.name:
            raise SemanticError(
                f"Joining table '{join_table.name}' with itself needs table aliases, which are not supported",
                join.table.line,
                join.table.column,
            )
        self.tables_in_scope.append(join_table)

        left = self._resolve_column(join.left)
        right = self._resolve_column(join.right)

        # The ON condition must connect the two tables: one column from each.
        if {left.table, right.table} != {from_table.name, join_table.name}:
            raise SemanticError(
                f"The JOIN condition must compare a column of '{from_table.name}' "
                f"with a column of '{join_table.name}'",
                left.line,
                left.column,
            )

        self._check_comparable(left, right)
        return replace(join, left=left, right=right)

    # ------------------------------------------------------------------
    # WHERE conditions (recursive, because conditions form a tree)
    # ------------------------------------------------------------------

    def _analyze_condition(self, condition: Condition) -> Condition:
        """
        Resolve and type-check a condition tree.

        A Comparison is checked directly; a LogicalOp (AND/OR) is handled by
        analyzing both of its children. This recursion visits every node of
        the WHERE tree exactly once.
        """
        if isinstance(condition, Comparison):
            left = self._resolve_operand(condition.left)
            right = self._resolve_operand(condition.right)
            self._check_comparable(left, right)
            return replace(condition, left=left, right=right)

        # Otherwise it's a LogicalOp.
        return replace(
            condition,
            left=self._analyze_condition(condition.left),
            right=self._analyze_condition(condition.right),
        )

    def _resolve_operand(self, operand: Operand) -> Operand:
        """Columns get resolved; literals need nothing and are returned as-is."""
        if isinstance(operand, ColumnRef):
            return self._resolve_column(operand)
        return operand

    # ------------------------------------------------------------------
    # Types
    # ------------------------------------------------------------------

    def _operand_type(self, operand: Operand) -> DataType:
        """Return the DataType of a RESOLVED column or of a literal."""
        if isinstance(operand, Literal):
            return literal_type(operand)
        table = self._find_table_in_scope(operand.table)
        return table.column_type(operand.name)

    def _check_comparable(self, left: Operand, right: Operand) -> None:
        """Raise SemanticError if the two operands' types can't be compared."""
        left_type = self._operand_type(left)
        right_type = self._operand_type(right)
        if not left_type.is_comparable_with(right_type):
            raise SemanticError(
                f"Cannot compare {left} ({left_type.value}) with {right} ({right_type.value})",
                left.line,
                left.column,
            )

    # ------------------------------------------------------------------
    # Columns (the heart of name resolution)
    # ------------------------------------------------------------------

    def _resolve_column(self, column: ColumnRef) -> ColumnRef:
        """
        Make sure a column exists and return it with its table filled in.

        Two cases:
            employees.salary  (qualified)   -> check that exact table and column
            salary            (unqualified) -> search every in-scope table
        """
        if column.table is not None:
            return self._resolve_qualified_column(column)

        matching_tables = [table for table in self.tables_in_scope if table.has_column(column.name)]

        if not matching_tables:
            visible_columns = [name for table in self.tables_in_scope for name in table.column_names]
            raise SemanticError(
                f"Column '{column.name}' does not exist in {self._scope_description()}"
                + did_you_mean(column.name, visible_columns),
                column.line,
                column.column,
            )

        if len(matching_tables) > 1:
            table_list = " and ".join(f"'{table.name}'" for table in matching_tables)
            options = " or ".join(f"{table.name}.{column.name}" for table in matching_tables)
            raise SemanticError(
                f"Column '{column.name}' is ambiguous: it exists in {table_list}. Write {options}",
                column.line,
                column.column,
            )

        return replace(column, table=matching_tables[0].name)

    def _resolve_qualified_column(self, column: ColumnRef) -> ColumnRef:
        """Check a column written as table.column."""
        table = self._find_table_in_scope(column.table)

        if table is None:
            if self.schema.has_table(column.table):
                message = f"Table '{column.table}' is not part of this query; add it to FROM or JOIN"
            else:
                in_scope_names = [scope_table.name for scope_table in self.tables_in_scope]
                message = f"Unknown table '{column.table}' in '{column}'" + did_you_mean(
                    column.table, in_scope_names
                )
            raise SemanticError(message, column.line, column.column)

        if not table.has_column(column.name):
            raise SemanticError(
                f"Column '{column.name}' does not exist in table '{table.name}'"
                + did_you_mean(column.name, table.column_names),
                column.line,
                column.column,
            )

        return column


# ----------------------------------------------------------------------
# Convenience function used by the rest of the project
# ----------------------------------------------------------------------

def analyze(query: SelectQuery, schema: Schema | None = None) -> SelectQuery:
    """
    Run Phase 3 on an AST and return the resolved AST.

    Args:
        query:  Output of the parser.
        schema: The schema to check against (defaults to data/schema.json).
    """
    if schema is None:
        schema = load_schema()
    return SemanticAnalyzer(schema).analyze(query)


# ----------------------------------------------------------------------
# Demo: python -m compiler.semantic ["optional SQL here"]
# ----------------------------------------------------------------------

if __name__ == "__main__":
    DEFAULT_QUERY = (
        "SELECT name, dept_name FROM employees\n"
        "JOIN departments ON dept_id = departments.id\n"
        "WHERE salary > 50000 AND location = 'Hyderabad'\n"
        "ORDER BY salary DESC;"
    )
    query_text = " ".join(sys.argv[1:]) if len(sys.argv) > 1 else DEFAULT_QUERY

    try:
        schema = load_schema()
        print(format_schema(schema))
        print()
        print("Input SQL:")
        print(query_text)
        print()

        resolved_query = analyze(parse(query_text), schema)

        print("Semantic analysis passed: all tables and columns exist and all types match.")
        print()
        print("Resolved AST (every column now carries its table name):")
        print(format_ast(resolved_query))
    except CompilerError as error:   # Lexer, Parser, Semantic, or Schema errors
        print(error.pretty(query_text))
        sys.exit(1)
