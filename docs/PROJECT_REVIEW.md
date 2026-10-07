# Project Review: SQL → Execution Plan Compiler

Final review notes for submission, the viva, interviews and the presentation.

---

## 1. Project Checklist

**Compiler phases**
- [x] Phase 1: Lexer with line/column tracking, comments, maximal munch, helpful errors
- [x] Phase 2: Recursive descent parser (LL(1)), AND/OR precedence, left associativity
- [x] Phase 3: Semantic analyzer with symbol table, scope, name resolution, type checking, suggestions
- [x] Phase 4: Logical plan in relational algebra (π, σ, ⋈, τ), tree and algebra output
- [x] Phase 5: Optimizer with constant folding, predicate pushdown, filter merging, rule log
- [x] Phase 6: Physical plan with row estimates, cost model, Nested Loop vs Hash Join choice

**Engineering**
- [x] Pipeline module separating logic from presentation
- [x] CLI with single-query, file and interactive modes, phase selection, exit codes, `--version`
- [x] Schema as validated JSON (no database required)
- [x] Custom error hierarchy with caret-pointing messages
- [x] Immutable (frozen) AST and plan nodes
- [x] 195 automated tests (unit + integration)
- [x] GitHub Actions CI: 2 operating systems × 4 Python versions
- [x] Example query files (valid and invalid), checked by tests and CI
- [x] README, LICENSE, requirements.txt, .gitignore, pytest.ini
- [x] Zero runtime dependencies

**Before submitting**
- [ ] Replace `USERNAME` in README badge links
- [ ] Add terminal screenshots to `docs/screenshots/` and link them in the README
- [ ] Push to GitHub and confirm the Actions badge is green
- [ ] Create the `v1.0.0` release
- [ ] Prepare the demo commands (Section 9)

---

## 2. Architecture Summary

A six-phase pipeline. Each phase is one module, takes the previous phase's output, and returns a new immutable structure:

| Phase | Module | Output | Key technique |
|---|---|---|---|
| 1 | `lexer.py` | `list[Token]` | Hand-coded DFA, maximal munch |
| 2 | `parser.py` | `SelectQuery` AST | Recursive descent, one method per grammar rule |
| 3 | `semantic.py` | Resolved AST | Symbol table (hash map), scope list, type rules |
| 4 | `logical_plan.py` | Relational algebra tree | Fixed bottom-up construction |
| 5 | `optimizer.py` | Optimized tree + rule log | Equivalence rules, two passes |
| 6 | `physical_plan.py` | Execution plan + decisions | Selectivity estimation, cost comparison |

`pipeline.py` drives the phases and returns every intermediate result (or the error and everything before it). `main.py` only formats and prints. The same pipeline could power a web UI unchanged.

**Design principles applied:** single responsibility per module; open/closed (new node types plug into polymorphic `label()`/`children`); dependency injection (schema passed in, so tests use their own); immutability; errors as data in the pipeline; fail fast with guards for programming errors (`ValueError`/`TypeError`) kept separate from user errors (`CompilerError`).

---

## 3. Known Limitations (Missing Improvements)

| Limitation | Why it was left out | Effort to add |
|---|---|---|
| No parentheses in `WHERE` | Lexer has no `(`/`)` tokens; grammar kept minimal | Small |
| One join only | Join-order optimization adds major complexity | Medium–Large |
| No table aliases / self-joins | Requires an alias layer in scope resolution | Medium |
| No `GROUP BY`, aggregates, `LIMIT`, subqueries | Out of scope for a first version | Medium each |
| No outer joins | Pushdown rules differ for outer joins | Medium |
| Default selectivities, independence assumption | No real data statistics | Medium |
| Always-false `WHERE` not short-circuited | Needs an "empty result" plan node | Small |
| No projection pushdown | Rule-based optimizer kept to three rules | Small |
| Plans are not executed | Project goal is compilation, not execution | Medium |
| Escaped quotes (`'O''Brien'`) unsupported | Lexer simplicity | Small |

---

## 4. Future Enhancements (Suggested Order)

1. **Parentheses in conditions.** Add `LPAREN`/`RPAREN` tokens and a `primary → '(' or_condition ')' | comparison` rule.
2. **Executor.** Run the physical plan on CSV files with iterator-style operators (the Volcano model), so queries return real rows.
3. **GROUP BY + aggregates** (`COUNT`, `SUM`, `AVG`, `MIN`, `MAX`), adding γ (gamma) to the algebra and a Hash Aggregate operator.
4. **Multiple joins + join ordering** using dynamic programming over join subsets (System R style).
5. **Statistics.** Store distinct counts and min/max per column; use them for selectivity (e.g. range fraction = (value − min)/(max − min)).
6. **Indexes.** Add index definitions to the schema and an Index Scan operator; choose between Seq Scan and Index Scan by cost.
7. **More optimizer rules:** projection pushdown, always-false short-circuit, redundant predicate removal.
8. **Web UI** (Streamlit) showing each phase in tabs, reusing `pipeline.py`.
9. **Graphviz export** of plans as images for reports.
10. **Comparison mode:** run the same query through PostgreSQL's `EXPLAIN` and display both plans side by side.

---

## 5. Viva Questions (with short answers)

**Compiler Design**

1. *What are the phases of a compiler, and how does your project map to them?*
   Lexical analysis → tokens; syntax analysis → AST; semantic analysis → resolved AST; intermediate code generation → relational algebra; optimization → rewritten algebra; code generation → physical execution plan.

2. *What is the difference between a token, a lexeme and a pattern?*
   Token = category (`IDENTIFIER`); lexeme = actual text (`Employees`); pattern = the rule describing valid lexemes (letter or `_`, then letters/digits/`_`).

3. *What is maximal munch?*
   Always take the longest possible token. `<=` must become one `LESS_EQUAL` token, not `<` then `=`. Two-character operators are checked before one-character ones.

4. *Why did you hand-write the lexer instead of using Lex/Flex?*
   About 30 token types is small enough; hand-written gives custom error messages, no dependencies, and fully explainable code. GCC and Clang also hand-write theirs.

5. *What type of parser did you build?*
   Recursive descent, top-down, LL(1): left-to-right scan, leftmost derivation, one token of lookahead, no backtracking.

6. *Why can't recursive descent handle left recursion, and how did you avoid it?*
   A rule like `or_cond → or_cond OR and_cond` makes the function call itself immediately and recurse forever. I rewrote it as `or_cond → and_cond { OR and_cond }`, implemented as a `while` loop.

7. *How does your parser make AND bind tighter than OR?*
   Grammar layering: `_parse_or_condition` calls `_parse_and_condition` for each operand, so AND groups are built deeper in the tree. Lower precedence = outer rule.

8. *What is the difference between a parse tree and an AST?*
   A parse tree has a node for every grammar rule and token (commas included). An AST keeps only meaningful structure.

9. *Why is semantic analysis a separate phase from parsing?*
   Rules like "a column must exist" are context-sensitive; a context-free grammar cannot express them.

10. *What is the symbol table in your project?*
    `schema.json`, loaded into a hash map from table name → (column → type, row count). Lookups are O(1) on average.

11. *Explain scope in your compiler.*
    Only columns of tables named in FROM/JOIN are visible. `dept_name` exists in the schema but is out of scope in `SELECT dept_name FROM employees`.

12. *What are synthesized and inherited attributes? Give examples from your code.*
    Inherited: scope (`tables_in_scope`) flows down to every column. Synthesized: whether a comparison is type-correct is computed from its children's types.

13. *What is an intermediate representation and why use one?*
    A simpler, uniform form between source and target. It decouples the front end from the back end and makes optimization easier. Ours is relational algebra.

14. *What is constant folding?*
    Evaluating constant expressions at compile time. `age < 30 AND 1 = 1` becomes `age < 30`; `age < 30 OR 1 = 1` removes the filter.

15. *How do you guarantee an optimization is correct?*
    Each rewrite is a proven relational algebra equivalence that holds for every possible database content, e.g. σ[c](R ⋈ S) ≡ σ[c](R) ⋈ S when c only uses R.

16. *What is your error recovery strategy?*
    Panic mode at the query level: a query stops at its first error, but compilation continues with the next query in file and interactive modes.

**Databases**

17. *Selection vs projection in relational algebra?*
    Selection (σ) filters rows, which is SQL's WHERE. Projection (π) keeps columns, which is SQL's SELECT list.

18. *What is predicate pushdown and why does it help?*
    Moving filters below joins so fewer rows are joined. Example: join input drops from 1,000 × 10 to about 333 × 1 rows.

19. *Why can't `age < 30 OR location = 'Pune'` be pushed below the join?*
    It needs columns from both tables. Splitting an OR would wrongly drop rows that satisfy only the other side.

20. *Is pushdown always valid?*
    For inner joins, yes. For outer joins, not always, since they preserve unmatched rows.

21. *Logical plan vs physical plan?*
    Logical says what to compute (join these tables). Physical says how (Hash Join with departments in the hash table).

22. *Compare Nested Loop Join and Hash Join.*
    Nested loop: O(L × R) time, no extra memory, best when one input is tiny. Hash join: O(L + R) average time, O(smaller input) memory, best when both are large.

23. *How do you estimate the number of rows?*
    System R default selectivities: equality 1/10, inequality 9/10, range 1/3; AND multiplies, OR uses inclusion–exclusion. Join: 1 / distinct key values, approximated by the smaller table's row count.

24. *What is the independence assumption and when does it fail?*
    Multiplying selectivities for AND assumes the conditions are unrelated. It fails for correlated columns (e.g. age and salary).

25. *Why does SQL process FROM before SELECT?*
    FROM and JOIN decide which columns exist. That's why a column from a JOIN table is visible in SELECT, and why SELECT aliases aren't visible in WHERE.

---

## 6. Interview Questions (Software Engineering)

1. *Walk me through the architecture.* Six-phase pipeline, one module per phase, immutable data passed between them, a pipeline driver, and a thin CLI on top.
2. *Why immutable dataclasses for trees?* Later phases cannot corrupt earlier results, before/after comparisons are trivial, and equality-based testing works out of the box.
3. *How did you separate logic from presentation?* `pipeline.py` returns a `CompilationResult` and never prints; `main.py` only formats. A web UI could reuse the pipeline unchanged.
4. *How do you handle errors?* A `CompilerError` hierarchy (Lexer/Parser/Semantic/Schema) carrying line and column. The pipeline stores errors as data; programming mistakes raise `ValueError`/`TypeError` instead.
5. *How did you test it?* 195 pytest tests: unit tests per phase, integration tests for the full pipeline and CLI, using fixtures, `parametrize`, `capsys`, `monkeypatch` and `tmp_path`. CI runs them on Linux and Windows across four Python versions.
6. *What bugs did you hit?* (a) A floating-point tie: `1000 × 0.1 × 0.1 = 10.000000000000002`, so I never test exact float equality. (b) Windows `PermissionError` on pytest's temp folder, fixed with `--basetemp`. (c) The semantic analyzer caught an ambiguous column in my own test.
7. *What is the time complexity?* Every phase is linear in the size of its input. Compiling a query takes about 1 ms.
8. *How would you scale this to many joins?* Join ordering is exponential if done naively. Use dynamic programming over subsets with pruning (System R), or heuristics for very large joins.
9. *Why not use `eval` for constant folding?* `eval` executes arbitrary code. A lookup table of `operator` functions is safe.
10. *What would you do differently in production?* Real statistics, an executor, more SQL features, a fuzz-testing suite, and structured logging.

---

## 7. Resume Bullet Points

Pick 2–3:

- Built a **SQL-to-execution-plan compiler** in Python implementing all six compiler phases from scratch (hand-written lexer, recursive descent LL(1) parser, semantic analyzer, relational algebra IR, rule-based optimizer, cost-based physical planner) with **zero runtime dependencies**.
- Implemented **predicate pushdown and constant folding** using relational algebra equivalence rules, plus **cost-based selection between Nested Loop and Hash Join** using System R selectivity estimates; reduced estimated query cost by up to ~30% on demo workloads.
- Designed a **semantic analyzer** with symbol-table name resolution, ambiguity detection, static type checking and fuzzy "did you mean" suggestions, reporting errors with exact line/column positions.
- Wrote **195 unit and integration tests** (pytest) and a **GitHub Actions CI pipeline** testing across Linux/Windows and Python 3.10–3.14.

---

## 8. Presentation Outline (10–12 slides)

1. **Title.** Project name, your name, course.
2. **Problem.** SQL says *what*, not *how*; databases must compile queries into plans. Show a PostgreSQL `EXPLAIN` example.
3. **Objective.** Build every compiler phase by hand and make each one visible.
4. **Architecture.** The six-phase pipeline diagram and module table.
5. **Phases 1–2.** Token table and AST tree for one query; mention maximal munch and AND/OR precedence.
6. **Phase 3.** Resolved AST; error examples (typo suggestion, ambiguous column, type mismatch).
7. **Phase 4.** Logical plan tree + relational algebra expression; selection vs projection.
8. **Phase 5.** BEFORE/AFTER plans with the rule log; why OR cannot be pushed.
9. **Phase 6.** Execution plan with rows and costs; Hash Join vs Nested Loop decision.
10. **Testing & CI.** 195 tests, the green Actions badge, the OS × Python matrix.
11. **Limitations & future work.** Pick the top 4 from Section 4.
12. **Live demo / Q&A.**

---

## 9. Demo Script (about 5 minutes)

```bash
# 1. All six phases for the headline query
python main.py "SELECT name, dept_name FROM employees JOIN departments ON dept_id = departments.id WHERE salary > 50000 AND location = 'Hyderabad' ORDER BY salary DESC"

# 2. Same join without filters: the planner switches to Hash Join
python main.py "SELECT name, dept_name FROM employees JOIN departments ON dept_id = departments.id" --show physical

# 3. An OR across tables stays above the join
python main.py "SELECT name FROM employees JOIN departments ON dept_id = departments.id WHERE age < 30 OR location = 'Pune'" --show optimized

# 4. Constant folding
python main.py "SELECT name FROM employees WHERE age > 30 AND 1 = 1" --show optimized

# 5. Every kind of error
python main.py --file examples/errors.sql --show tokens

# 6. Tests
pytest
```

---

## 10. Deployment Checklist

This is a command-line tool, so "deployment" means publishing the repository and keeping it installable and green.

- [ ] All tests pass locally (`pytest`)
- [ ] `requirements.txt` up to date
- [ ] `.gitignore` excludes caches, temp folders, environments, `.env`
- [ ] No secrets, personal paths or large generated files committed
- [ ] README installation steps tested from a fresh clone
- [ ] Pushed to GitHub; Actions workflow green on all 8 jobs
- [ ] Version in `compiler/__init__.py` matches the release tag
- [ ] Tag `v1.0.0` created and GitHub Release published with notes
- [ ] Repository description, topics (`compiler`, `sql`, `query-optimizer`, `python`) and license set on GitHub

---

## 11. Security Checklist

- [x] No `eval`/`exec`: constant folding uses a fixed table of `operator` functions
- [x] Input is never executed; it is only tokenized, parsed and validated
- [x] Lexer rejects unknown characters; parser requires end-of-input (no ignored trailing text)
- [x] Recursion depth is bounded (AND/OR chains use loops), so huge inputs cannot overflow the stack
- [x] Schema file fully validated (types, names, row counts) before use
- [x] Files read as UTF-8 explicitly; only files the user names are read; nothing is written
- [x] User-facing errors never show Python tracebacks
- [x] CI workflow uses least-privilege `permissions: contents: read`
- [x] No network access, credentials or secrets anywhere in the project

---

## 12. Performance Checklist

- [x] Every phase is O(n) in its input size; no backtracking parser
- [x] Symbol table lookups are O(1) (hash maps)
- [x] Schema loaded once per run, shared across queries and phases
- [x] Immutable nodes allow sharing unchanged subtrees instead of copying
- [x] "Did you mean" suggestions run only after an error is found
- [x] Measured: about 1 ms per query for all six phases
- [ ] (Future) Benchmark suite with generated large queries
- [ ] (Future) Profile with `python -m cProfile main.py --file examples/queries.sql`
