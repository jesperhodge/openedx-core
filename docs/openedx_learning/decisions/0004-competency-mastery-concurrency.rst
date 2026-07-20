.. _openedx-learning-adr-0004:

4. How should learner competency mastery be recorded concurrently and at scale?
================================================================================

Status
------
Proposed. Contingent on a cross-repo prerequisite (see `Prerequisite`_).

A companion set of diagrams for this decision lives alongside it in
``0004-competency-mastery-concurrency-diagrams.md``.

Context
-------
When a learner is graded on a subsection, the platform must evaluate whether that grade
demonstrates any attached competencies and record the learner's mastery. Mastery is recorded at
three levels: the criterion (leaf), the criteria group, and the competency. Per
:ref:`openedx-learning-adr-0002` and :ref:`openedx-learning-adr-0005`, all three levels are
*materialized* (stored), not recomputed on read, so that dashboards and other read surfaces stay
fast. A single grade change therefore writes the changed leaf's status and then re-evaluates and
re-writes the derived rows from that leaf up to the competency root. Per
:ref:`openedx-learning-adr-0005`, each level is stored as an ACTIVE row updated in place, holding
the current status for a learner and node, plus an append-only HISTORY row per genuine status
advance.

**The property this decision rests on: mastery only ever moves forward.** Per
:ref:`openedx-learning-adr-0005`, every node, at every level, advances through a small status
lattice (``AttemptedNotDemonstrated`` to ``PartiallyAttempted`` to ``Demonstrated``) and is never
regressed automatically. A status is a high-water mark: once banked it stays, even when a later
grade correction is lower. This is a domain rule, not an implementation convenience, and the rest
of this decision is built on it. It holds only while the criteria that combine child statuses into
a parent are *monotone* boolean functions (AND, OR, k-of-n thresholds), so that advancing any child
can only advance the parent. :ref:`openedx-learning-adr-0002`'s criteria trees contain no negation,
which is the assumption that keeps parents monotone; a ``NOT`` node would break it.

How grade changes reach openedx-core is constrained by layering. openedx-core must never import from
edx-platform and cannot read its grade tables. edx-platform is the higher layer and may depend on
openedx-core, so a grade change is *pushed* into openedx-core by edx-platform, either as a
synchronous call into an openedx-core recording API or as an ``openedx-events`` event. What
openedx-core may not do is reach back to read grades itself. ``openedx-events`` also delivers
in-process by default, because the event bus is not enabled in a stock deployment: a receiver runs
synchronously in the producer's worker and, in production, a receiver exception is swallowed and
logged rather than retried. In-process delivery is therefore best-effort, not durable, and the
recorder cannot assume the event bus is present.

Two forces shape how recording should happen:

- **Same-learner correctness.** A grade change writes the changed leaf and then re-derives the
  group and competency rows above it. Leaf rows are always correct, since each leaf is a pure
  function of its own grade. The derived rows are the hazard: two evaluations for the same learner
  that overlap can each read a stale snapshot of the sibling leaf statuses and each write a derived
  roll-up computed from an incomplete picture (a *write-skew*). Nothing crashes and no constraint is
  violated, but a learner's stored competency status can be silently wrong.

- **Throughput.** Grading is bursty and spans a very large number of learners, so the recording
  path must keep up under peak load.

The question is how to guarantee same-learner correctness at high throughput, over best-effort
in-process delivery, without making the event bus mandatory.

Decision
--------
Recording avoids global coordination by leaning on the monotonicity above: no deployment-wide lock,
no serialized pipeline, no partitioning-for-correctness. Concurrent same-learner writes are made
safe by a monotone merge plus a brief row-level lock on the one node being recomputed, not by
serializing the whole recorder. Two mechanisms provide correctness, and they are what any entry
point below must implement.

**1. Every write is a monotone merge, never a blind overwrite.** A node's status is written as
``status := max(stored status, newly computed status)`` (a single ``GREATEST``-style ``UPDATE``,
atomic at the row for the duration of that one statement, with no application-level lock). Because
the merge takes the higher of the two values, it is commutative, idempotent, and insensitive to
order: applying the same set of advances in any order, and re-applying any of them, yields the same
result. A late or duplicate delivery carries a status no higher than what is stored, so it is a
no-op. This is why out-of-order delivery and re-delivery are harmless without sequence tracking.

**2. When a child advances, its parent is recomputed in the same transaction, under a brief row
lock on that parent.** The merge in mechanism 1 makes a single-row write safe, but a *conjunctive*
parent (for example "demonstrated only when all children are demonstrated") is computed by reading
several child rows first, so two overlapping evaluations for one learner could each read a stale
sibling and compute a parent that is too low. To prevent that, recomputing a parent takes a
row-level lock on the parent row (a ``SELECT ... FOR UPDATE``) before reading its children: two
updates that touch the same parent for the same learner take turns, and the second reads the first's
committed children and computes from the complete picture. Locks are taken child-before-parent up
the path to the root, a consistent order, so concurrent updates cannot deadlock. This is an ordinary
single-row lock, the same serialization any two concurrent writes to one row already incur; it is
*not* the deployment-wide lock the previous design used, and it only ever contends when two grade
changes for the *same learner and same node* land at once, which is rare. Because every write only
advances a status, the result is correct and never over-stated, with no global coordination.

Two rules govern whether a status advances and a HISTORY row is written; both fall out of
monotonicity:

    - *Out-of-order defense.* A change older than the current leaf's effective source timestamp is
      ignored, so a late arrival cannot regress a newer status. The monotone merge already enforces
      this for the stored status; the timestamp check avoids writing a spurious HISTORY row for a
      stale advance.
    - *Advance-only; no automatic regression.* Once a status reaches a level it is banked at every
      level including the leaf (:ref:`openedx-learning-adr-0005`). A downward grade correction does
      not advance the status and writes no HISTORY row, which is what bounds HISTORY by
      monotonicity. Reversing a banked status is a separate administrative action, out of scope
      here.

**Entry point: two options, presented without a preference.** Both implement the two mechanisms
above; they differ only in *where the leaf write happens* and whether it is atomic with the grade
write. The choice is a follow-up decision.

    **Option A: record inside the subsection-grade transaction.** edx-platform wraps its subsection
    grade write and a synchronous call into an openedx-core recording API in one
    ``transaction.atomic()``. This assumes mastery tables and the subsection-grade table share one
    database, which makes grade and mastery genuinely atomic.

        - Pros: grade and mastery can never diverge; recording is real-time; no new event type is
          needed, only a call from code that already runs; and because the leaf and its ancestors
          recompute in that same transaction (mechanism 2), the whole subtree is airtight inline with
          no follow-up step.
        - Cons: mastery work sits on the synchronous grading path, so a slow mastery query or a bug
          adds latency to, or rolls back, the grade write (grades are the more critical data); and it
          requires the shared database.

    **Option B: record from a subsection-grade-changed signal received in openedx-core.**
    edx-platform emits a subsection-granular signal or event; an openedx-core receiver does the
    monotone merge and the upward re-evaluation.

        - Pros: keeps mastery off the grade transaction, so mastery failures or latency never touch
          grade writes; all mastery logic lives in openedx-core, which is the correct owner; no
          shared-database requirement.
        - Cons: not atomic with the grade write, so there is a brief window where the grade is
          written but mastery is not yet (acceptable because mastery lags but never contradicts the
          grade); needs a new subsection-granular event (see `Prerequisite`_); and in-process
          delivery is best-effort, so a dropped signal must be recovered by the re-scan in decision 3
          below.

**Throughput and optional batching.** Because writes commute (mechanism 1), throughput no longer
depends on a single serialized pipeline. Under Option B, more consumer workers can run in parallel;
under Option A, throughput is bounded by the grade path itself. Horizontal scale, which a lock-based
design would sacrifice, is available for free. Batching, a scheduled producer worker on the
edx-platform side that polls changed grades, coalesces them, and emits bounded batch events, is
therefore *optional*: it is one way to raise throughput, now competing with simply adding parallel
consumers rather than being the only route to it. Without batching, Option B receives one signal
per change and Option A needs no producer at all. Batching is *necessary* rather than merely
nice-to-have when:

    - Sustained per-change volume exceeds what parallel per-change consumers (Option B) or the
      grade path (Option A) can absorb, so the fixed per-change overhead (competency resolution,
      round-trips, task scheduling) dominates and only coalescing into bulk operations keeps up.
    - The stored-leaf write amplification (:ref:`openedx-learning-adr-0005`; up to ~200x the
      per-course leaf fan-out) makes per-change writes individually expensive, so bulk upserts
      across a batch are needed to bound the statement count.
    - Grading bursts (an exam closing, a bulk regrade) produce spikes that would otherwise saturate
      consumers or the grade path.

Resolving which competencies a subsection feeds is learner-independent, so when batching is used it
is cached and de-duplicated across the batch, and each batch does one bulk read of current ACTIVE
statuses, evaluates in memory, and bulk-writes the changed ACTIVE and HISTORY rows per level.

**Latency.** Option A records in real time. Option B without batching lags by receiver processing;
with batching it lags by the producer interval plus drain time. The dashboards this feeds tolerate
minutes.

**Transactions and durability.** Three points, each a deliberate part of this decision:

    1. *One atomic transaction per grade change, in both options.* A change is recorded in a single
       transaction that merges the leaf and recomputes its ancestors to the root (mechanism 2). The
       options differ only in what else shares that transaction: under Option A the subsection grade
       write is inside it, so grade and mastery commit or roll back together; under Option B the
       grade is already committed and the transaction covers the mastery write alone. Either way the
       mastery write is never half-applied.

    2. *No reconciliation for correctness.* Same-learner concurrency is fully handled by mechanisms 1
       and 2, so there is nothing to reconcile after the fact: a monotone merge cannot be corrupted
       by a concurrent writer, and the row-locked parent recompute always reads a complete, committed
       picture. This is the key difference from a non-monotone design, which would need a correcting
       sweep to converge. No such sweep, and no on-demand fix command, is part of this decision.

    3. *Recovering a lost delivery (Option B only).* Option A cannot lose a change, because the
       mastery write shares the grade transaction. Option B carries the change over a separate
       in-process signal, and in production a receiver exception is swallowed and logged, so a signal
       can be dropped silently. Recovery is a *trailing-overlap re-scan*: the producer tracks a
       watermark (how far it has read) and each cycle queries grades changed since a few minutes
       *before* the watermark, not exactly since it, so the most recent few minutes of changes are
       always re-read. A change whose signal was dropped is re-emitted on the next cycle, and the
       monotone merge makes the re-delivery a no-op if it was in fact already recorded. The watermark
       still advances, so the query stays a cheap short-window scan, not a full-table scan. A
       deployment that runs Option B with no producer at all (pure per-change signals) instead relies
       on the change healing the next time that leaf is graded; if that is not acceptable it runs the
       lightweight periodic re-scan.

Accepted tradeoffs:

    - Mastery is internally consistent, since the leaf and its roll-ups move together in one
      transaction (mechanism 2), but under Option B it lags the grade: a reader can briefly see an
      updated grade whose mastery is not yet recorded. Mastery is never over-stated and never
      contradicts a committed grade; it only trails it. Option A has no such lag. Consumers that need
      mastery exactly in step with the grade at all times should use Option A.
    - Mastery is a high-water mark. A downward or revoked grade never lowers it; un-mastering is a
      deliberate out-of-band action. This is inherited from :ref:`openedx-learning-adr-0005` and is
      the property that removes the deployment-wide lock from the write path.
    - Correctness depends on criteria staying monotone (no negation in
      :ref:`openedx-learning-adr-0002`'s trees). If a future criteria feature introduces negation,
      that node's parent is no longer monotone and this design does not cover it.
    - Both options need edx-platform code, and Option B needs a new ``openedx-events`` event
      (see `Prerequisite`_), which is cross-repo coordination, though it keeps the dependency
      direction correct.

Prerequisite
------------
This decision requires cross-repo work in edx-platform, differing by option. Option A requires the
subsection grade write to call an openedx-core recording API within its transaction, and the mastery
tables to be routed to the same database. Option B requires a scheduled or signal-driven producer
and the ``openedx-events`` event or events it emits; the batched variant additionally requires the
polling-and-coalescing producer worker. The edx-platform-side design (task location, Celery queue,
retry/backoff, watermark storage, crash recovery) is out of scope here and belongs in that companion
work. :ref:`openedx-learning-adr-0001` rejected this migration (its rejected alternative 8) as out of
scope at the time; this decision takes it up as a now-scheduled prerequisite, and any project
documentation listing the migration as a non-goal is correspondingly superseded.

Rejected Alternatives
---------------------

1. Serialize all writers with a deployment-wide lock (the previous form of this decision).

    Record under a single deployment-wide lock (or a per-learner lock), so no two workers ever
    evaluate the same learner at once and the write-skew cannot occur. This was the earlier design
    for competency mastery recording. This alternative is about the *global* serialization lock, not
    the brief per-node row lock the chosen design uses in mechanism 2, which is a different and far
    cheaper primitive.

    - Pros:
        - Correct regardless of delivery order or topology, without depending on monotonicity.
        - Self-contained: relies only on the database.
    - Cons:
        - A single deployment-wide lock forces a single serialized pipeline: recording does not
          scale horizontally, which is only adequate while one pipeline keeps up with peak grading.
        - A per-learner lock removes that cap but multiplies the lock lifecycle (acquisition,
          timeout, stale-lock recovery) across potentially millions of learner keys, and fights the
          cross-learner batching that throughput would otherwise rely on.
        - Either way it introduces a lock and its failure modes for a guarantee monotonicity already
          provides.
    - Why rejected: the write-skew a global lock exists to prevent is a consequence of computing
      derived rows non-monotonically. Once every write is a monotone merge (mechanism 1) and each
      parent recompute is serialized only against concurrent writers of that same node by a brief row
      lock (mechanism 2), correctness holds without serializing the whole recorder. The deployment-wide
      lock forces a single pipeline and gives up horizontal scale to buy a guarantee that a per-node
      row lock already provides at a fraction of the cost.

2. Allow non-monotonic mastery and repair drift with a reconciliation sweep.

    Let a status move up or down freely with the latest grade, accept that concurrency can leave
    derived rows wrong, and run a scheduled job that recomputes each learner from scratch to correct
    them.

    - Pros:
        - Mastery tracks the current grade exactly, including downward corrections, with no
          high-water-mark surprise.
    - Cons:
        - Gives up order-insensitivity: a non-monotone merge depends on the order writes arrive, so
          concurrent writers can corrupt (not merely understate) a derived row, and a brief row lock
          no longer suffices to keep them correct.
        - Requires an always-on correcting reconciliation process to converge, the kind of standing
          cost this decision avoids.
    - Why rejected: monotonicity is the linchpin that keeps the write path correct with only a brief
      per-node row lock and no global serialization. Trading it away to track downward corrections
      reintroduces exactly the coordination problem this decision removes, and the product treats
      mastery as banked anyway (:ref:`openedx-learning-adr-0005`).

3. Overwrite derived rows with the freshly computed value instead of a monotone merge.

    - Why rejected: a blind ``status := computed`` write is a lost update under concurrency, since a
      worker computing from a stale sibling snapshot can overwrite a higher value another worker
      just wrote. The monotone ``max`` merge is precisely what makes concurrent writes safe; without
      it, mechanism 1 does not hold and a lock would be back in scope.

4. Recompute derived levels on read instead of materializing them.

    - Pros:
        - Removes the write-skew hazard entirely: if nothing derived is stored, nothing derived can
          drift, and the write path is trivial.
    - Cons:
        - Reopens :ref:`openedx-learning-adr-0002`, which deliberately materializes derived levels
          for dashboard read performance, and moves the cost onto every read.
    - Why rejected: out of scope for this decision; the read-performance tradeoff was settled in
      :ref:`openedx-learning-adr-0002`.

5. Let openedx-core obtain grades directly instead of having edx-platform push them.

    Variants: openedx-core polls the persisted subsection-grade and override tables on their indexed
    ``modified`` columns; or the subsection-grade model is relocated into openedx-core so edx-platform
    imports it; or a swappable base model (in the style of ``AUTH_USER_MODEL``) is defined in
    openedx-core and supplied by edx-platform.

    - Why rejected: all three break the layering rule that openedx-core must not depend on
      edx-platform. Polling couples the recorder to a private grade schema that changes by migration
      without notice and re-implements the override layering it would drift from. Relocating a
      mature, deeply woven platform model is a large, risky migration out of proportion to this
      feature. The swappable-model pattern is effectively a one-off for the user model, cannot be
      retrofitted onto an existing table, and would be first-of-its-kind machinery here. A push from
      edx-platform (Option A's call or Option B's versioned event) gets the data across the boundary
      in the correct direction without any of this.

6. Deliver over a mandatory event bus.

    Route grade events over the Kafka or Redis event bus and consume them in openedx-core as a bus
    consumer.

    - Pros:
        - The bus is a durable buffer with native batch polling and at-least-once delivery, closing
          Option B's delivery gap without relying on the trailing-overlap re-scan.
    - Cons:
        - The event bus is not enabled in a stock edx-platform deployment, so requiring it makes
          Kafka or Redis mandatory infrastructure for any deployment that wants competencies.
    - Why rejected: mandating event-bus infrastructure is a far larger operational imposition than
      this feature should force on operators. Both options run over ordinary in-process delivery and
      can take advantage of the bus where a deployment already runs one, without requiring it.

7. Correctness by keyed partitioning.

    Partition grade-change events by ``user_id`` so every event for a learner is consumed by exactly
    one worker, making same-learner events serial by construction with no lock.

    - Why rejected: this was the lock-free alternative worth considering only while correctness
      required serialization. Under monotone merge, correctness no longer requires that any two
      same-learner events be serialized at all, so partitioning solves a problem this decision no
      longer has. It would also couple the recorder to a transport-level routing contract (native on
      Kafka, application-level sharding on Redis) that monotonicity makes unnecessary.
