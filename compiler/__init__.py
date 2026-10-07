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
