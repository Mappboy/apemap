"""Tests verifying clean package imports and absence of deprecated/archived modules."""

from __future__ import annotations

import importlib
import socket
from unittest.mock import patch

import pytest


@pytest.mark.unit
def test_clean_import_without_network_or_side_effects() -> None:
    """Verify that importing apemap and apemap.cli performs no outbound network connections."""
    with patch.object(socket.socket, "connect") as mock_connect:
        import apemap
        import apemap.cli

        assert apemap is not None
        assert apemap.cli is not None
        mock_connect.assert_not_called()


@pytest.mark.unit
@pytest.mark.parametrize(
    "archived_module",
    [
        "apemap.main",
        "apemap.utils",
        "apemap.database_queries",
        "apemap.sparql_queries",
        "apemap.download_mp_images_to_assets",
    ],
)
def test_archived_legacy_modules_cannot_be_imported(archived_module: str) -> None:
    """Verify that archived prototype modules are no longer present in the active package."""
    with pytest.raises(ModuleNotFoundError):
        importlib.import_module(archived_module)


@pytest.mark.unit
@pytest.mark.parametrize(
    "canonical_module",
    [
        "apemap",
        "apemap.analysis",
        "apemap.cli",
        "apemap.constants",
        "apemap.db",
        "apemap.export",
        "apemap.validate",
        "apemap.ingest",
        "apemap.ingest.abs",
        "apemap.ingest.acara",
        "apemap.ingest.aec",
        "apemap.ingest.aph",
        "apemap.ingest.funding",
        "apemap.ingest.matching",
        "apemap.ingest.pipeline",
        "apemap.ingest.review",
        "apemap.ingest.wikimedia",
    ],
)
def test_canonical_modules_import_successfully(canonical_module: str) -> None:
    """Verify all canonical package submodules are cleanly importable."""
    mod = importlib.import_module(canonical_module)
    assert mod is not None
