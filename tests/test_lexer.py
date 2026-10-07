"""
test_lexer.py
=============
Unit tests for Phase 1 (lexer.py).

Run from the project root with:
    pytest -v

Each test checks ONE behaviour, so when a test fails, its name tells you
exactly what broke.
"""

import pytest

from compiler.errors import LexerError
from compiler.lexer import tokenize
from compiler.tokens import TokenType as T


def token_types(sql: str) -> list[T]:
    """Helper: tokenize `sql` and return only the token types, without the final EOF."""
    return [token.type for token in tokenize(sql)][:-1]


# ---------------------------------------------------------------------------
# Basic token categories
# ---------------------------------------------------------------------------

def test_empty_input_produces_only_eof():
    tokens = tokenize("")
    assert len(tokens) == 1
    assert tokens[0].type == T.EOF


def test_eof_is_always_last():
    assert tokenize("SELECT * FROM t")[-1].type == T.EOF


def test_keywords_are_case_insensitive():
    assert token_types("select FROM WhErE") == [T.SELECT, T.FROM, T.WHERE]


def test_all_keywords_recognised():
    sql = "SELECT FROM WHERE JOIN ON AND OR ORDER BY ASC DESC"
    assert token_types(sql) == [
        T.SELECT, T.FROM, T.WHERE, T.JOIN, T.ON, T.AND, T.OR,
        T.ORDER, T.BY, T.ASC, T.DESC,
    ]


def test_identifier_value_is_lowercase_but_lexeme_is_original():
    token = tokenize("Employees")[0]
    assert token.type == T.IDENTIFIER
    assert token.lexeme == "Employees"
    assert token.value == "employees"


def test_identifier_with_underscore_and_digits():
    token = tokenize("dept_id2")[0]
    assert token.type == T.IDENTIFIER
    assert token.value == "dept_id2"


def test_keyword_inside_longer_word_is_identifier():
    # "selection" starts with "select" but is a name, not the keyword.
    token = tokenize("selection")[0]
    assert token.type == T.IDENTIFIER


def test_integer_and_decimal_numbers():
    tokens = tokenize("50000 3.5")
    assert tokens[0].type == T.NUMBER and tokens[0].value == 50000
    assert isinstance(tokens[0].value, int)
    assert tokens[1].type == T.NUMBER and tokens[1].value == 3.5
    assert isinstance(tokens[1].value, float)


def test_number_followed_by_dot_without_digits():
    # "3." is NUMBER(3) then DOT, because a digit must follow the decimal point.
    assert token_types("3.") == [T.NUMBER, T.DOT]


def test_string_value_has_no_quotes():
    token = tokenize("'Sales'")[0]
    assert token.type == T.STRING
    assert token.value == "Sales"
    assert token.lexeme == "'Sales'"


def test_string_keeps_spaces_and_case():
    assert tokenize("'Human Resources'")[0].value == "Human Resources"


# ---------------------------------------------------------------------------
# Operators and punctuation
# ---------------------------------------------------------------------------

def test_all_comparison_operators():
    assert token_types("= != <> < <= > >=") == [
        T.EQUAL, T.NOT_EQUAL, T.NOT_EQUAL, T.LESS,
        T.LESS_EQUAL, T.GREATER, T.GREATER_EQUAL,
    ]


def test_two_char_operators_without_spaces():
    # Maximal munch: "a<=5" must give LESS_EQUAL, not LESS then EQUAL.
    assert token_types("a<=5") == [T.IDENTIFIER, T.LESS_EQUAL, T.NUMBER]


def test_punctuation():
    assert token_types(", . * ;") == [T.COMMA, T.DOT, T.STAR, T.SEMICOLON]


def test_qualified_column_name():
    assert token_types("employees.salary") == [T.IDENTIFIER, T.DOT, T.IDENTIFIER]


# ---------------------------------------------------------------------------
# Whitespace, comments, and positions
# ---------------------------------------------------------------------------

def test_comments_are_skipped():
    sql = "SELECT * -- this is a comment\nFROM t"
    assert token_types(sql) == [T.SELECT, T.STAR, T.FROM, T.IDENTIFIER]


def test_line_and_column_tracking():
    tokens = tokenize("SELECT\n  name")
    name_token = tokens[1]
    assert name_token.line == 2
    assert name_token.column == 3


def test_full_query():
    sql = (
        "SELECT name, salary FROM employees "
        "JOIN departments ON employees.dept_id = departments.id "
        "WHERE salary > 50000 ORDER BY salary DESC;"
    )
    assert token_types(sql) == [
        T.SELECT, T.IDENTIFIER, T.COMMA, T.IDENTIFIER, T.FROM, T.IDENTIFIER,
        T.JOIN, T.IDENTIFIER, T.ON,
        T.IDENTIFIER, T.DOT, T.IDENTIFIER, T.EQUAL, T.IDENTIFIER, T.DOT, T.IDENTIFIER,
        T.WHERE, T.IDENTIFIER, T.GREATER, T.NUMBER,
        T.ORDER, T.BY, T.IDENTIFIER, T.DESC, T.SEMICOLON,
    ]


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------

def test_unexpected_character_reports_position():
    with pytest.raises(LexerError) as error_info:
        tokenize("SELECT name FROM @employees")
    assert error_info.value.line == 1
    assert error_info.value.column == 18
    assert "'@'" in error_info.value.message


def test_unterminated_string():
    with pytest.raises(LexerError) as error_info:
        tokenize("WHERE dept = 'Sales")
    assert "Unterminated string" in error_info.value.message


def test_number_followed_by_letters_is_error():
    with pytest.raises(LexerError) as error_info:
        tokenize("SELECT 12abc")
    assert "12abc" in error_info.value.message


def test_double_quotes_give_helpful_hint():
    with pytest.raises(LexerError) as error_info:
        tokenize('WHERE dept = "Sales"')
    assert "single quotes" in error_info.value.message


def test_lone_exclamation_mark_is_error():
    with pytest.raises(LexerError):
        tokenize("a ! b")


def test_pretty_error_shows_caret_under_problem():
    sql = "SELECT name FROM @employees"
    with pytest.raises(LexerError) as error_info:
        tokenize(sql)
    pretty = error_info.value.pretty(sql)
    lines = pretty.splitlines()
    # The caret line should have the ^ exactly under the '@'.
    caret_line = lines[-1]
    source_line = lines[-2]
    assert source_line[caret_line.index("^")] == "@"
