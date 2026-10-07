"""
pipeline.py
===========
Runs all six compiler phases in order and KEEPS every intermediate result,
so a user interface can show the whole journey from SQL text to execution plan.

    SQL text
      -> Phase 1  tokens
      -> Phase 2  AST
      -> Phase 3  resolved AST
      -> Phase 4  logical plan
      -> Phase 5  optimized logical plan (+ rules applied)
      -> Phase 6  physical execution plan (+ decisions, costs)

Why a separate module (instead of putting this in main.py)?
    This file is pure LOGIC: it returns data and prints nothing. main.py
    (command line) and app.py (web UI, Module 9) are PRESENTATION: they
    decide how to display that data. Keeping the two apart means both
    interfaces reuse the exact same pipeline, and tests can check results
    without capturing printed text. This is the "separation of concerns"
    principle.

If a phase fails, the pipeline stops there and returns everything that
succeeded so far, plus the error. A UI can then show "phases 1 and 2
passed, phase 3 failed because...".
"""

import time
from dataclasses import dataclass, field

from compiler.ast_nodes import SelectQuery
from compiler.errors import CompilerError
from compiler.lexer import tokenize
from compiler.logical_plan import LogicalNode, build_logical_plan
from compiler.optimizer import OptimizationResult, optimize
from compiler.parser import Parser
from compiler.physical_plan import PhysicalPlanResult, build_physical_plan
from compiler.schema import Schema
from compiler.semantic import SemanticAnalyzer
from compiler.tokens import Token

# Which phase each error class belongs to, for friendly messages.
PHASE_DESCRIPTIONS = {
    "Lexer": "Phase 1 (Lexical Analysis)",
    "Parser": "Phase 2 (Syntax Analysis)",
    "Semantic": "Phase 3 (Semantic Analysis)",
    "Schema": "loading the schema",
}


@dataclass
class CompilationResult:
    """
    Everything the compiler produced for one query.

    Fields stay None for phases that never ran (because an earlier phase failed).
    Not frozen: the pipeline fills it in one phase at a time.

    Attributes:
        source:               The original SQL text.
        tokens:               Phase 1 output.
        ast:                  Phase 2 output.
        resolved_ast:         Phase 3 output (every column has its table).
        logical_plan:         Phase 4 output (unoptimized).
        optimization:         Phase 5 output (plan + rules applied).
        physical:             Phase 6 output for the OPTIMIZED plan.
        unoptimized_physical: Phase 6 output for the UNOPTIMIZED plan (to compare costs).
        error:                The CompilerError that stopped compilation, or None.
        elapsed_ms:           How long compilation took, in milliseconds.
    """

    source: str
    tokens: list[Token] | None = None
    ast: SelectQuery | None = None
    resolved_ast: SelectQuery | None = None
    logical_plan: LogicalNode | None = None
    optimization: OptimizationResult | None = None
    physical: PhysicalPlanResult | None = None
    unoptimized_physical: PhysicalPlanResult | None = None
    error: CompilerError | None = None
    elapsed_ms: float = field(default=0.0)

    @property
    def succeeded(self) -> bool:
        """True if every phase completed."""
        return self.error is None

    @property
    def failed_phase_description(self) -> str | None:
        """E.g. 'Phase 3 (Semantic Analysis)', or None if compilation succeeded."""
        if self.error is None:
            return None
        return PHASE_DESCRIPTIONS.get(self.error.phase, self.error.phase)


def compile_query(source: str, schema: Schema) -> CompilationResult:
    """
    Run all six phases on one SQL query.

    Args:
        source: The SQL text.
        schema: The already-loaded schema (loaded once by the caller, so that
                compiling many queries doesn't re-read the file each time).

    Returns:
        A CompilationResult. Never raises CompilerError: errors are stored
        in result.error instead, alongside the phases that succeeded.
    """
    result = CompilationResult(source=source)
    start_time = time.perf_counter()   # high-resolution timer, good for short durations

    try:
        result.tokens = tokenize(source)                                           # Phase 1
        result.ast = Parser(result.tokens).parse()                                 # Phase 2
        result.resolved_ast = SemanticAnalyzer(schema).analyze(result.ast)         # Phase 3
        result.logical_plan = build_logical_plan(result.resolved_ast)              # Phase 4
        result.optimization = optimize(result.logical_plan)                        # Phase 5
        result.physical = build_physical_plan(result.optimization.plan, schema)    # Phase 6
        result.unoptimized_physical = build_physical_plan(result.logical_plan, schema)
    except CompilerError as error:
        result.error = error

    result.elapsed_ms = (time.perf_counter() - start_time) * 1000
    return result


def split_statements(text: str) -> list[str]:
    """
    Split a file containing several queries into individual statements.

    Splits at semicolons, EXCEPT semicolons that are inside a string literal
    ('a;b') or inside a comment (-- note; more). A plain text.split(";")
    would break those cases.

    Statements that contain only comments or whitespace are dropped.
    Each returned statement is stripped of surrounding whitespace and
    has no trailing semicolon.

    Example:
        "SELECT * FROM a; -- x;y\\nSELECT 'p;q' FROM b"
        -> ["SELECT * FROM a", "-- x;y\\nSELECT 'p;q' FROM b"]
    """
    statements: list[str] = []
    current: list[str] = []
    in_string = False
    in_comment = False
    index = 0

    while index < len(text):
        char = text[index]
        next_char = text[index + 1] if index + 1 < len(text) else ""

        if in_comment:
            if char == "\n":
                in_comment = False
            current.append(char)
        elif in_string:
            if char == "'":
                in_string = False
            current.append(char)
        elif char == "-" and next_char == "-":
            in_comment = True
            current.append(char)
        elif char == "'":
            in_string = True
            current.append(char)
        elif char == ";":
            statements.append("".join(current))   # end of one statement
            current = []
        else:
            current.append(char)
        index += 1

    statements.append("".join(current))           # text after the last semicolon
    return [_trim_leading_comments(statement) for statement in statements if _has_code(statement)]


def _is_code_line(line: str) -> bool:
    """True if a line is neither blank nor a comment-only line."""
    stripped = line.strip()
    return bool(stripped) and not stripped.startswith("--")


def _has_code(statement: str) -> bool:
    """True if the statement has at least one line that isn't blank or a comment."""
    return any(_is_code_line(line) for line in statement.splitlines())


def _trim_leading_comments(statement: str) -> str:
    """
    Drop blank lines and comment lines in front of the SQL, EXCEPT the one
    comment line directly above it, which usually describes the query:

        -- queries.sql            (file header: dropped)
        --                        (dropped)
        -- 1. Simplest query      (kept: the query's title)
        SELECT * FROM employees;
    """
    lines = statement.strip().splitlines()
    first_code_index = next(index for index, line in enumerate(lines) if _is_code_line(line))
    keep_from = first_code_index
    if first_code_index > 0 and lines[first_code_index - 1].strip().startswith("--"):
        keep_from = first_code_index - 1
    return "\n".join(lines[keep_from:]).strip()
