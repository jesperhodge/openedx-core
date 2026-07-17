# Competency data model (updated)

The authoritative data model for CBE competency criteria and learner mastery. It builds on the
original `images/CompetencyCriteriaModel.png` overview, corrected against ADRs 0001-0003 (which are
the source of truth where the PNG diverges) and updated for the storage decisions in
`0005-competency-mastery-storage.rst`.

Notable differences from the PNG:

- Each learner-status level is now two tables: an **ACTIVE** table (one current row per learner and
  node, updated in place) and an append-only **HISTORY** table (one row per genuine status advance,
  bounded by monotonicity). The PNG showed a single table per level.
- The `mastery_level_id` column the PNG drew on `StudentCompetencyCriteriaStatus` is **not** part of
  the model (mastery level is a future rule type; see ADR 0002).
- Learner-status tables use 64-bit primary keys and are reachable through a dedicated database alias
  (defaulting to the main database). Their references to the definition tables and to the user are
  **logical foreign keys without database-level constraints**, so the tables can live in a separate
  database (see ADR 0005).

Existing tagging tables (`oel_tagging_*`) are shown minimally, only enough to anchor the
relationships they participate in.

```mermaid
erDiagram
    oel_tagging_taxonomy ||--|| CompetencyTaxonomy : "MTI subtype"
    oel_tagging_taxonomy ||--o{ oel_tagging_tag : "taxonomy_id"
    oel_tagging_tag ||--o{ oel_tagging_objecttag : "tag_id"
    CompetencyTaxonomy ||--o{ CompetencyRuleProfile : "scopes"
    oel_tagging_tag ||--o{ CompetencyCriteriaGroup : "competency"
    CompetencyCriteriaGroup ||--o{ CompetencyCriteriaGroup : "parent_id (nesting)"
    CompetencyCriteriaGroup ||--o{ CompetencyCriteria : "contains"
    oel_tagging_objecttag ||--o{ CompetencyCriteria : "association"
    CompetencyRuleProfile ||--o{ CompetencyCriteria : "default rule"
    CompetencyCriteria ||--o{ StudentCompetencyCriteriaStatus : "leaf ACTIVE (no DB FK)"
    StudentCompetencyCriteriaStatus ||--o{ StudentCompetencyCriteriaStatusHistory : "advances (same key)"
    CompetencyCriteriaGroup ||--o{ StudentCompetencyCriteriaGroupStatus : "group ACTIVE (no DB FK)"
    StudentCompetencyCriteriaGroupStatus ||--o{ StudentCompetencyCriteriaGroupStatusHistory : "advances (same key)"
    oel_tagging_tag ||--o{ StudentCompetencyStatus : "competency ACTIVE (no DB FK)"
    StudentCompetencyStatus ||--o{ StudentCompetencyStatusHistory : "advances (same key)"
    CompetencyMasteryStatuses ||--o{ StudentCompetencyCriteriaStatus : "status_id"
    CompetencyMasteryStatuses ||--o{ StudentCompetencyCriteriaGroupStatus : "status_id"
    CompetencyMasteryStatuses ||--o{ StudentCompetencyStatus : "status_id"

    oel_tagging_taxonomy {
        int id PK
    }
    oel_tagging_tag {
        int id PK
        int taxonomy_id FK
    }
    oel_tagging_objecttag {
        int id PK
        int tag_id FK
    }
    CompetencyTaxonomy {
        int taxonomy_ptr_id PK "1:1 to oel_tagging_taxonomy"
    }
    CompetencyRuleProfile {
        int id PK
        int organization_id "nullable scope"
        varchar course_id "nullable scope"
        int competency_taxonomy_id FK "nullable scope"
        varchar rule_type "Grade (View, MasteryLevel future)"
        json rule_payload
    }
    CompetencyCriteriaGroup {
        int id PK
        int parent_id FK "self; null = root"
        int oel_tagging_tag_id FK "competency"
        varchar course_id "nullable course scope"
        varchar name
        int ordering
        varchar logic_operator "AND / OR / null"
    }
    CompetencyCriteria {
        int id PK
        int competency_criteria_group_id FK
        int oel_tagging_objecttag_id FK
        int competency_rule_profile_id FK "nullable"
        varchar rule_type_override "nullable"
        json rule_payload_override "nullable"
    }
    CompetencyMasteryStatuses {
        int id PK
        varchar status UK "Demonstrated / AttemptedNotDemonstrated / PartiallyAttempted"
    }
    StudentCompetencyCriteriaStatus {
        bigint id PK "64-bit"
        int user_id "logical FK, no DB constraint"
        int competency_criteria_id "logical FK, no DB constraint"
        int status_id FK "leaf: Demonstrated / AttemptedNotDemonstrated"
        datetime effective_source_timestamp
        datetime created
        datetime modified
    }
    StudentCompetencyCriteriaStatusHistory {
        bigint id PK "64-bit"
        int user_id
        int competency_criteria_id
        int status_id FK
        datetime effective_source_timestamp
        datetime created "one row per advance"
    }
    StudentCompetencyCriteriaGroupStatus {
        bigint id PK "64-bit"
        int user_id "logical FK, no DB constraint"
        int competency_criteria_group_id "logical FK, no DB constraint"
        int status_id FK
        datetime effective_source_timestamp
        datetime created
        datetime modified
    }
    StudentCompetencyCriteriaGroupStatusHistory {
        bigint id PK "64-bit"
        int user_id
        int competency_criteria_group_id
        int status_id FK
        datetime effective_source_timestamp
        datetime created "one row per advance"
    }
    StudentCompetencyStatus {
        bigint id PK "64-bit"
        int user_id "logical FK, no DB constraint"
        int oel_tagging_tag_id "logical FK, no DB constraint"
        int status_id FK "Demonstrated / PartiallyAttempted only"
        datetime effective_source_timestamp
        datetime created
        datetime modified
    }
    StudentCompetencyStatusHistory {
        bigint id PK "64-bit"
        int user_id
        int oel_tagging_tag_id
        int status_id FK
        datetime effective_source_timestamp
        datetime created "one row per advance"
    }
```

Notes on the diagram:

- Each ACTIVE table is **unique on `(user_id, node_id)`** (one current row per learner and node):
  `(user_id, competency_criteria_id)`, `(user_id, competency_criteria_group_id)`, and
  `(user_id, oel_tagging_tag_id)` respectively. HISTORY tables have no such uniqueness; they carry
  one row per advance.
- The ACTIVE-to-HISTORY relationship is drawn as one-to-many but is not a literal foreign key: a
  HISTORY row shares the ACTIVE row's `(user_id, node_id)` identity rather than pointing at its
  primary key.
- `status_id` references the shared `CompetencyMasteryStatuses` lookup so status semantics live in
  one place.
