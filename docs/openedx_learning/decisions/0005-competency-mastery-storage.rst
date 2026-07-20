.. _openedx-learning-adr-0005:

5. How should learner competency mastery be stored at scale?
=============================================================

Status
------
Proposed.

Relationship to prior decisions
--------------------------------
This decision refines two prior ADRs and reverses an earlier draft of this one:

- It refines :ref:`openedx-learning-adr-0002`'s learner-status model. ADR 0002 materializes
  (stores) mastery at all three levels: the criterion (leaf), the criteria group, and the
  competency. This decision keeps all three stored, but changes *how*: each level is split into an
  ACTIVE table (one current row per learner and node, updated in place) and an append-only HISTORY
  table, and the large per-learner leaf tables adopt the large-table techniques edx-platform uses
  for persistent grades.
- It supersedes :ref:`openedx-learning-adr-0003`'s rule that the learner-status tables are
  append-only with the current status being the most recent row. Those tables move to an ACTIVE
  table updated in place plus an append-only HISTORY table.
- It refines :ref:`openedx-learning-adr-0003`'s guardrail that "edits apply going forward; existing
  statuses are not retroactively updated," to the monotonic rule described under "Retroactive
  criteria changes are monotonic for the learner" below.
- It reverses an earlier draft of this ADR that computed leaves transiently and never stored them.
  That design is retained as the first rejected alternative below.

A companion set of diagrams for this decision lives alongside it in
``0005-competency-mastery-storage-diagrams.md``.

Context
-------
Per :ref:`openedx-learning-adr-0002`, competency achievement criteria form a boolean tree. An
internal ``CompetencyCriteriaGroup`` node combines child nodes with an ``AND``/``OR``
``logic_operator``, can be scoped to a course run, and can nest under a parent group. A
``CompetencyCriterion`` leaf is the tree's terminal node: it points at one tag/object association
and a rule (``CompetencyRuleProfile``; only the ``Grade`` rule type is in scope today), and is a
pure function of one subsection grade and that rule. Mastery status is one of ``Demonstrated``,
``PartiallyAttempted``, or ``AttemptedNotDemonstrated``; an absent row means the learner has not
started. A leaf is atomic, so it is only ever ``Demonstrated`` or ``AttemptedNotDemonstrated``;
``PartiallyAttempted`` is a group-level state.

The learner-facing and instructor-facing progress dashboards need per-assignment detail: for a
given competency they show which individual criteria a learner has and has not demonstrated, not
just the rolled-up group and competency verdicts. That per-criterion view is the primary driver for
storing leaf mastery rather than deriving it on the fly. A durable, append-only record of each
leaf's transitions over time is a secondary benefit, used for audit and tracing.

Storing every leaf multiplies out at scale. A course can carry on the order of 200 leaf criteria,
so the leaf level is where the row count concentrates: the ACTIVE leaf table (learners x attempted
leaves) reaches the low billions for an Open edX instance with millions of learners. The dominant
multiplier is this per-leaf breadth (roughly 200x per course), not time. Mastery is monotonic (see
"Advance-only banking" below): a node can only advance through the small status lattice
``AttemptedNotDemonstrated`` to ``PartiallyAttempted`` to ``Demonstrated``, at most a handful of
forward steps ever. The append-only HISTORY table records only those genuine advances, so it holds a
small constant number of rows per learner and node, bounded by monotonicity rather than growing
without limit as grades are corrected or re-evaluated over time. HISTORY is therefore about the same
order of size as ACTIVE, not a multiple that grows with a learner's history. This per-leaf scale is
what this decision has to make workable.

That scale is not, on its own, what makes a relational database struggle. A point lookup against a
billion-row table backed by the right composite index is a logarithmic-time index seek regardless
of the table's size; the dashboard reads this feature performs are exactly such point lookups. What
billions of rows makes painful is schema migrations, backups, and any non-indexed or aggregate
query. The design's job is therefore to keep rows narrow, keep every read on an index, keep the
append-only history off the hot path, and push cross-learner analytics to an analytics store rather
than the relational table.

openedx-core also cannot read edx-platform's grade tables directly and receives grade changes only
as ``openedx-events`` events (:ref:`openedx-learning-adr-0004`). Leaf mastery is therefore recorded
from the grade carried by the event when it arrives; the relational tables here are the record of
that computation, not a cache that can be rebuilt by re-reading a grade later.

Decision
--------
**Store mastery at every level, each split into ACTIVE and HISTORY.** The leaf, group, and
competency levels each keep one ACTIVE row per learner and node, updated in place, so reading a
learner's current status is a direct indexed lookup rather than a scan for the most recent of many
rows. Each level also has a parallel append-only HISTORY table that records one row per genuine
status advance, for audit and point-in-time reconstruction. Because status only advances, the status
at any past time is the latest recorded advance at or before that time, so point-in-time is fully
reconstructable from HISTORY. Because status is monotonic, the number of advances per node is bounded
by the status lattice (a small constant), so HISTORY grows with learners and nodes, not with time,
and stays about the same order of size as ACTIVE. Keeping ACTIVE and HISTORY separate still pays:
ACTIVE is a single in-place current row optimized for the dashboard point lookup and is the row
per-learner concurrency is anchored on (:ref:`openedx-learning-adr-0004`), while HISTORY is
append-only and read only for audit.

**Leaf mastery is stored per learner and criterion.** The leaf ACTIVE row keys on
``(user_id, competency_criteria_id)`` and carries the derived status plus an effective source
timestamp (the timestamp of the grade change it was computed from; see
:ref:`openedx-learning-adr-0004`). Dashboards read per-criterion status straight from this table.
The leaf HISTORY row is the append-only ledger of those changes. A leaf stores only the derived
status, not the grade value: openedx-core does not persist grades (see the non-goals), so the leaf
is a record of *what was demonstrated*, not a re-evaluable copy of the input.

**Group and competency roll-ups read stored leaf rows.** Because leaves are now stored, a group
re-evaluates its ``logic_operator`` by reading its child leaf (and child group) ACTIVE rows
directly. The prior draft carried a per-learner "attained-set" on each group row precisely because
leaves were not stored and siblings could not be read; that mechanism is removed. Roll-up is
incremental: when a grade event changes a leaf, the recorder re-evaluates the parent group, and if
the group's status changes, its parent in turn, up to the competency root, writing only the rows
whose status actually changed. Per-group fan-out is small, so reading a group's children is a cheap
indexed range read.

**Status semantics.** ``Demonstrated`` means the node's logic is satisfied. ``PartiallyAttempted``
means at least one child is demonstrated but the group's logic is not yet satisfied; it is
meaningful for ``AND`` groups and mixed trees, never for a leaf and never for an ``OR`` group (one
attained child already satisfies an ``OR``). ``AttemptedNotDemonstrated`` means the node has seen at
least one relevant grade event but nothing is yet demonstrated. An absent row means not started. At
the competency level only ``Demonstrated`` and ``PartiallyAttempted`` are surfaced, consistent with
:ref:`openedx-learning-adr-0002`: a ``Demonstrated`` root surfaces as ``Demonstrated``, a
``PartiallyAttempted`` root as ``PartiallyAttempted``, and a root that has only seen unsuccessful
attempts produces no competency-level row at all.

**The large leaf tables adopt edx-platform's persistent-grade playbook.** The leaf ACTIVE table has
the same shape and cardinality as edx-platform's ``PersistentSubsectionGrade`` (one row per learner
per graded subsection), which runs at production scale in the main database with no separate
database, no partitioning, and no sharding. This decision mirrors the techniques that make that
work, and consciously declines the ones edx-platform did not need:

    - *64-bit primary keys from the start.* The leaf ACTIVE and HISTORY tables use a 64-bit
      ``BigAutoField`` primary key, chosen up front, mirroring edx-platform's
      ``UnsignedBigIntAutoField`` on ``PersistentSubsectionGrade`` ("primary key will need to be
      large for this table"). Changing a primary-key type on a billion-row table later is
      prohibitively expensive.
    - *Narrow rows; content-hash dedup for any repeated blob.* edx-platform keeps grade rows narrow
      by storing only a fixed-width hash of the visible-block set on the hot row and holding the
      large JSON once in a ``VisibleBlocks`` dedup table (canonical JSON, SHA-1, base64,
      unique-constrained, referenced by hash rather than a surrogate id). Our leaf rows are already
      narrow (user, criterion, status, timestamps), so there is no per-row blob to deduplicate
      today. The rule adopted here is that if a large, mostly-repeated payload is ever added to
      these rows, it must be stored once behind a content hash rather than copied per row.
    - *Composite indexes derived from documented read paths.* Indexes are added per named read path,
      not by guessing: ``(user_id, competency_criteria_id)`` unique for the recorder's point read
      and write; ``(user_id, ...)`` for the learner dashboard; a course-scoped index for
      instructor and reporting reads. New read paths add exactly the index they need.
    - *In-place current row; history only where audit needs it.* The ACTIVE row is written with an
      upsert (``update_or_create`` / bulk upsert). edx-platform keeps no history table on its hot
      grade tables and pays for history only on the low-volume override table; this decision
      deliberately does keep a leaf HISTORY table, because leaf-transition traceability is an
      explicit (if secondary) goal, and isolates it from the ACTIVE path so the hot reads and
      writes never touch it. The cost of that choice is called out under accepted tradeoffs.
    - *Idempotency in domain logic, not a generic version column.* Correctness under duplicate and
      out-of-order delivery comes from two domain rules, not an optimistic-lock column: the
      monotonic banking below (a status is set once and only advances, mirroring edx-platform's
      write-once ``first_attempted`` and its "only persist a rescore if it is higher") and the
      effective-source-timestamp out-of-order defense in :ref:`openedx-learning-adr-0004`
      (mirroring edx-platform's check of the source score's timestamp before trusting a queued
      recompute).
    - *Batch the read and write paths, where batching is used.* When a deployment adopts the
      optional batching described in :ref:`openedx-learning-adr-0004`, the recorder reads current
      statuses in bulk and writes leaf ACTIVE (bulk upsert), leaf HISTORY (``bulk_create``), and the
      rolled-up group/competency rows in a small, fixed number of round-trips per batch, independent
      of the number of rows in the batch. This is the same "prefetch per cohort, bulk-write" posture
      edx-platform uses. Recording is correct without batching too (:ref:`openedx-learning-adr-0004`'s
      monotone merge does not depend on it); batching is a throughput optimization for high-volume
      deployments, not a correctness mechanism.
    - *Chunk mass recompute and provide a kill switch.* The bulk recompute triggered by a
      structural criteria edit is chunked by a configurable batch size and can be halted by an
      operational switch, mirroring edx-platform's ``ComputeGradesSetting`` batch size and its
      ``DISABLE_REGRADE_ON_POLICY_CHANGE`` switch, so a large edit cannot become an unbounded
      recompute storm.
    - *A dedicated database alias and router, baked in from the start.* The learner-status tables
      (leaf, group, and competency ACTIVE and HISTORY) are assigned to a dedicated Django database
      alias through a database router, mirroring edx-platform's courseware-history router
      (``StudentModuleHistoryExtended``). The alias defaults to the main database, so a stock
      deployment runs everything on one database and gains no new mandatory infrastructure, keeping
      faith with :ref:`openedx-learning-adr-0004`'s no-new-mandatory-infrastructure value; a large
      deployment points the alias at a separate physical database purely through settings, with no
      schema change and no data migration. Building the router now, rather than deferring it, is what
      makes that later split a configuration change instead of a migration on a billion-row table.
      Foreign keys that would cross the alias boundary (a learner-status row referencing a
      criteria-definition row, and the reference to the user) are declared without database-level
      constraints, as edx-platform does for ``PersistentSubsectionGrade.user_id``, so the tables can
      live in different databases; the delete protection of :ref:`openedx-learning-adr-0002` is
      therefore enforced in application code, not by a database constraint.
    - *No table partitioning, sharding, or read-replica routing.* These remain consciously rejected:
      the edx-platform grades app operates at scale without any of them, and narrow rows plus
      indexes plus dedup were sufficient. They can be revisited if a specific need is proven.

**Advance-only banking, monotonic.** Once a node reaches ``Demonstrated`` its ACTIVE row is retained
("banked"): the recorder never automatically regresses it, not on a later downward grade correction
and not on a criteria change. This applies at every level, including the leaf. A genuine downward
grade correction does not advance the status, so it writes no HISTORY row and leaves the banked
ACTIVE status unchanged; because HISTORY records only advances, it never carries suppressed
regressions. Reversing a banked status is a separate administrative action, out of scope here.
This monotonicity is what makes out-of-order and duplicate delivery safe, since a late or replayed
event can never lower a status, and :ref:`openedx-learning-adr-0004` relies on it.

**Retroactive criteria changes are monotonic for the learner.** A retroactive edit can newly grant
or preserve mastery, but it never silently revokes it, and it never rewrites a learner's recorded
leaf mastery downward, consistent with :ref:`openedx-learning-adr-0003`'s rule that edits apply
going forward. For STRUCTURAL edits (a leaf added to or removed from a group, an ``AND``/``OR``
flip, or retagged content), a chunked bulk recompute runs when the edit is published: for each
affected learner it re-evaluates the new group logic against the learner's stored leaf rows and
applies only upward transitions. This recompute is self-contained: it reads the learner's stored
leaf statuses, not the grades, which is why a learner who now meets eased criteria becomes
``Demonstrated`` with no further activity required, while a tightened edit leaves already-banked
learners untouched. A RULE or threshold change (for example, raising a passing bar) cannot be
recomputed from stored state, because whether a leaf was attained depends on the grade, which this
decision deliberately does not store; such a change takes effect only through a re-emission of the
affected grades via the normal recording pipeline, or lazily, the next time the learner produces a
relevant grade event.

**Non-goals (deferred/out of scope).** openedx-core does not store grades, so re-evaluating leaves
under a changed rule or threshold from stored state is not supported (see above). Administrative
revocation or correction of banked mastery (errata) is deferred to future work; the recorder here
is strictly advance-only. Cross-learner analytics and reporting are served by an analytics store
(ClickHouse/Aspects), not by aggregate queries over these relational tables. Because HISTORY records
only advances, its growth is already bounded by monotonicity (learners x nodes x a small constant),
so no retention or tiering policy is required to keep it workable; one may still be added later, and
the database router above lets the HISTORY tables be relocated to a separate database if a deployment
prefers. Emitting a status-change event for downstream notification is also out of scope here.

Accepted tradeoffs
------------------

    - The leaf tables are large by design (billions of rows at the top end), so schema migrations,
      backups, and any non-indexed query on them are expensive. This is the same posture
      edx-platform accepts for persistent grades; it is mitigated by the 64-bit primary key, narrow
      rows, and index-only read paths, but not eliminated.
    - Keeping a leaf HISTORY table is a deliberate departure from edx-platform, which keeps no
      history on its hot grade tables. Because it records only status advances, it is bounded by
      monotonicity to about the same order of size as the ACTIVE leaf table, rather than an unbounded
      ledger that grows as grades are re-evaluated. It is justified by the audit and point-in-time
      goal, isolated from the hot path, and the dedicated database alias and router above let it be
      split onto its own database when a deployment needs to.
    - Growth is managed structurally (narrow rows, big keys, indexes) and bounded by monotonicity
      (HISTORY records only advances), not by retention or deletion. A retention or tiering policy is
      therefore not required to keep the tables workable, and is left as optional future work.
    - This decision re-incurs the per-leaf breadth multiplier that the earlier transient-leaf draft
      avoided. That cost is accepted in exchange for the per-assignment dashboard detail that
      motivates it.

Rejected Alternatives
---------------------

1. Compute leaves transiently, never store them (the earlier draft of this ADR).

    Leaves are derived on the fly from the grade and the rule and never persisted; only group and
    competency mastery is stored, in ACTIVE and HISTORY tables, with a per-learner "attained-set" on
    each group row so an ``AND`` group can be evaluated without re-reading grades.

    - Pros:
        - Removes the ~200x per-leaf breadth multiplier from stored data entirely; the hot store is
          hundreds of millions of rows rather than billions.
        - No large-table machinery, no separate-database question, cheaper migrations and backups.
        - Lower operational burden on small and medium deployers.
    - Cons:
        - No stored per-criterion status, so the per-assignment dashboard detail that drives this
          decision would have to be recomputed on every read, or approximated from group-level
          state, rather than read directly.
        - No leaf-level history for audit or tracing.
        - The attained-set is a bespoke mechanism that exists only because leaves are not stored, and
          it must be kept correct under retroactive edits and out-of-order delivery.
    - Why rejected: the primary requirement is a live per-criterion dashboard view, which is a direct
      read of stored leaf status. Deriving it transiently either moves that cost onto every read or
      gives up the detail. Storing leaves also lets group roll-up read sibling rows directly and
      removes the attained-set entirely. The storage cost is real but is the same cost edx-platform
      already carries for persistent grades, and it is what the feature is for.

2. Keep everything append-only (no ACTIVE table); current status is the latest row.

    - Pros:
        - No in-place mutation; uniform with the original :ref:`openedx-learning-adr-0003` model.
    - Cons:
        - Current-status reads become "latest advance per node" queries (a group-by or ordered scan)
          rather than a single-row point lookup, on the dashboard hot path.
        - There is no in-place current row to anchor per-learner concurrency on, which
          :ref:`openedx-learning-adr-0004` relies on.
    - Why rejected: even with HISTORY bounded by monotonicity, a single in-place ACTIVE row is a
      cheaper and simpler dashboard read than resolving the latest advance out of a node's history,
      and it is the row per-learner concurrency in :ref:`openedx-learning-adr-0004` is anchored on.

3. Recompute group and competency status on read instead of storing them.

    - Pros:
        - No stored derived state above the leaf.
    - Cons:
        - Reopens :ref:`openedx-learning-adr-0002`, which materialized these levels for dashboard read
          performance, and moves the aggregate cost onto every read.
    - Why rejected: the read surface this feature serves is the one that decision protected; leaves
      are already stored here, so group and competency roll-ups are cheap incremental writes rather
      than repeated read-time aggregation.

4. Make a separate physical database mandatory, or partition/shard the leaf tables, up front.

    - Pros:
        - Isolates the large tables' write, backup, and migration load from the main database for
          every deployment.
    - Cons:
        - Makes separate database infrastructure mandatory for every deployment that enables
          competencies, in tension with :ref:`openedx-learning-adr-0004`'s value of adding no new
          mandatory infrastructure, and burdens small and medium deployers with a database they may
          barely use.
        - Partitioning and sharding add operational complexity the edx-platform grades app never
          needed at scale.
    - Why rejected: the chosen design already bakes in a database router and a dedicated alias, so a
      deployment that needs isolation gets it through settings with no migration, while a stock
      deployment keeps a single database. Forcing a separate database or a partitioning scheme on
      everyone buys nothing the router does not, at a real operational cost. Partitioning and
      sharding remain available to revisit if a specific need is proven.

5. Store a durable per-learner grade-input projection (effective grade per learner and subsection)
   and derive leaves on demand.

    - Pros:
        - Fully re-evaluable, including for rule/threshold changes.
        - Supports leaf-level audit.
    - Cons:
        - Duplicates grade data into openedx-core with its own PII and retention profile.
        - Is a table of the same cardinality as the leaf table, so it does not avoid the scale
          question; it adds grade governance on top of it.
    - Why rejected: it recreates the leaf-level cardinality and adds a grade-data governance problem,
      to buy rule-change recompute that is a documented, accepted limitation of not storing grades.

6. Store child evaluations on the parent group row instead of a leaf ACTIVE table (an enriched
   attained-set).

    Keep the leaf HISTORY table but drop the leaf ACTIVE table. Each group ACTIVE row carries an
    array of its direct children's frozen evaluations, one tuple per child of
    ``(competency_criteria_id, status, effective_source_timestamp)``. The current per-criterion
    status a dashboard shows is read from the parent group's array rather than from a leaf row, and
    the leaf HISTORY table remains the append-only audit trail. This is the "attained-set" of the
    transient-leaf design (alternative 1) enriched from a set of demonstrated ids to a full per-child
    status snapshot, so that it can serve the per-assignment dashboard the bare attained-set could
    not.

    - Pros:
        - Removes the ~200x leaf multiplier from the hot current-state store: the current-state
          tables are group-granular, roughly 7x fewer rows, and a group's whole child set is read in
          a single row.
        - The write path is slightly leaner: a leaf event appends HISTORY and rewrites the parent's
          array in memory, with no leaf ACTIVE upsert and no sibling-row read.
        - Preserves the frozen-evaluation, monotonic, and banking properties.
    - Cons:
        - Reintroduces a denormalized field that must be kept correct under out-of-order delivery and
          structural edits, updated by read-modify-write. This is the bespoke-mechanism cost that
          storing leaves as first-class rows was chosen to remove.
        - Couples a leaf's frozen evaluation to its position in the tree: the eval lives inside the
          parent group's row, so a criterion reparented by a later structural edit strands its frozen
          eval in the old group's array. A first-class leaf row is keyed by the criterion,
          independent of tree position, and is immune to this.
        - Gives up an indexed per-criterion query; cross-learner per-leaf reads must scan arrays or go
          to the analytics store.
        - The dominant table, leaf HISTORY, is unchanged, so the headline migration and backup cost
          is not reduced; only the hot store shrinks.
    - Why rejected: structural robustness is valued over the hot-store saving. First-class leaf rows
      keep a leaf's frozen mastery independent of how the criteria tree is later restructured, and
      avoid re-incurring the denormalized-array correctness burden that this decision removed by
      storing leaves. The hot-store reduction is real but does not address the largest table, and the
      single-row group read it optimizes is served acceptably by an indexed range read of a learner's
      leaf rows.
