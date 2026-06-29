---
name: code-review
description: Senior peer review of a diff for this Django library. Use when asked to review a PR, a branch, or a diff, or for a second opinion on changes. Also run as the final step of spike-and-stabilize. Review-only: surface findings with file:line anchors and a severity marker; never fix, commit, or push. Post to GitHub only when explicitly asked and approved.
---

# Code review

A senior peer review for `openedx-core`. Surface what is wrong and why; let the
developer decide what to act on. Never apply fixes, commit, or push from this skill.

## 1. Get the diff

Identify the target from what the developer asked: a PR number (`gh pr diff <n>`),
a branch, or the working changes. Default to `git diff "$(git merge-base HEAD origin/main)"..HEAD`.
Read the diff and the code it directly calls at the diff's ref, not a stale checkout.
When a finding needs to know how the changed code is used across the codebase, delegate
that exploration to a Sonnet `Explore` sub-agent and work from its summary rather than
reading many files yourself. If the diff is empty, say so and stop.

## 2. Review

Check the dimensions the diff actually touches; skip the rest and say which you skipped.

- Correctness: logic, edge cases, error paths, off-by-one, None handling.
- Security: REST endpoints enforce auth and validate input; no secrets; new Django
  models carry PII annotations.
- Data access: N+1 queries, missing indexes, unbounded loads, unsafe migrations.
- Architecture: respects `.importlinter` layering; no import from `openedx-platform`;
  breaking changes to stable (non-UNSTABLE) public APIs go through DEPR; model
  identifiers follow `id`/`uuid`/`key`/`num`.
- Tests: each behavior covered; assertions can actually fail; no vacuous tests.
- Naming and docstrings: clear names, RST docstrings on public APIs, consistent terms.
- Surgical scope: every changed line traces to the stated goal.

## 3. Verify before asserting

A claim that sets a finding's severity needs a source: a `file:line` or command
output, not memory. Verify against the actual code at the diff's ref. Checking a
library's documented behavior is a web lookup, so run it in a sub-agent, never inline.
If you can't back a claim, downgrade it to a question. Don't pad findings with weak
citations.

## 4. Present

Group findings by severity, each with a `file:line` anchor and a one-word marker:

- `Bug.` wrong behavior, security hole, or broken contract.
- `Question.` a premise you can't confirm; ask rather than assert.
- `Suggestion.` a real improvement that is optional.
- `Nit.` style or wording, lowest priority.

Lead with the point, then the mechanism. Keep the tone that of one engineer to another:
direct, specific, no praise padding. Open with a one-line verdict, then the findings.
If nothing is wrong, say so plainly. Post to GitHub only if the developer asks and
approves the wording.
