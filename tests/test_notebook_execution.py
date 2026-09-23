"""Smoke-test the canonical notebooks against a deterministic local fixture."""

# The optional dependency checks intentionally precede the fixture import.
# ruff: noqa: E402

from __future__ import annotations

import os
from pathlib import Path

import pytest

nbclient = pytest.importorskip("nbclient")
nbformat = pytest.importorskip("nbformat")

from tests.test_analysis import create_analysis_fixture


NOTEBOOKS = (
    "00_data_overview.ipynb",
    "01_demographics.ipynb",
    "02_education_sectors.ipynb",
    "03_school_finance.ipynb",
    "04_parliament_comparison.ipynb",
)


@pytest.mark.parametrize("notebook_name", NOTEBOOKS)
def test_canonical_notebook_executes_without_network_or_mutation(
    notebook_name: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Execute each notebook top-to-bottom using a read-only temporary database."""
    db_path = create_analysis_fixture(tmp_path / "notebook.duckdb")
    notebook_path = Path(__file__).parents[1] / "notebooks" / notebook_name
    notebook = nbformat.read(notebook_path, as_version=4)
    monkeypatch.setenv("APEMAP_DB_PATH", str(db_path))
    monkeypatch.setenv("APEMAP_PROJECT_ROOT", str(notebook_path.parents[1]))
    monkeypatch.setenv("APEMAP_PARLIAMENT", "47")
    before = db_path.read_bytes()

    client = nbclient.NotebookClient(
        notebook, timeout=120, kernel_name="python3", resources={"metadata": {}}
    )
    client.execute(
        cwd=str(notebook_path.parents[1]),
        env={**os.environ, "APEMAP_DB_PATH": str(db_path)},
    )

    assert db_path.read_bytes() == before
