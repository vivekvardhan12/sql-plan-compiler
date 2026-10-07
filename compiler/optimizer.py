"""
optimizer.py  -  PHASE 5: CODE OPTIMIZATION
===========================================
Rewrites the logical plan into an EQUIVALENT plan that does less work.
"Equivalent" means: same rows in the result, guaranteed, for every possible
table contents. Only the amount of work changes.

This is a RULE-BASED optimizer: it applies fixed rewrite rules that are
always beneficial, without estimating costs.

The three rules
---------------
1. CONSTANT FOLDING (the classic compiler optimization)
   Evaluate parts of the condition that don't depend on any table at
   compile time, instead of re-checking them for every row:
       salary > 5 AND 1 = 1   ->  salary > 5
       age < 30 OR 1 = 1      ->  (always true; filter removed entirely)

2. PREDICATE PUSHDOWN (the most important database optimization)
   Split the WHERE clause at its ANDs, then move each piece as far DOWN the
   tree as possible, so rows are thrown away BEFORE the expensive join:

       BEFORE                               AFTER
       σ salary>5 AND location='X'          ⋈ Join
       └── ⋈ Join                           ├── σ salary > 5
           ├── Scan employees               │   └── Scan employees
           └── Scan departments             └── σ location = 'X'
                                                └── Scan departments

   A condition can go below a join only onto a side that has ALL the tables
   the condition needs. A condition using both tables must stay above it.

3. FILTER MERGING (cleanup)
   If several conditions land in the same place, combine the stacked
   filters back into one:  σ[a](σ[b](R))  ->  σ[a AND b](R)

Every rule that changes something writes a human-readable line to a log, so
the user can see exactly WHY the plan changed.

Run this file directly to see it in action:
    python -m compiler.optimizer
    python -m compiler.optimizer "SELECT * FROM employees WHERE age < 30 AND 1 = 1"
"""

import operator
import sys
from dataclasses import dataclass, replace

from compiler.ast_nodes import Comparison, Condition, Literal, LogicalOp
from compiler.errors import CompilerError
from compiler.logical_plan import (
    Filter,
    Join,
    LogicalNode,
    Scan,
    compile_to_logical_plan,
    condition_tables,
    format_condition,
    format_logical_plan,
)
from compiler.schema import Schema

# Maps each SQL comparison symbol to the Python function that performs it.
# Using these functions (instead of eval()) is safe: no text is ever executed as code.
COMPARE_FUNCTIONS = {
    "=": operator.eq,
    "!=": operator.ne,
    "<": operator.lt,
    "<=": operator.le,
    ">": operator.gt,
    ">=": operator.ge,
}


# ---------------------------------------------------------------------------
# Condition utilities
# ---------------------------------------------------------------------------

def fold_constants(condition: Condition) -> Condition | bool:
    """
    RULE 1: simplify every part of a condition that can be decided now.

    Returns:
        True       if the condition is ALWAYS true (e.g. 1 = 1),
        False      if it is ALWAYS false (e.g. 1 = 2),
        otherwise  a (possibly simplified) Condition.

    Logic rules used (A is any condition):
        A AND True  -> A          A OR True  -> True
        A AND False -> False      A OR False -> A
    """
    if isinstance(condition, Comparison):
        if isinstance(condition.left, Literal) and isinstance(condition.right, Literal):
            compare = COMPARE_FUNCTIONS[condition.operator]
            return bool(compare(condition.left.value, condition.right.value))
        return condition   # involves a column, so it must be checked per row

    # A LogicalOp: fold both sides first (recursion), then combine.
    left = fold_constants(condition.left)
    right = fold_constants(condition.right)

    if condition.operator == "AND":
        if left is False or right is False:
            return False
        if left is True:
            return right
        if right is True:
            return left
    else:  # "OR"
        if left is True or right is True:
            return True
        if left is False:
            return right
        if right is False:
            return left

    # Neither side was a constant: rebuild the node (children may have been simplified).
    return LogicalOp(condition.operator, left, right)


def split_conjuncts(condition: Condition) -> list[Condition]:
    """
    Break a condition at its top-level ANDs into independent pieces ("conjuncts").

        a AND b AND c        ->  [a, b, c]
        a OR (b AND c)       ->  [a OR (b AND c)]   (an OR can't be split)

    This is valid because a row passes "a AND b" exactly when it passes
    both a and b, so each piece can be checked separately, in any place.
    """
    if isinstance(condition, LogicalOp) and condition.operator == "AND":
        return split_conjuncts(condition.left) + split_conjuncts(condition.right)
    return [condition]


def combine_conjuncts(conditions: list[Condition]) -> Condition:
    """
    The reverse of split_conjuncts: join pieces with AND, left to right.

        [a, b, c]  ->  (a AND b) AND c
    """
    combined = conditions[0]
    for condition in conditions[1:]:
        combined = LogicalOp("AND", combined, condition)
    return combined


def with_children(node: LogicalNode, children: list[LogicalNode]) -> LogicalNode:
    """
    Return a copy of `node` with new children (nodes are frozen, so we copy).

    This one helper lets every rewrite pass rebuild ANY node type generically.
    """
    if isinstance(node, Scan):
        return node
    if isinstance(node, Join):
        return replace(node, left=children[0], right=children[1])
    # Filter, Sort, and Project all have exactly one child.
    return replace(node, child=children[0])


def describe_tables(tables: frozenset[str]) -> str:
    """Text like "'employees'" or "'departments', 'employees'" for log messages."""
    return ", ".join(f"'{name}'" for name in sorted(tables))


# ---------------------------------------------------------------------------
# The optimizer
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class OptimizationResult:
    """
    What the optimizer returns.

    Attributes:
        plan:          The optimized logical plan.
        applied_rules: One readable sentence per change made, in order.
                       Empty if nothing could be improved.
    """

    plan: LogicalNode
    applied_rules: tuple[str, ...]


class Optimizer:
    """
    Applies the rules in two passes over the plan tree:

        Pass 1: constant folding + predicate pushdown (at each Filter)
        Pass 2: merge any filters that ended up stacked on top of each other

    The input plan is never modified; new nodes are built instead.
    """

    def __init__(self):
        """Start with an empty log of applied rules."""
        self.applied_rules: list[str] = []

    def optimize(self, plan: LogicalNode) -> OptimizationResult:
        """Run all passes and return the new plan plus the log."""
        self.applied_rules = []
        pushed_plan = self._rewrite(plan)
        merged_plan = self._merge_filters(pushed_plan)
        return OptimizationResult(merged_plan, tuple(self.applied_rules))

    # ------------------------------------------------------------------
    # Pass 1: walk the tree, optimizing every Filter found
    # ------------------------------------------------------------------

    def _rewrite(self, node: LogicalNode) -> LogicalNode:
        """Recursively rebuild the tree; Filters get special treatment."""
        if isinstance(node, Filter):
            return self._optimize_filter(node)
        new_children = [self._rewrite(child) for child in node.children]
        return with_children(node, new_children)

    def _optimize_filter(self, filter_node: Filter) -> LogicalNode:
        """Apply constant folding, then push each AND-piece down the tree."""
        original_text = format_condition(filter_node.condition)
        folded = fold_constants(filter_node.condition)
        child = self._rewrite(filter_node.child)

        if folded is True:
            self.applied_rules.append(
                f'Constant folding: WHERE "{original_text}" is always true, so the filter was removed'
            )
            return child   # no filter needed at all

        if folded is False:
            self.applied_rules.append(
                f'Constant folding: WHERE "{original_text}" is always false, '
                "so the query will return no rows (filter kept)"
            )
            return Filter(filter_node.condition, child)

        if folded != filter_node.condition:
            self.applied_rules.append(
                f'Constant folding: simplified "{original_text}" to "{format_condition(folded)}"'
            )

        # RULE 2: split at the ANDs and push each piece down independently.
        for conjunct in split_conjuncts(folded):
            child = self._push_down(conjunct, child)
        return child

    def _push_down(self, condition: Condition, node: LogicalNode) -> LogicalNode:
        """
        Place `condition` as low in the tree as it can legally go.

        Args:
            condition: One conjunct (no top-level AND).
            node:      The subtree the condition must apply to.

        Returns:
            The new subtree with the condition placed inside it.
        """
        needed_tables = condition_tables(condition)

        if isinstance(node, Join):
            # "<=" between sets means "is a subset of".
            if needed_tables <= node.left.tables:
                self.applied_rules.append(
                    f'Predicate pushdown: moved "{format_condition(condition)}" below the join, '
                    f"onto {describe_tables(node.left.tables)}"
                )
                return replace(node, left=self._push_down(condition, node.left))

            if needed_tables <= node.right.tables:
                self.applied_rules.append(
                    f'Predicate pushdown: moved "{format_condition(condition)}" below the join, '
                    f"onto {describe_tables(node.right.tables)}"
                )
                return replace(node, right=self._push_down(condition, node.right))

            self.applied_rules.append(
                f'Kept "{format_condition(condition)}" above the join, '
                "because it uses columns from both tables"
            )
            return Filter(condition, node)

        if isinstance(node, Filter):
            # Filters can be applied in any order (σa(σb(R)) = σb(σa(R))),
            # so slip underneath the existing filter and keep pushing.
            return replace(node, child=self._push_down(condition, node.child))

        # A Scan (or anything else): this is as low as the condition can go.
        return Filter(condition, node)

    # ------------------------------------------------------------------
    # Pass 2: merge stacked filters into one
    # ------------------------------------------------------------------

    def _merge_filters(self, node: LogicalNode) -> LogicalNode:
        """
        RULE 3: turn σ[a](σ[b](R)) into σ[a AND b](R), keeping the original order.
        Rebuilds the tree bottom-up so every stack is found.
        """
        if isinstance(node, Filter):
            # Collect the whole chain of directly stacked filters.
            conditions = []
            current: LogicalNode = node
            while isinstance(current, Filter):
                conditions.extend(split_conjuncts(current.condition))
                current = current.child
            below = self._merge_filters(current)
            return Filter(combine_conjuncts(conditions), below)

        new_children = [self._merge_filters(child) for child in node.children]
        return with_children(node, new_children)


# ---------------------------------------------------------------------------
# Convenience functions used by the rest of the project
# ---------------------------------------------------------------------------

def optimize(plan: LogicalNode) -> OptimizationResult:
    """Shortcut: optimize a logical plan with a fresh Optimizer."""
    return Optimizer().optimize(plan)


def compile_to_optimized_plan(source: str, schema: Schema | None = None) -> OptimizationResult:
    """
    Run Phases 1-5 on a SQL string.

    Raises:
        CompilerError (Lexer/Parser/Semantic/Schema) if the query is invalid.
    """
    return optimize(compile_to_logical_plan(source, schema))


# ---------------------------------------------------------------------------
# Demo: python -m compiler.optimizer ["optional SQL here"]
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    DEFAULT_QUERY = (
        "SELECT name, dept_name FROM employees\n"
        "JOIN departments ON dept_id = departments.id\n"
        "WHERE salary > 50000 AND location = 'Hyderabad' AND age < 40\n"
        "ORDER BY salary DESC;"
    )
    query_text = " ".join(sys.argv[1:]) if len(sys.argv) > 1 else DEFAULT_QUERY

    print("Input SQL:")
    print(query_text)
    print()

    try:
        logical_plan = compile_to_logical_plan(query_text)
    except CompilerError as error:
        print(error.pretty(query_text))
        sys.exit(1)

    result = optimize(logical_plan)

    print("BEFORE optimization:")
    print(format_logical_plan(logical_plan))
    print()
    print("AFTER optimization:")
    print(format_logical_plan(result.plan))
    print()
    print("Rules applied:")
    if result.applied_rules:
        for number, rule in enumerate(result.applied_rules, start=1):
            print(f"  {number}. {rule}")
    else:
        print("  None: no rule could improve this plan.")
    print()
    print("Relational algebra (after):")
    print(result.plan.to_algebra())
