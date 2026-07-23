.. _openedx-learning-adr-0005:

5. How should learner competency status be stored at scale?
===========================================================

Status
------
Proposed.

Context
-------
Per :ref:`openedx-learning-adr-0002`, competency achievement criteria form a boolean tree. An
internal ``CompetencyCriteriaGroup`` node combines child nodes with an ``AND``/``OR``
``logic_operator``, can be scoped to a course run, and can nest under a parent group. A
``CompetencyCriterion`` leaf is the tree's terminal node: it points at one tag/object association
and a rule.

The student mastery statuses tied to these tree nodes are stored in:
- `StudentCompetencyCriteriaStatus` (leaf nodes)
- `StudentCompetencyCriteriaGroupStatus` (middle nodes)
- `StudentCompetencyStatus` (top-level)

For each of these, we also need to persist history, because we need an audit trail to understand
why a learner did or didn't achieve mastery of a particular competency or any of the associated "measurement instruments"
(gradeable subsections).

Storing every leaf multiplies out at scale. A course can carry on the order of 200 leaf criteria,
so the leaf level is where the row count concentrates: the leaf table (learners x attempted
leaves) potentially reaches the low billions for an Open edX instance with millions of learners. The dominant
multiplier is this per-leaf breadth (roughly 200x per course), not time. Mastery is monotonic (see
"Advance-only banking" below): a node can only advance through the small status lattice, at most a handful of
forward steps ever.

That scale is not, on its own, what makes a relational database struggle. A point lookup against a
billion-row table backed by the right composite index is a logarithmic-time index seek regardless
of the table's size; the dashboard reads this feature performs are exactly such point lookups. What
billions of rows makes painful is schema migrations, backups, and any non-indexed or aggregate
query.

Decision
--------

**Alleviate performance concerns by following established edx-platform practices.**
edx-platform has already large tables of similar magnitude, such as `StudentModule`,
and they have proven themselves.
This means that as long as we follow their structure, we can have confidence that we are
not introducing any huge new problems.
They do not use physical partitioning, but work by using 64-bit primary keys,
enabling optional read-replica offloads, and splitting out a history table that
uses a dedicated database router. The decisions below are designed to mirror these
existing patterns.

**Store statuses / mastery at every level, each split into ACTIVE and HISTORY.** The leaf, group, and
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
append-only.

**64-bit primary keys from the start.** The leaf ACTIVE and HISTORY tables use a 64-bit
``BigAutoField`` primary key, chosen up front, mirroring edx-platform's
``UnsignedBigIntAutoField`` on ``PersistentSubsectionGrade`` ("primary key will need to be
large for this table"). Changing a primary-key type on a billion-row table later is
prohibitively expensive.

**A dedicated database alias and router for the leaf HISTORY table, baked in from the start.**
This only applies to the `StudentCompetencyCriteriaStatusHistory` table.
The leaf HISTORY table (``StudentCompetencyCriteriaStatusHistory``), the dominant table in this
model, is the one learner-status table assigned to a dedicated Django database alias through a
database router, mirroring edx-platform's courseware-history router
(``StudentModuleHistoryExtended``), which is likewise a history table. The alias defaults to the
main database.

**No database-level foreign keys to `user_id` on ACTIVE or HISTORY table.**
Foreign keys to `user_id` must have `db_constraint=False` set.

**Enable read-replica offload for heavy reads for the leaf tables.**
This only applies to the ACTIVE `StudentCompetencyCriteriaStatus` and HISTORY
`StudentCompetencyCriteriaStatusHistory` tables.
Prior art: ``edx_django_utils``'s ``read_replica_or_default()``.

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
leaf mastery downward.

Rejected Alternatives
---------------------

1. Compute leaves transiently, never store them).

Leaves could compute demonstration based on their rules together with the group-node
status together. That would eliminate the largest tables. But this does not account
for competency tree edits, which would result in leaf statuses being incorrect.

2. Keep everything append-only (no ACTIVE table); current status is the latest row.

Even with HISTORY bounded by monotonicity, a single in-place ACTIVE row is a
cheaper and simpler dashboard read than resolving the latest advance out of a node's history,
and it is the row per-learner concurrency in :ref:`openedx-learning-adr-0004` is anchored on.

3. Make a separate physical database mandatory, or partition/shard the leaf tables, up front.

Forcing a separate database or a partitioning scheme on
everyone buys nothing the router does not, at a real operational cost. Partitioning and
sharding remain available to revisit if a specific need is proven.

4. Store child evaluations on the parent group row instead of a leaf ACTIVE table (an enriched
   attained-set).

Structural robustness is valued over the hot-store saving. First-class leaf rows
keep a leaf's frozen mastery independent of how the criteria tree is later restructured, and
avoid re-incurring the denormalized-array correctness burden that this decision removed by
storing leaves. The hot-store reduction is real but does not address the largest table, and the
single-row group read it optimizes is served acceptably by an indexed range read of a learner's
leaf rows.
