# Taxonomies, Tags & Competencies

> Reference for future Claude agents on the tagging system (`openedx_tagging`) and the
> Competency-Based Education / competency-criteria design (planned `openedx_learning`).
> Synthesized from [docs/openedx_tagging/decisions/](docs/openedx_tagging/decisions/)
> and [docs/openedx_learning/decisions/](docs/openedx_learning/decisions/).
>
> For repo-wide context (apps, applets, publishing framework, identifier conventions,
> and a Glossary of Open edX terms like XBlock/Studio/LMS/opaque keys), see
> [Architecture Overview](.claude/architecture-overview.md).

The three layers, bottom to top:

1. **Tags & Taxonomies** — the controlled vocabulary. *(implemented: `openedx_tagging`)*
2. **ObjectTags** — attach a tag to a piece of content (or a course, user, etc.). *(implemented)*
3. **Competencies / Competency Criteria** — reuse tags + ObjectTags, layer evaluation
   rules and per-learner status on top. *(DESIGN-STAGE — ADRs only, not yet coded)*

---

## Part A — Tagging (`openedx_tagging`)

### A.1 Purpose & boundaries

Content tagging is treated as a **"kernel" platform feature** (OEP-57), foundational to
content reuse, flexible content structures, and adaptive learning.
[0001-content-tagging.rst](docs/openedx_tagging/decisions/0001-content-tagging.rst)

The hard rule ([0002-tagging-app.rst](docs/openedx_tagging/decisions/0002-tagging-app.rst)):
**`openedx_tagging` is a STANDALONE library with NO dependencies on other Open edX
projects.** Anything needing Open edX specifics (Organizations, CourseKey/UsageKey
validation, course-creator permissions) lives in **`edx-platform`'s
`openedx.core.djangoapps.content_tagging`** app instead, not here.

- It was implemented as a pluggable Django app (rejected: building it into the Discovery
  service, or standing up a new IDA microservice).
- Rejected: embedding tagging logic directly in `edx-platform` (we want reuse beyond
  content — e.g. tagging People, marketing contexts).

### A.2 Core models

Source: `src/openedx_tagging/models/` (`base.py`, `system_defined.py`,
`import_export.py`).

- **`Taxonomy`** — a namespaced set of related tags + usage rules. Self-contained to this
  app.
- **`Tag`** — a value within a taxonomy, optionally with an `external_id`. Hierarchy is a
  **simple parent foreign key (adjacency list)**, not a tree library.
- **`ObjectTag`** — links a `Tag` to some object (`object_id`). This is the
  labor-intensive, precious data.

### A.3 Key design decisions

**Tag tree = deliberately simple.**
[0003-tagging-tree-data-arch.rst](docs/openedx_tagging/decisions/0003-tagging-tree-data-arch.rst)
A formal tree structure (closure tables, `django-mptt`, `django-tree-queries`) was
rejected as overkill. Taxonomy trees were capped at **depth 3**, change infrequently, and
are traversed rarely. *(Status note: the single-taxonomy API has since evolved to support
unlimited depth — see A.4.)*

**Three taxonomy flavors:**
- **Closed** — tags chosen from a fixed list/tree.
- **Open / free-text** — arbitrary tag values allowed.
- **System-defined** — tags derived dynamically from settings or a model.

**System-defined taxonomies are dynamic.**
[0007-system-taxonomy-creation.rst](docs/openedx_tagging/decisions/0007-system-taxonomy-creation.rst)
e.g. Languages (from Django settings), Users, Organizations. The `Tag` row may not exist
until needed. Each `Taxonomy` provides:
- `validate_value()` / `validate_external_id()` → is this a valid tag?
- `tag_for_value()` / `tag_for_external_id()` → validate **and auto-create** the `Tag` if
  missing.
Subclasses override these. There is intentionally **no API to list all possible tags** for
these (you can't enumerate every user); UIs list only the *applied* `ObjectTag` values.

**System-defined auto-tagging.**
[0008-system-taxonomy-auto-tagging.rst](docs/openedx_tagging/decisions/0008-system-taxonomy-auto-tagging.rst)
Content creation/edit fires **`openedx-events`** signals; **receivers live in
`edx-platform`'s `content_tagging` app** (`handlers.py` → Celery tasks) — *not* the
`openedx.features.tagging` path the ADR originally proposed (that location does not exist).
Chose events over Django signals (some content lives in Mongo, not Django models) and over
`openedx-filters` (filters are for blocking/altering flow; not needed here). See §A.6 for
exactly which events fire and what actually gets tagged.

**ObjectTags survive taxonomy/tag changes (resilience).** — *important & subtle.*
[0006-tag-changes.rst](docs/openedx_tagging/decisions/0006-tag-changes.rst)
An `ObjectTag` stores **both** a FK to its `Taxonomy`/`Tag` **and a cached copy** of
`taxonomy.export_id` + `tag.value`. Consequences:
- If the taxonomy or tag is **deleted**, the ObjectTag survives and shows the cached
  `export_id:value`.
- The **authoritative** `Taxonomy.name` / `Tag.value` is consulted on display, so renames
  show up immediately.
- The cached `value` also lets free-text tags exist without mutating the taxonomy, and
  lets tagged content be **imported before** its taxonomy exists, then re-synced later via
  editing or a maintenance command.

**Permissions via `django-rules`, enforced in VIEWS not the API/models.**
[0004-tagging-administrators.rst](docs/openedx_tagging/decisions/0004-tagging-administrators.rst)
- Use `user.has_perm('openedx_tagging.<perm>', obj)` (OEP-9 style). Rules live in
  `src/openedx_tagging/rules.py`.
- Enforcement is in the REST views, **not** the Python API/models, so external code can
  call the API without a logged-in user — **use the API with care.**
- **Taxonomy Administrators** in Studio = a modified "course creator" concept: global
  staff/superusers (enforceable in this app), plus org-scoped course creators (requires
  `edx-platform`'s `CourseCreator` model, so enforced in `content_tagging`).

**Org scoping** ([0005](docs/openedx_tagging/decisions/0005-taxonomy-enable-context.rst) is
**REJECTED/superseded**): the described "enable all taxonomies" course advanced-setting and
the `OrgTaxonomy` subclass were never shipped. Instead a **`TaxonomyOrg` model** links
Organizations to plain `Taxonomy` instances. Likewise, `ObjectTag` is **no longer
subclassed** (2024 simplification) — platform extends the `can_change_object_tag` rule via
`django-rules` hooks instead.

### A.4 The single-taxonomy REST API

[0009-single-taxonomy-view-api.rst](docs/openedx_tagging/decisions/0009-single-taxonomy-view-api.rst)
(Accepted; has since evolved to support unlimited depth.)

- Endpoint: `GET /tagging/rest_api/v1/taxonomies/:id/tags/`
  - no `parent_tag` → root tags; `?parent_tag=...` → that tag's children (lazy expand).
  - both paginated independently; response includes child counts, page ranges, etc.
  - `?search_term=...` → returns matching tags **plus their ancestors** (so the tree
    renders correctly).
  - `?full_depth_threshold=N` → if results < N, return the whole tree as one page
    (powers "Expand all").
- Backend Python API: **`Taxonomy.get_filtered_tags()`** covers the same cases, returning a
  `QuerySet` of tag dicts rather than JSON.
- Search/filter of *applied* tags during content search is intentionally **out of scope**
  here — that's handled by Elasticsearch/OpenSearch.

### A.5 Source layout cheat-sheet

```
src/openedx_tagging/
  models/        base.py · system_defined.py · import_export.py · utils.py
  rest_api/      the v1 endpoints above
  import_export/ taxonomy import/export
  rules.py       django-rules permission predicates
  api.py         public Python API
  data.py        data classes / enums
  signal_handlers.py · tasks.py · urls.py
```

> Table prefix note: tagging tables still use the legacy **`oel_tagging_*`** prefix
> (e.g. `oel_tagging_taxonomy`, `oel_tagging_tag`, `oel_tagging_objecttag`). The CBE ADRs
> reference these names directly.

### A.6 How edx-platform's `content_tagging` extends this (verified against code)

`edx-platform`'s `openedx/core/djangoapps/content_tagging` is the integration layer
(it imports the library as `oel_tagging`). Confirmed in platform source:

- **Reuse, not subclass.** It imports `openedx_tagging`'s `Taxonomy`/`ObjectTag` directly
  and does **not** subclass them — consistent with the 2024 simplification noted in A.3.
- **Org-scoping = `TaxonomyOrg`** (`content_tagging/models/base.py`): a many-to-many join
  between `Taxonomy` and `organizations.Organization` with `rel_type=OWNER`, where
  **`org=None` means "all organizations."** API wrappers (`create_taxonomy`,
  `set_taxonomy_orgs`, `get_taxonomies_for_org`) wrap the `oel_tagging` equivalents and
  attach/filter by `TaxonomyOrg`. This is the model that **replaced the rejected
  `OrgTaxonomy` subclass** from ADR 0005.
- **Permissions are re-bound, not replaced.** `content_tagging/rules.py` calls
  `rules.set_perm(...)` to point `openedx_tagging`'s permission names at edx-aware
  predicates. `can_change_object_tag` **delegates to `oel_tagging.can_change_object_tag`
  first**, then adds an org/context check. Object-level write predicates check Content
  Libraries v2 authz, Studio course access, or org-admin role.
- **Who administers taxonomies:** `oel_tagging.is_taxonomy_admin(user)` plus org-level
  staff/creator roles. The **"course creator" tie-in** (ADR 0004) is realized via
  `get_user_orgs()` unioning content-creator, course-staff, and library-user org roles
  (`rules.py` / `auth.py`).
- **Auto-tagging** is wired in `apps.py.ready()` → `handlers.py`: `@receiver`s on
  `openedx_events.content_authoring` signals, all gated by the `CONTENT_TAGGING_AUTO`
  toggle — `COURSE_CREATED`, `XBLOCK_CREATED/UPDATED/DELETED/DUPLICATED`,
  `LIBRARY_BLOCK_CREATED/UPDATED`. Each enqueues a Celery task (`update_course_tags`,
  `update_xblock_tags`, `update_library_block_tags`, …). **What actually gets auto-applied
  today is the Language tag** (`_set_initial_language_tag` maps the content's language via
  the system Language taxonomy's `tag_for_external_id`, falling back to
  `settings.LANGUAGE_CODE`). Org association is handled through taxonomy *scoping*
  (`TaxonomyOrg`), not by applying an org tag.
- **Key validation:** `content_tagging/types.py` defines
  `ContentKey = LibraryLocatorV2 | CourseKey | UsageKey | CollectionKey | ContainerKey`
  and `ContextKey = LibraryLocatorV2 | CourseKey`; rules parse `object_id` into a context
  key and assert `.org` for org checks.

> **Takeaway:** the standalone-vs-edx-specific split from ADRs 0001–0004 holds up in code.
> `openedx_tagging` stays generic; everything Open-edX-specific (orgs, course-creator
> perms, opaque-key validation, auto-tagging) lives in `content_tagging`, layered via
> thin API wrappers + `django-rules` rebinding + event receivers.

---

## Part B — Competencies / Competency Criteria (CBE)

> **STATUS: DESIGN-STAGE.** This is fully specified in
> [docs/openedx_learning/decisions/](docs/openedx_learning/decisions/) (ADRs 0001–0003)
> but **`src/openedx_learning/` does not yet exist**. Treat the model below as the agreed
> design, not as code on disk. Verify against the actual source before implementing.

### B.1 What CBE is

**Competency-Based Education**: track a learner's **mastery of competencies** via
**competency achievement criteria**. Example: *"To master the Multiplication competency,
score ≥ 75% on Assignment 1 OR Assignment 2."* The competency + threshold + the assessed
objects + the boolean logic together form the criteria. Authors set these up in Studio;
the system evaluates them as learners progress; dashboards show competency progress.
CBE is intended as a **core (kernel) feature**, not an optional plugin.

A **competency is just a `Tag`** in a taxonomy that has been **enabled for competency
features** (a `CompetencyTaxonomy`). This is the bridge between Part A and Part B — and
because it surprises most people, the next section spells out exactly what it implies.

### B.2 The competency itself: a Tag, not its own table

This is the part that most often trips people up. **There is no `Competency` model.** A
competency is **literally a row in `oel_tagging_tag`** (Part A's `Tag`) whose taxonomy is a
`CompetencyTaxonomy`. "Being a competency" is therefore **contextual**: the same generic
`Tag` model is reused, and the *only* thing that makes a given tag a competency is that its
taxonomy has been competency-enabled. The CBE applet introduces **no new identity** for the
competency — it simply **references the existing tag**.

Read the chain one hop at a time, noting what each row *means* (this mapping was confirmed
with the architect):

| Row | What it is | Plain-language meaning |
|---|---|---|
| `oel_tagging_tag` (in a `CompetencyTaxonomy`) | **the competency** | The thing to be mastered, e.g. *"Writing Poetry"*. Exists once; course-independent. |
| `oel_tagging_objecttag` (that tag applied to content) | a **competency-tag application** | *"This assignment / unit / course can be used to demonstrate this competency."* |
| `CompetencyCriterion` → one `oel_tagging_objecttag` + a rule | a **rule bound to one application** | *"To count this content toward the competency, evaluate it like this (e.g. Grade ≥ 75%)."* |
| `StudentCompetencyStatus` → (`oel_tagging_tag_id`, user) | the **per-learner result for the competency** | *"How competent is this student at this tag/competency?"* |

**The tag-vs-application distinction is what resolves the confusion.** Notice *which*
tagging row each CBE table points at — they are deliberately different:

- A **`CompetencyCriteriaGroup`** (and the `CompetencyAchievementCriteria` tree it roots)
  carries **`oel_tagging_tag_id`** → *the whole criteria tree is about one competency (the
  Tag).*
- A **`CompetencyCriterion`** (a leaf) carries **`oel_tagging_objecttag_id`** → *each leaf
  pins a rule to one specific content application of that competency.*
- **`StudentCompetencyStatus`** carries **`oel_tagging_tag_id`** again → *the result is
  reported at the competency (Tag) level, not per piece of content.*

```
Tag  ("Writing Poetry", in a CompetencyTaxonomy)  ────────────┐  (the competency)
  │                                                           │
  ├─ ObjectTag (Assignment 7 → Tag) ── CompetencyCriterion ───┤  CriteriaGroup tree
  └─ ObjectTag (Assignment 9 → Tag) ── CompetencyCriterion ───┘  is *about* this Tag
                                                              │
                       StudentCompetencyStatus(user, Tag)  =  result for the competency
```

**What this means for relationships & dependencies:**

- **The dependency points one way: CBE → tagging, never the reverse.** Every CBE table
  holds FKs *into* `oel_tagging_tag` / `oel_tagging_objecttag`; `openedx_tagging` knows
  nothing about competencies. This is the concrete reason CBE could not live inside
  `openedx_tagging` (see B.3): tagging must remain a generic, standalone library, so the
  arrow has to point from competencies toward tags.
- **A competency is reusable and course-independent.** Because the competency *is* the Tag,
  many criteria groups — across many courses and course runs — can reference the **same**
  competency Tag, while the rules that define *how to demonstrate it* are course-scoped via
  `CompetencyCriteriaGroup.course_id`. One competency ↔ many course-specific rule trees.
- **It explains the resilience and delete behavior.** Since the competency's identity lives
  in the Tag, the tag/taxonomy are treated as **non-evaluative display metadata** (and so
  are deliberately *not* versioned — see B.5), and once any `StudentCompetencyStatus` exists
  the Tag **cannot be hard-deleted** (delete protection — see B.4), precisely because
  learner results are keyed by it.

### B.3 Where it lives & why
[0001-competency-criteria-location.rst](docs/openedx_learning/decisions/0001-competency-criteria-location.rst)

Decision: **the top-level `openedx_learning` app, as a `cbe` applet**, alongside a
`learning_pathways` applet:

```
src/openedx_learning/applets/cbe              # competency criteria + learner status
src/openedx_learning/applets/learning_pathways
```

Why not elsewhere (rejected alternatives):
- **Not `openedx_tagging`** — that must stay a standalone, Open-edX-free library; CBE needs
  the user model, courses, LMS/Studio workflows.
- **Not the `openedx_content` authoring app** — learner-status models FK to
  `settings.AUTH_USER_MODEL` (a runtime/learner concern); putting them in authoring would
  force an authoring-only package to carry learner/runtime deps.
- **Not `edx-platform`** — the whole point is to extract core concepts out of the monolith.
- **Not a separate repo** — too much packaging/CI/migration overhead for a tightly-coupled
  core feature; applets give a clean split-later path.

### B.4 The data model
[0002-competency-criteria-model.rst](docs/openedx_learning/decisions/0002-competency-criteria-model.rst)
(diagram: `docs/openedx_learning/decisions/images/CompetencyCriteriaModel.png`)

**Authoring / definition side:**

- **`CompetencyTaxonomy`** — the set of competency-enabled taxonomies. Modeled via Django
  **multi-table inheritance** `CompetencyTaxonomy(Taxonomy)` (a `taxonomy_ptr_id` 1:1 to
  `oel_tagging_taxonomy`), **not** a `taxonomy_type` column — chosen to keep CBE concerns
  out of the generic tagging model and to allow strong FKs to competency-enabled taxonomies.
- **`CompetencyAchievementCriteria`** — *conceptual* full criteria expression for one
  competency = one **root `CompetencyCriteriaGroup`** + all descendant groups/leaves.
- **`CompetencyCriteriaGroup`** — an **internal node** of the boolean expression tree:
  - `parent_id` (null ⇒ root), `oel_tagging_tag_id` (the competency), nullable
    `course_id` (→ `openedx_catalog_courserun`), `name`,
  - `logic_operator` = `AND` / `OR` / null,
  - `ordering` = deterministic sibling evaluation order (enables short-circuit).
  - Frontend authoring depth cap = 3 (`0=root`, `1=course-scope group`, `2=leaf`); backend
    supports deeper. **Empty groups must be rejected** by backend validation.
- **`CompetencyCriterion`** (DB table **`CompetencyCriteria`**) — a **leaf**:
  - belongs to one `CompetencyCriteriaGroup`,
  - points to one **`oel_tagging_objecttag`** (the tag↔object association),
  - uses a `CompetencyRuleProfile` by default, with optional `rule_type_override` /
    `rule_payload_override`.
- **`CompetencyRuleProfile`** — a **reusable default evaluation rule**, scopable by
  taxonomy / course / organization:
  - `rule_type` ∈ `View` / `Grade` / `MasteryLevel` (**only `Grade` supported initially**),
  - `rule_payload` = **structured, validated JSON** keyed by `rule_type`
    (e.g. `Grade`: `{"op":"gte","value":75,"scale":"percent"}`). JSON (not fixed columns) so
    new rule types add fields without migrations.
  - Fallback lookup when a criterion has no profile: taxonomy-scoped → course-scoped →
    org-scoped → system default.

**Learner / runtime side** — status is **materialized at every level** to avoid recompute:

- **`CompetencyMasteryStatuses`** — system-owned, immutable lookup table. Seeded:
  `Demonstrated`, `AttemptedNotDemonstrated`, `PartiallyAttempted`.
- **`StudentCompetencyCriteriaStatus`** — status at the **leaf** (`CompetencyCriterion`)
  level, per user.
- **`StudentCompetencyCriteriaGroupStatus`** — status at the **group** node level, per user.
- **`StudentCompetencyStatus`** — **overall** competency state per user
  (`oel_tagging_tag_id` + user). Constrained to only `Demonstrated` / `PartiallyAttempted`.

**Evaluation = bottom-up materialization with ordered short-circuit:**
1. A learner completion/grade event updates one leaf `StudentCompetencyCriteriaStatus` row.
2. Recompute ancestor group statuses upward to the root.
3. At each group, evaluate children in `ordering` order; short-circuit once the
   `logic_operator` result is determined.
4. **Persist only rows whose status changed.**
Read/evaluation paths are **windowed by course-run dates** (include `course_id is null`
nodes + complete subtrees of course-scoped groups whose run overlaps the window) — never
partial subtrees.

**Delete protection:** once *any* learner status exists for a competency definition,
hard-deleting the related definition rows (taxonomy, tag, objecttag, groups, criteria, rule
profile) is **blocked**; retiring becomes archive-only. With no learner status yet, hard
delete cascades freely.

### B.5 Versioning & audit
[0003-competency-criteria-versioning.rst](docs/openedx_learning/decisions/0003-competency-criteria-versioning.rst)

Initial approach (deliberately **not** the full publishing framework):

1. Apply **`django-simple-history`** to the **definition** models: `CompetencyCriteriaGroup`,
   `CompetencyCriteria`, `CompetencyRuleProfile`.
2. **Do NOT** version `oel_tagging_tag`, `oel_tagging_taxonomy`, or `CompetencyTaxonomy` —
   for CBE they're treated as non-evaluative display metadata (renames don't change outcomes).
3. `oel_tagging_objecttag` associations used by criteria: editable/deletable **until** a
   related learner status exists; after that, **archive-only (soft delete)** so status rows
   remain traceable.
4. **Authoring guardrails:** editing criteria/associations after learner status exists must
   show an explicit Studio warning (changes apply **going forward**, existing statuses are
   **not** retroactively recomputed) and require confirmation.
5. **Learner status tables are append-only history** (no `django-simple-history`): each
   change is a new row stamped with `created`; **current status = most recent row** for a
   learner+target (order by `created`, tie-break `id`). Older rows are the audit trail.

Rejected: deferring versioning entirely; ad-hoc per-model version columns; the full
publishable framework (too heavy for this phase); a generic append-only event log.

### B.6 Worked example (from ADR 0002)

Competency **"Writing Poetry"**, Course **X**, assignments 7 & 9 (both tagged "Writing
Poetry" via `oel_tagging_objecttag`). Course-scoped `CompetencyRuleProfile` default =
`Grade ≥ 75%`. Criteria tree: root `OR` → Group A (`AND`) + Group B (`AND`). Leaves attach
assignments to groups; one leaf overrides to `Grade ≥ 85%`, the rest inherit the default.
This shows the design goal: **set one default rule, override only where needed**, and let
the same tag↔object association participate in multiple groups without duplicating tagging
rows.

---

## How the three layers connect

- A **Tag** is the vocabulary term; a **competency is a Tag** in a `CompetencyTaxonomy`.
- An **ObjectTag** attaches that tag to a content object (assignment, unit, course, …).
- **Competency criteria** point at those same `ObjectTag` rows and wrap them in
  AND/OR groups + evaluation rules, then track **per-learner status** at leaf, group, and
  overall levels.
- Tagging's **resilience** design (cached `export_id`/`value`) and CBE's **non-versioning of
  tags/taxonomies** are consistent: for evaluation, tag/taxonomy text is display metadata;
  the evaluative state lives in criteria definitions (versioned) and learner status rows
  (append-only).
