"""
test_logical_plan.py
====================
Unit tests for Phase 4 (logical_plan.py).

Run from the project root with:
    pytest -v

Plan nodes are frozen dataclasses, so == compares whole trees. That lets us
write the expected plan by hand and compare it with the builder's output.
"""

from dataclasses import FrozenInstanceError

import pytest

from compiler.ast_nodes import ColumnRef, Comparison, Literal, LogicalOp
from compiler.logical_plan import (
    Filter,
    Join,
    Project,
    Scan,
    Sort,
    build_logical_plan,
    compile_to_logical_plan,
    condition_columns,
    condition_tables,
    format_condition,
    format_logical_plan,
)
from compiler.parser import parse
from compiler.schema import Schema

# Same small schema as the semantic tests, so these tests don't depend on data/schema.json.
TEST_SCHEMA = Schema.from_dict(
    {
        "tables": {
            "employees": {
                "columns": {"id": "INT", "name": "TEXT", "age": "INT", "salary": "INT", "dept_id": "INT"},
                "row_count": 1000,
            },
            "departments": {
                "columns": {"id": "INT", "dept_name": "TEXT", "location": "TEXT"},
                "row_count": 10,
            },
        }
    }
)


def plan(sql: str):
    """Helper: run Phases 1-4 against the test schema."""
    return compile_to_logical_plan(sql, TEST_SCHEMA)


def emp(column: str) -> ColumnRef:
    """Shortcut for an employees column."""
    return ColumnRef("employees", column)


def dept(column: str) -> ColumnRef:
    """Shortcut for a departments column."""
    return ColumnRef("departments", column)


# ---------------------------------------------------------------------------
# Plan shapes
# ---------------------------------------------------------------------------

def test_select_star_is_project_over_scan():
    assert plan("SELECT * FROM employees") == Project((), Scan("employees"))


def test_project_keeps_requested_columns():
    result = plan("SELECT name, salary FROM employees")
    assert result == Project((emp("name"), emp("salary")), Scan("employees"))


def test_where_adds_filter_below_project():
    result = plan("SELECT * FROM employees WHERE salary > 50000")
    condition = Comparison(emp("salary"), ">", Literal(50000))
    assert result == Project((), Filter(condition, Scan("employees")))


def test_order_by_adds_sort_below_project():
    result = plan("SELECT name FROM employees ORDER BY salary DESC")
    assert result == Project((emp("name"),), Sort(emp("salary"), True, Scan("employees")))


def test_join_has_two_scans():
    result = plan("SELECT * FROM employees JOIN departments ON dept_id = departments.id")
    expected_join = Join(Scan("employees"), Scan("departments"), emp("dept_id"), dept("id"))
    assert result == Project((), expected_join)


def test_join_condition_is_normalised_to_match_sides():
    # Written "backwards": departments column first.
    result = plan("SELECT * FROM employees JOIN departments ON departments.id = employees.dept_id")
    join = result.child
    assert join.left_column == emp("dept_id")
    assert join.right_column == dept("id")


def test_full_query_operator_order():
    result = plan(
        "SELECT name FROM employees JOIN departments ON dept_id = departments.id "
        "WHERE salary > 5 ORDER BY salary"
    )
    # Walk down from the root: Project -> Sort -> Filter -> Join -> (Scan, Scan)
    assert isinstance(result, Project)
    assert isinstance(result.child, Sort)
    assert isinstance(result.child.child, Filter)
    assert isinstance(result.child.child.child, Join)
    assert result.child.child.child.children == [Scan("employees"), Scan("departments")]


def test_whole_where_stays_in_one_filter_before_optimization():
    # Phase 4 is naive: the whole AND stays together above the join.
    result = plan(
        "SELECT * FROM employees JOIN departments ON dept_id = departments.id "
        "WHERE salary > 5 AND location = 'X'"
    )
    filter_node = result.child
    assert isinstance(filter_node, Filter)
    assert isinstance(filter_node.condition, LogicalOp)
    assert isinstance(filter_node.child, Join)


# ---------------------------------------------------------------------------
# The `tables` property
# ---------------------------------------------------------------------------

def test_tables_property():
    result = plan("SELECT * FROM employees JOIN departments ON dept_id = departments.id WHERE age > 1")
    assert result.tables == {"employees", "departments"}
    join = result.child.child
    assert join.left.tables == {"employees"}
    assert join.right.tables == {"departments"}


# ---------------------------------------------------------------------------
# Condition helpers
# ---------------------------------------------------------------------------

def test_condition_columns_and_tables():
    condition = LogicalOp(
        "AND",
        Comparison(emp("salary"), ">", Literal(5)),
        Comparison(dept("location"), "=", Literal("X")),
    )
    assert condition_columns(condition) == [emp("salary"), dept("location")]
    assert condition_tables(condition) == {"employees", "departments"}


def test_condition_with_only_literals_touches_no_tables():
    assert condition_tables(Comparison(Literal(1), "=", Literal(1))) == set()


def test_format_condition_strips_only_outer_parentheses():
    a = Comparison(emp("age"), "<", Literal(30))
    b = Comparison(emp("salary"), ">", Literal(5))
    c = Comparison(emp("name"), "=", Literal("A"))
    assert format_condition(a) == "employees.age < 30"
    assert format_condition(LogicalOp("OR", a, LogicalOp("AND", b, c))) == (
        "employees.age < 30 OR (employees.salary > 5 AND employees.name = 'A')"
    )


# ---------------------------------------------------------------------------
# Output formats
# ---------------------------------------------------------------------------

def test_relational_algebra_text():
    result = plan("SELECT name FROM employees WHERE salary > 50000 ORDER BY salary DESC")
    assert result.to_algebra() == (
        "π[employees.name](τ[employees.salary DESC](σ[employees.salary > 50000](employees)))"
    )


def test_relational_algebra_text_with_join():
    result = plan("SELECT * FROM employees JOIN departments ON dept_id = departments.id")
    assert result.to_algebra() == "π[*]((employees ⋈[employees.dept_id = departments.id] departments))"


def test_tree_output():
    result = plan(
        "SELECT name FROM employees JOIN departments ON dept_id = departments.id "
        "WHERE salary > 50000 ORDER BY salary DESC"
    )
    expected = "\n".join([
        "π Project: employees.name",
        "└── τ Sort: employees.salary DESC",
        "    └── σ Filter: employees.salary > 50000",
        "        └── ⋈ Join: employees.dept_id = departments.id",
        "            ├── Scan: employees",
        "            └── Scan: departments",
    ])
    assert format_logical_plan(result) == expected


# ---------------------------------------------------------------------------
# Safety
# ---------------------------------------------------------------------------

def test_unresolved_ast_is_rejected():
    raw_ast = parse("SELECT salary FROM employees")   # Phase 3 skipped on purpose
    with pytest.raises(ValueError) as error_info:
        build_logical_plan(raw_ast)
    assert "semantic analysis" in str(error_info.value)


def test_plan_nodes_are_read_only():
    node = Scan("employees")
    with pytest.raises(FrozenInstanceError):
        node.table = "departments"
