"""
compiler package
================

This folder holds every phase of the SQL-to-Execution-Plan compiler:

    Phase 1  lexer.py          SQL text        -> tokens
    Phase 2  parser.py         tokens          -> AST
    Phase 3  semantic.py       AST             -> validated AST
    Phase 4  logical_plan.py   validated AST   -> logical plan (relational algebra)
    Phase 5  optimizer.py      logical plan    -> optimized logical plan
    Phase 6  physical_plan.py  optimized plan  -> execution plan

The presence of this __init__.py file tells Python "this folder is a package",
which lets other files write imports like:

    from compiler.lexer import tokenize
"""

# The project's version number, following Semantic Versioning (MAJOR.MINOR.PATCH):
#   MAJOR changes when existing usage breaks, MINOR when features are added,
#   PATCH for bug fixes. Shown by `python main.py --version`.
__version__ = "1.0.0"
