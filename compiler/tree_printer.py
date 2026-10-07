"""
tree_printer.py
===============
A tiny, reusable helper that draws any tree as text, like the `tree` command:

    SelectQuery
    ├── SELECT
    │   ├── Column: name
    │   └── Column: salary
    └── FROM
        └── Table: employees

Why a separate file?
    We need to print trees in more than one phase: the AST (Phase 2) and the
    execution plan (Phase 6). Writing the drawing logic once and reusing it
    follows the DRY principle ("Don't Repeat Yourself").

How to use it:
    Build TreeNode objects (a label plus a list of children), then call
    render_tree(root).
"""

from dataclasses import dataclass, field

# The four drawing pieces.
BRANCH = "├── "      # a child that has more siblings below it
LAST_BRANCH = "└── " # the final child in a list
VERTICAL = "│   "    # continues a parent's line down past a child
SPACE = "    "       # nothing more to draw under a finished branch


@dataclass
class TreeNode:
    """
    One node of a display tree.

    Attributes:
        label:    The text shown for this node.
        children: The nodes shown underneath it (empty list = leaf node).
    """

    label: str
    children: list["TreeNode"] = field(default_factory=list)


def render_tree(root: TreeNode) -> str:
    """
    Return the whole tree as a multi-line string.

    Args:
        root: The top node of the tree.
    """
    lines = [root.label]                 # the root has no connector in front of it
    _render_children(root.children, prefix="", lines=lines)
    return "\n".join(lines)


def _render_children(children: list[TreeNode], prefix: str, lines: list[str]) -> None:
    """
    Recursively add one line per child (and its descendants) to `lines`.

    Args:
        children: The nodes to draw at this depth.
        prefix:   The vertical bars/spaces inherited from the ancestors, so
                  that deeper levels line up under their parents.
        lines:    The output list being built (modified in place).
    """
    for index, child in enumerate(children):
        is_last_child = index == len(children) - 1
        connector = LAST_BRANCH if is_last_child else BRANCH
        lines.append(prefix + connector + child.label)

        # If this child had siblings below it, keep drawing a vertical bar for them;
        # otherwise the column is empty from here down.
        child_prefix = prefix + (SPACE if is_last_child else VERTICAL)
        _render_children(child.children, child_prefix, lines)
