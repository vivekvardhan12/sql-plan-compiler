"""
test_cli.py
===========
Tests for compiler/pipeline.py and main.py (Module 7).

Run from the project root with:
    pytest -v

These are INTEGRATION tests: they run all six phases together, the same
way a user does, instead of testing one phase in isolation.

Two pytest fixtures appear here:
    capsys       captures everything printed, so tests can inspect output.
    monkeypatch  temporarily replaces a function (here: input()) during one test.
"""

from pathlib import Path

import pytest

import main
from compiler.errors import LexerError, ParserError, SemanticError
from compiler.pipeline import compile_query, split_statements
from compiler.schema import load_schema

PROJECT_ROOT = Path(__file__).resolve().parent.parent
QUERIES_FILE = PROJECT_ROOT / "examples" / "queries.sql"
ERRORS_FILE = PROJECT_ROOT / "examples" / "errors.sql"

JOIN_QUERY = (
    "SELECT name, dept_name FROM employees JOIN departments ON dept_id = departments.id "
    "WHERE salary > 50000 AND location = 'Hyderabad'"
)


@pytest.fixture(scope="module")
def schema():
    """The real schema file, loaded once for all tests in this file (scope='module')."""
    return load_schema()


# ---------------------------------------------------------------------------
# split_statements
# ---------------------------------------------------------------------------

def test_split_simple_statements():
    assert split_statements("SELECT * FROM a; SELECT * FROM b;") == ["SELECT * FROM a", "SELECT * FROM b"]


def test_split_without_trailing_semicolon():
    assert split_statements("SELECT * FROM a") == ["SELECT * FROM a"]


def test_semicolon_inside_string_does_not_split():
    assert split_statements("SELECT * FROM a WHERE x = 'p;q'; SELECT * FROM b") == [
        "SELECT * FROM a WHERE x = 'p;q'",
        "SELECT * FROM b",
    ]


def test_semicolon_inside_comment_does_not_split():
    assert split_statements("-- note; still a comment\nSELECT * FROM a;") == [
        "-- note; still a comment\nSELECT * FROM a"
    ]


def test_comment_only_and_empty_chunks_are_dropped():
    assert split_statements("-- just a comment\n;\n ; SELECT * FROM a;\n-- trailing") == ["SELECT * FROM a"]


def test_only_the_title_comment_is_kept():
    text = "-- file header\n--\n-- 1. Title\nSELECT * FROM a;"
    assert split_statements(text) == ["-- 1. Title\nSELECT * FROM a"]


# ---------------------------------------------------------------------------
# compile_query (the pipeline)
# ---------------------------------------------------------------------------

def test_successful_compilation_fills_every_phase(schema):
    result = compile_query(JOIN_QUERY, schema)
    assert result.succeeded
    assert result.error is None
    assert result.failed_phase_description is None
    assert result.tokens and result.ast and result.resolved_ast
    assert result.logical_plan and result.optimization and result.physical and result.unoptimized_physical
    assert result.elapsed_ms >= 0


def test_lexer_error_stops_at_phase_1(schema):
    result = compile_query("SELECT @ FROM employees", schema)
    assert isinstance(result.error, LexerError)
    assert result.tokens is None
    assert result.failed_phase_description == "Phase 1 (Lexical Analysis)"


def test_parser_error_keeps_tokens(schema):
    result = compile_query("SELECT FROM employees", schema)
    assert isinstance(result.error, ParserError)
    assert result.tokens is not None
    assert result.ast is None


def test_semantic_error_keeps_tokens_and_ast(schema):
    result = compile_query("SELECT salry FROM employees", schema)
    assert isinstance(result.error, SemanticError)
    assert result.ast is not None
    assert result.resolved_ast is None
    assert result.failed_phase_description == "Phase 3 (Semantic Analysis)"


# ---------------------------------------------------------------------------
# main(): single query
# ---------------------------------------------------------------------------

def test_single_query_shows_all_six_phases(capsys):
    exit_code = main.main([JOIN_QUERY])
    output = capsys.readouterr().out
    assert exit_code == main.EXIT_OK
    for phase_number in range(1, 7):
        assert f"[Phase {phase_number}]" in output
    assert "Compiled successfully" in output
    assert "Predicate pushdown" in output


def test_show_option_limits_output(capsys):
    main.main([JOIN_QUERY, "--show", "physical"])
    output = capsys.readouterr().out
    assert "[Phase 6]" in output
    assert "[Phase 1]" not in output
    assert "[Phase 4]" not in output


def test_show_option_keeps_pipeline_order(capsys):
    main.main([JOIN_QUERY, "--show", "physical", "tokens"])
    output = capsys.readouterr().out
    assert output.index("[Phase 1]") < output.index("[Phase 6]")


def test_invalid_query_returns_exit_code_1(capsys):
    exit_code = main.main(["SELECT name FROM employes"])
    output = capsys.readouterr().out
    assert exit_code == main.EXIT_COMPILE_ERROR
    assert "failed during Phase 3 (Semantic Analysis)" in output
    assert "did you mean 'employees'" in output
    assert "^" in output                      # caret under the problem
    assert "[Phase 2]" in output              # phases before the error are still shown


def test_unknown_show_choice_is_a_usage_error():
    # argparse exits with code 2 by raising SystemExit.
    with pytest.raises(SystemExit) as exit_info:
        main.main(["SELECT * FROM employees", "--show", "everything"])
    assert exit_info.value.code == 2


def test_query_and_file_together_is_a_usage_error(capsys):
    assert main.main(["SELECT * FROM employees", "--file", str(QUERIES_FILE)]) == main.EXIT_USAGE_ERROR


# ---------------------------------------------------------------------------
# main(): files and schema options
# ---------------------------------------------------------------------------

def test_example_queries_all_compile(capsys):
    exit_code = main.main(["--file", str(QUERIES_FILE), "--show", "physical"])
    output = capsys.readouterr().out
    assert exit_code == main.EXIT_OK
    assert "8 compiled, 0 failed" in output


def test_example_errors_all_fail(capsys):
    exit_code = main.main(["--file", str(ERRORS_FILE), "--show", "tokens"])
    output = capsys.readouterr().out
    assert exit_code == main.EXIT_COMPILE_ERROR
    assert "0 compiled, 10 failed" in output
    for phase in ("Phase 1", "Phase 2", "Phase 3"):
        assert f"failed during {phase}" in output   # every kind of error is demonstrated


def test_missing_file(capsys):
    assert main.main(["--file", "does_not_exist.sql"]) == main.EXIT_USAGE_ERROR
    assert "file not found" in capsys.readouterr().err


def test_file_with_no_statements(tmp_path, capsys):
    empty_file = tmp_path / "empty.sql"
    empty_file.write_text("-- nothing here\n", encoding="utf-8")
    assert main.main(["--file", str(empty_file)]) == main.EXIT_USAGE_ERROR


def test_bad_schema_path(capsys):
    assert main.main(["--schema", "missing.json", "SELECT * FROM employees"]) == main.EXIT_USAGE_ERROR
    assert "Schema file not found" in capsys.readouterr().err


def test_show_schema(capsys):
    assert main.main(["--show-schema"]) == main.EXIT_OK
    output = capsys.readouterr().out
    assert "employees (1,000 rows)" in output
    assert "dept_name: TEXT" in output


# ---------------------------------------------------------------------------
# main(): interactive mode
# ---------------------------------------------------------------------------

def fake_input(lines):
    """Return a replacement for input() that hands out `lines` one at a time."""
    remaining = iter(lines)

    def _input(prompt=""):
        try:
            return next(remaining)
        except StopIteration:
            raise EOFError   # same as the user pressing Ctrl+Z / Ctrl+D
    return _input


def test_interactive_mode(monkeypatch, capsys):
    monkeypatch.setattr("builtins.input", fake_input(
        ["", "help", "schema", "SELECT name FROM employees WHERE age < 30", "exit"]
    ))
    assert main.main([]) == main.EXIT_OK
    output = capsys.readouterr().out
    assert "interactive mode" in output
    assert "employees (1,000 rows)" in output
    assert "Compiled successfully" in output
    assert "Goodbye!" in output


def test_interactive_mode_ends_on_end_of_input(monkeypatch, capsys):
    monkeypatch.setattr("builtins.input", fake_input([]))
    assert main.main([]) == main.EXIT_OK
    assert "Goodbye!" in capsys.readouterr().out


def test_interactive_mode_survives_errors(monkeypatch, capsys):
    monkeypatch.setattr("builtins.input", fake_input(["SELECT @", "SELECT * FROM employees", "quit"]))
    assert main.main([]) == main.EXIT_OK
    output = capsys.readouterr().out
    assert "Lexer Error" in output
    assert "Compiled successfully" in output
