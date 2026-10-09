"""Guard GitHub dataset publication against stale tags and reused versions."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import json
import os
from pathlib import Path
import re
import subprocess
from urllib.parse import quote

from apemap.release.version import validate_dataset_version


@dataclass(frozen=True)
class PublicationTarget:
    """One validated dataset identity and its exact source revision."""

    version: str
    tag: str
    source_commit: str
    repository: str
    event_name: str


def _run(
    *command: str, allowed: tuple[int, ...] = (0,)
) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(command, capture_output=True, text=True, check=False)
    if result.returncode not in allowed:
        raise RuntimeError(
            f"{command[0]} {command[1]} failed: {result.stderr.strip() or result.stdout.strip()}"
        )
    return result


def _git_commit(ref: str) -> str:
    return _run("git", "rev-parse", "--verify", f"{ref}^{{commit}}").stdout.strip()


def _require_main_commit(commit: str) -> None:
    heads = _run(
        "git", "ls-remote", "--heads", "origin", "refs/heads/main", "refs/heads/master"
    ).stdout.splitlines()
    branches = {line.split()[1].removeprefix("refs/heads/") for line in heads}
    branch = (
        "main" if "main" in branches else "master" if "master" in branches else None
    )
    if branch is None:
        raise ValueError("Release repository must have a main or master branch")
    _run(
        "git", "fetch", "--no-tags", "origin", f"{branch}:refs/remotes/origin/{branch}"
    )
    result = _run(
        "git",
        "merge-base",
        "--is-ancestor",
        commit,
        f"refs/remotes/origin/{branch}",
        allowed=(0, 1),
    )
    if result.returncode:
        raise ValueError("Release source commit is not contained in main/master")


def _remote_version_tags(tags: tuple[str, str]) -> dict[str, str]:
    lines = _run(
        "git",
        "ls-remote",
        "--tags",
        "origin",
        *(ref for tag in tags for ref in (f"refs/tags/{tag}", f"refs/tags/{tag}^{{}}")),
    ).stdout.splitlines()
    refs = {line.split()[1]: line.split()[0] for line in lines}
    return {
        tag: refs.get(f"refs/tags/{tag}^{{}}", refs[f"refs/tags/{tag}"])
        for tag in tags
        if f"refs/tags/{tag}" in refs
    }


def _release_exists(
    repository: str, tag: str, *, dataset_version: str | None = None
) -> bool:
    """Reserve dataset tags, and legacy v tags only when they contain dataset assets."""
    result = _run(
        "gh",
        "api",
        f"repos/{repository}/releases/tags/{quote(tag, safe='')}",
        allowed=(0, 1),
    )
    if result.returncode == 0:
        if dataset_version is None:
            return True
        try:
            assets = json.loads(result.stdout)["assets"]
            if not isinstance(assets, list) or any(
                not isinstance(asset, dict) or not isinstance(asset.get("name"), str)
                for asset in assets
            ):
                raise ValueError("Invalid release assets")
            dataset_archives = {
                f"apemap-release-v{dataset_version}.tar.gz",
                f"apemap-historical-v{dataset_version}.tar.gz",
            }
            return any(asset["name"] in dataset_archives for asset in assets)
        except (ValueError, TypeError, KeyError) as err:
            raise RuntimeError("Cannot inspect legacy release asset inventory") from err
    if "(HTTP 404)" in result.stderr:
        return False
    raise RuntimeError(
        f"Cannot check existing dataset release: {result.stderr.strip()}"
    )


def prepare_publication(
    *,
    event_name: str,
    ref_name: str,
    requested_version: str,
    source_commit: str,
    repository: str,
) -> PublicationTarget:
    """Fail closed before building, and again immediately before publication."""
    if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repository):
        raise ValueError("Release repository must be owner/name")
    if not re.fullmatch(r"[0-9a-f]{40}|[0-9a-f]{64}", source_commit):
        raise ValueError("Release source must be a full Git object SHA")
    if event_name == "workflow_dispatch":
        if ref_name not in ("main", "master"):
            raise ValueError("Manual release dispatch must run on main/master")
        version = validate_dataset_version(requested_version)
        tag = f"data-v{version}"
    elif event_name == "push":
        if not ref_name.startswith("data-"):
            raise ValueError("Dataset publication requires a data- or data-v tag")
        version = validate_dataset_version(
            ref_name.removeprefix("data-").removeprefix("v")
        )
        tag = ref_name
    else:
        raise ValueError("Dataset publication requires manual dispatch or a tag push")
    commit = _git_commit(source_commit)
    if _git_commit("HEAD") != commit:
        raise ValueError("Checked-out source does not match the release trigger")
    _require_main_commit(commit)
    tags = (f"data-v{version}", f"data-{version}")
    existing = _remote_version_tags(tags)
    if event_name == "workflow_dispatch" and existing:
        raise ValueError(f"Dataset version already has a tag: {', '.join(existing)}")
    if event_name == "push":
        if any(existing_tag != tag for existing_tag in existing):
            raise ValueError(
                "Dataset version already has an alternate canonical/legacy tag"
            )
        if existing.get(tag) != commit or _git_commit(f"refs/tags/{tag}") != commit:
            raise ValueError(
                "Incoming dataset tag must resolve to the built source commit"
            )
    for candidate in tags:
        if _release_exists(repository, candidate):
            raise ValueError(
                f"Dataset release already exists, including drafts: {candidate}"
            )
    legacy_tag = f"v{version}"
    if _release_exists(repository, legacy_tag, dataset_version=version):
        raise ValueError(
            f"Dataset release already exists under historical tag: {legacy_tag}"
        )
    return PublicationTarget(version, tag, commit, repository, event_name)


def publish_publication(target: PublicationTarget, artifact_dir: Path) -> None:
    """Publish validated artifacts only after successfully establishing the exact tag."""
    manifest = json.loads((artifact_dir / "manifest.json").read_bytes())
    if (manifest.get("data_release_version"), manifest.get("source_commit")) != (
        target.version,
        target.source_commit,
    ):
        raise ValueError(
            "Packaged release does not match its publication version/source"
        )
    names = (
        f"apemap-release-v{target.version}.tar.gz",
        "manifest.json",
        "SHA256SUMS",
        "SHA256SUMS.dist",
    )
    artifacts = [artifact_dir / name for name in names]
    if any(not path.is_file() or path.is_symlink() for path in artifacts):
        raise ValueError(
            "Publication requires the complete regular-file package inventory"
        )
    if target.event_name == "workflow_dispatch":
        _run("git", "tag", target.tag, target.source_commit)
        _run("git", "push", "origin", f"refs/tags/{target.tag}")
    prerelease_flags = (
        ("--prerelease", "--latest=false")
        if "-" in target.version.split("+", 1)[0]
        else ()
    )
    _run(
        "gh",
        "release",
        "create",
        target.tag,
        *(str(path) for path in artifacts),
        "--repo",
        target.repository,
        "--verify-tag",
        "--title",
        f"APEMAP Dataset Release v{target.version}",
        "--notes",
        f"Immutable, cryptographically verified APEMAP dataset release v{target.version}. Contains web layers, analytical metrics, canonical data tables, manifest.json, and SHA256 checksums.",
        "--draft=false",
        *prerelease_flags,
    )


def main() -> None:
    """Prepare or publish using GitHub's explicit event environment."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("prepare", "publish"))
    parser.add_argument("--artifact-dir", type=Path, default=Path("dist"))
    args = parser.parse_args()
    target = prepare_publication(
        event_name=os.environ["GITHUB_EVENT_NAME"],
        ref_name=os.environ["GITHUB_REF_NAME"],
        requested_version=os.environ.get("REQUESTED_VERSION", ""),
        source_commit=os.environ["GITHUB_SHA"],
        repository=os.environ["GITHUB_REPOSITORY"],
    )
    if args.command == "prepare":
        with Path(os.environ["GITHUB_ENV"]).open(
            "a", encoding="utf-8", newline="\n"
        ) as output:
            output.write(f"RELEASE_VERSION={target.version}\nTAG_NAME={target.tag}\n")
        print(f"Prepared dataset release {target.tag} at {target.source_commit}")
    else:
        publish_publication(target, args.artifact_dir)


if __name__ == "__main__":
    main()
