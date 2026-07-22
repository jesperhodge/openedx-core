.. _openedx-learning-adr-0004:

4. How should learner competency mastery be recorded concurrently and at scale?
================================================================================

Status
------
Proposed.

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

**Monotonicity: competency statuses only ever move forward.** Per
:ref:`openedx-learning-adr-0005`, every node, at every level, advances through a small status
lattice (``AttemptedNotDemonstrated`` to ``PartiallyAttempted`` to ``Demonstrated``) and is never
lowered later. This holds for leaf nodes, group nodes, and top-level competency masteries.

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

**1. Every write is a monotone merge, never a blind overwrite.** A node's status is written as
``status := max(stored status, newly computed status)`` (a single ``GREATEST``-style ``UPDATE``,
atomic at the row for the duration of that one statement, with no application-level lock). Because
the merge takes the higher of the two values, it is commutative, idempotent, and insensitive to
order. This is why out-of-order delivery and re-delivery are harmless without sequence tracking.


**2. When a child advances, its parent is recomputed in the same transaction, under a brief row lock on that parent.**
The merge in mechanism 1 makes a single-row write safe, but a *conjunctive*
parent (for example "demonstrated only when all children are demonstrated") is computed by reading
several child rows first, so two overlapping evaluations for one learner could each read a stale
sibling and compute a parent that is too low. To prevent that, recomputing a parent takes a
row-level lock on the parent row (a ``SELECT ... FOR UPDATE``) before reading its children: two
updates that touch the same parent for the same learner take turns, and the second reads the first's
committed children and computes from the complete picture. Locks are taken child-before-parent up
the path to the root, a consistent order, so concurrent updates cannot deadlock. This is an ordinary
single-row lock. Every read the recorder makes, here and in the batch path (mechanism 6), runs
against the primary database and never a read replica: these reads feed the roll-up write and take
the row locks above, so a replica's lag would compute a roll-up from stale siblings. The
read-replica offload in :ref:`openedx-learning-adr-0005` is reserved for the read-only dashboard and
reporting paths.

**3. Out-of-order defense.** A change older than the current leaf's effective source timestamp is
ignored, so a late arrival cannot regress a newer status. The monotone merge already enforces
this for the stored status; the timestamp check avoids writing a spurious HISTORY row for a
stale advance.

**4. Advance-only; no automatic regression.** Once a status reaches a level it is banked at every
level including the leaf (:ref:`openedx-learning-adr-0005`). A downward grade correction does
not advance the status and writes no HISTORY row, which is what bounds HISTORY by
monotonicity. Reversing a banked status is a separate administrative action, out of scope
here.

**5. Entry point: record from a subsection-grade-changed signal received in openedx-core.**
edx-platform emits a subsection-granular openedx-event signal; an openedx-core receiver enqueues a
task that does the monotone merge and the upward re-evaluation (see decision 8 for why the receiver
only enqueues).

**6. Optional batching.** With this ADR, consumer workers can run in parallel,
scaling horizontally. If further performance increase is needed, batching can be introduced:
In that case, a scheduled producer worker on the
edx-platform side polls changed grades, coalesces them, and emits bounded batch events.
Resolving which competencies a subsection feeds is learner-independent, so when batching is used it
is cached and de-duplicated across the batch, and each batch does one bulk read of current ACTIVE
statuses, evaluates in memory, and bulk-writes the changed ACTIVE and HISTORY rows per level.

**7. One atomic transaction per grade change.** The recording task does the leaf merge and the
upward roll-up (mechanism 2) in one database transaction, so the leaf and every affected ancestor
for a change commit together or not at all. This transaction is the task's own; it is not shared
with edx-platform's grade write (contrast rejected alternative 8), so mastery trails the grade by
the enqueue-and-run delay rather than committing atomically with it.

**8. Enqueuing a celery task.** The receiver enqueues an async celery task
to do the work to ensure durability.


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
      edx-platform (the versioned event this decision uses, or the synchronous call of rejected
      alternative 8) gets the data across the boundary in the correct direction without any of this.

6. Deliver over a mandatory event bus.

    Route grade events over the Kafka or Redis event bus and consume them in openedx-core as a bus
    consumer.

    - Pros:
        - The bus is a durable buffer with native batch polling and at-least-once delivery, as a
          persistent log rather than the in-process signal plus task-queue retries this decision uses.
    - Cons:
        - The event bus is not enabled in a stock edx-platform deployment, so requiring it makes
          Kafka or Redis mandatory infrastructure for any deployment that wants competencies.
    - Why rejected: mandating event-bus infrastructure is a far larger operational imposition than
      this feature should force on operators. The chosen design runs over ordinary in-process
      delivery and can take advantage of the bus where a deployment already runs one, without
      requiring it.

7. Correctness by keyed partitioning.

    Partition grade-change events by ``user_id`` so every event for a learner is consumed by exactly
    one worker, making same-learner events serial by construction with no lock.

    - Why rejected: this was the lock-free alternative worth considering only while correctness
      required serialization. Under monotone merge, correctness no longer requires that any two
      same-learner events be serialized at all, so partitioning solves a problem this decision no
      longer has. It would also couple the recorder to a transport-level routing contract (native on
      Kafka, application-level sharding on Redis) that monotonicity makes unnecessary.

8. Record inside a subsection-grade transaction.

    edx-platform already synchronously, in-process, handles subsection grade changes.
    This would wrap a subsection grade change in a transaction that includes competency
    mastery updates.

        - Pros: grade and mastery can never diverge; recording is real-time; no new event type is
          needed, only a call from code that already runs; and because the leaf and its ancestors
          recompute in that same transaction (mechanism 2), the whole subtree is airtight inline with
          no follow-up step.
        - Cons: mastery work sits on the synchronous grading path, so a slow mastery query or a bug
          adds latency to, or rolls back, the grade write (grades are the more critical data); and it
          requires the shared database.
        - Why rejected: we do not intend for mastery to always be tied to a subsection grade.
          We've actually proposed tying mastery to several other things (course completion, unit grade
          (a concept that doesn't exist in the platform currently), problem grade, and rubric criterion grade
          (also a concept that doesn't exist in the platform currently)), and tying a mastery to a subsection
          grade would contradict that.
