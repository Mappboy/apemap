"""Shared validation for dataset Semantic Versions."""

from __future__ import annotations

import re


def validate_dataset_version(version: str) -> str:
    """Accept SemVer 2.0 versions without normalizing their immutable identity."""
    match = re.fullmatch(
        r"(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)"
        r"(?:-([0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*))?"
        r"(?:\+([0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*))?",
        version,
        flags=re.ASCII,
    )
    if match is None or any(
        len(identifier) > 1 and identifier.startswith("0") and identifier.isdigit()
        for identifier in (match[4] or "").split(".")
    ):
        raise ValueError("Dataset version must follow Semantic Versioning 2.0")
    return version
