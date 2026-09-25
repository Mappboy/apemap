"""Tests for documentation integrity, link validity, and CLI command synchronization."""

from __future__ import annotations

import re

from apemap.cli import app, ingest_app
from apemap.constants import PROJECT_ROOT

DOCS_DIR = PROJECT_ROOT / "docs"
ARCHIVE_DOCS_DIR = PROJECT_ROOT / "archive" / "legacy-docs" / "2026-09-23"

REQUIRED_DOCS = [
    PROJECT_ROOT / "README.md",
    DOCS_DIR / "README.md",
    DOCS_DIR / "quickstart.md",
    DOCS_DIR / "cli.md",
    DOCS_DIR / "package.md",
    DOCS_DIR / "methodology.md",
    DOCS_DIR / "data-model.md",
    DOCS_DIR / "data-sources.md",
    DOCS_DIR / "analysis.md",
    DOCS_DIR / "reproducibility.md",
    DOCS_DIR / "development.md",
    ARCHIVE_DOCS_DIR / "README.md",
    ARCHIVE_DOCS_DIR / "README-archive.md",
]


def test_required_documentation_files_exist():
    """All canonical and archived documentation files must exist on disk."""
    for doc in REQUIRED_DOCS:
        assert doc.exists(), f"Missing required documentation file: {doc}"


def test_root_readme_conciseness():
    """Root README must remain concise (between 80 and 220 lines)."""
    readme = PROJECT_ROOT / "README.md"
    lines = readme.read_text(encoding="utf-8").splitlines()
    assert 80 <= len(lines) <= 220, (
        f"Root README length {len(lines)} outside 80-220 target range."
    )


def test_markdown_relative_links_resolve():
    """All local relative markdown links in README and docs must resolve to existing files."""
    import urllib.parse

    link_pattern = re.compile(r"\[.*?\]\((?!https?://|mailto:|#)(.*?)\)")

    active_files = [
        PROJECT_ROOT / "README.md",
        *DOCS_DIR.glob("*.md"),
        ARCHIVE_DOCS_DIR / "README-archive.md",
    ]

    for md_file in active_files:
        content = md_file.read_text(encoding="utf-8")
        matches = link_pattern.findall(content)
        for target in matches:
            # Strip title string if present (e.g. url "title")
            raw_url = target.strip().split()[0].split('"')[0].split("'")[0]
            # Strip anchors if present (e.g. file.md#section -> file.md)
            clean_target = raw_url.split("#")[0]
            if not clean_target:
                continue

            clean_target = urllib.parse.unquote(clean_target)
            resolved_path = (md_file.parent / clean_target).resolve()
            assert resolved_path.exists(), (
                f"Broken link '{target}' in {md_file.relative_to(PROJECT_ROOT)} "
                f"resolves to non-existent target: {resolved_path}"
            )

    # For the archived root README, verify that its links resolved when located at project root
    archived_root_readme = ARCHIVE_DOCS_DIR / "README.md"
    content = archived_root_readme.read_text(encoding="utf-8")
    for target in link_pattern.findall(content):
        raw_url = target.strip().split()[0].split('"')[0].split("'")[0]
        clean_target = raw_url.split("#")[0]
        if not clean_target:
            continue
        clean_target = urllib.parse.unquote(clean_target).lstrip("/")
        resolved_path = (PROJECT_ROOT / clean_target).resolve()
        assert resolved_path.exists(), (
            f"Archived root README link '{target}' does not resolve at project root: {resolved_path}"
        )


def test_cli_documentation_matches_cli_commands():
    """Commands documented in docs/cli.md must match commands implemented in apemap.cli."""
    cli_doc = (DOCS_DIR / "cli.md").read_text(encoding="utf-8")

    # Top-level commands registered on app
    app_commands = {cmd.name for cmd in app.registered_commands if cmd.name}
    # Commands under ingest
    ingest_commands = {cmd.name for cmd in ingest_app.registered_commands if cmd.name}

    assert "transform" in app_commands
    assert "validate" in app_commands
    assert "analyze" in app_commands
    assert "export" in app_commands
    assert "run-all" in app_commands

    assert "aph" in ingest_commands
    assert "acara" in ingest_commands
    assert "wikimedia" in ingest_commands

    # Verify that each command has its dedicated section in docs/cli.md
    assert "apemap ingest aph" in cli_doc
    assert "apemap ingest acara" in cli_doc
    assert "apemap ingest wikimedia" in cli_doc
    assert "apemap transform" in cli_doc
    assert "apemap validate" in cli_doc
    assert "apemap analyze" in cli_doc
    assert "apemap export" in cli_doc
    assert "apemap run-all" in cli_doc
