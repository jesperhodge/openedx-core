# openedx-core — Architecture Overview

> Orientation doc for future Claude agents working in this repo. It is synthesized
> from the Architecture Decision Records (ADRs) in this repo's `docs/*/decisions/`
> folders, the actual `src/` layout, and the `.importlinter` contract. Where a term
> only makes sense in the wider Open edX world, see the **Glossary** at the bottom.
>
> Companion doc: [Taxonomies, Tags & Competencies](.claude/architecture-taxonomies-and-competencies.md).

---

## 1. What this repo is

`openedx-core` is an **installable PyPI library of Django apps** that represent the
core teaching-&-learning concepts of the Open edX platform. It was formerly called
**"Learning Core" / `openedx-learning`** and was renamed (see
[0002-openedx-core.rst](docs/openedx_core/decisions/0002-openedx-core.rst)).

The library is installed *into* `edx-platform` (the giant Open edX monolith — see
Glossary). Its mission is to be a **small, extensible, easy-to-reason-about core**
that platform code and community plugins can build on, gradually extracting core
concepts out of the half-million-line `edx-platform` repo.

- **Replacing `edx-platform` is explicitly NOT a goal.** Only a small fraction of
  the platform's concepts belong here. `edx-platform` will `import` these apps and
  call their in-process Python APIs.
- Rationale and history: [0001-purpose-of-this-repo.rst](docs/openedx_core/decisions/0001-purpose-of-this-repo.rst).
- This is the "third leg" of in-process extension, alongside `openedx-events`
  (event notification) and `openedx-filters` (intercept/modify workflows): this repo
  provides **content querying / data models**.

## 2. Non-negotiable invariants

These show up across nearly every ADR and are enforced mechanically:

1. **Dependencies go ONE WAY.** `openedx-core` code must **never** import from
   `edx-platform`. The platform depends on us, not the reverse. This also means our
   migrations must never depend on platform migrations (we run in CI and local dev
   without the platform).
2. **`.importlinter` is law.** [.importlinter](.importlinter) defines the allowed
   layering between top-level apps and between `openedx_content`'s internal applets.
   Do **not** loosen these rules "to fix the build" — the file itself warns against
   that. Update it deliberately when adding a new app/applet.
3. **Stable vs UNSTABLE APIs.** Anything marked `UNSTABLE` can change anytime.
   Everything else is stable; breaking changes go through the community **DEPR**
   (deprecation & removal) process.
4. **Public APIs are the contract**, not internal modules. Consumers import from a
   package's public API surface, not its internals (see §5).

## 3. Top-level apps (the `src/` layout)

Each top-level package under `src/` is a self-contained concern. Keeping them as
**top-level packages** (not nested under one namespace) deliberately leaves the door
open to extract any of them into its own repo later without breaking
`from openedx_x import ...` statements.

| Package | Status | Purpose |
|---|---|---|
| `openedx_content` | **Implemented (most developed)** | Authoring/content models: the publishing/versioning framework, components, containers, media/assets, collections. |
| `openedx_tagging` | **Implemented** | Standalone tagging library (taxonomies, tags, object tags). No Open edX-specific deps. |
| `openedx_catalog` | **Implemented (newer)** | `CourseRun`, `CatalogCourse`, and core course metadata. |
| `openedx_learning` | **PLANNED — not yet in `src/`** | Learner-facing models. Intended home for CBE/competency criteria and Learning Pathways (applets). |
| `openedx_django_lib` | **Implemented** | Shared Django utilities: identifier field helpers (`fields`, `id_fields`), managers, validators, collations, admin utils. Not a "real" app. |
| `openedx_core` | **Implemented (shell)** | Empty package that only exposes `__version__`. Depends on nothing. |

> **Watch out:** the CBE/competency work and the `openedx_learning` app are currently
> **design-stage (ADRs only)** — `src/openedx_learning/` does not yet exist. Don't
> assume those models are in the codebase. See the companion doc.

### Top-level layering (`.importlinter`)

`root_packages` covered by the linter: `openedx_content`, `openedx_tagging`,
`openedx_django_lib`, `openedx_core`. Layering (higher may import lower, never the
reverse):

```
openedx_content        # highest level today
  ↓
openedx_tagging        # simple & fundamental; should not depend on other real apps
  ↓
openedx_django_lib     # Django utilities; no dependency on the real apps above
  ↓
openedx_core           # empty version shell; depends on nothing
```

`openedx_catalog` is not yet listed in `.importlinter` `root_packages` (it is newer).
Expect the contract to grow as apps mature.

## 4. The "Applets" pattern (inside `openedx_content`)

Historically Learning Core used many tiny Django apps (`components`, `collections`,
`publishing`, …). That made individual apps simple but made **refactors, renames, and
moving models across apps painful** (Django `SeparateDatabaseAndState`, migration
ordering, long `INSTALLED_APPS`). So those apps were merged into a **single Django
app, `openedx_content`**, with internal boundaries preserved via **"Applets."**
Full reasoning + the tricky migration choreography:
[0010-merge-authoring-apps-into-openedx-content.rst](docs/openedx_content/decisions/0010-merge-authoring-apps-into-openedx-content.rst).

- A Django **app = a DDD Subdomain**; an **applet = a DDD Bounded Context**. (Called
  "applet" to avoid colliding with Django Contexts / Python context managers.)
- An applet looks like a mini-app: its own `models.py`, `api.py`, etc., living under
  `src/openedx_content/applets/<name>/`.
- **Applets must respect each other's API boundaries** — never query another applet's
  models directly. `.importlinter`'s `content_applet_layering` contract enforces it.
- `backcompat/` holds skeleton apps + their old migrations purely so the schema can be
  migrated smoothly from the old per-app tables to the merged `openedx_content` tables.

**Applets present in `src/openedx_content/applets/`:** `publishing`, `components`,
`containers`, `media`, `collections`, `sections`, `subsections`, `units`,
`backup_restore`.

**Applet layering** (from `.importlinter`; top imports down, `publishing` is the base):

```
openedx_content.api                              # public API; internal applets never call up to it
  ↓
backup_restore                                   # new export/import mechanism
  ↓
components | containers                          # peers — do NOT depend on each other
  ↓
media                                            # simplest binary/text data, unversioned, per Learning Package
  ↓
collections                                      # arbitrary groupings of PublishableEntities
  ↓
publishing                                       # base: Learning Packages, draft/publish primitives
```

> Note: `sections`, `subsections`, and `units` applets exist on disk but are not yet
> all enumerated in the layering contract — they build on `containers`. Add new applets
> to `.importlinter` when you create them.

## 5. API & module conventions

Follows OEP-49 (per-app `api.py`) **plus** a consumer-friendly public layer. Original
design: [0006-python-public-api-conventions.rst](docs/openedx_content/decisions/0006-python-public-api-conventions.rst)
(superseded in specifics by 0010, but the principles hold):

- Each app/applet has its own `api.py` and (ideally) `models_api.py`; optionally a
  `rest_api/` package.
- Apps declare their public functions via `__all__`.
- A **top-level public API package** aggregates them with wildcard imports (e.g.
  `openedx_content/api.py` does `from .applets.publishing.api import *`, etc.).
- **Within** a package, applets import directly from each other's `api` (no wildcards).
  **Across** packages, import only from the other package's *public* API — never reach
  into its internals.
- `import_linter` enforces all of this.
- Identifier helpers live in `openedx_django_lib.fields` /
  [src/openedx_django_lib/id_fields.py](src/openedx_django_lib/id_fields.py).

## 6. Core data-model concepts

### 6.1 Identifier conventions

Every content model uses these consistently (README "Model Conventions" +
[0003-identifier-conventions.rst](docs/openedx_content/decisions/0003-identifier-conventions.rst);
likely superseded by OEP-68):

- **`id`** — auto `BigAutoField` primary key, internal, never changes. **Make foreign
  keys to this** (Django convention). We avoid `_id`-suffixed names because Django uses
  that for FK columns.
- **`uuid`** — random UUID4, globally unique, immutable. **Use this to reference a row
  from another service.** (Kept separate from PK because UUID PKs are slow in MySQL.)
- **`key`** — human-meaningful, only locally unique (e.g. within a `LearningPackage`).
  May change; consumers must not assume stability. Stored in column `_key` (`key` is a
  reserved word in some DBs). Looser cousin of opaque keys (see Glossary).
- **`num`** — like `key`, but strictly numeric.

### 6.2 The Publishing framework (the base layer)

The `publishing` applet ([src/openedx_content/applets/publishing/](src/openedx_content/applets/publishing/))
is a **content-agnostic spine** for drafts, versions, and publishing. It knows nothing
about Components or Units; consumer apps attach their own content models via 1:1 mixins
(`PublishableEntityMixin` / `PublishableEntityVersionMixin`, registered through
`register_publishable_models`). Verified against the source:

Core models (`models/`):

- **`LearningPackage`** — top-level namespace for a body of authored content (a v2
  Content Library today; eventually a course).
- **`PublishableEntity`** — the *stable identity* of any publishable thing that ever
  existed (survives unpublish/soft-delete). FK to `LearningPackage`.
- **`PublishableEntityVersion`** — an **immutable** version, with a monotonically
  increasing `version_num` (≥1, unique per entity). "Latest" = highest `version_num`;
  versions are append-only and gaps are normal. A version may declare `dependencies`
  (M2M via `PublishableEntityVersionDependency`) — e.g. a container referencing unpinned
  children.
- **`Draft`** — a pointer (1:1 with entity) to the *active draft* version;
  `version = NULL` ⇒ soft-deleted.
- **`Published`** — a pointer (1:1 with entity) to the *currently published* version;
  `version = NULL` ⇒ published-then-deleted.
- **`DraftChangeLog` / `DraftChangeLogRecord` / `DraftSideEffect`** and
  **`PublishLog` / `PublishLogRecord` / `PublishSideEffect`** — commit-like logs: one log
  row per draft-change or publish operation, with per-entity records
  (`old_version`→`new_version`) and one-level cause→effect side-effect tracking.

Mechanics:

- **Draft and Published are deliberately separate pointer tables.** Editing creates a new
  version and moves `Draft.version`; **publishing copies the draft's version pointer into
  `Published.version`** and writes a `PublishLog`. `has_unpublished_changes` ≡
  `draft.version_id != published.version_id`.
- **Soft-delete** (`soft_delete_draft`) nulls a pointer; it deletes no rows.
  **Discard/revert** = `reset_drafts_to_published`.
- **Pruning** is described in the container ADRs as a design concept (removing unused,
  unpublished, non-latest versions) but is **NOT implemented in the publishing applet
  today** — there is no `prune` function; rows only disappear via `LearningPackage`
  CASCADE deletion. Treat ADR pruning language as intent, not current behavior.
- Signals (`OpenEdxPublicSignal`): `LEARNING_PACKAGE_CREATED/UPDATED/DELETED`,
  `ENTITIES_DRAFT_CHANGED` (on draft commit), `ENTITIES_PUBLISHED` (on publish).

Key public API (`publishing/api.py`, surfaced via `openedx_content.api`):
`create_learning_package`, `create_publishable_entity` /
`create_publishable_entity_version`, `set_draft_version`, `bulk_draft_changes_for`
(context manager batching edits), `publish_all_drafts` / `publish_from_drafts`,
`soft_delete_draft`, `reset_drafts_to_published`, the draft/publish history getters, and
`register_publishable_models`.

### 6.3 Extensibility through model relations

Key principle from [0002-content-extensibility.rst](docs/openedx_content/decisions/0002-content-extensibility.rst):

- There is a **core data model that is always introspectable and exportable without
  running any plugin code.** (Old XBlock pain: uninstalling a block could make courses
  un-exportable.)
- Plugins **progressively enhance** core models by adding their own related models,
  typically via `OneToOneField` (`primary_key=True`). They do **not** subclass concrete
  core models, and they do not store the canonical data only inside their own tables.
- This decouples plugins from each other and from the core; abstract helpers are exposed
  via `models_api.py`.

### 6.4 Content hierarchy: Containers, Components, Selectors

The flexible content vision ([0001-content-flexibility.rst](docs/openedx_content/decisions/0001-content-flexibility.rst))
replaces the rigid `Course > Section > Subsection > Unit > XBlock` hierarchy with
**composable primitives**:

- **Component** — a small leaf piece of content (video, problem, HTML). Maps to a
  single "leaf" XBlock. No children. (`components` applet.)
- **Container** — a *generalized* capability for one PublishableEntity to hold others
  in an ordered parent-child list. Sections, Subsections, Units are all container types.
  Design: [0007-generalized-containers.rst](docs/openedx_content/decisions/0007-generalized-containers.rst).
  Key rules:
  - Containers can nest; **content-type restrictions are enforced at the app layer, not
    the model layer** (e.g. "Units may only hold Components" is an app rule).
  - A container version's **entity list is fixed** for that version. Editing a child
    does **not** create a new parent version; **adding/removing/reordering** children or
    changing container metadata **does**.
  - Children can be **pinned** to a specific version or **unpinned** (`version = None`
    ⇒ always latest). A child can be shared across multiple containers.
  - Publishing a container publishes its draft children; children can also be published
    independently.
- **Unit** — the first concrete container type; holds Components.
  [0008-units-as-containers.rst](docs/openedx_content/decisions/0008-units-as-containers.rst).
- **Selectors / Variants** — *PROPOSED, not implemented.* A mechanism for **dynamically**
  choosing 0-N entities from a pool (A/B tests, "random 3 of 20 per student").
  [0009-selectors.rst](docs/openedx_content/decisions/0009-selectors.rst).

### 6.5 Media & static assets

- The `media` applet stores raw binary/text content (deduplicated by hash) per
  Learning Package, unversioned at the data level; assets are versioned via the
  Components that reference them.
- Serving authored static assets to browsers is a non-trivial design (separate domain
  for XSS isolation, `X-Accel-Redirect` via Caddy/Nginx instead of streaming through
  Django, per-component permission hooks extended by the platform, cookie auth flow).
  Full design: [0005-serving-static-assets.rst](docs/openedx_content/decisions/0005-serving-static-assets.rst).

## 7. The Catalog app

`openedx_catalog` is `CourseRun`-centric (`CourseRun`, `CatalogCourse`, plus core
metadata like `CourseSchedule`). See [src/openedx_catalog/ARCHITECTURE.md](src/openedx_catalog/ARCHITECTURE.md)
for a Mermaid diagram of its relationships. Notable points:

- References `edx-organizations` (Organization).
- Platform `enrollments`/`course_modes` reference `CourseRun`.
- A future learner-oriented, pluggable **discovery service** and `openedx_pathways` are
  expected to reference it.
- The competency model (companion doc) FKs `course_id` to
  `openedx_catalog_courserun`.

## 8. How edx-platform consumes openedx-core (integration map)

This repo is a library; the wiring lives in `edx-platform`. Two integration points have
been verified against platform code:

### 8.1 v2 Content Libraries → `openedx_content`

`edx-platform`'s `openedx/core/djangoapps/content_libraries` app treats `openedx_content`
(Learning Core) as the **single source of truth for content**. The Studio-side
`ContentLibrary` model holds only instance-local settings (org, slug, permissions,
license) and links to the content via a **`OneToOneField` to `LearningPackage`**
(`on_delete=RESTRICT`, nullable).

- It imports just three surfaces: `from openedx_content import api as content_api`,
  `from openedx_content.models_api import ...` (`LearningPackage`, `Component`,
  `ComponentVersion`, `Container`, `Collection`, …), and
  `from openedx_content.api import create_zip_file` (backup/export). There is **no
  separate `authoring`/`publishing` namespace** — `openedx_content.api` is one unified
  facade for both authoring (create/version) and publishing (publish/draft).
- A **library block ≈ a `Component`** (backed by a `PublishableEntity`). Created via
  `create_component_and_version` / `get_or_create_component_type`, edited via
  `create_next_component_version`; OLX stored as text media (`get_or_create_text_media`),
  static assets via the media helpers.
- **Containers/units** use `create_container_and_version` /
  `create_next_container_version`.
- **Publish/draft** go straight through the publishing API: library-wide
  `publish_all_drafts`; per-block/container `publish_from_drafts(draft_qset=…)`;
  `set_draft_version`, `soft_delete_draft`, `reset_drafts_to_published`; edits batched in
  `bulk_draft_changes_for`.
- **Import/export** is delegated entirely to Learning Core's zip mechanism
  (`create_zip_file(package_ref=…)`), consistent with the `backup_restore` applet.

This is the concrete realization of "v2 Content Libraries were the pilot use case" — they
exercise `LearningPackage` / `PublishableEntity` / Components / Containers end to end.
(A migration named `0012_switch_to_openedx_content` in that app marks the repoint from the
old `oel_*`/blockstore models to `openedx_content`.)

### 8.2 Tagging → `openedx_tagging`

`edx-platform`'s `openedx/core/djangoapps/content_tagging` is a thin layer adding
org-scoping, edx-aware permissions, and auto-tagging on top of the standalone library. The
headline: it **reuses (does not subclass)** `Taxonomy`/`ObjectTag`, adds a `TaxonomyOrg`
join model for org-scoping, re-binds `openedx_tagging`'s `django-rules` permissions to
org/object-aware predicates, and runs event-driven Celery auto-tagging that currently
applies only the **Language** tag. Full detail in the companion doc →
[Taxonomies, Tags & Competencies, §A.6](.claude/architecture-taxonomies-and-competencies.md).

## 9. Where to read things (ADR map)

| Folder | App | What's in it |
|---|---|---|
| [docs/openedx_core/decisions/](docs/openedx_core/decisions/) | repo-wide | Purpose of the repo; the rename to openedx-core. |
| [docs/openedx_content/decisions/](docs/openedx_content/decisions/) | `openedx_content` | Content flexibility, extensibility, identifiers, app-label prefix (obsolete), static assets, public API conventions, generalized containers, units, selectors, the applet merge. |
| [docs/openedx_tagging/decisions/](docs/openedx_tagging/decisions/) | `openedx_tagging` | Tagging design (see companion doc). |
| [docs/openedx_learning/decisions/](docs/openedx_learning/decisions/) | `openedx_learning` (planned) | CBE competency criteria: location, model, versioning (see companion doc). |

Each folder's `index.rst` just globs the ADRs. Several ADRs carry a **Status** line
(Proposed / Accepted / Superseded / Obsolete / Rejected) and a 2026-04-02 changelog —
**always check Status before trusting an ADR as current.** Known status notes:
- `0004-app-label-prefix` (content): **Obsolete** (the `oel_` prefix is gone, replaced
  by `openedx_content` labels — though older tagging tables still use `oel_tagging_*`).
- `0006-python-public-api-conventions` (content): **Superseded** by 0010.
- `0001-content-flexibility`, `0009-selectors`: **Proposed** (Sequences/Navigation/
  Selectors not realized).

---

## 10. Glossary — wider Open edX context

> These terms come from `edx-platform` and the broader platform; they are *not* defined
> in this repo's code. Summaries below were gathered from `edx-platform`'s own ADRs.

- **edx-platform** — the original Open edX monolith (~500k lines of Python). Contains
  two Django projects: **CMS/Studio** and **LMS**, usually deployed as separate services.
- **Studio (a.k.a. CMS)** — the **authoring** application: create, version, and publish
  course content. "CMS" is the web service; "Studio" is the product it powers (other
  tools also use the CMS service's APIs).
- **LMS** — the **learner- and instructor-facing** application where students consume
  courses.
- **XBlock** — Open edX's **plugin framework for course content components**. Each
  XBlock bundles its own rendering, state, and behavior. The current direction
  (edx-platform ADR "role of XBlock") **narrows XBlocks to leaf content** (a `Video`, a
  `Problem`, an `HTML` block) and to the Unit level, moving structural/navigational
  levels (Sequence, Section, Course) to dedicated, XBlock-agnostic apps. In this repo, a
  **Component ≈ a single leaf XBlock**.
- **XBlock runtime** — the host environment (in LMS/CMS) that executes XBlock code,
  mediating field storage, services, and rendering. Being constrained over time so an
  XBlock only sees its own Unit/siblings.
- **Modulestore** — `edx-platform`'s legacy course-content store, historically backed by
  **MongoDB**, storing content as a graph of XBlocks in opaque key/value documents. Known
  pain points: opaque DB-layer storage (hard to join/query), non-atomic publish, plugin
  coupling. The "Split Modulestore" is being migrated off MongoDB. This repo's
  publishing/versioning models are the modern replacement direction.
- **Opaque keys** — structured identifier *objects* you interact with via an API rather
  than parsing strings. **`CourseKey`** identifies a course (e.g.
  `course-v1:Org+Course+Run`); **`UsageKey`** identifies a specific XBlock instance in a
  course. "Opaque" because the string format can change without breaking callers. This
  repo's `key` field is a looser, locally-scoped cousin (it deliberately does *not* bake
  in the course key, to keep keys re-homeable).
- **Content Libraries** — shared, reusable pools of content blocks authors draw on across
  courses. **v1 (legacy)** live in Modulestore, limited block types, fragile course-sync.
  **v2** were rebuilt on the new authoring/publishing models — first on Blockstore, then on
  **this repo's `openedx_content`**. **v2 Content Libraries were the pilot use case** that
  proved out Learning Core's `LearningPackage` / `PublishableEntity` abstractions before
  extending them to courses.
- **CBE** — Competency-Based Education (see companion doc).
- **OEPs** — Open edX Proposals (architecture standards). Referenced here: OEP-49 (Django
  app patterns / API modules), OEP-57 (core product "kernel" features), OEP-9
  (permissions), OEP-68 (identifier conventions).
- **openedx-events / openedx-filters** — separate libraries for in-process event
  notification and workflow interception, respectively; the auto-tagging and (future) CBE
  recompute flows hang off platform events.
