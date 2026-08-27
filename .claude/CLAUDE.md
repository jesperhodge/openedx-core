# Agent Instructions

This is `openedx-core`: a published Django library of apps used by `openedx-platform`
and community plugins. Treat public APIs as contracts and keep changes small.

## Which skill to use

- Implementing a non-trivial change: use the `spike-and-stabilize` skill.
- Reviewing a PR, branch, or diff: use the `code-review` skill.

## Context hygiene

Keep the main context lean. Push context-heavy work into sub-agents and reason over the
summary they return, not the raw material.

- Understanding behavior or architecture across many files: dispatch an `Explore`
  sub-agent; don't read the files into your own context.
- Web search or fetching pages: always in a sub-agent, never inline.
- Reading any large document or other high-context material: in a sub-agent.
- Read into your own context only the few files the task actually touches. Start from a
  high-level overview and narrow down; don't pull in everything nearby.

Sub-agents run on a smaller model, not the session model: Haiku for enumeration and
"where is X" location, Sonnet for reading-and-reasoning, web research, and writing code.
Pass `model` explicitly on every dispatch. The main session keeps the strong model for
orchestration, planning, and final review.

## Working style

- Think before coding: state your assumptions, and ask when the request is ambiguous.
  Present options instead of guessing silently. Trivial tasks skip this.
- A question is not a work order: answer questions, don't edit code until told to.
  "Why did you do X?" means explain X, not undo it.
- Simplicity first: make the smallest correct change. No unrequested abstractions,
  flexibility, or error handling for impossible cases.
- Reuse before rebuild: prefer existing code, the stdlib, then Django/DRF, then an
  installed dependency, before writing something new.
- Surgical changes: don't refactor or reformat code your task doesn't touch. Match
  existing style. Every changed line should trace to the request.
- Don't extract shared code until three callers need it. DRY is about knowledge, not
  code that happens to look alike.

## Verify before done

- Run verification commands before reporting done, but don't use `make test` and `make validate` directly because they take long and produce too much output. Run the linters in question directly, and for tests, run pytest without coverage and only the tests that are related to code changes.
- Never silence a check to make it pass. Suppression directives (`# noqa`,
  `# pylint: disable`, `# type: ignore`) need explicit approval, never to go green.
- New code needs tests. If you ship without them, say so as a gap.

## Architecture

- Never import from `openedx-platform`. Code here is the lower layer.
- Respect the layering in `.importlinter`; `lint-imports` enforces it. Don't loosen a
  rule to fix the build.
- Public APIs are stable. Breaking changes to non-UNSTABLE APIs go through the DEPR
  process, not a silent edit. See `docs/openedx_content/decisions/0006-*`.
- Model identifiers (see README): `id` (internal PK), `uuid` (stable external ref),
  `key` (client-meaningful, DB field `_key`), `num` (numeric key).
- New Django models need PII annotations (`make pii_check`).
- To understand current architecture decisions, refer to ADRs (docs/decisions).
- Document impactful architecture decisions in new ADRs in proposed status.

## Comments and docstrings

- Comment why, not what. No change narration ("updated X", "as requested", "was Y").
- Public functions, classes, and models get docstrings in RST format describing the
  contract, not the implementation.
- Treat stale comments as bugs: update or delete them in the same change.

## Commits

- Use conventional commits (`feat:`, `fix:`, `chore:`, `docs:`, `perf:`, `build:`).
  `commitlint` enforces this. Imperative subject under 72 chars.
- Commit or push only when asked.

## Writing style

- Direct, engineer-to-engineer. No marketing tone, filler, or AI-slop openers.
- Avoid em dashes; use commas, colons, or periods. Use bold sparingly.
- US spelling. Inclusive terms (allow list / deny list, `main`, primary/replica).
- Write the current state, not its history. History lives in `git log`.
