"""
lexer.py  -  PHASE 1: LEXICAL ANALYSIS
======================================
Converts a raw SQL string into a list of Token objects.

How it works (in one sentence):
    Read the input one character at a time, from left to right, and whenever a
    complete "word" (keyword, name, number, string, operator) has been read,
    package it as a Token.

Example:
    Input : "SELECT name FROM employees WHERE salary >= 50000;"
    Output: SELECT IDENTIFIER('name') FROM IDENTIFIER('employees') WHERE
            IDENTIFIER('salary') GREATER_EQUAL NUMBER(50000) SEMICOLON EOF

Run this file directly to see it in action:
    python -m compiler.lexer
    python -m compiler.lexer "SELECT * FROM employees"
"""

import sys

from compiler.errors import LexerError
from compiler.tokens import (
    KEYWORDS,
    SINGLE_CHAR_TOKENS,
    TWO_CHAR_TOKENS,
    Token,
    TokenType,
)

# Returned by _peek() when we look past the end of the input.
# "\0" (the null character) never matches any rule, so it is a safe "nothing here" marker.
END_OF_INPUT = "\0"


# ----------------------------------------------------------------------
# Character classification helpers
# ----------------------------------------------------------------------
# We deliberately accept only ASCII letters/digits. Python's built-in
# str.isdigit() would also accept characters like '²', which int() can't convert.

def is_digit(char: str) -> bool:
    """Return True if `char` is one of 0-9."""
    return "0" <= char <= "9"


def is_identifier_start(char: str) -> bool:
    """Return True if `char` can START a name: an ASCII letter or underscore."""
    return ("a" <= char <= "z") or ("A" <= char <= "Z") or char == "_"


def is_identifier_part(char: str) -> bool:
    """Return True if `char` can appear INSIDE a name: letter, digit, or underscore."""
    return is_identifier_start(char) or is_digit(char)


# ----------------------------------------------------------------------
# The Lexer
# ----------------------------------------------------------------------

class Lexer:
    """
    Scans a SQL string and produces tokens.

    The lexer keeps a "cursor" (self.position) that moves forward through the
    text. It also tracks the current line and column so that every token,
    and every error, knows exactly where it came from.
    """

    def __init__(self, source: str):
        """
        Prepare a lexer for the given SQL text.

        Args:
            source: The complete SQL query as a string.
        """
        self.source = source
        self.position = 0          # index of the NEXT character to read
        self.line = 1              # current line (1-based, like editors show)
        self.column = 1            # current column (1-based)
        self.tokens: list[Token] = []

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def tokenize(self) -> list[Token]:
        """
        Scan the entire input and return the list of tokens.

        The list always ends with exactly one EOF token, so the parser
        never has to worry about running off the end of the list.

        Raises:
            LexerError: if an unrecognised character or malformed token is found.
        """
        while True:
            self._skip_whitespace_and_comments()
            if self._is_at_end():
                break
            self._scan_token()

        self.tokens.append(Token(TokenType.EOF, "", None, self.line, self.column))
        return self.tokens

    # ------------------------------------------------------------------
    # Low-level cursor helpers
    # ------------------------------------------------------------------

    def _is_at_end(self) -> bool:
        """Return True when every character has been consumed."""
        return self.position >= len(self.source)

    def _peek(self, offset: int = 0) -> str:
        """
        Look at a character WITHOUT consuming it.

        Args:
            offset: 0 = current character, 1 = the one after it, and so on.

        Returns:
            The character, or END_OF_INPUT if we would go past the end.
        """
        index = self.position + offset
        if index >= len(self.source):
            return END_OF_INPUT
        return self.source[index]

    def _advance(self) -> str:
        """
        Consume the current character, move the cursor forward, and
        update line/column tracking. Returns the consumed character.
        """
        char = self.source[self.position]
        self.position += 1
        if char == "\n":
            self.line += 1     # a newline moves us to the next line...
            self.column = 1    # ...and back to the first column
        else:
            self.column += 1
        return char

    # ------------------------------------------------------------------
    # Skipping things that are not tokens
    # ------------------------------------------------------------------

    def _skip_whitespace_and_comments(self) -> None:
        """
        Skip spaces, tabs, newlines, and SQL line comments ('-- ...').

        These carry no meaning for the compiler, so no tokens are produced.
        """
        while not self._is_at_end():
            char = self._peek()
            if char.isspace():
                self._advance()
            elif char == "-" and self._peek(1) == "-":
                # A comment runs until the end of the line.
                while not self._is_at_end() and self._peek() != "\n":
                    self._advance()
            else:
                break   # found the start of a real token

    # ------------------------------------------------------------------
    # The dispatcher: decide what kind of token starts here
    # ------------------------------------------------------------------

    def _scan_token(self) -> None:
        """
        Look at the current character, decide which kind of token it starts,
        read that token, and append it to self.tokens.
        """
        # Remember where this token begins (for the Token and for error messages).
        start_line, start_column = self.line, self.column
        char = self._peek()

        if is_identifier_start(char):
            token = self._read_identifier_or_keyword(start_line, start_column)

        elif is_digit(char):
            token = self._read_number(start_line, start_column)

        elif char == "'":
            token = self._read_string(start_line, start_column)

        elif char == '"':
            # Common beginner mistake, so give a helpful hint instead of a vague error.
            raise LexerError(
                "Double quotes are not supported; SQL strings use single quotes, e.g. 'Sales'",
                start_line,
                start_column,
            )

        elif char + self._peek(1) in TWO_CHAR_TOKENS:
            # Check two-character operators FIRST (maximal munch rule).
            lexeme = self._advance() + self._advance()
            token = Token(TWO_CHAR_TOKENS[lexeme], lexeme, None, start_line, start_column)

        elif char in SINGLE_CHAR_TOKENS:
            lexeme = self._advance()
            token = Token(SINGLE_CHAR_TOKENS[lexeme], lexeme, None, start_line, start_column)

        else:
            raise LexerError(f"Unexpected character {char!r}", start_line, start_column)

        self.tokens.append(token)

    # ------------------------------------------------------------------
    # Readers for each kind of multi-character token
    # ------------------------------------------------------------------

    def _read_identifier_or_keyword(self, line: int, column: int) -> Token:
        """
        Read a word made of letters, digits and underscores, then decide
        whether it is a keyword (SELECT, FROM...) or an identifier (a name).

        Keywords are case-insensitive: select, SELECT, and SeLeCt all match.
        Identifiers are stored in lowercase as their value, because unquoted
        SQL names are case-insensitive (Employees and employees are the same table).
        """
        start = self.position
        while is_identifier_part(self._peek()):
            self._advance()
        lexeme = self.source[start:self.position]

        keyword_type = KEYWORDS.get(lexeme.upper())
        if keyword_type is not None:
            return Token(keyword_type, lexeme, None, line, column)

        return Token(TokenType.IDENTIFIER, lexeme, lexeme.lower(), line, column)

    def _read_number(self, line: int, column: int) -> Token:
        """
        Read an integer (50000) or a decimal number (3.5).

        Rules:
            * A decimal point counts only if a digit follows it, so "3." is
              read as NUMBER(3) followed by DOT.
            * A number directly followed by a letter (e.g. "12abc") is an error,
              because names may not start with a digit.
        """
        start = self.position
        while is_digit(self._peek()):
            self._advance()

        is_decimal = False
        if self._peek() == "." and is_digit(self._peek(1)):
            is_decimal = True
            self._advance()                 # consume the '.'
            while is_digit(self._peek()):
                self._advance()

        if is_identifier_start(self._peek()):
            # Consume the rest of the bad word so the error shows all of it.
            while is_identifier_part(self._peek()):
                self._advance()
            bad_word = self.source[start:self.position]
            raise LexerError(
                f"Invalid number {bad_word!r} (names cannot start with a digit)",
                line,
                column,
            )

        lexeme = self.source[start:self.position]
        value = float(lexeme) if is_decimal else int(lexeme)
        return Token(TokenType.NUMBER, lexeme, value, line, column)

    def _read_string(self, line: int, column: int) -> Token:
        """
        Read a single-quoted string such as 'Sales'.

        The token's value is the text WITHOUT the quotes ("Sales").
        Escaped quotes inside strings (like 'O''Brien') are not supported,
        to keep the lexer simple.
        """
        start = self.position
        self._advance()                     # consume the opening '

        while not self._is_at_end() and self._peek() != "'":
            self._advance()

        if self._is_at_end():
            raise LexerError("Unterminated string: missing closing quote (')", line, column)

        self._advance()                     # consume the closing '
        lexeme = self.source[start:self.position]   # includes both quotes
        value = lexeme[1:-1]                         # strip the quotes
        return Token(TokenType.STRING, lexeme, value, line, column)


# ----------------------------------------------------------------------
# Convenience functions used by the rest of the project
# ----------------------------------------------------------------------

def tokenize(source: str) -> list[Token]:
    """Shortcut: create a Lexer for `source` and return its tokens."""
    return Lexer(source).tokenize()


def format_token_table(tokens: list[Token]) -> str:
    """
    Render tokens as a neat text table (used for demos and the CLI).

    Example row:
          5  IDENTIFIER       employees      'employees'      1:19
    """
    header = f"{'#':>3}  {'TYPE':<15} {'LEXEME':<14} {'VALUE':<14} LINE:COL"
    rows = [header, "-" * len(header)]
    for index, token in enumerate(tokens, start=1):
        value_text = "-" if token.value is None else repr(token.value)
        rows.append(
            f"{index:>3}  {token.type.name:<15} {token.lexeme:<14} "
            f"{value_text:<14} {token.line}:{token.column}"
        )
    return "\n".join(rows)


# ----------------------------------------------------------------------
# Demo: python -m compiler.lexer ["optional SQL here"]
# ----------------------------------------------------------------------

if __name__ == "__main__":
    DEFAULT_QUERY = (
        "SELECT name, salary FROM employees\n"
        "JOIN departments ON employees.dept_id = departments.id\n"
        "WHERE salary > 50000 ORDER BY salary DESC;"
    )
    # If the user passed a query on the command line, use it; otherwise use the default.
    query = " ".join(sys.argv[1:]) if len(sys.argv) > 1 else DEFAULT_QUERY

    print("Input SQL:")
    print(query)
    print()

    try:
        print(format_token_table(tokenize(query)))
    except LexerError as error:
        print(error.pretty(query))
        sys.exit(1)   # non-zero exit code signals failure to the terminal
