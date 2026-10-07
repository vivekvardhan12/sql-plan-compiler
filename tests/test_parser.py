"""
test_parser.py
==============
Unit tests for Phase 2 (parser.py, ast_nodes.py, tree_printer.py).

Run from the project root with:
    pytest -v

Because AST nodes ignore line/column in == comparisons, we can build the
expected tree by hand and compare it directly with the parser's output.
"""

import pytest

from compiler.ast_nodes import (
    ColumnRef,
    Comparison,
    JoinClause,
    Literal,
    LogicalOp,
    OrderBy,
    TableRef,
    format_ast,
)
from compiler.errors import LexerError, ParserError
from compiler.parser import parse


# ---------------------------------------------------------------------------
# Helpers: short names for building expected nodes
# ---------------------------------------------------------------------------

def col(name: str, table: str | None = None) -> ColumnRef:
    """Build a ColumnRef without positions."""
    return ColumnRef(table=table, name=name)


def lit(value) -> Literal:
    """Build a Literal without positions."""
    return Literal(value)


# ---------------------------------------------------------------------------
# SELECT and FROM
# ---------------------------------------------------------------------------

def test_select_star():
    query = parse("SELECT * FROM employees")
    assert query.select_all is True
    assert query.columns == ()
    assert query.from_table == TableRef("employees")


def test_select_column_list():
    query = parse("SELECT name, salary, age FROM employees")
    assert query.select_all is False
    assert query.columns == (col("name"), col("salary"), col("age"))


def test_qualified_column():
    query = parse("SELECT employees.name FROM employees")
    assert query.columns == (col("name", table="employees"),)


def test_optional_clauses_default_to_none():
    query = parse("SELECT * FROM employees")
    assert query.join is None
    assert query.where is None
    assert query.order_by is None


def test_semicolon_is_optional():
    assert parse("SELECT * FROM t;") == parse("SELECT * FROM t")


def test_identifiers_are_lowercased():
    query = parse("SELECT Name FROM Employees")
    assert query.columns == (col("name"),)
    assert query.from_table == TableRef("employees")


# ---------------------------------------------------------------------------
# JOIN
# ---------------------------------------------------------------------------

def test_join_clause():
    query = parse("SELECT * FROM employees JOIN departments ON employees.dept_id = departments.id")
    assert query.join == JoinClause(
        table=TableRef("departments"),
        left=col("dept_id", "employees"),
        right=col("id", "departments"),
    )


# ---------------------------------------------------------------------------
# WHERE
# ---------------------------------------------------------------------------

def test_simple_where():
    query = parse("SELECT * FROM t WHERE salary > 50000")
    assert query.where == Comparison(col("salary"), ">", lit(50000))


def test_where_with_string_literal():
    query = parse("SELECT * FROM t WHERE dept = 'Sales'")
    assert query.where == Comparison(col("dept"), "=", lit("Sales"))


def test_literal_on_left_side():
    query = parse("SELECT * FROM t WHERE 50000 < salary")
    assert query.where == Comparison(lit(50000), "<", col("salary"))


def test_angle_bracket_not_equal_is_normalised():
    query = parse("SELECT * FROM t WHERE a <> 1")
    assert query.where.operator == "!="


def test_all_operators_parse():
    for symbol in ["=", "!=", "<", "<=", ">", ">="]:
        query = parse(f"SELECT * FROM t WHERE a {symbol} 1")
        assert query.where.operator == symbol


def test_and_binds_tighter_than_or():
    # a = 1 OR b = 2 AND c = 3   must mean   a = 1 OR (b = 2 AND c = 3)
    query = parse("SELECT * FROM t WHERE a = 1 OR b = 2 AND c = 3")
    a = Comparison(col("a"), "=", lit(1))
    b = Comparison(col("b"), "=", lit(2))
    c = Comparison(col("c"), "=", lit(3))
    assert query.where == LogicalOp("OR", a, LogicalOp("AND", b, c))


def test_and_chain_is_left_associative():
    # a AND b AND c   must mean   (a AND b) AND c
    query = parse("SELECT * FROM t WHERE a = 1 AND b = 2 AND c = 3")
    a = Comparison(col("a"), "=", lit(1))
    b = Comparison(col("b"), "=", lit(2))
    c = Comparison(col("c"), "=", lit(3))
    assert query.where == LogicalOp("AND", LogicalOp("AND", a, b), c)


# ---------------------------------------------------------------------------
# ORDER BY
# ---------------------------------------------------------------------------

def test_order_by_defaults_to_ascending():
    query = parse("SELECT * FROM t ORDER BY salary")
    assert query.order_by == OrderBy(col("salary"), descending=False)


def test_order_by_explicit_asc_and_desc():
    assert parse("SELECT * FROM t ORDER BY salary ASC").order_by.descending is False
    assert parse("SELECT * FROM t ORDER BY salary DESC").order_by.descending is True


# ---------------------------------------------------------------------------
# Full query + positions
# ---------------------------------------------------------------------------

def test_full_query_all_clauses():
    query = parse(
        "SELECT name, salary FROM employees "
        "JOIN departments ON employees.dept_id = departments.id "
        "WHERE salary > 50000 ORDER BY salary DESC;"
    )
    assert query.columns == (col("name"), col("salary"))
    assert query.from_table == TableRef("employees")
    assert query.join.table == TableRef("departments")
    assert query.where == Comparison(col("salary"), ">", lit(50000))
    assert query.order_by == OrderBy(col("salary"), descending=True)


def test_nodes_remember_their_position():
    query = parse("SELECT name\nFROM employees")
    assert (query.columns[0].line, query.columns[0].column) == (1, 8)
    assert (query.from_table.line, query.from_table.column) == (2, 6)


# ---------------------------------------------------------------------------
# Syntax errors
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "sql, expected_text",
    [
        ("", "start with SELECT"),
        ("FROM employees", "start with SELECT"),
        ("SELECT FROM employees", "column name or '*'"),
        ("SELECT name employees", "Expected FROM"),
        ("SELECT name, FROM employees", "column name after ','"),
        ("SELECT * FROM", "table name after FROM"),
        ("SELECT * FROM order", "table name after FROM"),
        ("SELECT * FROM t WHERE", "column name, number, or string"),
        ("SELECT * FROM t WHERE salary 5", "comparison operator"),
        ("SELECT * FROM t WHERE a = 1 AND", "column name, number, or string"),
        ("SELECT * FROM a JOIN b", "Expected ON"),
        ("SELECT * FROM a JOIN b ON a.x > b.y", "only equality joins"),
        ("SELECT * FROM t ORDER salary", "Expected BY"),
        ("SELECT * FROM t LIMIT 5", "end here"),
        ("SELECT * FROM t; SELECT", "end here"),
        ("SELECT employees. FROM t", "column name after '.'"),
    ],
)
def test_syntax_errors(sql, expected_text):
    with pytest.raises(ParserError) as error_info:
        parse(sql)
    assert expected_text in error_info.value.message


def test_error_reports_what_was_found():
    with pytest.raises(ParserError) as error_info:
        parse("SELECT name employees")
    assert "but found 'employees'" in error_info.value.message


def test_error_at_end_says_end_of_input():
    with pytest.raises(ParserError) as error_info:
        parse("SELECT * FROM")
    assert "end of input" in error_info.value.message


def test_error_position_points_at_bad_token():
    with pytest.raises(ParserError) as error_info:
        parse("SELECT name employees")
    assert (error_info.value.line, error_info.value.column) == (1, 13)


def test_lexer_errors_still_surface_through_parse():
    with pytest.raises(LexerError):
        parse("SELECT @ FROM t")


# ---------------------------------------------------------------------------
# Tree printing
# ---------------------------------------------------------------------------

def test_format_ast_output():
    query = parse("SELECT name FROM employees WHERE a = 1 OR b = 2 ORDER BY name")
    expected = "\n".join([
        "SelectQuery",
        "├── SELECT",
        "│   └── Column: name",
        "├── FROM",
        "│   └── Table: employees",
        "├── WHERE",
        "│   └── OR",
        "│       ├── a = 1",
        "│       └── b = 2",
        "└── ORDER BY",
        "    └── name ASC",
    ])
    assert format_ast(query) == expected
