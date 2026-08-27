---
name: spike-and-stabilize
description: Incremental coding methodology for non-trivial tasks. Survey the code, get one combined clarify-and-plan approval, write a thin throwaway spike, then stabilize it (types, tests, checks, review). Skip the flow for trivial tasks like renames, typo fixes, and one-liners.
---

# Spike & Stabilize

Requirements are often vague. Discipline comes from stabilizing one layer at a time.
For every non-trivial task, run these four steps in order. Don't reorder, skip, or
merge them; if a step doesn't apply, say why and move on.

1. Recon
2. Clarify and plan (one approval gate)
3. Spike
4. Stabilize

## Trivial escape hatch

Skip the flow for obviously trivial work: renames, typo fixes, one-line changes,
import tweaks. Say "Trivial: <reason>", make the fix, run one relevant check, present
the result. "Small" or "well-understood" is not trivial; only the absence of business
logic and branching is.

## 1. Recon

Before touching code, get a high-level map, then narrow to the few files that matter.
Delegate broad searches to an `Explore` sub-agent (model Haiku for plain location,
Sonnet when it must reason about behavior) so file dumps stay out of the main context;
it returns a summary, not the files. Read into your own context only the specific files
the task touches, not everything nearby. Search inline only when one targeted Grep
answers it. Report back:

- Existing patterns for similar features, with file paths.
- Reusable code (helpers, base classes, public APIs) the work can build on.
- Where the new code should live, and why, given the `.importlinter` layering.
- Conflicts and surprises: deprecated code, clashing patterns, naming drift.

Use the findings to answer your own questions before asking the developer.

## 2. Clarify and plan

Do the clarify-and-plan work in plan mode (`EnterPlanMode`). Ask focused questions with
`AskUserQuestion` (2-4 options each), then present a short plan. Do not write any
implementation code, including illustrative snippets, until the developer approves the
plan via `ExitPlanMode`.

On plan approval, delegate the implementation (the spike and stabilize coding) to a
`general-purpose` sub-agent on Sonnet via the Agent tool. Brief it with the approved
plan, the target files, and the conventions to follow. You stay on the strong model to
run the gates, judge results, and do the final review; don't write the code yourself.

- Enumerate edge cases yourself and propose handling; don't ask the developer to list
  them. Time/timezones, parsing, and concurrency are the usual traps.
- Present options when interpretations differ; don't pick silently.
- Zero questions is fine when recon resolved it: confirm your assumptions and proceed.
- For a significant, hard-to-reverse decision (new dependency, data-model or public-API
  change, new boundary), draft an ADR in proposed status under
  `docs/openedx_content/decisions/` and name it in the plan. See CLAUDE.md.

## 3. Spike

Have the implementation sub-agent write the thinnest vertical slice that does something
real end-to-end. Rough is fine. Brief it with these rules:

- Happy path only; defer error handling and edge cases.
- Loose types where the shape is unclear; tighten later.
- Hard limit 50 lines. If it doesn't fit, narrow the scope, don't raise the cap.
- Mark it `# SPIKE: shape not final`.
- Before writing logic that already exists elsewhere, reuse it or say why it doesn't fit.

Show the developer the result and confirm the shape before stabilizing.

## 4. Stabilize

Once the shape is confirmed, add no new features. Hand the same sub-agent (continue it
via `SendMessage` so it keeps context) these steps in order:

1. Tighten types: full annotations; prefer dataclasses or `TypedDict` over bare `dict`.
2. Write tests: one per behavior, not per function. Ask "what would tell me this works?"
3. Run checks until clean: the relevant linters directly (pylint, mypy, pycodestyle,
   pydocstyle, isort) on changed files, plus targeted `pytest --no-cov` for related
   tests. See CLAUDE.md for why not `make test`. Never suppress a check to go green.
4. Apply the rule of three: don't extract shared code until a third caller needs it.
5. Report: what's covered, which checks ran clean, what's next.

As the final step, you (the strong main model, not the sub-agent) run the `code-review`
skill on the diff. Fix anything it flags as wrong before the change is done.

## Expand

For the next increment, repeat the clarify-and-plan gate unless it's trivial. Pause and
confirm before significant complexity: a new dependency, a data-model or public-API
signature change, going async, cross-module changes, or any ambiguity recon left open.

## When blocked

If the next step is low-confidence and high-cost, stop. Summarize what's not working,
give 2-3 options, and ask which path to take. Don't keep guessing.
