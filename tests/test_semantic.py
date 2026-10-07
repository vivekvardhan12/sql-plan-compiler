"""
test_semantic.py
================
Unit tests for Phase 3 (schema.py and semantic.py).

Run from the project root with:
    pytest -v

Most tests use a small schema built in code (the `schema` fixture), so they
keep working even if someone edits data/schema.json. One test loads the real
file to make sure it stays valid.
"""

import json

import pytest

from compiler.ast_nodes import ColumnRef, Comparison, Literal, LogicalOp
from compiler.errors import SchemaError, SemanticError
from compiler.parser import parse
from compiler.schema import DataType, Schema, load_schema
from compiler.semantic import analyze

# ---------------------------------------------------------------------------
# Test fixtures
# ---------------------------------------------------------------------------

TEST_SCHEMA_DATA = {
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


@pytest.fixture
def schema() -> Schema:
    """
    A pytest FIXTURE: any test that takes a parameter named `schema`
    automatically receives this freshly built object.
    """
    return Schema.from_dict(TEST_SCHEMA_DATA)


def check(sql: str, schema: Schema):
    """Helper: parse + analyze in one call."""
    return analyze(parse(sql), schema)


def semantic_error(sql: str, schema: Schema) -> SemanticError:
    """Helper: assert that `sql` fails semantic analysis and return the error."""
    with pytest.raises(SemanticError) as error_info:
        check(sql, schema)
    return error_info.value


JOIN = "SELECT * FROM employees JOIN departments ON employees.dept_id = departments.id"

# ---------------------------------------------------------------------------
# Schema loading and validation
# ---------------------------------------------------------------------------

def test_real_schema_file_loads():
    real_schema = load_schema()
    assert real_schema.has_table("employees")
    assert real_schema.has_table("departments")


def test_table_and_column_lookup(schema):
    employees = schema.get_table("employees")
    assert employees.has_column("salary")
    assert employees.column_type("salary") == DataType.INT
    assert employees.row_count == 1000
    assert schema.get_table("missing") is None


def test_schema_names_are_lowercased():
    data = {"tables": {"Employees": {"columns": {"Salary": "int"}}}}
    lowered = Schema.from_dict(data)
    assert lowered.get_table("employees").column_type("salary") == DataType.INT


def test_row_count_defaults_when_missing():
    data = {"tables": {"t": {"columns": {"a": "INT"}}}}
    assert Schema.from_dict(data).get_table("t").row_count == 1000


@pytest.mark.parametrize(
    "bad_data, expected_text",
    [
        ({}, '"tables"'),
        ({"tables": {}}, "at least one table"),
        ({"tables": {"t": {"columns": {}}}}, "non-empty"),
        ({"tables": {"t": {"columns": {"a": "DATE"}}}}, "unknown type"),
        ({"tables": {"t": {"columns": {"first name": "TEXT"}}}}, "not a valid column name"),
        ({"tables": {"select": {"columns": {"a": "INT"}}}}, "not a valid table name"),
        ({"tables": {"t": {"columns": {"a": "INT", "A": "INT"}}}}, "defined twice"),
        ({"tables": {"T": {"columns": {"a": "INT"}}, "t": {"columns": {"a": "INT"}}}}, "more than once"),
        ({"tables": {"t": {"columns": {"a": "INT"}, "row_count": -5}}}, "row_count"),
        ({"tables": {"t": {"columns": {"a": "INT"}, "row_count": True}}}, "row_count"),
    ],
)
def test_invalid_schemas_are_rejected(bad_data, expected_text):
    with pytest.raises(SchemaError) as error_info:
        Schema.from_dict(bad_data)
    assert expected_text in error_info.value.message


def test_missing_schema_file(tmp_path):
    # tmp_path is a built-in pytest fixture: a fresh, empty temporary folder.
    with pytest.raises(SchemaError) as error_info:
        load_schema(tmp_path / "nope.json")
    assert "not found" in error_info.value.message


def test_broken_json_file(tmp_path):
    bad_file = tmp_path / "schema.json"
    bad_file.write_text('{"tables": {', encoding="utf-8")
    with pytest.raises(SchemaError) as error_info:
        load_schema(bad_file)
    assert "Invalid JSON" in error_info.value.message


def test_schema_file_round_trip(tmp_path):
    good_file = tmp_path / "schema.json"
    good_file.write_text(json.dumps(TEST_SCHEMA_DATA), encoding="utf-8")
    assert load_schema(good_file).table_names == ["departments", "employees"]


# ---------------------------------------------------------------------------
# Type compatibility rules
# ---------------------------------------------------------------------------

def test_type_compatibility_rules():
    assert DataType.INT.is_comparable_with(DataType.INT)
    assert DataType.INT.is_comparable_with(DataType.FLOAT)
    assert DataType.TEXT.is_comparable_with(DataType.TEXT)
    assert not DataType.INT.is_comparable_with(DataType.TEXT)
    assert not DataType.TEXT.is_comparable_with(DataType.FLOAT)


# ---------------------------------------------------------------------------
# Valid queries and column resolution
# ---------------------------------------------------------------------------

def test_select_star_passes(schema):
    result = check("SELECT * FROM employees", schema)
    assert result.select_all is True


def test_unqualified_columns_get_their_table(schema):
    result = check("SELECT name, salary FROM employees", schema)
    assert result.columns == (ColumnRef("employees", "name"), ColumnRef("employees", "salary"))


def test_columns_from_joined_table_resolve(schema):
    result = check("SELECT name, dept_name FROM employees JOIN departments ON dept_id = departments.id", schema)
    assert result.columns == (ColumnRef("employees", "name"), ColumnRef("departments", "dept_name"))
    assert result.join.left == ColumnRef("employees", "dept_id")


def test_join_condition_may_be_written_in_either_order(schema):
    result = check("SELECT * FROM employees JOIN departments ON departments.id = employees.dept_id", schema)
    assert result.join.left == ColumnRef("departments", "id")


def test_where_columns_resolved_inside_and_or(schema):
    result = check("SELECT * FROM employees WHERE age < 30 OR salary > 5 AND name = 'A'", schema)
    expected = LogicalOp(
        "OR",
        Comparison(ColumnRef("employees", "age"), "<", Literal(30)),
        LogicalOp(
            "AND",
            Comparison(ColumnRef("employees", "salary"), ">", Literal(5)),
            Comparison(ColumnRef("employees", "name"), "=", Literal("A")),
        ),
    )
    assert result.where == expected


def test_order_by_column_resolved(schema):
    result = check("SELECT * FROM employees ORDER BY salary DESC", schema)
    assert result.order_by.column == ColumnRef("employees", "salary")
    assert result.order_by.descending is True


def test_int_compared_with_float_is_allowed(schema):
    check("SELECT * FROM employees WHERE salary > 3.5", schema)


def test_text_compared_with_text_is_allowed(schema):
    check("SELECT * FROM employees WHERE name = 'Alice'", schema)


def test_qualified_ambiguous_name_is_fine(schema):
    result = check(JOIN + " WHERE employees.id = 1", schema)
    assert result.where.left == ColumnRef("employees", "id")


def test_original_ast_is_not_modified(schema):
    original = parse("SELECT salary FROM employees")
    analyze(original, schema)
    assert original.columns[0].table is None   # still unqualified: frozen nodes were copied, not changed


# ---------------------------------------------------------------------------
# Semantic errors
# ---------------------------------------------------------------------------

def test_unknown_table_with_suggestion(schema):
    error = semantic_error("SELECT * FROM employes", schema)
    assert "Table 'employes' does not exist" in error.message
    assert "did you mean 'employees'" in error.message


def test_unknown_table_without_close_match(schema):
    error = semantic_error("SELECT * FROM xyz", schema)
    assert "did you mean" not in error.message


def test_unknown_column_with_suggestion(schema):
    error = semantic_error("SELECT salry FROM employees", schema)
    assert "Column 'salry' does not exist in table 'employees'" in error.message
    assert "did you mean 'salary'" in error.message


def test_column_from_other_table_not_visible(schema):
    # dept_name lives in departments, which isn't in this query.
    error = semantic_error("SELECT dept_name FROM employees", schema)
    assert "does not exist" in error.message


def test_ambiguous_column(schema):
    error = semantic_error(JOIN + " WHERE id = 1", schema)
    assert "ambiguous" in error.message
    assert "employees.id or departments.id" in error.message


def test_qualified_column_of_table_not_in_query(schema):
    error = semantic_error("SELECT departments.dept_name FROM employees", schema)
    assert "not part of this query" in error.message


def test_qualified_column_of_unknown_table(schema):
    error = semantic_error("SELECT emp.name FROM employees", schema)
    assert "Unknown table 'emp'" in error.message


def test_qualified_column_missing_from_table(schema):
    error = semantic_error("SELECT employees.dept_name FROM employees", schema)
    assert "does not exist in table 'employees'" in error.message


def test_unknown_join_table(schema):
    error = semantic_error("SELECT * FROM employees JOIN depts ON dept_id = depts.id", schema)
    assert "Table 'depts' does not exist" in error.message


def test_self_join_rejected(schema):
    error = semantic_error("SELECT * FROM employees JOIN employees ON id = id", schema)
    assert "with itself" in error.message


def test_join_condition_must_link_both_tables(schema):
    error = semantic_error(
        "SELECT * FROM employees JOIN departments ON employees.id = employees.dept_id", schema
    )
    assert "must compare a column of 'employees' with a column of 'departments'" in error.message


def test_join_type_mismatch(schema):
    error = semantic_error(
        "SELECT * FROM employees JOIN departments ON employees.name = departments.id", schema
    )
    assert "Cannot compare employees.name (TEXT) with departments.id (INT)" in error.message


def test_where_type_mismatch(schema):
    error = semantic_error("SELECT * FROM employees WHERE salary > 'abc'", schema)
    assert "Cannot compare employees.salary (INT) with 'abc' (TEXT)" in error.message


def test_type_mismatch_deep_inside_or(schema):
    error = semantic_error("SELECT * FROM employees WHERE age < 30 OR name > 5", schema)
    assert "Cannot compare employees.name (TEXT) with 5 (INT)" in error.message


def test_unknown_order_by_column(schema):
    error = semantic_error("SELECT * FROM employees ORDER BY salry", schema)
    assert "did you mean 'salary'" in error.message


def test_error_points_at_the_bad_column(schema):
    error = semantic_error("SELECT name,\n  salry FROM employees", schema)
    assert (error.line, error.column) == (2, 3)
