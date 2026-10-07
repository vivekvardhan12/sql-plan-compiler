"""
main.py
=======
Command-line interface (CLI) for the SQL -> Execution Plan Compiler.

Usage examples (run from the project root):

    python main.py "SELECT name FROM employees WHERE age < 30"
        Compile one query and show all six phases.

    python main.py "SELECT ..." --show logical physical
        Show only the chosen phases.

    python main.py --file examples/queries.sql
        Compile every query in a .sql file.

    python main.py
        Interactive mode: type queries one per line; 'exit' to quit.

    python main.py --show-schema
        Print the tables and columns the compiler knows about.

    python main.py --help
        Full list of options.

Exit codes (useful in scripts and CI pipelines):
    0  everything compiled
    1  at least one query had a compile error
    2  usage problem (bad option, missing file, invalid schema)
"""

import argparse
import sys
from pathlib import Path

from compiler.ast_nodes import format_ast
from compiler.errors import SchemaError
from compiler.lexer import format_token_table
from compiler.logical_plan import format_logical_plan
from compiler.physical_plan import format_estimate, format_physical_plan
from compiler.pipeline import CompilationResult, compile_query, split_statements
from compiler.schema import DEFAULT_SCHEMA_PATH, Schema, format_schema, load_schema

EXIT_OK = 0
EXIT_COMPILE_ERROR = 1
EXIT_USAGE_ERROR = 2

# Names accepted by --show, in pipeline order.
PHASES = ("tokens", "ast", "semantic", "logical", "optimized", "physical")

RULE = "─" * 72          # horizontal line under section titles
BANNER = "═" * 72        # heavier line between queries

INTERACTIVE_HELP = """\
Type a SQL query on one line and press Enter.
Commands:  schema  show tables and columns
           help    show this message
           exit    quit (or press Ctrl+C)"""


# ---------------------------------------------------------------------------
# Building the text report (pure functions: they return strings, print nothing)
# ---------------------------------------------------------------------------

def section(title: str, body: str) -> str:
    """Format one report section: a title, a line, then the body."""
    return f"{title}\n{RULE}\n{body}"


def indent(text: str, prefix: str = "  ") -> str:
    """Indent every line of `text`."""
    return "\n".join(prefix + line for line in text.splitlines())


def format_phase_sections(result: CompilationResult, phases: tuple[str, ...]) -> list[str]:
    """
    Build one section per requested phase that actually ran.

    Args:
        result: Output of compile_query().
        phases: Which phases the user asked to see.
    """
    sections = []

    if "tokens" in phases and result.tokens is not None:
        sections.append(section(
            f"[Phase 1] Lexical Analysis: {len(result.tokens)} tokens",
            format_token_table(result.tokens),
        ))

    if "ast" in phases and result.ast is not None:
        sections.append(section(
            "[Phase 2] Syntax Analysis: Abstract Syntax Tree",
            format_ast(result.ast),
        ))

    if "semantic" in phases and result.resolved_ast is not None:
        sections.append(section(
            "[Phase 3] Semantic Analysis: passed ✔",
            "All tables and columns exist and every comparison is type-correct.\n"
            "Resolved AST (each column now carries its table):\n\n"
            + format_ast(result.resolved_ast),
        ))

    if "logical" in phases and result.logical_plan is not None:
        sections.append(section(
            "[Phase 4] Intermediate Code: Logical Plan (relational algebra)",
            format_logical_plan(result.logical_plan)
            + "\n\nRelational algebra:\n"
            + indent(result.logical_plan.to_algebra()),
        ))

    if "optimized" in phases and result.optimization is not None:
        rules = result.optimization.applied_rules
        if rules:
            rules_text = "\n".join(f"  {number}. {rule}" for number, rule in enumerate(rules, start=1))
        else:
            rules_text = "  None: no rule could improve this plan."
        sections.append(section(
            f"[Phase 5] Optimization: {len(rules)} rule(s) applied",
            format_logical_plan(result.optimization.plan) + "\n\nRules applied:\n" + rules_text,
        ))

    if "physical" in phases and result.physical is not None:
        sections.append(section(
            "[Phase 6] Code Generation: Execution Plan (read bottom-up)",
            format_physical_plan(result.physical.plan) + "\n\n" + format_cost_summary(result),
        ))

    return sections


def format_cost_summary(result: CompilationResult) -> str:
    """Planner decisions plus optimized vs unoptimized cost."""
    lines = []
    if result.physical.decisions:
        lines.append("Planner decisions:")
        lines += [f"  - {decision}" for decision in result.physical.decisions]
        lines.append("")

    optimized_cost = result.physical.total_cost
    unoptimized_cost = result.unoptimized_physical.total_cost
    lines.append(f"Estimated total cost: {format_estimate(optimized_cost)}"
                 f"  (without optimization: {format_estimate(unoptimized_cost)})")

    if unoptimized_cost > optimized_cost:
        saving = (1 - optimized_cost / unoptimized_cost) * 100
        lines.append(f"Optimization saved about {saving:.0f}% of the estimated work.")
    return "\n".join(lines)


def format_report(result: CompilationResult, phases: tuple[str, ...]) -> str:
    """
    Build the complete report for one query: the query, each phase, and
    either a success line or the error with a caret under the problem.
    """
    parts = ["Query:\n" + indent(result.source)]
    parts += format_phase_sections(result, phases)

    if result.succeeded:
        parts.append(f"✔ Compiled successfully in {result.elapsed_ms:.2f} ms")
    else:
        parts.append(section(
            f"✘ Compilation failed during {result.failed_phase_description}",
            result.error.pretty(result.source),
        ))

    return "\n\n".join(parts)


# ---------------------------------------------------------------------------
# The three modes
# ---------------------------------------------------------------------------

def run_single_query(source: str, schema: Schema, phases: tuple[str, ...]) -> int:
    """Compile and report one query. Returns an exit code."""
    result = compile_query(source, schema)
    print(format_report(result, phases))
    return EXIT_OK if result.succeeded else EXIT_COMPILE_ERROR


def run_file(path: Path, schema: Schema, phases: tuple[str, ...]) -> int:
    """Compile every statement in a .sql file. Returns an exit code."""
    if not path.is_file():
        print(f"Error: file not found: {path}", file=sys.stderr)
        return EXIT_USAGE_ERROR

    statements = split_statements(path.read_text(encoding="utf-8"))
    if not statements:
        print(f"Error: no SQL statements found in {path}", file=sys.stderr)
        return EXIT_USAGE_ERROR

    failures = 0
    for number, statement in enumerate(statements, start=1):
        print(BANNER)
        print(f" Query {number} of {len(statements)}  ({path.name})")
        print(BANNER)
        result = compile_query(statement, schema)
        print(format_report(result, phases))
        print()
        if not result.succeeded:
            failures += 1

    print(BANNER)
    print(f" Summary: {len(statements)} queries, "
          f"{len(statements) - failures} compiled, {failures} failed")
    print(BANNER)
    return EXIT_OK if failures == 0 else EXIT_COMPILE_ERROR


def run_interactive(schema: Schema, phases: tuple[str, ...]) -> int:
    """
    Read-Eval-Print Loop (REPL): read a query, compile it, print the report,
    repeat. Ends on 'exit', 'quit', Ctrl+C, or end of input.
    """
    print("SQL → Execution Plan Compiler (interactive mode)")
    print(INTERACTIVE_HELP)

    while True:
        try:
            line = input("\nsql> ").strip()
        except (EOFError, KeyboardInterrupt):   # Ctrl+Z/Ctrl+D or Ctrl+C
            print()
            break

        command = line.lower()
        if not line:
            continue
        if command in ("exit", "quit"):
            break
        if command == "help":
            print(INTERACTIVE_HELP)
            continue
        if command == "schema":
            print(format_schema(schema))
            continue

        print()
        print(format_report(compile_query(line, schema), phases))

    print("Goodbye!")
    return EXIT_OK


# ---------------------------------------------------------------------------
# Argument parsing and entry point
# ---------------------------------------------------------------------------

def build_argument_parser() -> argparse.ArgumentParser:
    """Define every command-line option (argparse also generates --help from this)."""
    parser = argparse.ArgumentParser(
        prog="main.py",
        description="Compile SQL queries into execution plans, showing every compiler phase.",
        epilog='Example: python main.py "SELECT name FROM employees WHERE age < 30" --show physical',
    )
    parser.add_argument(
        "query",
        nargs="?",   # optional positional argument
        help="SQL query to compile (wrap it in double quotes). Omit for interactive mode.",
    )
    parser.add_argument(
        "-f", "--file",
        type=Path,
        help="compile every query in a .sql file (queries separated by ';')",
    )
    parser.add_argument(
        "-s", "--show",
        nargs="+",
        choices=PHASES,
        default=list(PHASES),
        metavar="PHASE",
        help=f"phases to display (default: all). Choices: {', '.join(PHASES)}",
    )
    parser.add_argument(
        "--schema",
        type=Path,
        default=DEFAULT_SCHEMA_PATH,
        help="path to the schema JSON file (default: data/schema.json)",
    )
    parser.add_argument(
        "--show-schema",
        action="store_true",
        help="print the tables and columns in the schema, then exit",
    )
    return parser


def use_utf8_output() -> None:
    """
    Make sure tree lines (├──), Greek letters (π σ ⋈ τ) and ✔/✘ can be printed,
    even when output is redirected to a file on Windows (which would
    otherwise use an old 'cp1252' encoding and crash).
    """
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")
        except (AttributeError, ValueError):
            pass   # some environments (e.g. test capture) don't support this; that's fine


def main(argv: list[str] | None = None) -> int:
    """
    Program entry point.

    Args:
        argv: Command-line arguments (None = use the real ones). Tests pass a
              list here instead of running a separate process.

    Returns:
        The exit code (0, 1, or 2).
    """
    use_utf8_output()
    args = build_argument_parser().parse_args(argv)

    if args.query and args.file:
        print("Error: give either a query or --file, not both.", file=sys.stderr)
        return EXIT_USAGE_ERROR

    try:
        schema = load_schema(args.schema)
    except SchemaError as error:
        print(error, file=sys.stderr)
        return EXIT_USAGE_ERROR

    # Keep phases in pipeline order, however the user typed them, without duplicates.
    phases = tuple(phase for phase in PHASES if phase in args.show)

    if args.show_schema:
        print(format_schema(schema))
        return EXIT_OK
    if args.file:
        return run_file(args.file, schema, phases)
    if args.query:
        return run_single_query(args.query, schema, phases)
    return run_interactive(schema, phases)


if __name__ == "__main__":
    # sys.exit passes main()'s return value to the operating system as the exit code.
    sys.exit(main())
