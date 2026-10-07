"""
tokens.py
=========
Defines WHAT a token is and WHICH kinds of tokens exist in our SQL subset.

A token is the smallest meaningful unit of a language, like a "word" in English.
The lexer (lexer.py) reads raw characters and produces a list of these tokens.

Example:
    "SELECT name FROM employees"
        ->  SELECT  IDENTIFIER('name')  FROM  IDENTIFIER('employees')  EOF
"""

from dataclasses import dataclass
from enum import Enum, auto


class TokenType(Enum):
    """
    Every category of token our SQL subset understands.

    auto() just gives each member a unique number; we never use the number,
    only the name (TokenType.SELECT, TokenType.COMMA, ...).
    """

    # ---------- Keywords (reserved words with special meaning) ----------
    SELECT = auto()
    FROM = auto()
    WHERE = auto()
    JOIN = auto()
    ON = auto()
    AND = auto()
    OR = auto()
    ORDER = auto()
    BY = auto()
    ASC = auto()
    DESC = auto()

    # ---------- Names and literal values ----------
    IDENTIFIER = auto()  # table or column names: employees, salary
    NUMBER = auto()      # 50000, 3.5
    STRING = auto()      # 'Sales'

    # ---------- Comparison operators ----------
    EQUAL = auto()          # =
    NOT_EQUAL = auto()      # != or <>
    LESS = auto()           # <
    LESS_EQUAL = auto()     # <=
    GREATER = auto()        # >
    GREATER_EQUAL = auto()  # >=

    # ---------- Punctuation ----------
    COMMA = auto()      # ,
    DOT = auto()        # .   (as in employees.salary)
    STAR = auto()       # *   (as in SELECT *)
    SEMICOLON = auto()  # ;

    # ---------- Special ----------
    EOF = auto()  # "End Of File": always the last token, tells the parser to stop


# Maps the UPPERCASE spelling of each keyword to its token type.
# The lexer reads a word, uppercases it, and looks it up here.
# If the word is found -> keyword. If not -> identifier.
KEYWORDS: dict[str, TokenType] = {
    "SELECT": TokenType.SELECT,
    "FROM": TokenType.FROM,
    "WHERE": TokenType.WHERE,
    "JOIN": TokenType.JOIN,
    "ON": TokenType.ON,
    "AND": TokenType.AND,
    "OR": TokenType.OR,
    "ORDER": TokenType.ORDER,
    "BY": TokenType.BY,
    "ASC": TokenType.ASC,
    "DESC": TokenType.DESC,
}

# Operators that are TWO characters long.
# The lexer checks this table BEFORE the single-character table, so that
# "<=" becomes one LESS_EQUAL token instead of LESS followed by EQUAL.
TWO_CHAR_TOKENS: dict[str, TokenType] = {
    "<=": TokenType.LESS_EQUAL,
    ">=": TokenType.GREATER_EQUAL,
    "!=": TokenType.NOT_EQUAL,
    "<>": TokenType.NOT_EQUAL,  # standard SQL spelling of "not equal"
}

# Operators and punctuation that are exactly ONE character long.
SINGLE_CHAR_TOKENS: dict[str, TokenType] = {
    ",": TokenType.COMMA,
    ".": TokenType.DOT,
    "*": TokenType.STAR,
    ";": TokenType.SEMICOLON,
    "=": TokenType.EQUAL,
    "<": TokenType.LESS,
    ">": TokenType.GREATER,
}

# Token types whose `value` is worth showing when printing (the rest are self-explanatory).
VALUE_CARRYING_TYPES = (TokenType.IDENTIFIER, TokenType.NUMBER, TokenType.STRING)


@dataclass(frozen=True)
class Token:
    """
    One token produced by the lexer.

    Attributes:
        type:    Which category this token belongs to (TokenType.SELECT, ...).
        lexeme:  The exact text as the user typed it, e.g. "Employees" or "'Sales'".
                 Used in error messages so the user recognises their own input.
        value:   The meaningful value the later phases use:
                   IDENTIFIER -> lowercase name     ("employees")
                   NUMBER     -> int or float       (50000, 3.5)
                   STRING     -> text without quotes ("Sales")
                   others     -> None
        line:    1-based line number where the token starts.
        column:  1-based column number where the token starts.

    frozen=True makes tokens read-only after creation, so no later phase can
    accidentally modify them.
    """

    type: TokenType
    lexeme: str
    value: str | int | float | None
    line: int
    column: int

    def __str__(self) -> str:
        """Short readable form, e.g. SELECT, IDENTIFIER('name'), NUMBER(50000)."""
        if self.type in VALUE_CARRYING_TYPES:
            return f"{self.type.name}({self.value!r})"
        return self.type.name
