"""
errors.py
=========
Custom exception classes for every phase of the SQL compiler.

Why custom exceptions?
    Python's built-in exceptions (ValueError, SyntaxError, ...) don't tell us
    WHICH compiler phase failed or WHERE in the query the problem is.
    Our own classes carry the phase name, line and column, so the user sees:

        Lexer Error at line 1, column 18: Unexpected character '@'

            SELECT name FROM @employees
                             ^
"""


class CompilerError(Exception):
    """
    Base class for every error the compiler can raise.

    All phase-specific errors (LexerError, ParserError, SemanticError) inherit
    from this class, so main.py can catch ALL of them with a single
    `except CompilerError:` block.
    """

    # Human-readable phase name. Each subclass overrides this.
    phase = "Compiler"

    def __init__(self, message: str, line: int | None = None, column: int | None = None):
        """
        Store the details of the error.

        Args:
            message: What went wrong, in plain English.
            line:    1-based line number where the problem starts (optional).
            column:  1-based column number where the problem starts (optional).
        """
        self.message = message
        self.line = line
        self.column = column
        # Give Exception the formatted text so that print(error) shows it nicely.
        super().__init__(self.header())

    def header(self) -> str:
        """Return the one-line summary, e.g. 'Lexer Error at line 1, column 5: ...'."""
        if self.line is not None and self.column is not None:
            return f"{self.phase} Error at line {self.line}, column {self.column}: {self.message}"
        return f"{self.phase} Error: {self.message}"

    def pretty(self, source: str) -> str:
        """
        Return the error summary PLUS the offending line of SQL with a caret (^)
        pointing at the exact column where the problem is.

        Args:
            source: The complete original SQL text the user typed.

        Returns:
            A multi-line string ready to print.
        """
        summary = self.header()

        # Without a position we can't draw the caret, so return just the summary.
        if self.line is None or self.column is None:
            return summary

        source_lines = source.splitlines()

        # Safety check: the line number must exist in the source.
        if not 1 <= self.line <= len(source_lines):
            return summary

        offending_line = source_lines[self.line - 1]   # lists are 0-based, lines are 1-based
        caret_line = " " * (self.column - 1) + "^"      # spaces up to the column, then ^
        return f"{summary}\n\n    {offending_line}\n    {caret_line}"


class LexerError(CompilerError):
    """Phase 1: raised when the input has characters or words we cannot turn into tokens."""

    phase = "Lexer"


class ParserError(CompilerError):
    """Phase 2: raised when the tokens do not follow the SQL grammar."""

    phase = "Parser"


class SemanticError(CompilerError):
    """Phase 3: raised when the query is grammatically correct but meaningless (e.g. unknown column)."""

    phase = "Semantic"
