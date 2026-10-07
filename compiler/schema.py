"""
schema.py
=========
Loads data/schema.json and turns it into Python objects that the semantic
analyzer can query: "does table X exist?", "what type is column Y?".

In compiler terms, the schema is our SYMBOL TABLE: the data structure that
stores every name the program may legally use, plus facts about each name
(here: the column's data type and the table's row count).

Example schema.json:
    {
      "tables": {
        "employees": {
          "columns": { "id": "INT", "name": "TEXT", "salary": "INT" },
          "row_count": 1000
        }
      }
    }

row_count is not needed for checking queries. It is kept for Phase 6, where
the execution plan shows estimated row counts, like a real database does.
"""

import json
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

from compiler.errors import SchemaError
from compiler.lexer import is_identifier_part, is_identifier_start
from compiler.tokens import KEYWORDS
from compiler.tree_printer import TreeNode, render_tree

# Absolute path to data/schema.json, built from THIS file's location
# (compiler/schema.py -> parent = compiler/ -> parent = project root).
# This works no matter which folder you run Python from.
DEFAULT_SCHEMA_PATH = Path(__file__).resolve().parent.parent / "data" / "schema.json"

# Used when a table in the JSON doesn't specify "row_count".
DEFAULT_ROW_COUNT = 1000


class DataType(Enum):
    """The column data types our compiler understands."""

    INT = "INT"
    FLOAT = "FLOAT"
    TEXT = "TEXT"

    @property
    def is_numeric(self) -> bool:
        """True for INT and FLOAT."""
        return self in (DataType.INT, DataType.FLOAT)

    def is_comparable_with(self, other: "DataType") -> bool:
        """
        Can a value of this type be compared with a value of `other`?

        Rules:
            * Same type          -> yes  (TEXT vs TEXT, INT vs INT)
            * Both numeric       -> yes  (INT vs FLOAT, e.g. salary > 3.5)
            * Anything else      -> no   (INT vs TEXT, e.g. salary > 'abc')
        """
        return self == other or (self.is_numeric and other.is_numeric)


def is_valid_name(name: str) -> bool:
    """
    Return True if `name` could be typed in a query as a table/column name.

    It must follow the lexer's identifier rules (letter or _ first, then
    letters/digits/_) and must not be a keyword. A schema column called
    "first name" or "select" could never be referenced, so we reject it early.
    """
    if not name or not is_identifier_start(name[0]):
        return False
    if not all(is_identifier_part(char) for char in name):
        return False
    return name.upper() not in KEYWORDS


@dataclass(frozen=True)
class Table:
    """
    One table from the schema.

    Attributes:
        name:      Lowercase table name.
        columns:   Maps lowercase column name -> DataType, in schema order.
        row_count: Approximate number of rows (used for plan estimates later).
    """

    name: str
    columns: dict[str, DataType]
    row_count: int

    def has_column(self, column_name: str) -> bool:
        """Return True if this table has a column with that (lowercase) name."""
        return column_name in self.columns

    def column_type(self, column_name: str) -> DataType:
        """Return the DataType of a column. Call has_column() first."""
        return self.columns[column_name]

    @property
    def column_names(self) -> list[str]:
        """All column names, in the order they appear in the schema file."""
        return list(self.columns)


class Schema:
    """
    The whole database schema: a collection of tables looked up by name.

    Lookups use a dictionary, so finding a table takes O(1) time on average,
    no matter how many tables exist.
    """

    def __init__(self, tables: dict[str, Table]):
        """
        Args:
            tables: Maps lowercase table name -> Table.
        """
        self._tables = tables

    def has_table(self, table_name: str) -> bool:
        """Return True if a table with that (lowercase) name exists."""
        return table_name in self._tables

    def get_table(self, table_name: str) -> Table | None:
        """Return the Table, or None if it doesn't exist."""
        return self._tables.get(table_name)

    @property
    def table_names(self) -> list[str]:
        """All table names, sorted alphabetically (for stable error messages)."""
        return sorted(self._tables)

    @property
    def tables(self) -> list[Table]:
        """All Table objects, sorted by name."""
        return [self._tables[name] for name in self.table_names]

    @classmethod
    def from_dict(cls, data: object) -> "Schema":
        """
        Build a Schema from already-parsed JSON data, validating everything.

        Args:
            data: The object produced by json.load().

        Raises:
            SchemaError: describing exactly what is wrong with the data.
        """
        if not isinstance(data, dict) or not isinstance(data.get("tables"), dict):
            raise SchemaError('The schema must be a JSON object containing a "tables" object')
        if not data["tables"]:
            raise SchemaError("The schema must define at least one table")

        tables: dict[str, Table] = {}
        for raw_table_name, table_data in data["tables"].items():
            table = _build_table(raw_table_name, table_data)
            if table.name in tables:
                raise SchemaError(f"Table '{table.name}' is defined more than once")
            tables[table.name] = table

        return cls(tables)


def _build_table(raw_table_name: str, table_data: object) -> Table:
    """
    Validate one table's JSON entry and turn it into a Table object.

    Names are lowercased so they match the lexer, which lowercases every
    identifier in the query.
    """
    table_name = raw_table_name.lower()
    if not is_valid_name(table_name):
        raise SchemaError(f"'{raw_table_name}' is not a valid table name")

    columns_data = table_data.get("columns") if isinstance(table_data, dict) else None
    if not isinstance(columns_data, dict) or not columns_data:
        raise SchemaError(f"Table '{table_name}' must have a non-empty \"columns\" object")

    columns: dict[str, DataType] = {}
    for raw_column_name, raw_type in columns_data.items():
        column_name = raw_column_name.lower()
        if not is_valid_name(column_name):
            raise SchemaError(f"'{raw_column_name}' in table '{table_name}' is not a valid column name")
        if column_name in columns:
            raise SchemaError(f"Column '{column_name}' is defined twice in table '{table_name}'")
        try:
            columns[column_name] = DataType(str(raw_type).upper())
        except ValueError:
            allowed = ", ".join(data_type.value for data_type in DataType)
            raise SchemaError(
                f"Column '{table_name}.{column_name}' has unknown type '{raw_type}' (allowed: {allowed})"
            ) from None

    row_count = table_data.get("row_count", DEFAULT_ROW_COUNT)
    # bool is a subclass of int in Python (True == 1), so reject it explicitly.
    if not isinstance(row_count, int) or isinstance(row_count, bool) or row_count < 0:
        raise SchemaError(f"Table '{table_name}' has an invalid row_count (must be a whole number >= 0)")

    return Table(name=table_name, columns=columns, row_count=row_count)


def load_schema(path: str | Path = DEFAULT_SCHEMA_PATH) -> Schema:
    """
    Read a schema JSON file from disk and return a validated Schema.

    Args:
        path: Location of the JSON file (defaults to data/schema.json).

    Raises:
        SchemaError: if the file is missing, isn't valid JSON, or has bad content.
    """
    path = Path(path)
    if not path.is_file():
        raise SchemaError(f"Schema file not found: {path}")

    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as error:
        raise SchemaError(
            f"Invalid JSON in {path.name}: {error.msg} (line {error.lineno}, column {error.colno})"
        ) from None

    return Schema.from_dict(data)


def format_schema(schema: Schema) -> str:
    """
    Draw the schema as a tree (used by demos and the CLI):

        Schema
        └── employees (1,000 rows)
            ├── id: INT
            └── name: TEXT
    """
    table_nodes = []
    for table in schema.tables:
        column_nodes = [
            TreeNode(f"{column_name}: {data_type.value}")
            for column_name, data_type in table.columns.items()
        ]
        table_nodes.append(TreeNode(f"{table.name} ({table.row_count:,} rows)", column_nodes))
    return render_tree(TreeNode("Schema", table_nodes))
