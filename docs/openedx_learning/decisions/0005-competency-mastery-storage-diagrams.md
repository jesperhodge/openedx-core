# ADR 0005 diagrams: competency mastery storage

Companion diagrams for `0005-competency-mastery-storage.rst`. They are kept here as Markdown so
they render natively on GitHub; they are not part of the Sphinx/readthedocs build. Refer to the ADR
for the authoritative decision text.

## 1. Data model and the ACTIVE/HISTORY split

Criteria definitions live in the main database. Learner status is stored at every level (leaf,
group, competency), each split into an ACTIVE table (current status, updated in place) and an
append-only HISTORY table. All learner-status tables are assigned to a dedicated `competency_mastery`
database alias through a router; that alias defaults to the main database, so a stock deployment runs
on one database and a large deployment can point the alias at a separate database with no migration.
Foreign keys that could cross the alias boundary are declared without database-level constraints.

```mermaid
flowchart TB
    subgraph DEF["Criteria definitions (main database)"]
        CT["CompetencyTaxonomy"]
        CRP["CompetencyRuleProfile"]
        CCG["CompetencyCriteriaGroup<br/>AND/OR, nestable, course-scoped"]
        CC["CompetencyCriterion (leaf)<br/>tag/object association + rule"]
        CCG -->|"parent_id self-nest"| CCG
        CCG -->|"contains"| CC
        CRP -.->|"default rule"| CC
        CT -.->|"scopes"| CRP
    end

    subgraph MAST["Learner status: competency_mastery alias (defaults to the main database)"]
        LA["Leaf ACTIVE<br/>StudentCompetencyCriteriaStatus<br/>one row per learner + criterion"]
        LH["Leaf HISTORY<br/>append-only, one row per status advance (bounded)"]
        GA["Group ACTIVE<br/>StudentCompetencyCriteriaGroupStatus"]
        GH["Group HISTORY<br/>append-only"]
        CA["Competency ACTIVE<br/>StudentCompetencyStatus"]
        CH["Competency HISTORY<br/>append-only"]
        CMS["CompetencyMasteryStatuses (lookup)<br/>Demonstrated / PartiallyAttempted /<br/>AttemptedNotDemonstrated"]
        LA -.->|"append on change"| LH
        GA -.->|"append on change"| GH
        CA -.->|"append on change"| CH
        LA -->|"rolls up to"| GA
        GA -->|"rolls up to"| CA
        CMS -.->|"status_id"| LA
    end

    CC -.->|"FK, no DB constraint (may cross alias)"| LA
```

## 2. Recording a grade change: incremental roll-up with banking

A grade-change event resolves the criteria fed by the subsection, evaluates the leaf as a pure
function of the grade and the rule, applies the out-of-order and advance-only rules, then rolls the
result up the tree, writing only the rows whose status changed.

```mermaid
flowchart TD
    E["Grade-change event<br/>(carries effective source timestamp)"] --> EV["Evaluate leaf = f(grade, rule)"]
    EV --> OOO{"Event older than the<br/>leaf's current timestamp?"}
    OOO -->|"yes"| DROP["Ignore: out-of-order defense"]
    OOO -->|"no"| BANK{"Leaf already banked Demonstrated<br/>and event is a downward correction?"}
    BANK -->|"yes"| SUP["Keep ACTIVE banked;<br/>no HISTORY row (advances only)"]
    BANK -->|"no / upward"| WLEAF["Upsert leaf ACTIVE;<br/>append leaf HISTORY"]
    WLEAF --> RU{"Leaf now Demonstrated?"}
    RU -->|"no"| ENSURE["Ensure ancestor ACTIVE rows exist<br/>as AttemptedNotDemonstrated"]
    RU -->|"yes"| GEVAL["Re-evaluate parent group<br/>from stored child rows"]
    GEVAL --> GCHG{"Group status changed?"}
    GCHG -->|"no"| STOP["Stop: short-circuit"]
    GCHG -->|"yes"| GWRITE["Upsert group ACTIVE + HISTORY"]
    GWRITE --> UP["Continue up to the competency root"]
```

## 3. Status lifecycle for a node (advance-only)

A node advances through statuses and is banked once Demonstrated: it never auto-regresses, so late
or duplicate events are safe. A leaf is atomic, so it only ever uses NotStarted (absent row),
AttemptedNotDemonstrated, or Demonstrated; PartiallyAttempted is a group-level state.

```mermaid
stateDiagram-v2
    [*] --> NotStarted: no row
    NotStarted --> AttemptedNotDemonstrated: first unsuccessful attempt
    AttemptedNotDemonstrated --> PartiallyAttempted: some children attained (AND / mixed groups)
    AttemptedNotDemonstrated --> Demonstrated: logic satisfied
    PartiallyAttempted --> Demonstrated: logic satisfied
    Demonstrated --> Demonstrated: banked; downward corrections write no HISTORY row
    note right of Demonstrated
        Advance-only: never auto-regresses.
        Competency level surfaces only
        Demonstrated / PartiallyAttempted.
    end note
```
