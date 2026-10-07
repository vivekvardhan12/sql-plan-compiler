# SQL → Execution Plan Compiler

[![Tests](https://github.com/vivekvardhan12/sql-plan-compiler/actions/workflows/tests.yml/badge.svg)](https://github.com/vivekvardhan12/sql-plan-compiler/actions/workflows/tests.yml)
![Python](https://img.shields.io/badge/python-3.10%20%7C%203.12%20%7C%203.13%20%7C%203.14-blue)
![Dependencies](https://img.shields.io/badge/runtime%20dependencies-none-brightgreen)
![License](https://img.shields.io/badge/license-MIT-green)

A compiler that translates SQL `SELECT` queries into **database execution plans**, built from scratch in Python as a Compiler Design project. Every classic compiler phase is implemented by hand and made visible: lexical analysis, parsing, semantic analysis, intermediate code generation, optimization, and code generation.

```
SQL text ──► Tokens ──► AST ──► Validated AST ──► Logical Plan ──► Optimized Plan ──► Execution Plan
           Phase 1    Phase 2     Phase 3         Phase 4          Phase 5           Phase 6
```

> Replace `vivekvardhan12` in the badge links above with your GitHub vivekvardhan12.

---

## Table of Contents

- [What it does](#what-it-does)
- [Features](#features)
- [Tech Stack](#tech-stack)
- [Architecture](#architecture)
- [Folder Structure](#folder-structure)
- [Installation](#installation)
- [Usage](#usage)
- [Supported SQL](#supported-sql)
- [Example Output](#example-output)
- [Configuration](#configuration)
- [Testing](#testing)
- [Continuous Integration](#continuous-integration)
- [Troubleshooting](#troubleshooting)
- [Limitations and Future Work](#limitations-and-future-work)
- [Contributing](#contributing)
- [License](#license)
- [Acknowledgements](#acknowledgements)

---

## What it does

When you send a query to a database such as PostgreSQL, it is not executed as text. The database first **compiles** it into an **execution plan**: a tree of concrete steps like "scan this table", "filter these rows", "join with a hash table". You can see one with PostgreSQL's `EXPLAIN` command.

This project builds that compiler for a subset of SQL. For a query like:

```sql
SELECT name, dept_name FROM employees
JOIN departments ON dept_id = departments.id
WHERE salary > 50000 AND location = 'Hyderabad'
ORDER BY salary DESC;
```

it produces, phase by phase, the tokens, the syntax tree, the validated tree, the relational algebra, the optimized plan (with an explanation of each rule applied), and finally an execution plan with estimated row counts and costs, choosing the cheaper join algorithm.

## Features

- **Six hand-written compiler phases**, with no parser generators or SQL libraries.
- **Precise error messages** with line, column and a caret (`^`) under the problem, plus "did you mean …?" suggestions for misspelled tables and columns.
- **Semantic analysis**: checks that tables and columns exist, resolves which table each column belongs to, detects ambiguous columns, and type-checks every comparison.
- **Relational algebra IR**: the logical plan prints both as a tree and as an algebra expression (π, σ, ⋈, τ).
- **Rule-based optimizer** with a log explaining every change:
  - constant folding (`age < 30 AND 1 = 1` → `age < 30`)
  - predicate pushdown (filters move below the join)
  - filter merging
- **Cost-based join selection**: estimates rows and cost, then picks **Nested Loop Join** or **Hash Join**.
- **Command-line interface** with single-query, file and interactive modes, plus phase selection.
- **Zero runtime dependencies**: only the Python standard library.
- **195 automated tests**, run by GitHub Actions on Linux and Windows across four Python versions.

## Tech Stack

| Component | Choice | Why |
|---|---|---|
| Language | Python 3.10+ | Readable, fast to write, strong standard library |
| Lexer / Parser | Hand-written (recursive descent) | Shows every compiler phase explicitly; best error messages |
| Schema storage | JSON file | Human-editable, no database server needed |
| CLI | `argparse` (standard library) | Option parsing, validation and `--help` for free |
| Testing | `pytest` | Concise tests, fixtures, parametrization |
| CI | GitHub Actions | Runs the test suite automatically on every push |

## Architecture

Each phase is a separate module with one job. Each takes the previous phase's output as input, so they can be developed, tested and explained independently.

| Phase | Module | Input → Output | Compiler-theory concept |
|---|---|---|---|
| 1 | `lexer.py` | SQL text → tokens | Lexical analysis, maximal munch, finite automata |
| 2 | `parser.py` | tokens → AST | Recursive descent (LL(1)), precedence, left-recursion elimination |
| 3 | `semantic.py` | AST → resolved AST | Symbol table, scope, name resolution, static type checking |
| 4 | `logical_plan.py` | resolved AST → logical plan | Intermediate representation (relational algebra) |
| 5 | `optimizer.py` | logical plan → optimized plan | Constant folding, equivalence-preserving rewrites |
| 6 | `physical_plan.py` | optimized plan → execution plan | Code generation, cost-based instruction selection |

Supporting modules:

- `pipeline.py` runs all six phases and keeps every intermediate result. It is pure logic, with no printing.
- `main.py` is the command-line interface (presentation only).
- `schema.py` loads and validates `data/schema.json`, the compiler's symbol table.
- `tree_printer.py` draws any tree as text (used for the AST and both plans).
- `errors.py` defines one error class per phase, all inheriting from `CompilerError`.

```
                          data/schema.json
                                 │
SQL ─► Lexer ─► Parser ─► Semantic Analyzer ─► Logical Plan ─► Optimizer ─► Physical Planner ─► Execution Plan
        │         │              │                                                 ▲
        └─────────┴──────────────┴── CompilerError (line, column, caret) ──────────┘ (row counts)
```

## Folder Structure

```
sql-plan-compiler/
├── .github/workflows/
│   └── tests.yml          # CI: runs tests on every push and pull request
├── compiler/              # the compiler itself (one module per phase)
│   ├── __init__.py        # package marker + version number
│   ├── tokens.py          # token types and the Token class
│   ├── lexer.py           # Phase 1: lexical analysis
│   ├── ast_nodes.py       # AST node classes
│   ├── parser.py          # Phase 2: syntax analysis
│   ├── schema.py          # schema loading/validation (symbol table)
│   ├── semantic.py        # Phase 3: semantic analysis
│   ├── logical_plan.py    # Phase 4: relational algebra IR
│   ├── optimizer.py       # Phase 5: optimization
│   ├── physical_plan.py   # Phase 6: execution plan, estimates, costs
│   ├── pipeline.py        # runs all phases, keeps every result
│   ├── tree_printer.py    # draws trees as text
│   └── errors.py          # error classes for every phase
├── data/
│   └── schema.json        # tables, columns, types, row counts
├── docs/
│   └── PROJECT_REVIEW.md  # checklists, viva/interview questions, presentation notes
├── examples/
│   ├── queries.sql        # 8 valid demo queries
│   └── errors.sql         # 10 deliberately broken queries
├── tests/                 # 195 pytest tests (one file per module + CLI)
├── main.py                # command-line entry point
├── requirements.txt       # test dependency (pytest)
├── pytest.ini             # pytest configuration
├── .gitignore
├── LICENSE                # MIT
└── README.md
```

## Installation

### Prerequisites

- **Python 3.10 or newer** (tested on 3.10, 3.12, 3.13 and 3.14), for example via [Anaconda](https://www.anaconda.com/download) or [python.org](https://www.python.org/downloads/)
- **Git**: [git-scm.com/downloads](https://git-scm.com/downloads)

### Steps

```bash
# 1. Get the code
git clone https://github.com/vivekvardhan12/sql-plan-compiler.git
cd sql-plan-compiler

# 2. Create an isolated environment (pick ONE option)
conda create -n sqlcompiler python=3.13 -y     # Option A: Anaconda
conda activate sqlcompiler

python -m venv .venv                           # Option B: plain Python
.venv\Scripts\activate                         #   Windows
source .venv/bin/activate                      #   macOS / Linux

# 3. Install the test dependency
pip install -r requirements.txt

# 4. Verify everything works
pytest
python main.py --version
```

## Usage

```bash
# Compile one query and show all six phases
python main.py "SELECT name FROM employees WHERE age < 30"

# Show only some phases (any of: tokens ast semantic logical optimized physical)
python main.py "SELECT name FROM employees WHERE age < 30" --show logical physical

# Compile every query in a file
python main.py --file examples/queries.sql

# See how each phase reports errors
python main.py --file examples/errors.sql --show tokens

# Interactive mode: type queries one per line ('schema', 'help', 'exit')
python main.py

# List the tables and columns the compiler knows about
python main.py --show-schema

# Use a different schema file
python main.py --schema path/to/schema.json "SELECT * FROM my_table"

# Save a report to a file
python main.py --file examples/queries.sql > report.txt
```

Each phase module can also be run on its own for experiments, e.g. `python -m compiler.lexer "SELECT * FROM t"`.

**Exit codes:** `0` all queries compiled · `1` a query had a compile error · `2` usage problem (bad option, missing file, invalid schema).

**Quoting on Windows:** wrap the whole query in double quotes and use single quotes for SQL strings: `"... WHERE location = 'Hyderabad'"`.

## Supported SQL

```
SELECT * | column [, column ...]
FROM table
[JOIN table ON column = column]
[WHERE condition [AND | OR condition ...]]
[ORDER BY column [ASC | DESC]]
[;]
```

- Columns may be qualified: `employees.salary`.
- Comparison operators: `=`, `!=`, `<>`, `<`, `<=`, `>`, `>=`.
- Values: integers, decimals, `'single-quoted strings'`.
- `AND` binds tighter than `OR`, as in standard SQL.
- `-- line comments` are allowed.
- Keywords are case-insensitive; unquoted names are case-insensitive.

The grammar (EBNF), implemented one method per rule in `parser.py`:

```
query         → SELECT select_list FROM IDENT [join] [where] [order_by] [';'] EOF
select_list   → '*' | column { ',' column }
column        → IDENT [ '.' IDENT ]
join          → JOIN IDENT ON column '=' column
where         → WHERE or_condition
or_condition  → and_condition { OR and_condition }
and_condition → comparison { AND comparison }
comparison    → operand ( = | != | <> | < | <= | > | >= ) operand
operand       → column | NUMBER | STRING
order_by      → ORDER BY column [ ASC | DESC ]
```

## Example Output

```
[Phase 5] Optimization: 2 rule(s) applied
────────────────────────────────────────────────────────────────────────
π Project: employees.name, departments.dept_name
└── τ Sort: employees.salary DESC
    └── ⋈ Join: employees.dept_id = departments.id
        ├── σ Filter: employees.salary > 50000
        │   └── Scan: employees
        └── σ Filter: departments.location = 'Hyderabad'
            └── Scan: departments

Rules applied:
  1. Predicate pushdown: moved "employees.salary > 50000" below the join, onto 'employees'
  2. Predicate pushdown: moved "departments.location = 'Hyderabad'" below the join, onto 'departments'

[Phase 6] Code Generation: Execution Plan (read bottom-up)
────────────────────────────────────────────────────────────────────────
Project: employees.name, departments.dept_name  (rows=33 cost=33)
└── Sort: employees.salary DESC  (rows=33 cost=169)
    └── Nested Loop Join: employees.dept_id = departments.id  (rows=33 cost=333)
        ├── Filter: employees.salary > 50000  (rows=333 cost=1,000)
        │   └── Seq Scan on employees  (rows=1,000 cost=1,000)
        └── Filter: departments.location = 'Hyderabad'  (rows=1 cost=10)
            └── Seq Scan on departments  (rows=10 cost=10)

Planner decisions:
  - Chose Nested Loop Join (Nested Loop Join cost 333 vs Hash Join cost 335): one input is small

Estimated total cost: 2,555  (without optimization: 3,232)
Optimization saved about 21% of the estimated work.
```

An error, reported with its phase, position and a suggestion:

```
✘ Compilation failed during Phase 3 (Semantic Analysis)
────────────────────────────────────────────────────────────────────────
Semantic Error at line 1, column 18: Table 'employes' does not exist (did you mean 'employees'?)

    SELECT name FROM employes
                     ^
```

### Screenshots

> Add screenshots of your terminal here (for example `docs/screenshots/full-pipeline.png` and `docs/screenshots/errors.png`) and reference them like this:
>
> `![Full pipeline](docs/screenshots/full-pipeline.png)`

## Configuration

The tables the compiler knows about live in **`data/schema.json`**:

```json
{
  "tables": {
    "employees": {
      "columns": { "id": "INT", "name": "TEXT", "age": "INT", "salary": "INT", "dept_id": "INT" },
      "row_count": 1000
    },
    "departments": {
      "columns": { "id": "INT", "dept_name": "TEXT", "location": "TEXT" },
      "row_count": 10
    }
  }
}
```

- **Types:** `INT`, `FLOAT`, `TEXT`. INT and FLOAT can be compared with each other; TEXT only with TEXT.
- **`row_count`** (optional, default 1000) feeds the row and cost estimates in Phase 6.
- Names must be valid identifiers and not SQL keywords; the file is fully validated on load.

Add a table by adding an entry here; no code changes are needed. Try changing `departments.row_count` to `500` and watch the join algorithm and costs change.

## Testing

```bash
pytest                               # all 195 tests
pytest -v                            # one line per test
pytest tests/test_optimizer.py -v    # one module's tests
pytest -k pushdown                   # tests whose name contains "pushdown"
```

| Test file | Covers |
|---|---|
| `test_lexer.py` | tokens, positions, comments, lexical errors |
| `test_parser.py` | grammar, precedence, associativity, syntax errors |
| `test_semantic.py` | schema validation, name resolution, scope, types |
| `test_logical_plan.py` | plan shapes, relational algebra output |
| `test_optimizer.py` | constant folding, pushdown legality, filter merging |
| `test_physical_plan.py` | selectivity, row estimates, costs, join choice |
| `test_cli.py` | full pipeline, every CLI mode, exit codes, example files |

## Continuous Integration

`.github/workflows/tests.yml` runs on every push and pull request to `main`, using a matrix of **Ubuntu + Windows × Python 3.10, 3.12, 3.13, 3.14** (8 jobs). Each job:

1. runs the full test suite,
2. compiles every query in `examples/queries.sql`, and
3. checks that every query in `examples/errors.sql` is rejected with exit code 1.

Results appear on the repository's **Actions** tab and as the badge at the top of this README.

## Troubleshooting

| Problem | Cause | Fix |
|---|---|---|
| `ModuleNotFoundError: No module named 'compiler'` | Running a phase file directly (`python compiler/lexer.py`) or from the wrong folder | Run from the project root, using `python -m compiler.lexer` or `python main.py` |
| `PermissionError: [WinError 5] ... pytest-of-<user>` | Windows denied access to pytest's folder in `%TEMP%` | Already handled: `pytest.ini` sets `--basetemp=.pytest_tmp`. Make sure you have the latest `pytest.ini` |
| Tree lines or π σ ⋈ show as `?` or garbage | Terminal not using UTF-8 | `main.py` switches to UTF-8 automatically; for `python -m compiler.*` demos run `set PYTHONUTF8=1` (Windows) first |
| `pytest` not found | Environment not activated | `conda activate sqlcompiler` (or activate your venv), then `pip install -r requirements.txt` |
| `Double quotes are not supported` | Used `"Sales"` inside the SQL | SQL strings use single quotes: `'Sales'` |
| Query cut off on Windows | Inner and outer quotes clash | Outer double quotes, inner single quotes |
| `Column 'id' is ambiguous` | Both joined tables have an `id` column | Qualify it: `employees.id` or `departments.id` |

## Limitations and Future Work

Deliberately out of scope to keep the project focused: `GROUP BY`/aggregates, subqueries, parentheses in `WHERE`, multiple joins, table aliases, outer joins, `LIMIT`, and `INSERT`/`UPDATE`/`DELETE`.

Natural next steps:

- Parentheses in conditions, then `GROUP BY` with `COUNT`/`SUM`/`AVG`.
- Multiple joins with join-order optimization (dynamic programming, as in System R).
- Projection pushdown and an always-false short-circuit to an empty result.
- Real statistics (distinct counts, histograms) instead of default selectivities.
- An **executor** that runs the physical plan on CSV data and returns real rows.
- A web interface (Streamlit) reusing `compiler/pipeline.py`.

See [`docs/PROJECT_REVIEW.md`](docs/PROJECT_REVIEW.md) for the full list, checklists and viva preparation.

## Contributing

1. Fork the repository and create a branch: `git checkout -b feature/group-by`
2. Make your change and add tests for it.
3. Run `pytest`; all tests must pass.
4. Commit with a clear message and open a pull request against `main`.

Please keep the style: one responsibility per module, docstrings on every function, frozen dataclasses for tree nodes, and no runtime dependencies.

## License

Released under the [MIT License](LICENSE).

## Acknowledgements

- Aho, Lam, Sethi & Ullman, *Compilers: Principles, Techniques, and Tools* (the "Dragon Book"), for the phase structure.
- Silberschatz, Korth & Sudarshan, *Database System Concepts*, for relational algebra and query processing.
- Selinger et al., *Access Path Selection in a Relational Database Management System* (1979, IBM System R), for the default selectivity estimates.
- PostgreSQL's `EXPLAIN` output, which inspired the execution-plan format.
