.. _openedx-learning-adr-0004:

4. How should learner competency mastery be recorded concurrently and at scale?
================================================================================

Status
------
Proposed (draft).

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
throughput.

Decision
--------
This decision is still open. Two approaches keep same-learner correctness while batching for
throughput. They share the entire pipeline and differ only in how they prevent same-learner
write-skew. The choice turns on expected volume, the event transport, and how much platform-side
coupling is acceptable.

**Common to both approaches.** Grade-change events are consumed from the openedx-events event bus
and processed in windowed micro-batches. Resolving which competencies a subsection feeds is
learner-independent, so it is cached and de-duplicated across the batch. Each batch does one bulk
read of current statuses, evaluates in memory, and does one bulk append, collapsing per-event
transaction overhead into a small, fixed number of round-trips. The in-memory engine
(:ref:`openedx-learning-adr-0002`) is unchanged: to apply several of one learner's events, the
recorder folds them in edited-timestamp order against an evolving snapshot. Persistence stays
append-only and writes a new row only when the computed status differs from the current one; an
older, out-of-order grade is ignored by comparing its source edited timestamp against the current
leaf's, so a late arrival cannot regress a newer status.

They differ only in the correctness mechanism.

**Approach A — correctness by keyed partitioning.**
Events are partitioned by ``user_id``: the key is *hashed* onto a small, fixed number of partitions
(not one partition per learner), so every event for a learner lands on the same partition and is
consumed by exactly one consumer. Same-learner events are processed serially by construction, with
no database lock, while different learners are processed in parallel across partitions.

    - Pros:
        - Correctness is structural; no lock and no lock lifecycle.
        - The write path scales horizontally with the partition count.
    - Cons:
        - Relies on the transport routing by key. Kafka does this natively; Redis Streams consumer
          groups do not, so on Redis this needs application-level sharding into per-shard streams.
        - Couples core to a platform-side contract: the producer must set the partition key and keep
          the partition count stable (owned by the platform-side ticket).
        - Changing the partition count reshuffles learners and can briefly let two consumers touch
          one learner, so it needs an on-demand reconciliation command (Ticket R) as a backstop.

**Approach B — correctness by a single serializing batch lock.**
One lock guards the whole batch operation, so only one batch runs at a time across the deployment.
The read, evaluation, and append for a batch all happen under that lock, so no two workers ever
evaluate the same learner concurrently.

    - Pros:
        - Correctness is total: no write-skew is possible, including across any reshuffle, so no
          reconciliation backstop is needed.
        - Self-contained: no keyed-partition contract, no platform-side partition key, no Ticket P
          or Ticket R.
        - Transport-agnostic; behaves the same on Kafka or Redis.
    - Cons:
        - The write path is a single pipeline: only one batch runs at a time, so recording does not
          scale horizontally. Adequate only while one batch pipeline keeps up with peak grading.
        - Re-introduces a lock and its lifecycle (acquisition, timeout, stale-lock recovery on
          crash), the machinery this epic otherwise removes.

Deciding factors:

    - **Peak throughput.** A single serialized pipeline (B) is adequate at modest volume; sustained
      high-volume, bursty grading favors A's horizontal scaling.
    - **Transport.** Kafka gives A its keyed routing for free; on Redis, A needs application-level
      sharding, which narrows A's simplicity advantage.
    - **Coupling tolerance.** B is self-contained; A trades a platform-side partition contract and a
      reconciliation backstop for horizontal scale.

Rejected Alternatives
---------------------

1. Shrink the batch lock to guard only the bulk read and bulk write, running the per-learner
   evaluation in parallel outside the lock.
    - Motivation: the database I/O is cheap (a few bulk statements), so locking only the I/O and
      parallelizing the heavier evaluation looks like it would keep correctness while lifting
      Approach B's single-pipeline cap.
    - Why rejected: the lock exists to make each learner's read-evaluate-write atomic against other
      writers, not to protect the I/O. Moving evaluation outside the lock reintroduces the exact
      write-skew from the Context: two workers read the same snapshot for one learner, both
      evaluate, and both append a roll-up from an incomplete picture. Making parallel evaluation
      correct requires that each learner is only ever handled by one worker at a time, which is
      per-learner isolation, i.e. Approach A. This is therefore not a third option: with the
      isolation added it is Approach A; without it, it is incorrect.

2. Per-learner database row lock with per-event recording.
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
      This was the earlier design for this epic; it is correct but the least performant. Both
      Approach A and Approach B supersede it: A provides the same same-learner serialization as a
      structural property while allowing parallelism and batching, and B provides it with a single
      lock and batched writes instead of a per-learner lock and per-event writes.

3. No lock; assume same-learner conflicts are rare and tolerate them.
    - Pros:
        - The least machinery of any option: no lock, no partitioning-for-correctness, relying on
          append-only self-healing and a reconciliation job.
    - Cons:
        - Correctness becomes best-effort. A concurrent same-learner conflict can leave a transient
          wrong derived status.
        - A wrong status lingers indefinitely if the learner receives no further relevant event.
        - Unacceptable where mastery feeds credentialing and learner- or instructor-facing
          dashboards, in which an incorrect status is directly visible.

4. Recompute derived levels on read instead of materializing them.
    - Pros:
        - Removes the write-skew hazard entirely: if nothing derived is stored, nothing derived can
          drift, and the write path is trivial.
    - Cons:
        - Reopens :ref:`openedx-learning-adr-0002`, which deliberately materializes derived levels
          for dashboard read performance.
        - Moves the cost onto every read, which is the surface that decision was protecting.
        - Out of scope for this epic.
