# ADR 0004 diagrams: competency mastery recording and concurrency

Companion diagrams for `0004-competency-mastery-concurrency.rst`. They are kept here as Markdown so
they render natively on GitHub; they are not part of the Sphinx/readthedocs build. Refer to the ADR
for the authoritative decision text.

## 1. Entry point and durable hand-off

edx-platform emits a subsection-grade-changed event; an openedx-core receiver only enqueues an
idempotent, retriable task, and that task does the monotone merge and re-evaluates the parents. The
thin receiver plus retriable task is edx-platform's own grade-recompute pattern, and it is what makes
a dropped in-process signal survivable. Batching (the dashed producer) is optional (decision 6).

```mermaid
flowchart TD
    subgraph EP["edx-platform (higher layer)"]
        GRADE["Subsection grade write"]
        SIG["Emit subsection-grade-changed event"]
        PROD["Optional producer worker:<br/>poll changed grades, coalesce,<br/>emit bounded batch events"]
    end
    subgraph CORE["openedx-core (lower layer)"]
        RCV["Receiver: enqueue task only"]
        TASK["Task (retriable, idempotent):<br/>monotone merge + re-evaluate parents<br/>in one transaction"]
    end

    GRADE --> SIG
    SIG -->|"in-process event"| RCV
    PROD -.->|"optional batched events"| RCV
    RCV -->|"apply_async"| TASK
```

## 2. Why it is correct: one transaction, monotone merge, brief per-node row lock

Every write is `status := max(stored, computed)`, so writes commute, repeat harmlessly, and never
regress. A leaf is a single value, so its atomic merge needs no extra lock. A conjunctive parent is
computed by reading several children first, so recomputing it takes a brief `SELECT ... FOR UPDATE`
on the parent row: two updates that touch the same parent for the same learner take turns, and the
second reads the first's committed children and computes from the complete picture. This is an
ordinary single-row lock, not the deployment-wide lock the previous design used.

```mermaid
sequenceDiagram
    autonumber
    participant WA as Worker A (child L1 advances)
    participant WB as Worker B (child L2 advances)
    participant DB as Mastery tables (ACTIVE + HISTORY)

    Note over WA,WB: L1 and L2 are siblings under conjunctive parent G, same learner
    WA->>DB: merge L1 (atomic max)
    WB->>DB: merge L2 (atomic max)
    WA->>DB: SELECT G FOR UPDATE (acquires row lock)
    WB->>DB: SELECT G FOR UPDATE (waits for A)
    WA->>DB: read children (L1, L2), merge G, commit → releases lock
    WB->>DB: now reads L1 and L2 committed, merges G → correct
    Note over DB: locks taken child-before-parent up to root → no deadlock
```

## 3. Recovering a lost delivery: retriable task, not a re-scan

The in-process event can be dropped silently (``send_robust`` catches and logs a receiver
exception). Durability follows edx-platform's grades pattern: the receiver only enqueues, and the
task queue's at-least-once delivery plus task retries plus the monotone merge's idempotency carry the
work through. No scheduled reconciliation sweep; a genuine loss (enqueue failed during a broker
outage) is recovered by an operator-run bulk recompute, exactly as edx-platform recovers a missed
grade recompute.

```mermaid
flowchart LR
    RCV["Receiver (send_robust:<br/>exception logged, not fatal)"] -->|"apply_async"| Q["Task queue<br/>(at-least-once, persisted)"]
    Q --> TASK["Task: monotone merge + roll-up"]
    TASK -->|"transient error"| RETRY["self.retry"]
    RETRY --> Q
    TASK -->|"stale / duplicate"| NOOP["no-op via max-merge +<br/>effective-source-timestamp check"]
    BROKER["Rare: enqueue lost in broker outage"] -.->|"operator escape hatch"| CMD["Manual bulk recompute<br/>(monotone; only advances)"]
```
