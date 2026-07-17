# ADR 0004 diagrams: competency mastery recording and concurrency

Companion diagrams for `0004-competency-mastery-concurrency.rst`. They are kept here as Markdown so
they render natively on GitHub; they are not part of the Sphinx/readthedocs build. Refer to the ADR
for the authoritative decision text.

## 1. End-to-end recording pipeline

A scheduled producer on the edx-platform side reads changed subsection grades and emits bounded
batch events. openedx-core receives them in-process, writes them idempotently into an inbox, and
returns. A separate scheduled task drains the inbox in windowed micro-batches under a single
deployment-wide lock, doing bulk reads and bulk writes.

```mermaid
sequenceDiagram
    autonumber
    participant PG as edx-platform persisted grades
    participant PROD as Scheduled producer (edx-platform)
    participant EV as openedx-events
    participant RCV as Receiver (openedx-core, in-process)
    participant INBOX as Inbox table
    participant DRAIN as Scheduled drain task
    participant DB as Mastery tables (ACTIVE + HISTORY)

    PROD->>PG: range query by modified watermark (+ trailing overlap window)
    PG-->>PROD: changed subsection grades (effective timestamp)
    PROD->>EV: bounded, fixed-size batch events
    EV->>RCV: in-process delivery
    RCV->>INBOX: idempotent write of rows
    RCV-->>EV: return (minimal work)
    loop each drain cycle
        DRAIN->>DRAIN: acquire deployment-wide batch lock
        DRAIN->>INBOX: read a window of rows
        DRAIN->>DB: bulk read current ACTIVE statuses
        DRAIN->>DRAIN: evaluate in memory (fold per learner by effective timestamp)
        DRAIN->>DB: bulk upsert ACTIVE + bulk append HISTORY
        DRAIN->>DRAIN: release lock
    end
```

## 2. Batch drain: correctness and performance

The lock serializes whole batches (not rows), so no two workers evaluate the same learner at once
and the same-learner write-skew cannot occur. Writes are bulk, so the higher per-batch row count of
stored leaves adds bulk-write time within a batch rather than per-row overhead or lock contention
between batches.

```mermaid
flowchart TD
    START["Drain cycle"] --> LOCK{"Acquire deployment-wide batch lock"}
    LOCK -->|"not acquired"| SKIP["Skip this cycle"]
    LOCK -->|"acquired"| READ["Read inbox window +<br/>bulk read current ACTIVE statuses"]
    READ --> FOLD["Per learner: fold events in<br/>effective-source-timestamp order"]
    FOLD --> RULES["Per leaf, apply two rules"]
    RULES --> OOO["Out-of-order: ignore if older<br/>than current leaf timestamp"]
    RULES --> ADV["Advance-only: never regress a banked<br/>status; suppressed events go to HISTORY"]
    OOO --> WRITE["Bulk upsert ACTIVE +<br/>bulk append HISTORY<br/>(leaf and rolled-up levels)"]
    ADV --> WRITE
    WRITE --> UNLOCK["Release lock"]
    UNLOCK --> DUR["Durability: producer overlap re-scan +<br/>idempotent inbox make re-delivery safe"]
```
