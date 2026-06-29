# CBE Project Context (Competency-Based Education)

Broad background for the AI agents working in this repo, the `business-analyst` and
`software-architect` especially. It explains the program we are part of, the domain, and
where the work in this repo sits. It is orientation, not instructions: it tells you what
the project *is* so you can judge a ticket's intent, scope, and relevance. It does not
tell you what to build. Verify specifics against the repo and the ADRs before relying on
them; this document can drift.

Companion docs: [`architecture-overview.md`](architecture-overview.md) (what this repo is
and how it is layered) and
[`architecture-taxonomies-and-competencies.md`](architecture-taxonomies-and-competencies.md)
(the current tagging/taxonomy code that CBE builds on).

## The ecosystem and where we sit

Open edX is a large, multi-repo learning platform. The repos that matter for this work:

- **`openedx-core`** (this repo). A published Django library, formerly named "Learning
  Core" / `openedx-learning` (see `docs/.../0002-openedx-core.rst`). It is the lower
  layer: `openedx-platform` imports its apps and calls their in-process public APIs.
  Code here never imports from `openedx-platform`. Database tables use the `oel_` prefix
  (Open edX Learning). Top-level packages under `src/`: `openedx_catalog`,
  `openedx_content`, `openedx_core`, `openedx_django_lib`, `openedx_tagging`. The
  `.importlinter` contracts enforce the layering.
- **`openedx-platform`** (edx-platform). The big LMS and Studio (CMS) application. It is
  the upper layer and depends on this repo. Grading lives here, and the CBE *evaluation
  logic* (the grade-signal handler that computes mastery) is planned to live in an app
  here, calling into this repo.
- **A separate frontend.** The program includes authoring and learner UI work delivered
  in a separate frontend (React micro-frontend) and in Studio. This backend repo defines
  no frontend code; treat the frontend as a sibling deliverable, not part of this repo.

The practical takeaway: this repo owns the **data model and the in-process APIs** for
competencies and competency criteria. It does not own the grading pipeline, the
evaluation trigger, or the UI.

## What CBE is

Competency-Based Education is a model where learners advance on demonstrated **mastery**
of competencies rather than seat time. A *competency* is a measurable statement of what a
learner knows and can do. Mastery is demonstrated by meeting *competency criteria*: rules
about what a learner must achieve on a piece of gradable content (for the MVP, a grade
threshold such as ">= 75% on Assignment 1"). The same competency can be earned through
different combinations of content, within or across courses, combined with AND/OR logic.

Open edX already supports **Taxonomies**: generic hierarchies of tags associated with
course content (the `openedx_tagging` app: `Taxonomy`, `Tag`, `ObjectTag` in
`src/openedx_tagging/models/base.py`, public API in `src/openedx_tagging/api.py`, tables
`oel_tagging_*`). The core CBE idea reuses this: a **competency is a tag** in a taxonomy
specialized to the "Competency" type. Associating a competency tag with gradable content
is the entry point for attaching criteria. So CBE is largely an extension of the existing
tagging stack, not a parallel system.

## The program, and the slice we are in

The CBE program is large and spans multiple epics and repositories: instance-wide
competency authoring (CMS side), learner progress views (LMS side), the criteria data
model and evaluation (this repo plus edx-platform), and pathway/program work led with
OpenCraft.

**Our current epic is "sprint-0".** Its only goal is to produce a **backlog of roughly 80
tickets** for future CBE work across these areas. We are not building the feature in this
epic; we are scoping and writing the work. So when reviewing or authoring a ticket here:

- The deliverable is well-formed backlog tickets, not code.
- A ticket only belongs in this repo's backlog if its work lands in `openedx-core` (the
  data model, models, APIs, migrations) or its directly related frontend. Learner-UI
  behavior, grading changes, and the evaluation handler belong to other repos; reference
  them as context or dependencies, do not write them as work here.
- No competency code exists yet. This is greenfield. The authoritative in-repo design is
  the set of ADRs below, all currently proposals.

## Domain model (conceptual)

The in-repo design (ADR 0002, see below) is canonical for naming and structure. At a
conceptual level:

- **CompetencyTaxonomy**: a `Taxonomy` specialized as a competency framework. Managed
  instance-wide by admins. Course authors get read-only access to taxonomies.
- **Competency (tag)**: a `Tag` within a competency taxonomy. Hierarchical
  (competency / sub-competency). Sub-competencies roll up: a parent is mastered only when
  all its sub-competencies are.
- **CompetencyCriterion**: a single rule. For the MVP, a "Grade" rule: an operator from
  `>`, `>=`, `<`, `<=`, `=` and a threshold, evaluated against one gradable content
  object (a course final grade or a subsection assignment).
- **CompetencyCriteriaGroup**: groups criteria under a competency and evaluates them with
  an AND/OR `logic_operator`. Nestable (self-referential parent). Associated with 0 or 1
  courses; if course-scoped, its criteria reference only content in that course. A
  competency may have several groups (for example one per course), OR-ed together by
  default.
- **CompetencyRuleProfile**: a reusable rule definition (rule type plus payload) scoped to
  an org/course/taxonomy, so criteria can share a profile and override it when needed.
- **Student status** entities at three levels (criterion, group, competency), recording
  per-learner mastery.

ADR 0002 also names `CompetencyAchievementCriteria`; read the ADR for the precise model
rather than treating this list as a schema.

### Two different status vocabularies

Keep these separate; they are easy to confuse.

- **Backend evaluation statuses** (this repo, ADR-defined): `Demonstrated`,
  `AttemptedNotDemonstrated`, `PartiallyAttempted`, applied at the criterion, group, and
  competency levels. `AttemptedNotDemonstrated` is intentionally **excluded** at the
  competency level, because computing "no remaining options" can depend on which courses
  a learner is or could be enrolled in.
- **Learner-facing completion statuses** (the progress-view PRD, frontend/LMS): a richer
  display set such as Not Started, In Progress, Partially Attempted, Attempted Not
  Demonstrated, Completed, Mastered for competencies, and Not Started / In Progress /
  Submitted / Completed for activities. These are UI states, not the backend's evaluation
  enum.

## Evaluation and data flow (for context, not work here)

Evaluation is asynchronous and triggered by **grade-persistence events**. When a grade is
awarded for a content object associated with a competency tag, the related criteria,
groups, and competency statuses are recomputed. This logic is planned to live in an
**app inside `edx-platform`** that receives the grade signal, computes the result, and
pushes it into `openedx-core` via a function call. Grading data stays the authoritative
source for grades; criteria compare against it. Re-evaluation on criteria changes
(recalculate from all time / from a date / forward only, and whether to lower a status)
is an open design area.

## Scope boundaries

**MVP goals (per the technical design):** associate competencies to courses (by final
grade) and to subsection assignments; "Grade" criteria with the five operators; AND/OR
groups including nesting; the three-level status model; a re-evaluation workflow.

**Explicit non-goals / out of scope (do not scope tickets into these unless asked):**

- Criteria on sections, units, or problems (only course and subsection in MVP).
- A "MasteryLevel" grading type (custom mastery words instead of percentages); deferred.
- Rubrics as a criteria type; a "View content = mastery" type (rejected).
- Retaking assignments.
- Versioning of criteria or taxonomies, and publish/draft workflows (ADR 0003 explores
  this; still out of MVP).
- Migrating taxonomies/criteria into Libraries.
- Foreign-key-safe identifiers for Modulestore/MongoDB content (`object_id` stays
  polymorphic and not FK-safe, inherited from tagging).
- Migrating grading events/signals to `openedx-events`.
- Learner progress UI, next-best-action, pacing, and program/pathway-level rollup (these
  are frontend/LMS, and several are marked "out of scope for Willow", a named release).
- Drag-and-drop authoring and visual mapping badges (parking-lot items in the PRD).

## Authoritative in-repo resources

- **CBE ADRs** under `docs/openedx_learning/decisions/` (all currently proposals; note
  these ADRs use Context/Decision headings and do not carry a `Status:` line):
  - `0001-competency-criteria-location.rst`: CBE code lives in this repo at
    `src/openedx_learning/applets/cbe` (a new `openedx_learning` package).
  - `0002-competency-criteria-model.rst`: the data model and domain terms.
  - `0003-competency-criteria-versioning.rst`: the versioning approach.
- **Tagging foundation**: `src/openedx_tagging/` (`models/base.py`, `api.py`), tables
  `oel_tagging_*`. CBE extends this.
- `architecture-taxonomies-and-competencies.md` for how taxonomies and competencies fit
  the current code.

## Naming and source caveats

The three source PRDs/design docs predate, and are broader than, this repo's work. When
you encounter them or their terminology:

- "Assessment criteria" in older material is the same concept now called **competency
  criteria**.
- The design doc references an external "ADR 0022 / assessment-criteria-model" in
  `openedx-learning`. In this repo the equivalent design is renumbered as ADRs 0001-0003
  under `docs/openedx_learning/decisions/`. Trust the in-repo ADRs.
- The design doc's table names (`oel_competency_*`, `student_*`) describe an earlier model
  sketch. Use ADR 0002 for the canonical entities.
- The PRDs go deep into authoring UX and learner progress views. Most of that is other
  repos. Pull a learner-UI or grading detail into a ticket here only when it genuinely
  constrains this repo's data model or API.
