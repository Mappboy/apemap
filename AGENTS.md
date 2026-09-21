# AGENTS.md

## Purpose and scope

This repository maps the education histories of Australian parliamentarians and
contains Python data-collection helpers, exploratory notebooks, spatial data,
SQL views, and a Plotly Dash application. Treat data provenance, reproducibility,
and preservation of checked-in research artifacts as first-class requirements.

These instructions apply to the entire repository. More-specific `AGENTS.md`
files may add or override guidance within their own directories.

## Repository map

- `apemap/`: reusable Python helpers and network-backed data collectors.
- `app/`: the Dash application. It currently expects to be launched with
  `app/` as the working directory and reads `../data/aped.gpkg`.
- `data/`: databases, GeoPackages, QGIS projects, SQL views, and source or
  processed datasets. Some files are large binary research artifacts.
- `notebooks/`: analysis and exploratory notebooks plus rendered HTML outputs.
- `images/`: generated figures used by the README and analysis.
- `README.md`: project context, data sources, caveats, and manual workflows.

## Start every task

1. Read this file and the relevant source, tests, README sections, and any
   nested `AGENTS.md` files before editing.
2. Inspect `git status` and preserve unrelated or pre-existing user changes.
3. Identify whether the work changes source data, derived data, generated
   output, application code, or packaging; state the expected validation.
4. Use an installed skill when it clearly fits. If a necessary skill is
   missing, install or create the narrowly scoped skill from a trusted source
   and document what was added. Do not add a skill merely as a substitute for
   understanding the repository.

## Issue, branch, commit, and pull-request workflow

- Do not implement issue work directly on `master` or another protected branch.
  Create a fresh branch without asking, preferably `issue-<number>-<slug>`; use
  `docs/`, `fix/`, `feat/`, `data/`, or `chore/` prefixes when no issue number
  exists.
- Keep each branch focused on one issue or coherent job. Do not mix opportunistic
  cleanup into the change.
- Commit without asking whenever a coherent, verified checkpoint is reached.
  Prefer several reviewable commits over one large final commit. Use imperative,
  descriptive commit messages and never commit secrets, `.env` files, caches,
  local virtual environments, test reports, or unrelated generated files.
- Before handoff, review the complete branch diff and run the relevant checks.
- At the end of every completed job, push the branch and open a draft pull
  request without asking. Link the issue, explain data or schema effects, list
  validation commands and results, and call out follow-up work or limitations.
  Mark the PR ready only when requested or when repository policy explicitly
  requires it. If authentication, permissions, or missing remotes prevent the
  PR, leave the branch and commits ready and report the exact blocker.

## Python environment and dependencies

- Use `uv` for Python versions, environments, dependency resolution, locking,
  and command execution. Do not introduce new Poetry, pipenv, conda, or ad-hoc
  `pip install` workflows.
- Run repository tools through `uv`, for example:

  ```text
  uv sync
  uv run pytest
  uv run ruff check .
  uv run ruff format --check .
  uv run ty check
  ```

- Add or remove dependencies with `uv add` and `uv remove`; use `--dev` for
  development-only tools. Update the lockfile in the same change.
- Upgrade dependencies when needed to complete the task or remediate a known
  incompatibility or vulnerability. Prefer targeted upgrades such as
  `uv lock --upgrade-package <name>` over an unreviewed whole-environment
  upgrade. Review release notes and run the full validation set after upgrades.
- Install missing dependencies when required to build, test, type-check, lint,
  or run the requested workflow. Keep dependency scope minimal and explain
  non-obvious additions in the PR.
- The current root `pyproject.toml`, `poetry.lock`, `requirements.txt`,
  `.flake8`, `.pytest.ini`, and pre-commit configuration are legacy inputs.
  When a task modernizes packaging, replace them deliberately rather than
  layering conflicting active configurations.

## Archiving legacy files

- Never delete historical packaging, configuration, notebooks, data, or
  generated research artifacts merely because they appear old.
- When replacement is necessary, preserve the exact prior file under
  `archive/<area>/<YYYY-MM-DD>/` before replacing or removing the active copy.
  For the legacy Poetry migration, use `archive/legacy-packaging/<YYYY-MM-DD>/`
  for the previous `pyproject.toml`, `poetry.lock`, `requirements.txt`, and any
  superseded tool configuration.
- Keep only the new canonical configuration active at the repository root; do
  not leave two competing sources of truth. Add a short README in the archive
  describing why the files moved and which commit superseded them.
- Do not archive a file just to avoid understanding or fixing it. Avoid moving
  large binary data unless the issue explicitly requires it and Git history is
  insufficient for the preservation requirement.

## Coding practices

- Support the Python version declared by the canonical `pyproject.toml`.
- Add type annotations to new or materially changed functions. Run `ty` and
  fix issues introduced by the change; do not silence errors broadly.
- Use Ruff for formatting and linting. Prefer focused `# noqa` comments with a
  reason only when a rule cannot be satisfied cleanly.
- Keep functions small and separate network access, parsing, transformation,
  persistence, and presentation logic so each can be tested independently.
- Parameterize SQL values. Never construct SQL with user- or data-controlled
  f-strings.
- Use `pathlib.Path` and resolve paths relative to a stable project or module
  location, not an assumed shell working directory, when modifying path logic.
- Use timeouts for HTTP requests, identify the client responsibly, call
  `raise_for_status()`, and respect upstream rate limits. Do not disable TLS
  verification in new code.
- Never log API keys, tokens, cookies, connection strings, or full sensitive
  responses. Keep credentials in environment variables and provide redacted
  examples only.

## Data and notebook safety

- Treat `data/external/` as source material and avoid in-place mutation.
  Write transformations to `data/processed/` or a task-specific output.
- Preserve provenance: record the upstream URL or API, retrieval date, relevant
  query or transformation, and licensing or attribution requirements for new
  datasets.
- Do not refresh large databases, GeoPackages, QGIS projects, notebook outputs,
  or images unless the task requires it. Inspect diffs and file sizes before
  committing binary or generated artifacts.
- For notebook changes, keep outputs only when they are a deliberate deliverable.
  Ensure notebooks can run from a clean environment and keep reusable logic in
  Python modules rather than notebook-only cells.
- Tests must not depend on live Wikidata, Wikipedia, APH, ACARA, Google, Mapbox,
  PostgreSQL, or other network services. Mock clients or use small deterministic
  fixtures. Put explicit opt-in integration tests behind a marker.

## Testing and quality gates

- Put tests under `tests/` and use `pytest`. Every bug fix needs a regression
  test; new behavior needs focused unit tests and, where useful, an integration
  test over a small fixture.
- During development, run the narrowest useful test first. Before opening the
  PR, run the full available suite and all quality gates:

  ```text
  uv run pytest
  uv run ruff check .
  uv run ruff format --check .
  uv run ty check
  ```

- If a full check cannot run because of a pre-existing failure, missing system
  dependency, or external service, still run the unaffected checks and document
  the exact command, error, and scope in the PR. Never claim an unrun check
  passed.
- For Dash changes, verify the application starts from `app/` and exercise the
  affected callbacks with tests where practical. Do not use production secrets
  or live paid APIs for validation.
- For schema, view, or transformation changes, verify representative row counts,
  uniqueness, null handling, joins, and relevant geographic metadata before and
  after the change.

## Long-running work, goals, and queued follow-ups

- Use Goal mode (`/goal`) for work that genuinely needs multiple turns and has
  a verifiable stopping condition. Define the outcome, constraints, checkpoints,
  validation commands, and definition of done before starting.
- Let a running goal work in the background. Do not busy-poll it, manufacture
  repeated status turns, or ask the user to keep checking in. Use the available
  event/session wait mechanism for long-running commands and report only
  meaningful progress, completion, failure, or a decision that needs the user.
- A follow-up that should happen only after the current run finishes belongs in
  the Queue, not as steering of the active run. In Codex CLI, `Tab` queues the
  composed message for the next turn; `Enter` steers the current turn. In the
  desktop app, use the configured Queue follow-up behavior. Preserve queued
  order and do not turn queued work into scope creep for the current checkpoint.
- Pause only for a real approval, missing decision, or external blocker. A goal
  does not expand permissions or authorize unrelated changes.

## Definition of done

A job is complete only when the requested behavior or artifact exists, relevant
tests and checks have run, documentation and data provenance are current, the
branch diff contains no unrelated changes or secrets, coherent checkpoints are
committed, and a draft PR has been opened (or an exact PR blocker is reported).
