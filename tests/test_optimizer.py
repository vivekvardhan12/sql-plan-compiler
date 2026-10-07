"""
test_optimizer.py
=================
Unit tests for Phase 5 (optimizer.py).

Run from the project root with:
    pytest -v

Most tests compile a query, optimize it, and compare the resulting plan
tree with one written by hand.
"""

from compiler.ast_nodes import ColumnRef, Comparison, Literal, LogicalOp
from compiler.logical_plan import Filter, Join, Project, Scan, Sort, compile_to_logical_plan
from compiler.optimizer import (
    combine_conjuncts,
    compile_to_optimized_plan,
    fold_constants,
    optimize,
    split_conjuncts,
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


def optimized(sql: str):
    """Helper: compile + optimize against the test schema; returns OptimizationResult."""
    return compile_to_optimized_plan(sql, TEST_SCHEMA)


def emp(column: str) -> ColumnRef:
    """Shortcut for an employees column."""
    return ColumnRef("employees", column)


def dept(column: str) -> ColumnRef:
    """Shortcut for a departments column."""
    return ColumnRef("departments", column)


def cmp(left, op, right) -> Comparison:
    """Shortcut for a Comparison; plain Python values become Literals."""
    left = left if isinstance(left, ColumnRef) else Literal(left)
    right = right if isinstance(right, ColumnRef) else Literal(right)
    return Comparison(left, op, right)


def join_of(left, right) -> Join:
    """The standard employees-departments join with the given inputs."""
    return Join(left, right, emp("dept_id"), dept("id"))


# ---------------------------------------------------------------------------
# Helper functions
# ---------------------------------------------------------------------------

def test_split_conjuncts():
    a, b, c = cmp(emp("age"), "<", 1), cmp(emp("age"), "<", 2), cmp(emp("age"), "<", 3)
    assert split_conjuncts(LogicalOp("AND", LogicalOp("AND", a, b), c)) == [a, b, c]


def test_or_is_not_split():
    a, b = cmp(emp("age"), "<", 1), cmp(emp("age"), "<", 2)
    condition = LogicalOp("OR", a, b)
    assert split_conjuncts(condition) == [condition]


def test_combine_conjuncts_is_left_associative():
    a, b, c = cmp(emp("age"), "<", 1), cmp(emp("age"), "<", 2), cmp(emp("age"), "<", 3)
    assert combine_conjuncts([a, b, c]) == LogicalOp("AND", LogicalOp("AND", a, b), c)


def test_fold_constants_literal_comparisons():
    assert fold_constants(cmp(1, "=", 1)) is True
    assert fold_constants(cmp(1, "=", 2)) is False
    assert fold_constants(cmp(5, ">", 3.5)) is True
    assert fold_constants(cmp("a", "<", "b")) is True


def test_fold_constants_leaves_column_comparisons_alone():
    condition = cmp(emp("age"), "<", 30)
    assert fold_constants(condition) == condition


def test_fold_constants_logic_rules():
    a = cmp(emp("age"), "<", 30)
    true, false = cmp(1, "=", 1), cmp(1, "=", 2)
    assert fold_constants(LogicalOp("AND", a, true)) == a        # A AND True  -> A
    assert fold_constants(LogicalOp("AND", a, false)) is False   # A AND False -> False
    assert fold_constants(LogicalOp("OR", a, true)) is True      # A OR True   -> True
    assert fold_constants(LogicalOp("OR", a, false)) == a        # A OR False  -> A


# ---------------------------------------------------------------------------
# Plans that should NOT change
# ---------------------------------------------------------------------------

def test_no_where_means_no_changes():
    result = optimized(JOIN_SQL)
    assert result.plan == compile_to_logical_plan(JOIN_SQL, TEST_SCHEMA)
    assert result.applied_rules == ()


def test_single_table_filter_stays_on_scan():
    result = optimized("SELECT * FROM employees WHERE salary > 5 AND age < 30")
    expected_condition = LogicalOp("AND", cmp(emp("salary"), ">", 5), cmp(emp("age"), "<", 30))
    assert result.plan == Project((), Filter(expected_condition, Scan("employees")))
    assert result.applied_rules == ()


def test_input_plan_is_not_modified():
    original = compile_to_logical_plan(JOIN_SQL + " WHERE salary > 5", TEST_SCHEMA)
    snapshot = original
    optimize(original)
    assert original == snapshot
    assert isinstance(original.child, Filter)   # still the naive shape


# ---------------------------------------------------------------------------
# Predicate pushdown
# ---------------------------------------------------------------------------

def test_push_left_side_condition():
    result = optimized(JOIN_SQL + " WHERE salary > 5")
    expected = Project((), join_of(Filter(cmp(emp("salary"), ">", 5), Scan("employees")), Scan("departments")))
    assert result.plan == expected
    assert 'moved "employees.salary > 5" below the join' in result.applied_rules[0]


def test_push_right_side_condition():
    result = optimized(JOIN_SQL + " WHERE location = 'X'")
    expected = Project((), join_of(Scan("employees"), Filter(cmp(dept("location"), "=", "X"), Scan("departments"))))
    assert result.plan == expected


def test_and_is_split_across_both_sides():
    result = optimized(JOIN_SQL + " WHERE salary > 5 AND location = 'X'")
    expected = Project(
        (),
        join_of(
            Filter(cmp(emp("salary"), ">", 5), Scan("employees")),
            Filter(cmp(dept("location"), "=", "X"), Scan("departments")),
        ),
    )
    assert result.plan == expected
    assert len(result.applied_rules) == 2


def test_same_side_conditions_are_merged_in_original_order():
    result = optimized(JOIN_SQL + " WHERE salary > 5 AND location = 'X' AND age < 30")
    left = result.plan.child.left
    assert left == Filter(
        LogicalOp("AND", cmp(emp("salary"), ">", 5), cmp(emp("age"), "<", 30)),
        Scan("employees"),
    )


def test_three_same_side_conditions_flatten_left_associative():
    result = optimized(JOIN_SQL + " WHERE salary > 1 AND age < 2 AND employees.id = 3")
    left = result.plan.child.left
    expected_condition = LogicalOp(
        "AND",
        LogicalOp("AND", cmp(emp("salary"), ">", 1), cmp(emp("age"), "<", 2)),
        cmp(emp("id"), "=", 3),
    )
    assert left == Filter(expected_condition, Scan("employees"))


def test_or_across_tables_stays_above_join():
    result = optimized(JOIN_SQL + " WHERE age < 30 OR location = 'X'")
    filter_node = result.plan.child
    assert isinstance(filter_node, Filter)
    assert isinstance(filter_node.child, Join)
    assert "above the join" in result.applied_rules[0]


def test_or_on_one_table_is_pushed_whole():
    result = optimized(JOIN_SQL + " WHERE age < 30 OR salary > 9")
    join = result.plan.child
    assert isinstance(join, Join)
    assert isinstance(join.left, Filter)
    assert join.left.condition.operator == "OR"


def test_condition_comparing_both_tables_stays_above():
    result = optimized(JOIN_SQL + " WHERE employees.id > departments.id")
    assert isinstance(result.plan.child, Filter)
    assert isinstance(result.plan.child.child, Join)


def test_mixed_pushable_and_non_pushable():
    # The column-vs-column comparison uses both tables (stays up); salary > 5 goes down.
    result = optimized(JOIN_SQL + " WHERE salary > 5 AND employees.id > departments.id")
    top_filter = result.plan.child
    assert top_filter.condition == cmp(emp("id"), ">", dept("id"))
    assert top_filter.child.left == Filter(cmp(emp("salary"), ">", 5), Scan("employees"))


def test_sort_and_project_stay_on_top():
    result = optimized(
        "SELECT name FROM employees JOIN departments ON dept_id = departments.id "
        "WHERE location = 'X' ORDER BY salary DESC"
    )
    assert isinstance(result.plan, Project)
    assert isinstance(result.plan.child, Sort)
    assert isinstance(result.plan.child.child, Join)


# ---------------------------------------------------------------------------
# Constant folding in full queries
# ---------------------------------------------------------------------------

def test_always_true_where_removes_filter():
    result = optimized("SELECT * FROM employees WHERE 1 = 1")
    assert result.plan == Project((), Scan("employees"))
    assert "always true" in result.applied_rules[0]


def test_always_true_part_is_dropped():
    result = optimized("SELECT * FROM employees WHERE salary > 5 AND 1 = 1")
    assert result.plan == Project((), Filter(cmp(emp("salary"), ">", 5), Scan("employees")))
    assert "simplified" in result.applied_rules[0]


def test_or_with_always_true_removes_filter():
    result = optimized("SELECT * FROM employees WHERE age < 30 OR 1 = 1")
    assert result.plan == Project((), Scan("employees"))


def test_or_with_always_false_keeps_other_side():
    result = optimized("SELECT * FROM employees WHERE age < 30 OR 1 = 2")
    assert result.plan == Project((), Filter(cmp(emp("age"), "<", 30), Scan("employees")))


def test_always_false_where_is_kept_and_reported():
    result = optimized("SELECT * FROM employees WHERE salary > 5 AND 1 = 2")
    assert isinstance(result.plan.child, Filter)
    assert "always false" in result.applied_rules[0]


def test_folding_then_pushdown_together():
    result = optimized(JOIN_SQL + " WHERE location = 'X' AND 2 > 1")
    assert result.plan.child.right == Filter(cmp(dept("location"), "=", "X"), Scan("departments"))
    assert len(result.applied_rules) == 2   # one folding + one pushdown
