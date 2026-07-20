# ADR 0004 diagrams: competency mastery recording and concurrency

Companion diagrams for `0004-competency-mastery-concurrency.rst`. They are kept here as Markdown so
they render natively on GitHub; they are not part of the Sphinx/readthedocs build. Refer to the ADR
for the authoritative decision text.

## 1. Entry points: two options (no preference)

Both options push a grade change from edx-platform into openedx-core, which records it with a
monotone merge and re-evaluates the parents. They differ only in where the leaf write happens and
whether it is atomic with the grade write. Batching (the dashed producer) is optional and applies to
Option B.

```mermaid
flowchart TD
    subgraph EP["edx-platform (higher layer)"]
        GRADE["Subsection grade write"]
        PROD["Optional producer worker:<br/>poll changed grades, coalesce,<br/>emit bounded batch events"]
    end
    subgraph CORE["openedx-core (lower layer)"]
        API["Recording API:<br/>monotone merge + re-evaluate parents"]
        RCV["Signal/event receiver"]
    end

    GRADE -->|"Option A: synchronous call<br/>inside the same transaction<br/>(shared DB, atomic)"| API
    GRADE -->|"Option B: emit subsection-grade-changed<br/>signal/event"| RCV
    PROD -.->|"optional batched events"| RCV
    RCV --> API
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

## 3. Recovering a lost delivery (Option B): trailing-overlap re-scan

Option A cannot lose a change (mastery shares the grade transaction). Option B carries it over an
in-process signal that can be dropped silently. The producer re-reads a short overlap window behind
its watermark each cycle, so a dropped change is re-emitted next cycle; the monotone merge makes the
re-delivery a no-op if it was already recorded. No reconciliation command and no correcting sweep.

```mermaid
flowchart LR
    WM["Watermark T"] --> SCAN["Query grades changed since (T - overlap)"]
    SCAN --> EMIT["Emit signal/event per change"]
    EMIT --> MERGE["Recorder: monotone merge<br/>(re-delivery of a recorded change is a no-op)"]
    SCAN --> ADV["Advance watermark → next scan stays a short-window seek"]
```
