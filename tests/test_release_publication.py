"""Publication preflight and tag safety without live GitHub or Git operations."""

from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys
from urllib.parse import unquote

import pytest

from apemap.release.publication import (
    PublicationTarget,
    main,
    prepare_publication,
    publish_publication,
)
from apemap.release.version import validate_dataset_version

COMMIT = "a" * 40
OTHER_COMMIT = "b" * 40
VERSION = "0.5.0-rc.1"


class CommandFixture:
    """Model remote tags and API failures at the external command boundary."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, ...]] = []
        self.tags: dict[str, str] = {}
        self.annotated_tags: set[str] = set()
        self.local_tag_commits: dict[str, str] = {}
        self.releases: dict[str, int] = {}
        self.release_assets: dict[str, list[str]] = {}
        self.release_payloads: dict[str, str] = {}
        self.head = COMMIT
        self.ancestor = True
        self.push_succeeds = True
        self.remote_lookup_succeeds = True

    def run(
        self, command: tuple[str, ...], *, capture_output: bool, text: bool, check: bool
    ) -> subprocess.CompletedProcess[str]:
        assert capture_output and text and not check
        self.calls.append(command)
        stdout, stderr, code = "", "", 0
        if command[:2] == ("git", "rev-parse"):
            ref = command[-1].removesuffix("^{commit}")
            if ref == "HEAD":
                stdout = self.head
            elif ref.startswith("refs/tags/"):
                tag = ref.removeprefix("refs/tags/")
                stdout = self.local_tag_commits.get(tag, self.tags.get(tag, ""))
            else:
                stdout = ref
        elif command[:3] == ("git", "ls-remote", "--heads"):
            stdout = f"{COMMIT}\trefs/heads/main\n"
        elif command[:3] == ("git", "ls-remote", "--tags"):
            if not self.remote_lookup_succeeds:
                return subprocess.CompletedProcess(
                    command, 128, "", "remote unavailable"
                )
            lines: list[str] = []
            for tag, commit in self.tags.items():
                lines.append(
                    f"{OTHER_COMMIT if tag in self.annotated_tags else commit}\trefs/tags/{tag}"
                )
                if tag in self.annotated_tags:
                    lines.append(f"{commit}\trefs/tags/{tag}^{{}}")
            stdout = "\n".join(lines)
        elif command[:2] == ("git", "merge-base"):
            code = 0 if self.ancestor else 1
        elif command[:2] == ("git", "push"):
            if not self.push_succeeds:
                code, stderr = 128, "remote rejected tag push"
        elif command[:2] == ("gh", "api"):
            tag = unquote(command[-1].rsplit("/", 1)[1])
            status = self.releases.get(tag, 404)
            if status == 200:
                stdout = self.release_payloads.get(
                    tag,
                    json.dumps(
                        {
                            "draft": True,
                            "assets": [
                                {"name": name}
                                for name in self.release_assets.get(tag, [])
                            ],
                        }
                    ),
                )
            else:
                code, stderr = 1, f"gh: request failed (HTTP {status})"
        elif (
            command[:2] != ("git", "fetch")
            and command[:2] != ("git", "tag")
            and command[:3] != ("gh", "release", "create")
        ):
            raise AssertionError(f"Unexpected command: {command}")
        return subprocess.CompletedProcess(command, code, stdout, stderr)


@pytest.fixture
def commands(monkeypatch: pytest.MonkeyPatch) -> CommandFixture:
    fixture = CommandFixture()
    monkeypatch.setattr("apemap.release.publication.subprocess.run", fixture.run)
    return fixture


def prepare(
    *,
    event_name: str = "workflow_dispatch",
    ref_name: str = "main",
    version: str = VERSION,
) -> PublicationTarget:
    return prepare_publication(
        event_name=event_name,
        ref_name=ref_name,
        requested_version=version,
        source_commit=COMMIT,
        repository="Mappboy/apemap",
    )


def package_fixture(root: Path, target: PublicationTarget) -> Path:
    root.mkdir()
    (root / "manifest.json").write_text(
        json.dumps(
            {
                "data_release_version": target.version,
                "source_commit": target.source_commit,
            }
        )
    )
    for name in (
        f"apemap-release-v{target.version}.tar.gz",
        "SHA256SUMS",
        "SHA256SUMS.dist",
    ):
        (root / name).write_bytes(b"fixture")
    return root


@pytest.mark.unit
@pytest.mark.parametrize(
    "version",
    ["0.0.0", "1.2.3", "0.5.0-rc.1", "1.2.3-alpha-1.2+build.01", "1.2.3+build.01"],
)
def test_dataset_semver_preserves_valid_identity(version: str) -> None:
    assert validate_dataset_version(version) == version


@pytest.mark.unit
@pytest.mark.parametrize(
    "version",
    [
        "1.2",
        "01.2.3",
        "1.02.3",
        "1.2.03",
        "1.2.3-01",
        "1.2.3-rc..1",
        "1.2.3-rc.",
        "1.2.3+build..1",
        "1.2.3\nTAG_NAME=injected",
        "١.2.3",
    ],
)
def test_dataset_semver_rejects_invalid_or_unsafe_identity(version: str) -> None:
    with pytest.raises(ValueError, match="Semantic Versioning"):
        validate_dataset_version(version)


@pytest.mark.unit
@pytest.mark.parametrize("prefix", ["data-v", "data-"])
@pytest.mark.parametrize("commit", [COMMIT, OTHER_COMMIT])
def test_dispatch_rejects_existing_version_tag(
    commands: CommandFixture, prefix: str, commit: str
) -> None:
    commands.tags[f"{prefix}{VERSION}"] = commit
    with pytest.raises(ValueError, match="already has a tag"):
        prepare()


@pytest.mark.unit
@pytest.mark.parametrize("prefix", ["data-v", "data-"])
def test_dispatch_rejects_any_existing_release_including_drafts(
    commands: CommandFixture, prefix: str
) -> None:
    commands.releases[f"{prefix}{VERSION}"] = 200
    with pytest.raises(ValueError, match="including drafts"):
        prepare()


@pytest.mark.unit
@pytest.mark.parametrize("archive_prefix", ["apemap-release-v", "apemap-historical-v"])
@pytest.mark.parametrize("event_name", ["workflow_dispatch", "push"])
def test_historical_dataset_release_reserves_version_across_tag_names(
    commands: CommandFixture, archive_prefix: str, event_name: str
) -> None:
    version, legacy_tag = "0.3.3", "v0.3.3"
    commands.tags[legacy_tag] = OTHER_COMMIT
    commands.releases[legacy_tag] = 200
    commands.release_assets[legacy_tag] = [
        f"{archive_prefix}{version}.tar.gz",
        "manifest.json",
    ]
    ref_name = "main"
    if event_name == "push":
        ref_name = f"data-v{version}"
        commands.tags[ref_name] = COMMIT
    with pytest.raises(ValueError, match="historical tag: v0.3.3"):
        prepare(event_name=event_name, ref_name=ref_name, version=version)


@pytest.mark.unit
@pytest.mark.parametrize("has_package_release", [False, True])
def test_package_v_tag_does_not_reserve_independent_dataset_version(
    commands: CommandFixture, has_package_release: bool
) -> None:
    commands.tags["v0.3.3"] = OTHER_COMMIT
    if has_package_release:
        commands.releases["v0.3.3"] = 200
        commands.release_assets["v0.3.3"] = [
            "apemap-0.3.3-py3-none-any.whl",
            "apemap-0.3.3.tar.gz",
        ]
    assert prepare(version="0.3.3").version == "0.3.3"


@pytest.mark.unit
def test_legacy_release_lookup_fails_closed_on_api_error(
    commands: CommandFixture,
) -> None:
    commands.releases[f"v{VERSION}"] = 500
    with pytest.raises(RuntimeError, match="Cannot check"):
        prepare()


@pytest.mark.unit
def test_legacy_release_lookup_fails_closed_on_malformed_inventory(
    commands: CommandFixture,
) -> None:
    commands.releases[f"v{VERSION}"] = 200
    commands.release_payloads[f"v{VERSION}"] = "{}"
    with pytest.raises(RuntimeError, match="Cannot inspect"):
        prepare()


@pytest.mark.unit
@pytest.mark.parametrize("prefix", ["data-v", "data-"])
@pytest.mark.parametrize("annotated", [False, True])
def test_tag_push_retains_exact_incoming_tag(
    commands: CommandFixture, prefix: str, annotated: bool, tmp_path: Path
) -> None:
    tag = f"{prefix}{VERSION}"
    commands.tags[tag] = COMMIT
    if annotated:
        commands.annotated_tags.add(tag)
    target = prepare(event_name="push", ref_name=tag)
    assert target.tag == tag
    publish_publication(target, package_fixture(tmp_path / "dist", target))
    assert not any(
        command[:2] in (("git", "tag"), ("git", "push")) for command in commands.calls
    )
    publication = next(
        command
        for command in commands.calls
        if command[:3] == ("gh", "release", "create")
    )
    assert publication[3] == tag
    assert "--verify-tag" in publication


@pytest.mark.unit
def test_tag_push_rejects_alternate_version_alias(commands: CommandFixture) -> None:
    commands.tags = {f"data-v{VERSION}": COMMIT, f"data-{VERSION}": COMMIT}
    with pytest.raises(ValueError, match="alternate"):
        prepare(event_name="push", ref_name=f"data-{VERSION}")


@pytest.mark.unit
def test_tag_push_rejects_stale_remote_tag(commands: CommandFixture) -> None:
    tag = f"data-v{VERSION}"
    commands.tags[tag] = OTHER_COMMIT
    with pytest.raises(ValueError, match="built source commit"):
        prepare(event_name="push", ref_name=tag)


@pytest.mark.unit
def test_tag_push_rejects_local_remote_tag_mismatch(commands: CommandFixture) -> None:
    tag = f"data-v{VERSION}"
    commands.tags[tag] = COMMIT
    commands.local_tag_commits[tag] = OTHER_COMMIT
    with pytest.raises(ValueError, match="built source commit"):
        prepare(event_name="push", ref_name=tag)


@pytest.mark.unit
def test_tag_lookup_failure_prevents_publication(commands: CommandFixture) -> None:
    commands.remote_lookup_succeeds = False
    with pytest.raises(RuntimeError, match="remote unavailable"):
        prepare()


@pytest.mark.unit
@pytest.mark.parametrize("status", [401, 403, 429, 500])
def test_release_lookup_fails_closed_on_api_errors(
    commands: CommandFixture, status: int
) -> None:
    commands.releases[f"data-v{VERSION}"] = status
    with pytest.raises(RuntimeError, match="Cannot check"):
        prepare()


@pytest.mark.unit
def test_publication_rejects_checkout_mismatch(commands: CommandFixture) -> None:
    commands.head = OTHER_COMMIT
    with pytest.raises(ValueError, match="Checked-out source"):
        prepare()


@pytest.mark.unit
def test_publication_rejects_commit_outside_main(commands: CommandFixture) -> None:
    commands.ancestor = False
    with pytest.raises(ValueError, match="not contained"):
        prepare()


@pytest.mark.unit
def test_dispatch_rejects_feature_branch(commands: CommandFixture) -> None:
    with pytest.raises(ValueError, match="main/master"):
        prepare(ref_name="feature/release")
    assert commands.calls == []


@pytest.mark.unit
def test_failed_tag_push_prevents_release_creation(
    commands: CommandFixture, tmp_path: Path
) -> None:
    target = prepare()
    commands.push_succeeds = False
    with pytest.raises(RuntimeError, match="remote rejected"):
        publish_publication(target, package_fixture(tmp_path / "dist", target))
    assert not any(
        command[:3] == ("gh", "release", "create") for command in commands.calls
    )


@pytest.mark.unit
def test_dispatch_publishes_exact_commit_with_verified_tag(
    commands: CommandFixture, tmp_path: Path
) -> None:
    target = prepare()
    publish_publication(target, package_fixture(tmp_path / "dist", target))
    assert ("git", "tag", target.tag, COMMIT) in commands.calls
    push = ("git", "push", "origin", f"refs/tags/{target.tag}")
    publication = next(
        command
        for command in commands.calls
        if command[:3] == ("gh", "release", "create")
    )
    assert commands.calls.index(push) < commands.calls.index(publication)
    assert publication[3] == target.tag
    assert "--verify-tag" in publication


@pytest.mark.unit
def test_semver_candidate_is_published_as_prerelease(
    commands: CommandFixture, tmp_path: Path
) -> None:
    version = "0.5.0-rc.1+build.01"
    target = PublicationTarget(
        version, f"data-v{version}", COMMIT, "Mappboy/apemap", "push"
    )
    publish_publication(target, package_fixture(tmp_path / "dist", target))
    publication = commands.calls[-1]
    assert "--prerelease" in publication
    assert "--latest=false" in publication


@pytest.mark.unit
def test_semver_metadata_hyphen_preserves_stable_publication(
    commands: CommandFixture, tmp_path: Path
) -> None:
    version = "0.5.0+build-01"
    target = PublicationTarget(
        version, f"data-v{version}", COMMIT, "Mappboy/apemap", "push"
    )
    publish_publication(target, package_fixture(tmp_path / "dist", target))
    publication = commands.calls[-1]
    assert "--prerelease" not in publication
    assert "--latest=false" not in publication


@pytest.mark.unit
@pytest.mark.parametrize("changed_state", ["tag", "release", "legacy_release"])
def test_publish_command_rechecks_remote_state_after_build(
    commands: CommandFixture,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    changed_state: str,
) -> None:
    for name, value in {
        "GITHUB_EVENT_NAME": "workflow_dispatch",
        "GITHUB_REF_NAME": "main",
        "REQUESTED_VERSION": VERSION,
        "GITHUB_SHA": COMMIT,
        "GITHUB_REPOSITORY": "Mappboy/apemap",
        "GITHUB_ENV": str(tmp_path / "job-env"),
    }.items():
        monkeypatch.setenv(name, value)
    monkeypatch.setattr(sys, "argv", ["publication", "prepare"])
    main()
    assert (
        tmp_path / "job-env"
    ).read_text() == f"RELEASE_VERSION={VERSION}\nTAG_NAME=data-v{VERSION}\n"
    if changed_state == "tag":
        commands.tags[f"data-v{VERSION}"] = OTHER_COMMIT
    elif changed_state == "release":
        commands.releases[f"data-v{VERSION}"] = 200
    else:
        commands.releases[f"v{VERSION}"] = 200
        commands.release_assets[f"v{VERSION}"] = [f"apemap-release-v{VERSION}.tar.gz"]
    monkeypatch.setattr(sys, "argv", ["publication", "publish"])
    with pytest.raises(ValueError, match="already"):
        main()
    assert not any(
        command[:2] == ("git", "tag") or command[:3] == ("gh", "release", "create")
        for command in commands.calls
    )


@pytest.mark.unit
def test_publication_rejects_artifacts_from_different_source(
    commands: CommandFixture, tmp_path: Path
) -> None:
    target = prepare()
    artifacts = package_fixture(tmp_path / "dist", target)
    (artifacts / "manifest.json").write_text(
        json.dumps({"data_release_version": VERSION, "source_commit": OTHER_COMMIT})
    )
    with pytest.raises(ValueError, match="does not match"):
        publish_publication(target, artifacts)
    assert not any(command[:2] == ("git", "tag") for command in commands.calls)
