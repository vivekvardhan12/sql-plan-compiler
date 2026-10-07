"""
parser.py  -  PHASE 2: SYNTAX ANALYSIS
======================================
Checks that the tokens follow the SQL grammar and builds the AST.

Technique: RECURSIVE DESCENT PARSING
    One method per grammar rule. Each method reads the tokens its rule needs
    and calls other methods for the sub-rules, so the call stack mirrors the
    grammar's structure. It's a top-down parser that looks at one token at
    a time to decide what to do, which makes this an LL(1) parser.

The grammar (each rule below = one _parse_... method):

    query       -> SELECT select_list FROM table [join] [where] [order_by] [';'] EOF
    select_list -> '*' | column { ',' column }
    column      -> IDENTIFIER [ '.' IDENTIFIER ]
    table       -> IDENTIFIER
    join        -> JOIN table ON column '=' column
    where       -> WHERE or_condition
    or_condition  -> and_condition { OR and_condition }
    and_condition -> comparison { AND comparison }
    comparison  -> operand ( '=' | '!=' | '<' | '<=' | '>' | '>=' ) operand
    operand     -> column | NUMBER | STRING
    order_by    -> ORDER BY column [ ASC | DESC ]

    Notation:  [ x ] = optional,  { x } = zero or more times,  | = or

Run this file directly to see it in action:
    python -m compiler.parser
    python -m compiler.parser "SELECT * FROM employees WHERE salary > 100"
"""

import sys

from compiler.ast_nodes import (
    ColumnRef,
    Comparison,
    Condition,
    JoinClause,
    Literal,
    LogicalOp,
    Operand,
    OrderBy,
    SelectQuery,
    TableRef,
    format_ast,
)
from compiler.errors import CompilerError, ParserError
from compiler.lexer import tokenize
from compiler.tokens import Token, TokenType

# Maps each comparison token to the operator symbol stored in the AST.
# Both "!=" and "<>" become "!=", so later phases only handle one spelling.
COMPARISON_OPERATORS: dict[TokenType, str] = {
    TokenType.EQUAL: "=",
    TokenType.NOT_EQUAL: "!=",
    TokenType.LESS: "<",
    TokenType.LESS_EQUAL: "<=",
    TokenType.GREATER: ">",
    TokenType.GREATER_EQUAL: ">=",
}


def describe_token(token: Token) -> str:
    """
    Describe a token for an error message, e.g. "'FROM'" or "end of input".
    Uses the lexeme so the user sees exactly what they typed.
    """
    if token.type == TokenType.EOF:
        return "end of input"
    return f"'{token.lexeme}'"


class Parser:
    """
    Turns a list of tokens into a SelectQuery AST.

    The parser keeps an index (self.current) into the token list. Helper
    methods look at or consume the token at that index; grammar methods
    use those helpers to follow the rules above.
    """

    def __init__(self, tokens: list[Token]):
        """
        Args:
            tokens: Output of the lexer. Must end with an EOF token.
        """
        self.tokens = tokens
        self.current = 0   # index of the next token to look at

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def parse(self) -> SelectQuery:
        """
        Parse the whole token list as one query and return its AST.

        Raises:
            ParserError: if the tokens break any grammar rule.
        """
        return self._parse_query()

    # ------------------------------------------------------------------
    # Token helpers (the only methods that touch self.current)
    # ------------------------------------------------------------------

    def _peek(self) -> Token:
        """Return the current token WITHOUT consuming it."""
        return self.tokens[self.current]

    def _advance(self) -> Token:
        """Consume the current token and return it. Never moves past EOF."""
        token = self.tokens[self.current]
        if token.type != TokenType.EOF:
            self.current += 1
        return token

    def _check(self, token_type: TokenType) -> bool:
        """Return True if the current token has the given type (doesn't consume)."""
        return self._peek().type == token_type

    def _match(self, *token_types: TokenType) -> Token | None:
        """
        If the current token is one of `token_types`, consume and return it.
        Otherwise return None and consume nothing.
        Used for OPTIONAL parts of the grammar.
        """
        if self._peek().type in token_types:
            return self._advance()
        return None

    def _expect(self, token_type: TokenType, message: str) -> Token:
        """
        Consume the current token if it has the required type; otherwise
        raise a ParserError. Used for MANDATORY parts of the grammar.

        Args:
            token_type: The type that must appear here.
            message:    What we expected, e.g. "Expected FROM after the column list".
        """
        if self._check(token_type):
            return self._advance()
        raise self._error(message)

    def _error(self, message: str) -> ParserError:
        """Build a ParserError that points at the current token."""
        token = self._peek()
        return ParserError(
            f"{message}, but found {describe_token(token)}",
            token.line,
            token.column,
        )

    # ------------------------------------------------------------------
    # Grammar rules (one method per rule)
    # ------------------------------------------------------------------

    def _parse_query(self) -> SelectQuery:
        """query -> SELECT select_list FROM table [join] [where] [order_by] [';'] EOF"""
        self._expect(TokenType.SELECT, "Expected the query to start with SELECT")
        select_all, columns = self._parse_select_list()

        self._expect(TokenType.FROM, "Expected FROM after the column list")
        from_table = self._parse_table("Expected a table name after FROM")

        # Each optional clause is parsed only if its first keyword is present.
        join = self._parse_join() if self._check(TokenType.JOIN) else None
        where = self._parse_where() if self._check(TokenType.WHERE) else None
        order_by = self._parse_order_by() if self._check(TokenType.ORDER) else None

        self._match(TokenType.SEMICOLON)   # the semicolon is optional
        self._expect(TokenType.EOF, "Expected the query to end here")

        return SelectQuery(
            select_all=select_all,
            columns=tuple(columns),
            from_table=from_table,
            join=join,
            where=where,
            order_by=order_by,
        )

    def _parse_select_list(self) -> tuple[bool, list[ColumnRef]]:
        """
        select_list -> '*' | column { ',' column }

        Returns:
            (True, [])          for SELECT *
            (False, [cols...])  for SELECT a, b, c
        """
        if self._match(TokenType.STAR):
            return True, []

        columns = [self._parse_column("Expected a column name or '*' after SELECT")]
        while self._match(TokenType.COMMA):
            columns.append(self._parse_column("Expected a column name after ','"))
        return False, columns

    def _parse_column(self, message: str) -> ColumnRef:
        """
        column -> IDENTIFIER [ '.' IDENTIFIER ]

        Args:
            message: Error text to use if no column name is found here.
        """
        first = self._expect(TokenType.IDENTIFIER, message)
        if self._match(TokenType.DOT):
            second = self._expect(TokenType.IDENTIFIER, "Expected a column name after '.'")
            # "employees.salary": first part is the table, second is the column.
            return ColumnRef(table=first.value, name=second.value, line=first.line, column=first.column)
        return ColumnRef(table=None, name=first.value, line=first.line, column=first.column)

    def _parse_table(self, message: str) -> TableRef:
        """table -> IDENTIFIER"""
        token = self._expect(TokenType.IDENTIFIER, message)
        return TableRef(name=token.value, line=token.line, column=token.column)

    def _parse_join(self) -> JoinClause:
        """join -> JOIN table ON column '=' column"""
        self._expect(TokenType.JOIN, "Expected JOIN")
        table = self._parse_table("Expected a table name after JOIN")
        self._expect(TokenType.ON, "Expected ON after the joined table name")
        left = self._parse_column("Expected a column name after ON")
        self._expect(TokenType.EQUAL, "Expected '=' in the JOIN condition (only equality joins are supported)")
        right = self._parse_column("Expected a column name after '=' in the JOIN condition")
        return JoinClause(table=table, left=left, right=right)

    def _parse_where(self) -> Condition:
        """where -> WHERE or_condition"""
        self._expect(TokenType.WHERE, "Expected WHERE")
        return self._parse_or_condition()

    def _parse_or_condition(self) -> Condition:
        """
        or_condition -> and_condition { OR and_condition }

        OR is parsed at a HIGHER level than AND, which makes AND bind tighter:
            a OR b AND c   is read as   a OR (b AND c)
        This matches standard SQL operator precedence.

        The loop builds a LEFT-associative chain:
            a OR b OR c    becomes      ((a OR b) OR c)
        """
        condition = self._parse_and_condition()
        while self._match(TokenType.OR):
            right = self._parse_and_condition()
            condition = LogicalOp("OR", condition, right)
        return condition

    def _parse_and_condition(self) -> Condition:
        """and_condition -> comparison { AND comparison }   (left-associative, like OR)"""
        condition: Condition = self._parse_comparison()
        while self._match(TokenType.AND):
            right = self._parse_comparison()
            condition = LogicalOp("AND", condition, right)
        return condition

    def _parse_comparison(self) -> Comparison:
        """comparison -> operand operator operand"""
        left = self._parse_operand()

        operator_token = self._match(*COMPARISON_OPERATORS.keys())
        if operator_token is None:
            raise self._error("Expected a comparison operator (=, !=, <>, <, <=, >, >=)")

        right = self._parse_operand()
        return Comparison(left, COMPARISON_OPERATORS[operator_token.type], right)

    def _parse_operand(self) -> Operand:
        """operand -> column | NUMBER | STRING"""
        if self._check(TokenType.IDENTIFIER):
            return self._parse_column("Expected a column name")

        literal_token = self._match(TokenType.NUMBER, TokenType.STRING)
        if literal_token is not None:
            return Literal(literal_token.value, line=literal_token.line, column=literal_token.column)

        raise self._error("Expected a column name, number, or string")

    def _parse_order_by(self) -> OrderBy:
        """order_by -> ORDER BY column [ ASC | DESC ]"""
        self._expect(TokenType.ORDER, "Expected ORDER")
        self._expect(TokenType.BY, "Expected BY after ORDER")
        column = self._parse_column("Expected a column name after ORDER BY")

        descending = False                      # ASC is the default in SQL
        if self._match(TokenType.DESC):
            descending = True
        else:
            self._match(TokenType.ASC)          # explicit ASC is allowed but changes nothing
        return OrderBy(column=column, descending=descending)


# ----------------------------------------------------------------------
# Convenience function used by the rest of the project
# ----------------------------------------------------------------------

def parse(source: str) -> SelectQuery:
    """
    Run Phase 1 + Phase 2 on a SQL string and return the AST.

    Raises:
        LexerError or ParserError (both subclasses of CompilerError).
    """
    return Parser(tokenize(source)).parse()


# ----------------------------------------------------------------------
# Demo: python -m compiler.parser ["optional SQL here"]
# ----------------------------------------------------------------------

if __name__ == "__main__":
    DEFAULT_QUERY = (
        "SELECT name, salary FROM employees\n"
        "JOIN departments ON employees.dept_id = departments.id\n"
        "WHERE salary > 50000 AND dept_name = 'Sales' OR age < 30\n"
        "ORDER BY salary DESC;"
    )
    query = " ".join(sys.argv[1:]) if len(sys.argv) > 1 else DEFAULT_QUERY

    print("Input SQL:")
    print(query)
    print()

    try:
        print(format_ast(parse(query)))
    except CompilerError as error:   # catches LexerError AND ParserError
        print(error.pretty(query))
        sys.exit(1)
