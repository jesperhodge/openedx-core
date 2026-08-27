---
name: business-analyst
description: Expert review or authoring of a feature ticket before development for this Open edX library. Use when asked to analyze, refine, scope, sanity-check, improve, or write a ticket, issue, or requirement, or to check one against SMART and 1-2 developer-day feasibility. Runs a business-analyst pass (requirements quality and true intent) then a software-architect pass (scoping, split or merge, technical design with alternatives), and produces an improved or new ticket in the project's canonical six-section format plus the findings behind it. Advisory: does not change code or post to trackers unless asked and approved.
---

# Business analyst

Review or write a feature ticket before any code is written. Two experts do the deep work
as consultants hired to roll out the feature: they guard scope and warranted effort and
serve the developers. The business-analyst checks requirements quality and recovers the
true intent; the software-architect checks scoping and designs the approach. You
synthesize the result into one ticket in the project's canonical format
(`.claude/ticket-template.md`), plus the findings behind it.

This is advisory. It produces an assessment; it does not change code. Apply changes to
the real ticket (edit the issue, post a comment) only when explicitly asked, and after
the developer approves the wording.

You, the main session on the strong model, orchestrate. The two experts inherit your
model on purpose, so the judgment runs on the strong model; both push their own
context-heavy reading down to cheaper sub-agents. This deliberate exception to the usual
"sub-agents run on a small model" rule is why these two are dispatched without a `model`
override.

## 1. Get the ticket or feature request

Identify what you are working from:

- Pasted text: use it directly.
- A file path: read it.
- A GitHub issue: `gh issue view <n> --comments`.
- A URL or other tracker (Jira and similar): delegate the fetch to a Sonnet sub-agent;
  never fetch inline.
- A feature idea with no ticket yet: treat it as a request to draft a new ticket in the
  canonical format. Capture the intent and proceed.

Skim `.claude/ticket-template.md` so you know the six sections and who owns each. For
competency, skills, or CBE work, also read `.claude/cbe-project-context.md` for program
background and scope boundaries. Also collect any related tickets or backlog the developer
points at; the architect needs them to judge split and merge boundaries. If there is
neither a ticket nor a feature request to work from, say so and stop.

## 2. Business-analyst pass

Dispatch the `business-analyst` sub-agent (Agent tool; it inherits your model) with the
ticket text and any context. It returns the true-intent summary, requirements findings
(missing / open questions / contradictions / outdated / improvements), a SMART
scorecard, a feasibility flag, and its owned sections (Use Case, Description, Acceptance
Criteria, non-code Context) improved, or for a new ticket drafted, in the canonical
format. Reason over its summary; do not redo its reading.

## 3. Software-architect pass: scoping

Dispatch the `software-architect` sub-agent (Agent tool; it inherits your model) with the
ticket and the business-analyst's findings, plus any related tickets. Ask for Mode A:
is it well-scoped, or should it be refined, split into sub-tickets along named seams, or
merged with a related ticket? Get its own SMART read and a 1-2 developer-day feasibility
verdict with the split if one is needed.

## 4. Software-architect pass: technical design

Continue the same architect sub-agent (via `SendMessage`, so it keeps its context) into
Mode B: the constraints and forces, at least three alternative implementations with
their tradeoffs, and one recommended approach (simplest correct, cleanest, best fit),
plus the contracts, migrations, and ADR flags to watch. Have it express the
recommendation as its owned sections (Technical Details, Files to modify, code and
prior-art Context) in the canonical format.

## 5. Synthesize

Combine the two into the deliverable, verdict first and skimmable:

- The assembled ticket in the canonical format (`.claude/ticket-template.md`): all six
  sections, the BA-owned and architect-owned sections merged into one coherent ticket.
  For a review this is the improved ticket; for a new request, the drafted one.
- The findings behind it: true intent and whether the ticket serves it; open questions
  for the human BA, the blocking ones first; the scope verdict (ready / refine / split /
  merge) with any sub-tickets or merge; the SMART scorecard and an effort estimate; and
  the rejected technical alternatives in one line each.

For a split, produce one ticket per resulting sub-ticket in the canonical format. Touch
the real tracker only on explicit request and approval.

## Context hygiene

Keep your own context lean. The two experts run on your model and must delegate
context-heavy work: `Explore` on Haiku for location and enumeration, `Explore` or
`general-purpose` on Sonnet for read-and-reason and web. The strong model stays on
orchestration and the final synthesis. Do not read large docs or run web lookups inline.
