.. _openedx-learning-adr-0004:

4. How should learner competency mastery be recorded concurrently and at scale?
================================================================================

Status
------
Proposed. Contingent on a cross-repo prerequisite (see `Prerequisite`_).

Context
-------
When a learner is graded on a subsection, the platform must evaluate whether that grade
demonstrates any attached competencies and record the learner's mastery. Mastery is recorded at
three levels: the criterion (leaf), the criteria group, and the competency. Per
:ref:`openedx-learning-adr-0002`, the group and competency levels are *materialized* (stored),
not recomputed on read, so that dashboards and other read surfaces stay fast. A single grade
change therefore has to recompute and re-write the derived rows from the changed leaf up to the
competency root. Per :ref:`openedx-learning-adr-0003`, every status table is append-only: a change
is a new row, and the current status is the most recent row for a learner and entity.

How grade changes can reach openedx-core is constrained. openedx-core must never import from
edx-platform and cannot read its grade tables, so grade changes can arrive only as
``openedx-events`` events. The only grade event that exists today is course-level and carries no
subsection identifier, so recording at subsection granularity needs a new event that edx-platform
does not yet emit (see `Prerequisite`_). ``openedx-events`` also delivers in-process by default,
because the event bus is not enabled in a stock deployment: a receiver runs synchronously in the
producer's worker and, in production, a receiver exception is swallowed and logged rather than
retried. Delivery is therefore best-effort, not durable, and the recorder cannot assume the event
bus is present.

Two forces shape how this recording should happen:

- **Same-learner correctness.** Grade-change events arrive asynchronously and can be delivered out
  of order and processed on more than one worker. Because writes are append-only, two evaluations
  for the same learner that overlap can each read a stale snapshot of the sibling leaf statuses and
  each append a derived roll-up computed from an incomplete picture (a write-skew). Leaf rows are
  always correct, since each leaf is a pure function of its own grade; only the derived
  group/competency rows can be left wrong. Nothing crashes and no constraint is violated, but a
  learner's stored competency status can be silently incorrect.

- **Throughput.** Grading is bursty and spans a very large number of learners, so the recording
  path must keep up without paying a serialization or per-event transaction cost that grows with
  the number of learners being graded.

The question is how to guarantee same-learner correctness while still recording at high aggregate
throughput, over best-effort in-process delivery and without making the event bus mandatory.

Decision
--------
Grade changes are delivered to openedx-core as batched ``openedx-events`` events produced by a
scheduled task on the edx-platform side, buffered into an openedx-core inbox, then recorded in
windowed micro-batches serialized by a single deployment-wide lock. Correctness is chosen over
horizontal write throughput: a single serialized pipeline is adequate while it keeps up with peak
grading, it keeps the recorder self-contained and behaving identically on any Open edX deployment,
and it adds no new mandatory infrastructure. This decision depends on the `Prerequisite`_ below and
stays Proposed until that work lands.

**Producing grade changes (edx-platform side).** A scheduled task in edx-platform reads the
subsections whose grade changed since a stored ``modified``-timestamp watermark (an indexed range
query on the persisted subsection-grade table, joined with its separately stored overrides to get
the effective grade), selects only the fields the recorder needs, and emits them as an
``openedx-events`` event. Because an ``openedx-events`` payload is conventionally a single entity
and, when the event bus is enabled, is size-capped by the transport, a cycle's rows are split into
bounded, fixed-size batch events rather than one large payload; carrying a list of rows is a
deliberate exception to the single-entity convention, scoped to this producer. Each row carries an
effective source timestamp, computed as the later of the base grade's and the override's
``modified`` time, so an override-only correction, which does not touch the base row, still
registers as newer.

**Buffering and batching (openedx-core side).** Because delivery is in-process and synchronous, the
receiver does the minimum: it writes the batch's rows into an openedx-core-owned inbox table,
idempotently keyed so a re-delivered row is a no-op, and returns. A separate openedx-core scheduled
task drains the inbox in windowed micro-batches. Keeping the evaluation and the lock out of the
receiver keeps them off edx-platform's scheduler, bounds what a slow or wedged recorder can do to
the producer to "one more row in a table," and makes the durable inbox write, not the evaluation,
the step that must succeed for an event not to be lost.

**Recording pipeline.** Resolving which competencies a subsection feeds is learner-independent, so
it is cached and de-duplicated across the batch. Each batch does one bulk read of current statuses,
evaluates in memory, and does one bulk append, collapsing per-event transaction overhead into a
small, fixed number of round-trips. The in-memory engine (:ref:`openedx-learning-adr-0002`) is
unchanged: to apply several of one learner's events, the recorder folds them in
effective-source-timestamp order against an evolving snapshot. Persistence stays append-only
(:ref:`openedx-learning-adr-0003`). Two rules govern whether a new status row is written:

    - *Out-of-order defense.* A change is ignored when its effective source timestamp is older than
      the current leaf's, so a late arrival cannot regress a newer status. This is a
      delivery-ordering guarantee, distinct from the next rule.
    - *Advance-only; no automatic regression.* Once a status reaches the demonstrated level it is
      retained ("banked"). The recorder appends first attainment and advancements automatically but
      does not auto-append a regression below an already-demonstrated status, even for a
      legitimately newer downward grade correction. Reversing a banked status is a separate
      administrative action, out of scope here.

**Correctness by a single serializing batch lock.** One deployment-wide lock guards the whole
drain-batch operation, so only one batch runs at a time across the deployment. The read,
evaluation, and append for a batch all happen under that lock, so no two workers ever evaluate the
same learner concurrently and the same-learner write-skew described in the Context cannot occur.
The lock is realized on infrastructure every deployment already has (for example, a database-backed
advisory lock) rather than on the event transport, so the recorder behaves the same on any
event-bus backend and adds no new operational dependency.

**Durability.** In-process delivery swallows receiver exceptions and the producer's watermark
advances regardless, so a dropped event could otherwise be lost silently. Two low-cost mechanisms
cover this: the producer re-scans a trailing overlap window behind its watermark each cycle, so a
row committed just behind the cursor or missed once is re-emitted, and the consumer is idempotent,
so re-delivery is harmless. An on-demand reconciliation command is provided as an operator escape
hatch for incidents. A permanent scheduled reconciliation sweep is deliberately not included: it is
the kind of always-on cost this decision otherwise avoids, and can be added if real drops are ever
observed.

**Latency.** Recording is expected to lag grading by minutes, not to be real-time; the dashboards
this feeds tolerate that. The producer's interval is a deployment setting with a documented floor.

Accepted tradeoffs:

    - The write path is a single pipeline: only one batch runs at a time, so recording and evaluating does not
      scale horizontally. This is adequate only while one batch pipeline keeps up with peak grading;
      sustained high-volume growth would force a revisit.
    - It introduces a lock and its lifecycle (acquisition, timeout, stale-lock recovery on crash)
      that a lock-free partitioning design would avoid.
    - Recording lags grading by the producer interval plus drain time; mastery is not updated in
      real time.
    - It requires new edx-platform code and a new ``openedx-events`` event (see `Prerequisite`_),
      which is cross-repo coordination, though it keeps the dependency direction correct.

Prerequisite
------------
This decision requires, in edx-platform, the scheduled producer task and the ``openedx-events``
event or events it emits. Their edx-platform-side design (task location, Celery queue,
retry/backoff, watermark storage, and crash recovery) is out of scope here and belongs in that
companion work. :ref:`openedx-learning-adr-0001` rejected this migration (its rejected alternative
8) as out of scope at the time; this decision takes it up as a now-scheduled prerequisite, and any
project documentation listing the migration as a non-goal is correspondingly superseded.

Rejected Alternatives
---------------------

1. Correctness by keyed partitioning instead of a lock.

    Partition grade-change events by ``user_id``: the key is *hashed* onto a small, fixed number of
    partitions (not one partition per learner), so every event for a learner lands on the same
    partition and is consumed by exactly one consumer. Same-learner events are then processed serially
    by construction, with no database lock, while different learners are processed in parallel across
    partitions.

    - Pros:
        - Correctness is structural; no lock and no lock lifecycle.
        - The write path scales horizontally with the partition count.
    - Cons:
        - Relies on the transport routing by key. Kafka does this natively; Redis Streams consumer
          groups do not, so on Redis it needs application-level sharding into per-shard streams.
        - Couples openedx-core to a platform-side contract in the wrong direction: the producer, in
          openedx-platform, must set the partition key and keep the partition count stable, so the
          core recorder's correctness depends on platform-side behavior.
        - Changing the partition count reshuffles learners and can briefly let two consumers touch
          one learner, so it needs an on-demand reconciliation command as a backstop.
    - Why rejected: it optimizes for horizontal throughput at the cost of the two properties this
      decision values most. It depends on the event transport (Kafka's keyed routing, or Redis-side
      sharding that is not an established pattern in vanilla Open edX), so it would not behave
      uniformly on a stock deployment, whereas the chosen approach is transport-agnostic. And it
      inverts the intended dependency direction by making openedx-core rely on an openedx-platform
      partition-key contract plus a reconciliation backstop. The horizontal scale it buys is not
      needed while a single batch pipeline keeps up with peak grading, and accuracy is the priority
      over the extra latency the single pipeline adds.

2. Shrink the batch lock to guard only the bulk read and bulk write, running the per-learner
   evaluation in parallel outside the lock.

    - Motivation: the database I/O is cheap (a few bulk statements), so locking only the I/O and
      parallelizing the heavier evaluation looks like it would keep correctness while lifting the
      chosen approach's single-pipeline cap.
    - Why rejected: the lock exists to make each learner's read-evaluate-write atomic against other
      writers, not to protect the I/O. Moving evaluation outside the lock reintroduces the exact
      write-skew from the Context: two workers read the same snapshot for one learner, both
      evaluate, and both append a roll-up from an incomplete picture. Making parallel evaluation
      correct requires that each learner is only ever handled by one worker at a time, which is
      per-learner isolation, i.e. the keyed-partitioning alternative above. This is therefore not a
      distinct option: with the isolation added it becomes keyed partitioning; without it, it is
      incorrect.

3. Per-learner database row lock with per-event recording.
    - Pros:
        - Correct regardless of delivery order or deployment topology, without depending on how
          events are routed or partitioned.
        - Conceptually simple and self-contained: it relies only on the database, with no
          event-bus partitioning contract.
    - Cons:
        - Processes one event at a time behind a lock and a transaction, so it does not keep up
          under bursty grading across many learners.
        - Holds a database connection and a worker for each event while the lock is contended or
          waited on.
        - Requires an extra per-learner lock table and its locking machinery.
        - Does not batch writes, so per-event transaction and commit overhead dominates at volume.

    This was an earlier design for competency mastery recording; it is correct but the least
    performant. Both the chosen batch-lock approach and the keyed-partitioning alternative
    supersede it: keyed partitioning provides the same same-learner serialization as a structural
    property while allowing parallelism and batching, and the batch lock provides it with a single
    lock and batched writes instead of a per-learner lock and per-event writes.

4. No lock; assume same-learner conflicts are rare and tolerate them.
    - Pros:
        - The least machinery of any option: no lock, no partitioning-for-correctness, relying on
          append-only self-healing and a reconciliation job.
    - Cons:
        - Correctness becomes best-effort. A concurrent same-learner conflict can leave a transient
          wrong derived status.
        - A wrong status lingers indefinitely if the learner receives no further relevant event.
        - Unacceptable on the learner- and instructor-facing dashboards this feeds, where an
          incorrect status is directly visible, and on any future credentialing that consumes it.

5. Recompute derived levels on read instead of materializing them.
    - Pros:
        - Removes the write-skew hazard entirely: if nothing derived is stored, nothing derived can
          drift, and the write path is trivial.
    - Cons:
        - Reopens :ref:`openedx-learning-adr-0002`, which deliberately materializes derived levels
          for dashboard read performance.
        - Moves the cost onto every read, which is the surface that decision was protecting.
        - Out of scope for this decision.

6. Real-time per-subsection events with batching on the openedx-core side.

    edx-platform emits one ``openedx-events`` event per subsection grade change as it happens, and
    openedx-core absorbs the per-event stream, buffering and batching on its own side instead of
    having the producer batch first.

    - Pros:
        - Lower latency: mastery can update within seconds of a grade change rather than within a
          polling interval.
        - No new scheduled producer task; it reuses the point where the grade is already written.
    - Cons:
        - The event rate is set by grading volume and scales with however widely competencies are
          adopted, which is unknown, so the consumer must be sized for a firehose it cannot bound.
        - Each event is an inter-process signal and, on the openedx-core side, a durable write on or
          near the hot grading path.
    - Why rejected: producer-side batching bounds the event rate at the source regardless of
      adoption, so the recorder is scalable by default on a very large deployment without having to
      predict how widely competencies will be enabled or whether per-event volume becomes a
      problem. Trading a few minutes of latency for that bound is the priority here.

7. openedx-core polls the persisted grade tables directly.

    Instead of consuming events, openedx-core runs its own periodic query against the persisted
    subsection-grade and override tables, using their indexed ``modified`` columns to find changes.

    - Pros:
        - Cheapest possible read: the ``modified`` indexes exist for exactly this timespan query, so
          it is one indexed range scan per cycle with no per-event cost.
        - No dependency on any event being emitted.
    - Cons:
        - It makes openedx-core depend on edx-platform's private grade schema, an implementation
          detail with no stability guarantee, rather than on a versioned contract.
        - The effective grade must be recomputed by re-implementing edx-platform's separate override
          layering, which will drift as the platform changes it.
    - Why rejected: it breaks the layering rule (openedx-core must not depend on edx-platform) and
      this decision's value of behaving identically on any deployment. A table schema is an
      implementation detail that changes by migration without notice; a versioned event is a
      contract. The cost saving is real but does not justify coupling the recorder to platform
      internals.

8. Move the persisted subsection-grade model into openedx-core so edx-platform imports it.

    Follow this repo's established pattern (a library-owned model that edx-platform depends on) by
    relocating the subsection-grade model into openedx-core.

    - Pros:
        - openedx-core could read the data in-process with no cross-boundary contract at all.
        - It is the same ownership pattern openedx-core already uses for content models.
    - Cons:
        - The subsection-grade model is a large, deeply woven edx-platform model with extensive
          signal wiring, override semantics, and migration history.
        - Relocating it inverts ownership of a central platform concern and is a major migration.
    - Why rejected: that pattern fits models built new in openedx-core, not a mature, central
      edx-platform model. The extraction would be a large, risky effort out of all proportion to
      recording competency mastery. It may be the right long-term shape if Open edX decides grades
      belong in the learning core, but that is a separate, much larger decision.

9. A shared-contract or swappable grade model.

    openedx-core defines a minimal base model as a contract; edx-platform supplies the concrete
    table (with any extra columns it needs) and openedx-core resolves it through a setting, in the
    style of Django's swappable ``AUTH_USER_MODEL``.

    - Pros:
        - openedx-core reads the data without importing edx-platform, and the platform keeps freedom
          to extend its own table.
    - Cons:
        - Django's swappable-model machinery is, in practice, a one-off for the user model; the
          third-party generalizations are niche and carry hard constraints (a swappable model must
          exist from the app's first migration, and retrofitting an existing table is unsupported).
        - It still couples the platform to an openedx-core-dictated schema, and there is no
          precedent for it in this ecosystem beyond the user-model foreign key.
    - Why rejected: it would be first-of-its-kind machinery for the project, applied to a table that
      already exists and so hits exactly the retrofit constraints the pattern handles worst, for a
      benefit a versioned event contract already provides more cleanly.

10. Enable and rely on the ``openedx-events`` event bus as the delivery mechanism.

    Route grade events over the Kafka or Redis event bus and have openedx-core consume them as a bus
    consumer.

    - Pros:
        - The bus is a durable buffer with native batch polling and at-least-once delivery, which
          would close the durability gap without an inbox or a reconciliation command.
        - A bus consumer runs in its own process, off edx-platform's workers.
    - Cons:
        - The event bus is not enabled in a stock edx-platform deployment.
        - Requiring it makes Kafka or Redis mandatory infrastructure for any deployment that wants
          competencies.
    - Why rejected: mandating event-bus infrastructure is a far larger operational decision than
      this feature should force on operators. The chosen design runs over ordinary in-process
      signals and is transport-agnostic: it can take advantage of the bus where a deployment already
      runs one, but it does not require it.
