.. _openedx-learning-adr-0003:

3. How should versioning be handled for CBE competency achievement criteria?
=============================================================================

Context
-------
Course Authors and/or Platform Administrators will be entering the competency achievement criteria rules in Studio that learners are required to meet in order to demonstrate competencies. Depending on the institution, these Course Authors or Platform Administrators may have a variety of job titles, including Instructional Designer, Curriculum Designer, Instructor, LMS Administrator, Faculty, or other Staff.

Typically, only one person would be responsible for entering competency achievement criteria rules in Studio for each course, though this person may change over time. However, entire programs could have many different Course Authors or Platform Administrators with this responsibility.

Typically, institutions and instructional designers do not change the mastery requirements (competency achievement criteria) for their competencies frequently over time. However, the ability to do historical audit logging of changes within Studio can be a valuable feature to those who have mistakenly made changes and want to revert or those who want to experiment with new approaches.

Currently, Open edX always displays the latest edited version of content in the Studio UI and always shows the latest published version of content in the LMS UI, despite having more robust version tracking on the backend (Publishable Entities).

Authoring data (criteria definitions) and runtime learner data (status) have different governance needs. The former is long-lived and typically non-PII, while the latter is user-specific, can be large (learners x criteria/competencies x time), and may require stricter retention and access controls. These differing lifecycles can make deep coupling of authoring and runtime data harder to manage at scale. Performance is also a consideration as computing or resolving versioned criteria for large courses could add overhead in Studio authoring screens or LMS views.

Decision
--------
For the initial implementation, versioning and traceability of competency achievement criteria will be handled with a combination of model history and lifecycle guardrails:

1. Apply ``django-simple-history`` to competency criteria definition moodels/tables:

   - ``CompetencyCriteriaGroup``
   - ``CompetencyCriteria``
   - ``CompetencyRuleProfile``

   This provides historical row snapshots and audit metadata for authored criteria definitions, without adopting the full publishable framework for this phase.

2. Do not apply ``django-simple-history`` to ``oel_tagging_tag``, ``oel_tagging_taxonomy``, or ``CompetencyTaxonomy`` in this phase.

   These models are treated as non-evaluative display/metadata for competency criteria purposes; edits to names or metadata in these tables are not intended to change evaluation outcomes.

3. ``oel_tagging_objecttag`` associations used by competency criteria follow post-use archive rules:

   - Before any related learner status exists, edits and deletes are allowed.
   - After any related learner status exists, disassociation/deletion is archive-only (soft delete), not hard delete.
   - Archived rows remain queryable so learner status records can continue to be traced back to their source association.

4. Authoring guardrails must warn on potentially impactful edits:

   - If a user edits competency criteria definitions or competency object/tag associations after related learner status exists, Studio must display an explicit warning that student statuses have already been set. Such edits are monotonic for the learner (:ref:`openedx-learning-adr-0005`): they never lower or revoke an existing status; a structural edit triggers a recompute that can only raise statuses (for example, eased criteria a learner already meets); and a rule or threshold change takes effect only going forward, as new grades arrive.
   - Applying these changes requires explicit user confirmation.

5. Learner status is stored as an ACTIVE table plus an append-only HISTORY table, using explicit tables rather than ``django-simple-history``:

   - For each level (``StudentCompetencyCriteriaStatus``, ``StudentCompetencyCriteriaGroupStatus``, and ``StudentCompetencyStatus``), an ACTIVE table holds one current row per learner and node, updated in place, and a parallel append-only HISTORY table records one row per genuine status advance. The columns are defined in :ref:`openedx-learning-adr-0002`, Decision 6.
   - Current status is a direct lookup on the ACTIVE row, not the most recent of many rows.
   - A HISTORY row is written only when a status advances. Because mastery is monotonic and advance-only (:ref:`openedx-learning-adr-0005`), the number of advances per node is bounded by the status lattice, so HISTORY grows with learners and nodes, not with time, and still supports point-in-time reconstruction: the status at any past time is the latest advance at or before that time.
   - These tables do not use ``django-simple-history``. The advance-only HISTORY is written explicitly by the recorder (:ref:`openedx-learning-adr-0004`) only on a genuine advance, and the recorder writes the ACTIVE and HISTORY tables in bulk. ``django-simple-history`` snapshots on every ``save`` and is oriented to per-row saves, which fits neither the advance-only bounding nor the bulk write path, and it adds history-metadata columns and per-save signal overhead this decision does not want on billion-row tables. It remains the right tool for the low-volume definition tables in Decision 1.


Rejected Alternatives
---------------------

1. Defer competency achievement criteria versioning for the initial implementation. Store only the latest authored criteria and expose the latest published state in the LMS, consistent with current Studio/LMS behavior.
    - Pros:
        - Keeps the initial implementation lightweight
    - Cons:
        - There is no built-in rollback or audit history
        - Adding versioning later will require data migration and careful choices about draft vs published defaults
2. Each model indicates version, status, and audit fields
    - Pros:
        - Simple and familiar pattern (version + status + created/updated metadata)
        - Straightforward queries for the current published state
        - Can support rollback by marking an earlier version as published
        - Stable identifiers (original_ids) can anchor versions and ease potential future migrations
    - Cons:
        - Requires custom conventions for versioning across related tables and nested groups
        - Lacks shared draft/publish APIs and immutable version objects that other authoring apps can reuse
        - Not necessarily consistent with existing patterns in the codebase (though these are already not overly consistent).
3. Publishable framework in openedx-learning
    - Pros:
        - First-class draft/published semantics with immutable historical versions
        - Consistent APIs and patterns shared across other authoring apps
    - Cons:
        - Requires modeling criteria/groups as publishable entities and wiring Studio/LMS workflows to versioning APIs
        - Adds schema and migration complexity for a feature that does not yet require full versioning
4. Append-only audit log table (event history)
    - Pros:
        - Lightweight way to capture who changed what and when
        - Enables basic rollback by replaying or reversing events
    - Cons:
        - Requires custom tooling to reconstruct past versions
        - Does not align with existing publishable versioning patterns
5. Append-only learner status only, with current status as the most recent row (the earlier form of this decision, before the ACTIVE/HISTORY split).
    - Pros:
        - No in-place mutation; a single table per level.
    - Cons:
        - Current-status reads must resolve the latest of several rows instead of a single-row lookup, on the dashboard hot path.
        - There is no in-place current row to anchor per-learner concurrency (:ref:`openedx-learning-adr-0004`).
6. Use ``django-simple-history`` for the learner status tables (as Decision 1 does for definition tables).
    - Pros:
        - Automatic shadow-table history with no hand-written HISTORY writes, consistent with the definition tables.
    - Cons:
        - It snapshots on every ``save``, so it cannot express advance-only HISTORY without extra suppression logic, and it does not fit the recorder's bulk write path.
        - It adds history-metadata columns and per-save signal overhead that this decision avoids on billion-row tables.
