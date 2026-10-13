# AGENTS.md

## Purpose and scope

This repository maps the education histories of Australian parliamentarians and
contains Python data-collection helpers, exploratory notebooks, spatial data,
SQL views, and a Plotly Dash application. Treat data provenance, reproducibility,
and preservation of checked-in research artifacts as first-class requirements.

These instructions apply to the entire repository. More-specific `AGENTS.md`
files may add or override guidance within their own directories.


**Working code only. Finish the job. Plausibility is not correctness.**

This file follows the [AGENTS.md](https://agents.md) open standard (Linux Foundation / Agentic AI Foundation). Claude Code, Codex, Cursor, Windsurf, Copilot, Aider, Devin, Amp read it natively. For tools that look elsewhere, symlink:

```bash
ln -s AGENTS.md CLAUDE.md
ln -s AGENTS.md GEMINI.md
```

---

## 0. Non-negotiables

These rules override everything else in this file when in conflict:

1. **No flattery, no filler.** Skip openers like "Great question", "You're absolutely right", "Excellent idea", "I'd be happy to". Start with the answer or the action.
2. **Disagree when you disagree.** If the user's premise is wrong, say so before doing the work. Agreeing with false premises to be polite is the single worst failure mode in coding agents.
3. **Never fabricate.** Not file paths, not commit hashes, not API names, not test results, not library functions. Use provider-returned URLs for CI jobs; never guess run IDs. If you don't know, read the file, run the command, or say "I don't know, let me check."
4. **Stop when confused.** If the task has two plausible interpretations, ask. Do not pick silently and proceed.
5. **Touch only what you must.** Every changed line must trace directly to the user's request. No drive-by refactors, reformatting, or "while I was in there" cleanups.

---

## 1. Before writing code

**Goal: understand the problem and the codebase before producing a diff.**

- State your plan in one or two sentences before editing. For anything non-trivial, produce a numbered list of steps with a verification check for each.
- Read the files you will touch. Read the files that call the files you will touch. Claude Code: use subagents for exploration so the main context stays clean.
- Match existing patterns in the codebase. If the project uses pattern X, use pattern X, even if you'd do it differently in a greenfield repo.
- Surface assumptions out loud: "I'm assuming you want X, Y, Z. If that's wrong, say so." Do not bury assumptions inside the implementation.
- If two approaches exist, present both with tradeoffs. Do not pick one silently. Exception: trivial tasks (typo, rename, log line) where the diff fits in one sentence.

---

## 2. Writing code: simplicity first

**Goal: the minimum code that solves the stated problem. Nothing speculative.**

- No features beyond what was asked.
- No abstractions for single-use code. No configurability, flexibility, or hooks that were not requested.
- No error handling for impossible scenarios. Handle the failures that can actually happen.
- If the solution runs 200 lines and could be 50, rewrite it before showing it.
- If you find yourself adding "for future extensibility", stop. Future extensibility is a future decision.
- Bias toward deleting code over adding code. Shipping less is almost always better.

The test: would a senior engineer reading the diff call this overcomplicated? If yes, simplify.

---

## 3. Surgical changes

**Goal: clean, reviewable diffs. Change only what the request requires.**

- Do not "improve" adjacent code, comments, formatting, or imports that are not part of the task.
- Do not refactor code that works just because you are in the file.
- Do not delete pre-existing dead code unless asked. If you notice it, mention it in the summary.
- Do clean up orphans created by your own changes (unused imports, variables, functions your edit made obsolete).
- Match the project's existing style exactly: indentation, quotes, naming, file layout.

The test: every changed line traces directly to the user's request. If a line fails that test, revert it.

---

## 4. Goal-driven execution

**Goal: define success as something you can verify, then loop until verified.**

Rewrite vague asks into verifiable goals before starting:

- "Add validation" becomes "Write tests for invalid inputs (empty, malformed, oversized), then make them pass."
- "Fix the bug" becomes "Write a failing test that reproduces the reported symptom, then make it pass."
- "Refactor X" becomes "Ensure the existing test suite passes before and after, and no public API changes."
- "Make it faster" becomes "Benchmark the current hot path, identify the bottleneck with profiling, change it, show the benchmark is faster."

For every task:

1. State the success criteria before writing code.
2. Write the verification (test, script, benchmark, screenshot diff) where practical.
3. Run the verification. Read the output. Do not claim success without checking.
4. If the verification fails, fix the cause, not the test.

---

## 5. Tool use and verification

- Prefer running the code to guessing about the code. If a test suite exists, run it. If a linter exists, run it. If a type checker exists, run it.
- Never report "done" based on a plausible-looking diff alone. Plausibility is not correctness.
- When debugging, address root causes, not symptoms. Suppressing the error is not fixing the error.
- For UI changes, verify visually: screenshot before, screenshot after, describe the diff.
- Use CLI tools (gh, aws, gcloud, kubectl) when they exist. They are more context-efficient than reading docs or hitting APIs unauthenticated.
- When reading logs, errors, or stack traces, read the whole thing. Half-read traces produce wrong fixes.

---

## 6. Session hygiene

- Context is the constraint. Long sessions with accumulated failed attempts perform worse than fresh sessions with a better prompt.
- After two failed corrections on the same issue, stop. Summarize what you learned and ask the user to reset the session with a sharper prompt.
- Use subagents (Claude Code: "use subagents to investigate X") for exploration tasks that would otherwise pollute the main context with dozens of file reads.
- When committing, write descriptive commit messages (subject under 72 chars, body explains the why). No "update file" or "fix bug" commits. No "Co-Authored-By: Claude" attribution unless the project explicitly wants it.

---

## 7. Communication style

- Direct, not diplomatic. "This won't scale because X" beats "That's an interesting approach, but have you considered...".
- Concise by default. Two or three short paragraphs unless the user asks for depth. No padding, no restating the question, no ceremonial closings.
- When a question has a clear answer, give it. When it does not, say so and give your best read on the tradeoffs.
- Celebrate only what matters: shipping, solving genuinely hard problems, metrics that moved. Not feature ideas, not scope creep, not "wouldn't it be cool if".
- No excessive bullet points, no unprompted headers, no emoji. Prose is usually clearer than structure for short answers.

---

## 8. When to ask, when to proceed

**Ask before proceeding when:**
- The request has two plausible interpretations and the choice materially affects the output.
- The change touches something you've been told is load-bearing, versioned, or has a migration path.
- You need a credential, a secret, or a production resource you don't have access to.
- The user's stated goal and the literal request appear to conflict.

**Proceed without asking when:**
- The task is trivial and reversible (typo, rename a local variable, add a log line).
- The ambiguity can be resolved by reading the code or running a command.
- The user has already answered the question once in this session.

---

## 9. Self-improvement loop

**This file is living. Keep it short by keeping it honest.**

After every session where the agent did something wrong:

1. Ask: was the mistake because this file lacks a rule, or because the agent ignored a rule?
2. If lacking: add the rule under "Project Learnings" below, written as concretely as possible ("Always use X for Y" not "be careful with Y").
3. If ignored: the rule may be too long, too vague, or buried. Tighten it or move it up.
4. Every few weeks, prune. For each line, ask: "Would removing this cause the agent to make a mistake?" If no, delete. Bloated AGENTS.md files get ignored wholesale.

Boris Cherny (creator of Claude Code) keeps his team's file around 100 lines. Under 300 is a good ceiling. Over 500 and you are fighting your own config.

---

## 10. Project Learnings

**Accumulated corrections. This section is for the agent to maintain, not just the human.**

When the user corrects your approach, append a one-line rule here before ending the session. Write it concretely ("Always use X for Y"), never abstractly ("be careful with Y"). If an existing line already covers the correction, tighten it instead of adding a new one. Remove lines when the underlying issue goes away (model upgrades, refactors, process changes).

- Run performance baselines from an immutable checkout before editing fixtures or workflows.
## 11. Other important factors

### Repository map

- `apemap/`: reusable Python helpers and network-backed data collectors.
- `app/`: preserved legacy Dash prototype. The maintained CLI, DuckDB pipeline,
  and optional local review UI live in `apemap/`.
- `data/`: databases, GeoPackages, QGIS projects, SQL views, and source or
  processed datasets. Some files are large binary research artifacts.
- `notebooks/`: analysis and exploratory notebooks plus rendered HTML outputs.
- `images/`: generated figures used by the README and analysis.
- `README.md`: project context, data sources, caveats, and manual workflows.

### Start every task

1. Read this file and the relevant source, tests, README sections, and any
   nested `AGENTS.md` files before editing.
2. Inspect `git status` and preserve unrelated or pre-existing user changes.
3. Identify whether the work changes source data, derived data, generated
   output, application code, or packaging; state the expected validation.
4. Use an installed skill when it clearly fits. If a necessary skill is
   missing, install or create the narrowly scoped skill from a trusted source
   and document what was added. Do not add a skill merely as a substitute for
   understanding the repository.

### Issue, branch, commit, and pull-request workflow

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

### Semantic versioning and agent coordination

- At the start of each job, read `[project].version` in `pyproject.toml`, the
  root `apemap` entry in `uv.lock`, and relevant Git tags and release records.
  `pyproject.toml` is the canonical package version; branch names, documentation
  examples, and historical dataset versions are not version authorities.
- Use Semantic Versioning (`MAJOR.MINOR.PATCH`). Bump PATCH for compatible bug
  fixes, MINOR for compatible new functionality, and MAJOR for incompatible
  public API, CLI, or supported data-contract changes after version 1.0.0.
  While the package is at 0.x, bump MINOR for new functionality or incompatible
  changes and PATCH for compatible fixes; describe any incompatibility in the PR.
  Reset lower components to zero when bumping a higher component.
- Agents must assess the version impact of each coherent job and perform the
  required bump before its final verified commit and PR handoff. Documentation,
  tests, and internal refactors that do not change observable behavior normally
  need no package bump; state that decision in the PR. Dependency changes use the
  same behavior and compatibility criteria rather than forcing a bump by default.
- Coordinate one target version for a release or shared branch. Read the current
  version again before editing it; a coordinating agent owns the bump, and other
  agents use that agreed version instead of independently incrementing it for
  every task, checkpoint, or commit. If the intended version is already set for
  the job, retain it unless the final scope requires a larger bump.
- Update package metadata with `uv version <target-version>` and regenerate the
  lockfile with `uv lock`. Commit `pyproject.toml` and `uv.lock` together whenever
  both change. Before handoff, run `uv lock --check` and verify that the root
  package versions match; explain any pre-existing mismatch that is repaired.
- Dataset releases have their own versions, supplied explicitly to
  `apemap release build --version <target-version>` and export commands that
  accept `--data-release-version`. Reviewed data corrections require a new
  dataset PATCH release; additive dataset functionality requires MINOR, and
  breaking schema or contract changes require MAJOR (MINOR while at 0.x).
  A data-only correction does not require a package bump. Record the selected
  dataset version in its new manifest and release notes, and use the same version
  for build, verification, handoff, and the existing `data-v<version>` tag workflow.
- Never reuse a published version for changed contents or rewrite historical
  manifests, checksums, dataset directories, tags, or documentation examples to
  make them match the current package version. Use an unused version for each
  new release. Use SemVer prereleases such as `0.3.3-rc.1` for dataset candidates;
  Python package prereleases use the equivalent PEP 440 form (`0.3.3rc1`).
  Version preparation and a draft PR do not authorize release publication;
  publish only through the documented workflow when requested.
- In every PR, state the previous and target versions, the reason for the bump
  (or no bump), package versus dataset effects, and version validation results.

### Python environment and dependencies

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
- Root `pyproject.toml` and `uv.lock` are the canonical Hatchling/uv package and
  dependency configuration; `prek.toml` defines Git hooks. Legacy Poetry and
  superseded tool configurations are preserved under `archive/legacy-packaging/`.
  Do not reintroduce competing active dependency or tool configurations.

### Archiving legacy files

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

### Coding practices

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

### Data and notebook safety

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

### Testing and quality gates

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

### Long-running work, goals, and queued follow-ups

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

### Definition of done

A job is complete only when the requested behavior or artifact exists, relevant
tests and checks have run, documentation and data provenance are current, the
branch diff contains no unrelated changes or secrets, coherent checkpoints are
committed, and a draft PR has been opened (or an exact PR blocker is reported).
