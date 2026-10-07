"""
test_physical_plan.py
=====================
Unit tests for Phase 6 (physical_plan.py).

Run from the project root with:
    pytest -v

Estimates are floats, so comparisons use pytest.approx(), which allows
tiny rounding differences (e.g. 333.3333 vs 1000/3).
"""

import math

import pytest

from compiler.ast_nodes import ColumnRef, Comparison, Literal, LogicalOp
from compiler.logical_plan import compile_to_logical_plan
from compiler.optimizer import optimize
from compiler.physical_plan import (
    FilterOp,
    HashJoin,
    NestedLoopJoin,
    ProjectOp,
    SeqScan,
    SortOp,
    build_physical_plan,
    compile_to_physical_plan,
    estimate_selectivity,
    format_estimate,
    format_physical_plan,
    sort_cost,
    total_cost,
)
from compiler.schema import Schema

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

JOIN_SQL = "SELECT * FROM employees JOIN departments ON dept_id = departments.id"


def physical(sql: str):
    """Helper: run all six phases against the test schema."""
    return compile_to_physical_plan(sql, TEST_SCHEMA)


def unoptimized_physical(sql: str):
    """Helper: physical plan built WITHOUT Phase 5, for comparisons."""
    return build_physical_plan(compile_to_logical_plan(sql, TEST_SCHEMA), TEST_SCHEMA)


def age_cmp(op: str, value: int = 30) -> Comparison:
    """A comparison on employees.age, for selectivity tests."""
    return Comparison(ColumnRef("employees", "age"), op, Literal(value))


# ---------------------------------------------------------------------------
# Selectivity and small helpers
# ---------------------------------------------------------------------------

def test_selectivity_of_single_comparisons():
    assert estimate_selectivity(age_cmp("=")) == pytest.approx(0.1)
    assert estimate_selectivity(age_cmp("!=")) == pytest.approx(0.9)
    for op in ["<", "<=", ">", ">="]:
        assert estimate_selectivity(age_cmp(op)) == pytest.approx(1 / 3)


def test_selectivity_of_and_multiplies():
    condition = LogicalOp("AND", age_cmp("="), age_cmp("<"))
    assert estimate_selectivity(condition) == pytest.approx(0.1 * (1 / 3))


def test_selectivity_of_or_uses_inclusion_exclusion():
    condition = LogicalOp("OR", age_cmp("="), age_cmp("<"))
    assert estimate_selectivity(condition) == pytest.approx(0.1 + 1 / 3 - 0.1 / 3)


def test_selectivity_of_constant_comparisons():
    assert estimate_selectivity(Comparison(Literal(1), "=", Literal(1))) == 1.0
    assert estimate_selectivity(Comparison(Literal(1), "=", Literal(2))) == 0.0


def test_sort_cost():
    assert sort_cost(0) == 0
    assert sort_cost(1) == 1
    assert sort_cost(8) == pytest.approx(8 * 3)   # log2(8) = 3


def test_format_estimate():
    assert format_estimate(0) == "0"
    assert format_estimate(0.2) == "1"          # anything above zero shows as at least 1
    assert format_estimate(1234.6) == "1,235"


# ---------------------------------------------------------------------------
# Single-table plans
# ---------------------------------------------------------------------------

def test_seq_scan_uses_row_count():
    plan = physical("SELECT * FROM employees").plan
    assert isinstance(plan, ProjectOp)
    scan = plan.child
    assert scan == SeqScan("employees", rows=1000, cost=1000)


def test_filter_estimates_rows_and_costs_its_input():
    plan = physical("SELECT * FROM employees WHERE age < 30").plan
    filter_op = plan.child
    assert isinstance(filter_op, FilterOp)
    assert filter_op.rows == pytest.approx(1000 / 3)
    assert filter_op.cost == 1000          # every input row is tested once


def test_sort_and_project():
    plan = physical("SELECT name FROM employees ORDER BY age DESC").plan
    sort_op = plan.child
    assert isinstance(sort_op, SortOp)
    assert sort_op.descending is True
    assert sort_op.rows == 1000
    assert sort_op.cost == pytest.approx(1000 * math.log2(1000))
    assert plan.rows == 1000               # projection keeps every row


def test_always_false_filter_estimates_zero_rows():
    plan = physical("SELECT * FROM employees WHERE 1 = 2").plan
    assert plan.child.rows == 0


# ---------------------------------------------------------------------------
# Joins
# ---------------------------------------------------------------------------

def test_join_row_estimate_for_foreign_key_join():
    # 1000 x 10 pairs, 1 in 10 match -> 1000 rows (each employee has one department).
    join = physical(JOIN_SQL).plan.child
    assert join.rows == pytest.approx(1000)


def test_large_unfiltered_join_uses_hash_join():
    result = physical(JOIN_SQL)
    join = result.plan.child
    assert isinstance(join, HashJoin)
    assert join.build_side == "right"               # departments (10 rows) is smaller
    assert join.cost == pytest.approx(2 * 10 + 1000)
    assert "Chose Hash Join" in result.decisions[0]


def test_tiny_input_uses_nested_loop_join():
    # After pushdown, departments shrinks to ~1 row: a nested loop is cheapest.
    result = physical(JOIN_SQL + " WHERE salary > 5 AND location = 'X'")
    join = result.plan.child
    assert isinstance(join, NestedLoopJoin)
    assert join.cost == pytest.approx((1000 / 3) * 1)
    assert "Chose Nested Loop Join" in result.decisions[0]


def test_hash_join_builds_on_the_smaller_left_side():
    # employees filtered to 1000 x 0.1 x 0.1 x 0.9 = 9 rows, fewer than departments (10),
    # and nested loop (9 x 10 = 90) costs more than hash (2 x 9 + 10 = 28).
    # (An earlier version used exactly 10 vs 10, but floating point made
    #  1000 x 0.1 x 0.1 = 10.000000000000002, so never test exact float ties.)
    result = physical(JOIN_SQL + " WHERE employees.id = 5 AND age = 3 AND salary != 7")
    join = result.plan.child
    assert isinstance(join, HashJoin)
    assert join.build_side == "left"
    assert "hash table on employees" in join.label()


def test_join_children_keep_logical_order():
    join = physical(JOIN_SQL).plan.child
    assert join.left.label() == "Seq Scan on employees"
    assert join.right.label() == "Seq Scan on departments"


# ---------------------------------------------------------------------------
# Costs, optimization benefit, and output
# ---------------------------------------------------------------------------

def test_total_cost_sums_every_node():
    plan = physical("SELECT * FROM employees WHERE age < 30").plan
    # Seq Scan 1000 + Filter 1000 + Project 333.33
    assert total_cost(plan) == pytest.approx(1000 + 1000 + 1000 / 3)


def test_optimization_lowers_estimated_cost():
    sql = JOIN_SQL + " WHERE salary > 50000 AND location = 'Hyderabad'"
    assert physical(sql).total_cost < unoptimized_physical(sql).total_cost


def test_no_join_means_no_decisions():
    assert physical("SELECT * FROM employees").decisions == ()


def test_tree_output():
    plan = physical("SELECT name FROM employees WHERE age < 30").plan
    expected = "\n".join([
        "Project: employees.name  (rows=333 cost=333)",
        "└── Filter: employees.age < 30  (rows=333 cost=1,000)",
        "    └── Seq Scan on employees  (rows=1,000 cost=1,000)",
    ])
    assert format_physical_plan(plan) == expected


def test_physical_plan_from_optimizer_result_matches_full_pipeline():
    sql = JOIN_SQL + " WHERE age < 30"
    by_hand = build_physical_plan(optimize(compile_to_logical_plan(sql, TEST_SCHEMA)).plan, TEST_SCHEMA)
    assert by_hand == physical(sql)
