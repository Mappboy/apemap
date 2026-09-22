"""Regression checks that notebooks remain read-only database consumers."""

from __future__ import annotations

import ast
import json
import re
from pathlib import Path


NOTEBOOKS_DIR = Path(__file__).resolve().parents[1] / "notebooks"
MUTATING_SQL = re.compile(
    r"\b(?:INSERT|UPDATE|DELETE|DROP|ALTER|CREATE\s+TABLE|TRUNCATE|REPLACE)\b",
    re.IGNORECASE,
)
DATABASE_FILE_DRIVERS = {"gpkg", "sqlite", "spatialite"}


def _static_text(node: ast.AST, assignments: dict[str, str]) -> str | None:
    """Resolve simple SQL expressions without treating documentation as code."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.Name):
        return assignments.get(node.id)
    if isinstance(node, ast.JoinedStr):
        return ast.unparse(node)
    if isinstance(node, ast.Call) and node.args:
        # SQLAlchemy's text("...") wrapper is common in the notebooks.
        return _static_text(node.args[0], assignments)
    return None


class DatabaseMutationVisitor(ast.NodeVisitor):
    """Find executable database writes while ignoring comments and string docs."""

    def __init__(self) -> None:
        self.assignments: dict[str, str] = {}
        self.violations: list[str] = []

    def visit_Assign(self, node: ast.Assign) -> None:
        value = _static_text(node.value, self.assignments)
        if value is not None:
            for target in node.targets:
                if isinstance(target, ast.Name):
                    self.assignments[target.id] = value
        self.generic_visit(node)

    def visit_AnnAssign(self, node: ast.AnnAssign) -> None:
        value = _static_text(node.value, self.assignments) if node.value else None
        if value is not None and isinstance(node.target, ast.Name):
            self.assignments[node.target.id] = value
        self.generic_visit(node)

    def visit_Call(self, node: ast.Call) -> None:
        method = node.func.attr if isinstance(node.func, ast.Attribute) else None

        if method in {"to_sql", "to_postgis", "commit", "executemany"}:
            self.violations.append(f"{method} at line {node.lineno}")
        elif method == "to_file" and self._is_database_file_writer(node):
            self.violations.append(f"to_file database writer at line {node.lineno}")
        elif method == "execute":
            sql = _static_text(node.args[0], self.assignments) if node.args else None
            if sql is None or MUTATING_SQL.search(sql):
                self.violations.append(f"execute at line {node.lineno}")
        elif self._is_read_sql_call(node):
            sql = _static_text(node.args[0], self.assignments) if node.args else None
            if sql is not None and MUTATING_SQL.search(sql):
                self.violations.append(f"read_sql mutation at line {node.lineno}")

        self.generic_visit(node)

    @staticmethod
    def _is_database_file_writer(node: ast.Call) -> bool:
        driver = next(
            (
                keyword.value
                for keyword in node.keywords
                if keyword.arg == "driver"
                and isinstance(keyword.value, ast.Constant)
                and isinstance(keyword.value.value, str)
            ),
            None,
        )
        if isinstance(driver, str) and driver.lower() in DATABASE_FILE_DRIVERS:
            return True

        path_text = _static_text(node.args[0], {}) if node.args else None
        return bool(
            path_text
            and any(
                path_text.lower().endswith(f".{suffix}")
                for suffix in DATABASE_FILE_DRIVERS
            )
        )

    @staticmethod
    def _is_read_sql_call(node: ast.Call) -> bool:
        if not isinstance(node.func, ast.Attribute):
            return False
        return node.func.attr in {"read_sql", "read_sql_query", "read_sql_table"}


def _notebook_mutations(path: Path) -> list[str]:
    notebook = json.loads(path.read_text(encoding="utf-8"))
    violations: list[str] = []
    for cell_number, cell in enumerate(notebook.get("cells", [])):
        if cell.get("cell_type") != "code":
            continue
        source = "".join(cell.get("source", []))
        # Permit ordinary Jupyter magics in otherwise parseable Python cells.
        source = "\n".join(
            f"# {line}" if line.lstrip().startswith("%") else line
            for line in source.splitlines()
        )
        try:
            tree = ast.parse(source, filename=str(path))
        except SyntaxError as exc:
            violations.append(f"code cell {cell_number} is not parseable: {exc}")
            continue
        visitor = DatabaseMutationVisitor()
        visitor.visit(tree)
        violations.extend(
            f"code cell {cell_number}: {violation}" for violation in visitor.violations
        )
    return violations


def test_notebooks_have_no_executable_database_mutations() -> None:
    """Catch database writes without flagging markdown, comments, or SQL docs."""
    failures: dict[str, list[str]] = {}
    for path in sorted(NOTEBOOKS_DIR.rglob("*.ipynb")):
        violations = _notebook_mutations(path)
        if violations:
            failures[str(path.relative_to(NOTEBOOKS_DIR))] = violations
    assert failures == {}, (
        "Executable notebook database mutations found: "
        + json.dumps(failures, indent=2)
    )
