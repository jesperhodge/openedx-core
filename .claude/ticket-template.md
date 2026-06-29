# Ticket template

The canonical shape for a development ticket in this project. The `business-analyst`
skill and the `business-analyst` and `software-architect` agents read this to review,
improve, and author tickets, whether tightening an existing one or drafting a new one.

Section ownership: the business-analyst owns Use Case, Description, Acceptance Criteria,
and the non-code parts of Context. The software-architect owns Technical Details, Files
to modify, and the code and prior-art parts of Context.

A ticket has six sections, in this order.

## Use Case

A user story: who, what, why. "As a <role>, I want <capability>, so that <outcome>."
Name a real Open edX role (course author, library maintainer, learner, operator, plugin
developer), not "the user." The "so that" is the stakeholder's true intent; if you can't
state it, the ticket is not ready.

## Description

Prose context: the problem, the current behavior, the desired behavior, and what is
explicitly out of scope. One or two tight paragraphs. State assumptions and constraints,
and call out what this ticket does NOT do, to hold the scope line.

## Acceptance Criteria

Gherkin-style scenarios (Given / When / Then), one per distinct behavior, including the
unhappy paths. Detailed, but treated as living: they evolve as understanding and
alignment change, so write them to be revised, not frozen. Each criterion must be
observable and testable. Cover the empty, error, and permission cases, not just the
happy path.

    Scenario: <short name>
      Given <precondition>
      When <action>
      Then <observable outcome>

## Technical Details

How to build it, at the design level: the recommended approach, the data model and
migrations, the public-API and contract impact, the layer it sits in (respect
`.importlinter`), performance and backward-compatibility notes, and the test strategy.
Note any ADR that is warranted. This is the software-architect's recommended approach,
not a code dump.

## Context

What a developer should read before starting: relevant code files (with paths), prior
art and similar features, docs and ADRs, and related or blocking tickets. This is the
on-ramp; a good Context section saves the developer the discovery the analyst and
architect already did.

## Files to modify

A table of the files the work touches and how, one row per file:

    | File | Nature of modification |
    | --- | --- |
    | src/openedx_content/api.py | add `publish_container()` to the public facade |
    | src/openedx_content/applets/publishing/models.py | new index on `PublishLog` |

List new files too, marked as new. This is the architect's map; it must match the
Technical Details and the import layering.

## What "awesome" looks like

- The Use Case names a real role and a real outcome; the "so that" is not hand-waved.
- Scope is bounded: the Description says what is out of scope.
- Every acceptance criterion is observable and could fail; unhappy paths are covered.
- Technical Details pick one approach and justify it, respecting the layering and
  contracts.
- Context and Files to modify are concrete, with real paths, so a developer can start
  cold.
- The whole thing is a 1-2 developer-day unit of work, or it is split and says so.
- It is SMART: Specific, Measurable, Achievable, Relevant, Time-bound.
