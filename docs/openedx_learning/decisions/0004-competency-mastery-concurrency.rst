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
  function of its own grade. The derived rows are the hazard: We want to avoid a case where two evaluations for the same learner
  that overlap can each read a stale snapshot of the sibling leaf statuses and each write a derived
  roll-up computed from an incomplete picture (a *write-skew*).

- **Throughput.** Grading is bursty and spans a very large number of learners, so the recording
  path must keep up under peak load.

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
single-row lock. Every read the recorder makes, here and in the mass-recompute path
(:ref:`openedx-learning-adr-0005`), runs against the primary database and never a read replica:
these reads feed the roll-up write and take the row locks above, so a replica's lag would compute a
roll-up from stale siblings. The read-replica offload in :ref:`openedx-learning-adr-0005` is
reserved for the read-only dashboard and reporting paths.

**3. Entry point: edx-platform subsection grade change.** edx-platform
computes subsection grades in an async celery task (`recalculate_subsection_grade_v3`) triggered by a score-change signal, not on the
request thread. After that task writes the subsection grade, it calls a public openedx-core function
within the same transaction; this function does the monotone merge and the upward roll-up. This should be generalized as needed to other places that trigger a competency status update.

**4. ACTIVE writes and roll-ups commit atomically with the grade, or the whole task rolls back and retries.**

**5. The leaf HISTORY append runs outside the transaction, best effort.** Because the leaf HISTORY table
(``StudentCompetencyCriteriaStatusHistory``) may be routed to a
separate physical database (:ref:`openedx-learning-adr-0005`), its append runs after that transaction commits, as a best-effort,
non-blocking write.


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
      edx-platform (the synchronous in-transaction call this decision uses) gets the data across the
      boundary in the correct direction without any of this.

6. Deliver over a mandatory event bus.

    Route grade events over the Kafka or Redis event bus and consume them in openedx-core as a bus
    consumer.

    - Pros:
        - The bus is a durable buffer with native batch polling and at-least-once delivery, as a
          persistent log rather than the in-process call this decision uses.
    - Cons:
        - The event bus is not enabled in a stock edx-platform deployment, so requiring it makes
          Kafka or Redis mandatory infrastructure for any deployment that wants competencies.
    - Why rejected: mandating event-bus infrastructure is a far larger operational imposition than
      this feature should force on operators. The chosen design records through a direct in-process
      call inside the grade task and needs no bus at all; a deployment that already runs one gains
      nothing this recording path requires.

7. Correctness by keyed partitioning.

    Partition grade-change events by ``user_id`` so every event for a learner is consumed by exactly
    one worker, making same-learner events serial by construction with no lock.

    - Why rejected: this was the lock-free alternative worth considering only while correctness
      required serialization. Under monotone merge, correctness no longer requires that any two
      same-learner events be serialized at all, so partitioning solves a problem this decision no
      longer has. It also presumes an event transport to partition, which the chosen synchronous
      in-transaction call does not have.

8. Record asynchronously in a task openedx-core enqueues from an event, in its own transaction (the previous form of this decision).

    edx-platform emits a subsection-grade-changed openedx-event; an openedx-core receiver enqueues a
    celery task that does the monotone merge and the upward roll-up in its own transaction, separate
    from edx-platform's grade write.

        - Pros: mastery work is fully isolated from the grade write, so a slow or failing merge can
          never touch the grade transaction; recording scales as its own horizontally-scalable
          consumer pool; and it can ride an event bus where a deployment already runs one.
        - Cons: it adds a second async hop after an already-async grade computation, so mastery trails
          the grade by the enqueue-and-run delay and can diverge if the task is lost; it requires
          defining and versioning a new openedx-event, its consumer, and a task queue as the recording
          path's infrastructure; and the isolation it buys is only necessary if the merge is treated
          as unsafe to run inline, which mechanism 1's monotone merge and the grade task's idempotent
          retry (mechanism 9) already make safe.
        - Why rejected: the grade is already produced in an async task, so recording inline in that
          task's transaction commits mastery with the grade (mechanism 7) without a second hop, a new
          event contract, or a separate queue. The async design's real advantage is isolation: a
          mastery fault never touches the grade write. This decision gives that up deliberately
          (mechanism 7) in exchange for no grade/mastery divergence and no extra moving parts; a
          transient fault is handled by the shared retry, and a persistent one is a bug to fix rather
          than a reason to let mastery drift silently behind the grade.

9. Record inside a subsection-grade transaction, but only ever for the subsection grade.

    Wrap competency mastery updates in edx-platform's subsection-grade transaction, treating that
    single trigger as the one and only recording entry point.

        - Why rejected: mastery is not meant to be tied to the subsection grade alone. It is also
          intended to derive from course completion, unit grade, problem grade, and rubric criterion
          grade, so a subsection-grade-only coupling would contradict that direction. This decision
          keeps the in-transaction recording but generalizes it (mechanism 6): the subsection grade is
          the first trigger to record in its producing transaction, not the only one that ever will.
