"""
Delete-behavior tests for the three CompetencyAchievementCriteria models.

Every test that deletes a row another row points at lives here. Each model declares its own
``on_delete`` values in the change that adds it; this module is where the behavior those values
produce, especially across more than one model, is pinned.

ADR-0002 Decision 7 in one sentence: ``on_delete`` expresses containment rather than protection.
It governs deletion of the row a foreign key points *at*, never the row holding it, so the seven
cascading edges are how Django's collector walks *down* the tree, and the two remaining edges are
what refuse a delete outright.

| Foreign key | Value | Why |
| CompetencyCriteriaGroup.parent | CASCADE | a subtree is meaningless without its parent |
| CompetencyCriteriaGroup.tag | CASCADE | a criteria tree is meaningless without its competency |
| CompetencyCriteriaGroup.course | CASCADE | a course-scoped tree is meaningless without its run |
| CompetencyRuleProfile.organization | PROTECT | an Organization is not a competency record |
| CompetencyRuleProfile.course | CASCADE | a course-scoped profile goes with its run |
| CompetencyRuleProfile.competency_taxonomy | CASCADE | a taxonomy-scoped profile goes with its taxonomy |
| CompetencyCriterion.group | CASCADE | a leaf is meaningless without its group |
| CompetencyCriterion.object_tag | CASCADE | a leaf is meaningless without its content association |
| CompetencyCriterion.rule_profile | RESTRICT | a profile is never hard-deleted out from under a leaf |

``rule_profile`` is RESTRICT rather than PROTECT because the two differ exactly where it matters
here. Both refuse a direct profile delete while a criterion is assigned to it. Only RESTRICT
ignores referencing rows that the same operation is already deleting, which is what lets a scope
owner's deletion carry its profile away instead of failing on a criterion that delete was about to
remove anyway.

Only the cascade half of each case is asserted. Every matching "raises ProtectedError because a
learner status row exists" case needs #642's three Student*Status tables, and #642 is the change
that creates them, so those assertions belong there. Nothing here stubs or fakes a status model
to stand in for them. Until #642 merges, main carries a cascade chain with no PROTECT at the
bottom, so deleting a tag removes the whole authored tree and nothing objects. That window is
expected and harmless, because the learner status tables do not exist yet.

Fixtures live in this directory's conftest.py.
"""
import pytest
from django.apps import apps
from django.db import connection
from django.db.models import ProtectedError, RestrictedError
from organizations.models import Organization

from openedx_catalog.models import CourseRun
from openedx_learning.models import (
    CompetencyCriteriaGroup,
    CompetencyCriterion,
    CompetencyRuleProfile,
    CompetencyTaxonomy,
    RuleType,
)
from openedx_tagging.models import ObjectTag, Tag

pytestmark = pytest.mark.django_db

_GRADE_PAYLOAD = {"op": "gte", "value": 0.8, "scale": "percent"}


# ---------------------------------------------------------------------------------------------
# CompetencyCriteriaGroup's three foreign keys
# Each CASCADE test asserts the referencing row existed beforehand and is gone afterward, not
# merely that no exception was raised.


# ---------------------------------------------------------------------------------------------


def test_deleting_a_group_also_deletes_its_child_groups(tag: Tag) -> None:
    """
    Deleting a CompetencyCriteriaGroup cascades to any child group referencing it via `parent`:
    the delete succeeds and the child row is gone too.
    """
    root = CompetencyCriteriaGroup.objects.create(tag=tag)
    child = CompetencyCriteriaGroup.objects.create(tag=tag, parent=root)
    assert CompetencyCriteriaGroup.objects.filter(pk=child.pk).exists()

    root.delete()

    assert not CompetencyCriteriaGroup.objects.filter(pk=root.pk).exists()
    assert not CompetencyCriteriaGroup.objects.filter(pk=child.pk).exists()


def test_deleting_a_tag_also_deletes_its_competency_criteria_groups(tag: Tag, group: CompetencyCriteriaGroup) -> None:
    """
    Deleting a Tag cascades to any CompetencyCriteriaGroup referencing it via `tag`: the delete
    succeeds and the group row is gone. Also confirms django-simple-history records the cascaded
    removal as its own historical row (history_type='-'), not silently: an author or auditor
    reviewing history for a group that vanished this way still finds why it did.
    """
    assert CompetencyCriteriaGroup.objects.filter(pk=group.pk).exists()
    group_pk = group.pk

    tag.delete()

    assert not CompetencyCriteriaGroup.objects.filter(pk=group_pk).exists()

    historical_group = apps.get_model("openedx_learning", "HistoricalCompetencyCriteriaGroup")
    assert historical_group.objects.filter(id=group_pk, history_type="-").exists()


def test_deleting_a_course_run_also_deletes_its_course_scoped_criteria_groups(
    tag: Tag, course_run: CourseRun
) -> None:
    """
    Deleting a CourseRun cascades to any CompetencyCriteriaGroup scoped to it via `course`: the
    delete succeeds and the group row is gone too. A course-scoped criteria tree has no meaning
    once the course run it evaluates against no longer exists.
    """
    group = CompetencyCriteriaGroup.objects.create(tag=tag, course=course_run)
    assert CompetencyCriteriaGroup.objects.filter(pk=group.pk).exists()

    course_run.delete()

    assert not CompetencyCriteriaGroup.objects.filter(pk=group.pk).exists()


def test_a_cascaded_group_removal_is_recorded_in_history(tag: Tag) -> None:
    """
    A group removed by a cascade, rather than by a direct delete, still gets its own historical
    row with history_type '-'. An author or auditor reviewing history for a group that vanished
    this way still finds why it did.
    """
    historical_group = apps.get_model("openedx_learning", "HistoricalCompetencyCriteriaGroup")
    group = CompetencyCriteriaGroup.objects.create(tag=tag)
    group_pk = group.pk

    tag.delete()

    assert historical_group.objects.filter(id=group_pk, history_type="-").exists()


# ---------------------------------------------------------------------------------------------
# CompetencyRuleProfile's three foreign keys
# A profile is never hard-deleted by a direct delete; retirement is an archive. That does not
# stop a profile being cascaded away with the course or taxonomy it is scoped to. The PROTECT and
# RESTRICT tests inspect the exception's collected objects rather than only catching the
# exception, because several such relationships can fire on one delete.


# ---------------------------------------------------------------------------------------------


def test_deleting_an_organization_with_a_scoped_profile_raises_protected_error_naming_the_profile(
    organization2: Organization,
) -> None:
    """
    Deleting an Organization that a CompetencyRuleProfile references via `organization` raises
    ProtectedError naming the profile.

    Uses `organization2`, which this test never attaches a CatalogCourse to, instead of
    `organization` (the one `course_run` uses elsewhere in this module): CatalogCourse.org is
    itself PROTECT, so deleting an organization with a CatalogCourse attached raises
    ProtectedError regardless of whether a CompetencyRuleProfile references it too, and this
    test would pass for the wrong reason.
    """
    profile = CompetencyRuleProfile.objects.create(
        organization=organization2, rule_type=RuleType.GRADE, rule_payload=_GRADE_PAYLOAD
    )

    with pytest.raises(ProtectedError) as exc_info:
        organization2.delete()

    protected = exc_info.value.protected_objects
    assert any(isinstance(obj, CompetencyRuleProfile) and obj.pk == profile.pk for obj in protected)


def test_deleting_a_course_run_with_a_scoped_rule_profile_also_deletes_the_profile(
    course_run: CourseRun,
) -> None:
    """
    Deleting a CourseRun cascades to any CompetencyRuleProfile scoped to it via `course`: the
    delete succeeds and the profile row is gone too. A CompetencyRuleProfile is never hard-deleted
    by a *direct* delete of the profile itself (ADR-0002 Decision 7); that does not stop it being
    cascaded away as a side effect of deleting the course it is scoped to, once nothing else (no
    CompetencyCriterion still assigned to it) protects it -- a course is only ever hard-deleted
    once nothing beneath it needs protecting.
    """
    profile = CompetencyRuleProfile.objects.create(
        course=course_run, rule_type=RuleType.GRADE, rule_payload=_GRADE_PAYLOAD
    )
    assert CompetencyRuleProfile.objects.filter(pk=profile.pk).exists()

    course_run.delete()

    assert not CompetencyRuleProfile.objects.filter(pk=profile.pk).exists()


def test_deleting_a_taxonomy_with_a_scoped_rule_profile_also_deletes_the_profile(
    competency_taxonomy: CompetencyTaxonomy,
) -> None:
    """
    Deleting a CompetencyTaxonomy cascades to any CompetencyRuleProfile scoped to it via
    `competency_taxonomy`: the delete succeeds and the profile row is gone too, as #641
    requires. A CompetencyRuleProfile is never hard-deleted by a *direct* delete of the profile itself
    (ADR-0002 Decision 7); that does not stop it being cascaded away as a side effect of deleting
    the taxonomy it is scoped to, once nothing else protects it. Nothing changes behaviorally in
    this MVP, since only the all-null system-default profile exists otherwise, so this scenario
    cannot arise until a taxonomy-scoped profile is actually created, which no authoring screen
    does yet. See the "residual tension" section below for what happens instead when a
    CompetencyCriterion is still assigned to the scoped profile being cascaded away.
    """
    profile = CompetencyRuleProfile.objects.create(
        competency_taxonomy=competency_taxonomy, rule_type=RuleType.GRADE, rule_payload=_GRADE_PAYLOAD
    )
    assert CompetencyRuleProfile.objects.filter(pk=profile.pk).exists()

    competency_taxonomy.delete()

    assert not CompetencyRuleProfile.objects.filter(pk=profile.pk).exists()


# ---------------------------------------------------------------------------------------------
# CompetencyCriterion's three foreign keys


# ---------------------------------------------------------------------------------------------


def test_deleting_a_group_also_deletes_its_criteria(
    group: CompetencyCriteriaGroup, object_tag: ObjectTag, default_rule_profile: CompetencyRuleProfile
) -> None:
    """
    Deleting a CompetencyCriteriaGroup cascades to any CompetencyCriterion referencing it via
    `group`: the delete succeeds and the criterion row is gone too.
    """
    criterion = CompetencyCriterion.objects.create(
        group=group, object_tag=object_tag, rule_profile=default_rule_profile
    )
    assert CompetencyCriterion.objects.filter(pk=criterion.pk).exists()

    group.delete()

    assert not CompetencyCriteriaGroup.objects.filter(pk=group.pk).exists()
    assert not CompetencyCriterion.objects.filter(pk=criterion.pk).exists()


def test_deleting_an_object_tag_also_deletes_its_criteria(
    group: CompetencyCriteriaGroup, object_tag: ObjectTag, default_rule_profile: CompetencyRuleProfile
) -> None:
    """
    Deleting an ObjectTag cascades to any CompetencyCriterion referencing it via `object_tag`: the
    delete succeeds and the criterion row is gone too. Doubles as the "OURS" half of #641's
    Deletions criterion for oel_tagging_objecttag, since ObjectTag has only this one hop down to
    CompetencyCriterion.
    """
    criterion = CompetencyCriterion.objects.create(
        group=group, object_tag=object_tag, rule_profile=default_rule_profile
    )
    assert CompetencyCriterion.objects.filter(pk=criterion.pk).exists()

    object_tag.delete()

    assert not CompetencyCriterion.objects.filter(pk=criterion.pk).exists()


def test_deleting_a_rule_profile_referenced_by_a_criterion_raises_restricted_error(
    group: CompetencyCriteriaGroup, object_tag: ObjectTag, default_rule_profile: CompetencyRuleProfile
) -> None:
    """
    Deleting a CompetencyRuleProfile that a CompetencyCriterion references via `rule_profile`
    raises RestrictedError, which is what holds ADR-0002 Decision 7's "a profile is never
    hard-deleted by a direct delete" at the ORM layer.

    Nothing cascades from a profile down to a criterion, so the criterion is not part of this
    delete and RESTRICT refuses, exactly as PROTECT would have.
    """
    criterion = CompetencyCriterion.objects.create(
        group=group, object_tag=object_tag, rule_profile=default_rule_profile
    )

    with pytest.raises(RestrictedError) as exc_info:
        default_rule_profile.delete()

    restricted = exc_info.value.restricted_objects
    assert any(isinstance(obj, CompetencyCriterion) and obj.pk == criterion.pk for obj in restricted)


# ---------------------------------------------------------------------------------------------
# Transitive deletes required by issue #641
# Deleting a Tag, a group at depth, or a Taxonomy takes the whole referencing criteria tree
# with it. Tag.taxonomy is already CASCADE in openedx_tagging, which is what makes the tag
# case hold transitively from a taxonomy.


# ---------------------------------------------------------------------------------------------


def test_tag_delete_with_no_status_cascades_whole_criteria_tree(
    tag: Tag, group: CompetencyCriteriaGroup, object_tag: ObjectTag, default_rule_profile: CompetencyRuleProfile
) -> None:
    """
    Deleting an oel_tagging.Tag with no learner status beneath it succeeds and cascades away
    every CompetencyCriteriaGroup and CompetencyCriterion that references it, transitively:
    Tag -> CompetencyCriteriaGroup.tag (CASCADE) -> CompetencyCriterion.group (CASCADE).
    """
    criterion = CompetencyCriterion.objects.create(
        group=group, object_tag=object_tag, rule_profile=default_rule_profile
    )
    assert CompetencyCriteriaGroup.objects.filter(pk=group.pk).exists()
    assert CompetencyCriterion.objects.filter(pk=criterion.pk).exists()

    tag.delete()

    assert not CompetencyCriteriaGroup.objects.filter(pk=group.pk).exists()
    assert not CompetencyCriterion.objects.filter(pk=criterion.pk).exists()


def test_group_delete_at_depth_cascades_descendants_and_their_criteria(
    tag: Tag, object_tag: ObjectTag, default_rule_profile: CompetencyRuleProfile
) -> None:
    """
    Deleting a CompetencyCriteriaGroup that is not a root removes it, every descendant group, and
    every CompetencyCriterion under any of them, while leaving the rest of the tree (here, the
    root) alone.

    Builds a genuinely nested tree, root -> child -> grandchild, with criteria at two different
    levels (on `child` and on `grandchild`), so "at depth" and "every descendant" both mean
    something: a shallower tree could pass this by accident.
    """
    root = CompetencyCriteriaGroup.objects.create(tag=tag)
    child = CompetencyCriteriaGroup.objects.create(tag=tag, parent=root)
    grandchild = CompetencyCriteriaGroup.objects.create(tag=tag, parent=child)
    child_criterion = CompetencyCriterion.objects.create(
        group=child, object_tag=object_tag, rule_profile=default_rule_profile
    )
    grandchild_criterion = CompetencyCriterion.objects.create(
        group=grandchild, object_tag=object_tag, rule_profile=default_rule_profile
    )
    assert CompetencyCriteriaGroup.objects.filter(pk=root.pk).exists()
    assert CompetencyCriteriaGroup.objects.filter(pk=child.pk).exists()
    assert CompetencyCriteriaGroup.objects.filter(pk=grandchild.pk).exists()
    assert CompetencyCriterion.objects.filter(pk=child_criterion.pk).exists()
    assert CompetencyCriterion.objects.filter(pk=grandchild_criterion.pk).exists()

    child.delete()

    assert CompetencyCriteriaGroup.objects.filter(pk=root.pk).exists()
    assert not CompetencyCriteriaGroup.objects.filter(pk=child.pk).exists()
    assert not CompetencyCriteriaGroup.objects.filter(pk=grandchild.pk).exists()
    assert not CompetencyCriterion.objects.filter(pk=child_criterion.pk).exists()
    assert not CompetencyCriterion.objects.filter(pk=grandchild_criterion.pk).exists()


def test_taxonomy_delete_cascades_every_tag_and_its_criteria(
    competency_taxonomy: CompetencyTaxonomy,
    tag: Tag,
    group: CompetencyCriteriaGroup,
    object_tag: ObjectTag,
    default_rule_profile: CompetencyRuleProfile,
) -> None:
    """
    Deleting an oel_tagging.Taxonomy collects every Tag beneath it (Tag.taxonomy is CASCADE), so
    the tag-deletion cases above hold transitively through a taxonomy delete too. This asserts the
    succeeding case (no learner status beneath the tag), which is what #641's Deletions criterion
    for taxonomy-level deletion requires "at minimum".

    Chain exercised: CompetencyTaxonomy -> Tag (CASCADE) -> CompetencyCriteriaGroup.tag (CASCADE)
    -> CompetencyCriterion.group (CASCADE).
    """
    criterion = CompetencyCriterion.objects.create(
        group=group, object_tag=object_tag, rule_profile=default_rule_profile
    )
    assert Tag.objects.filter(pk=tag.pk).exists()
    assert CompetencyCriteriaGroup.objects.filter(pk=group.pk).exists()
    assert CompetencyCriterion.objects.filter(pk=criterion.pk).exists()

    competency_taxonomy.delete()

    assert not Tag.objects.filter(pk=tag.pk).exists()
    assert not CompetencyCriteriaGroup.objects.filter(pk=group.pk).exists()
    assert not CompetencyCriterion.objects.filter(pk=criterion.pk).exists()


# ---------------------------------------------------------------------------------------------
# Scope-owner deletes that reach a profile a criterion is assigned to
# These are what RESTRICT on CompetencyCriterion.rule_profile buys, and what it still refuses.
# Both are unreachable until a taxonomy- or course-scoped profile can be authored, which no code
# path does yet. See ADR-0002 Decision 7.


# ---------------------------------------------------------------------------------------------


def test_taxonomy_delete_reaching_its_scoped_profile_through_a_criterion_succeeds(
    competency_taxonomy: CompetencyTaxonomy, group: CompetencyCriteriaGroup, object_tag: ObjectTag
) -> None:
    """
    Deleting a CompetencyTaxonomy whose taxonomy-scoped profile is itself assigned to a criterion
    succeeds, and takes the profile and the criterion with it.

    This is the case RESTRICT exists for. The delete reaches the profile through
    `competency_taxonomy` (CASCADE) and reaches the criterion through the tag chain
    (Tag -> CompetencyCriteriaGroup.tag -> CompetencyCriterion.group, all CASCADE). RESTRICT then
    finds nothing left restricting the profile, because the only row referencing it is one this
    same operation is already deleting. Under PROTECT this raised ProtectedError naming that
    criterion, which was a spurious failure: an author deleting a taxonomy was told a criterion
    was in the way, when nothing about that criterion survived the delete either.
    """
    profile = CompetencyRuleProfile.objects.create(
        competency_taxonomy=competency_taxonomy, rule_type=RuleType.GRADE, rule_payload=_GRADE_PAYLOAD
    )
    criterion = CompetencyCriterion.objects.create(group=group, object_tag=object_tag, rule_profile=profile)

    competency_taxonomy.delete()

    assert not CompetencyRuleProfile.objects.filter(pk=profile.pk).exists()
    assert not CompetencyCriterion.objects.filter(pk=criterion.pk).exists()
    assert not CompetencyCriteriaGroup.objects.filter(pk=group.pk).exists()


def test_course_run_delete_is_refused_by_a_criterion_outside_its_scope(
    course_run: CourseRun, group: CompetencyCriteriaGroup, object_tag: ObjectTag
) -> None:
    """
    Deleting a CourseRun whose course-scoped profile is assigned to a criterion that the same
    delete does NOT reach raises RestrictedError, and nothing is removed.

    A criterion's profile assignment is independent of its tree's `course` scope (ADR-0002
    Decision 4), so a criterion in a tree with `course=None`, which `group` is, can still be
    assigned a course-scoped profile. Deleting that run collects the profile but not the
    criterion, so RESTRICT correctly refuses: unlike the taxonomy case above, this criterion
    really would have been left pointing at a deleted profile. ADR-0002 Decision 7 records this
    as the residual case, whose fix is a fifth reassignment event on Decision 4.
    """
    profile = CompetencyRuleProfile.objects.create(
        course=course_run, rule_type=RuleType.GRADE, rule_payload=_GRADE_PAYLOAD
    )
    criterion = CompetencyCriterion.objects.create(group=group, object_tag=object_tag, rule_profile=profile)
    assert group.course is None

    with pytest.raises(RestrictedError) as exc_info:
        course_run.delete()

    restricted = exc_info.value.restricted_objects
    assert any(isinstance(obj, CompetencyCriterion) and obj.pk == criterion.pk for obj in restricted)
    # Nothing was removed: the whole operation raised before any DELETE executed.
    assert CompetencyRuleProfile.objects.filter(pk=profile.pk).exists()
    assert CompetencyCriterion.objects.filter(pk=criterion.pk).exists()
    assert CourseRun.objects.filter(pk=course_run.pk).exists()


# ---------------------------------------------------------------------------------------------
# MySQL collector semantics, reproduced on SQLite
# MySQL cannot defer foreign-key constraint checks, and Django's CASCADE handler reads that
# flag directly: it nulls a nullable cascading foreign key before the DELETE. On SQLite that
# nulling never happens, so the tests below monkeypatch the flag to reproduce it. Without the
# monkeypatch they pass against broken and correct code alike, so do not drop it. This is also
# why CompetencyRuleProfile.scope_code is a plain column rather than a GeneratedField: a
# generated column would recompute from the nulled scope foreign key mid-cascade and collide
# with whichever row already holds the resulting blank scope.


# ---------------------------------------------------------------------------------------------


def test_course_run_delete_cascades_its_course_scoped_criteria_group_under_mysql_collector_semantics(
    monkeypatch: pytest.MonkeyPatch, tag: Tag, course_run: CourseRun
) -> None:
    """
    Deleting a CourseRun with a course-scoped CompetencyCriteriaGroup succeeds and cascades the
    group away even under MySQL's non-deferred constraint semantics. `course` is one of the two
    nullable cascading foreign keys, so it shares the exact pre-delete-nulling collector path the
    taxonomy case below does; unlike scope_code,
    CompetencyCriteriaGroup carries no uniqueness constraint a null `course_id` could collide with,
    so this path is expected to just succeed. Pinned here anyway, alongside the taxonomy case,
    since a future fix to one foreign key without the other would otherwise go unnoticed.
    """
    monkeypatch.setattr(type(connection.features), "can_defer_constraint_checks", False, raising=False)
    group = CompetencyCriteriaGroup.objects.create(tag=tag, course=course_run)

    course_run.delete()

    assert not CompetencyCriteriaGroup.objects.filter(pk=group.pk).exists()


def test_taxonomy_delete_cascades_its_scoped_profile_under_mysql_collector_semantics(
    monkeypatch: pytest.MonkeyPatch, competency_taxonomy: CompetencyTaxonomy
) -> None:
    """
    Deleting a CompetencyTaxonomy with a taxonomy-scoped profile succeeds and cascades the profile
    away even under MySQL's non-deferred constraint semantics, the same as it does under ordinary
    SQLite semantics (see test_deleting_a_taxonomy_with_a_scoped_rule_profile_also_deletes_the_
    profile above). Nulling the profile's `competency_taxonomy_id` before deleting it leaves
    `scope_code` alone, so it cannot collide with the seeded system-default profile's identical
    blank scope and raise IntegrityError instead of completing the cascade.
    """
    monkeypatch.setattr(type(connection.features), "can_defer_constraint_checks", False, raising=False)
    profile = CompetencyRuleProfile.objects.create(
        competency_taxonomy=competency_taxonomy, rule_type=RuleType.GRADE, rule_payload=_GRADE_PAYLOAD
    )

    competency_taxonomy.delete()

    assert not CompetencyRuleProfile.objects.filter(pk=profile.pk).exists()


def test_course_run_delete_cascades_its_scoped_rule_profile_under_mysql_collector_semantics(
    monkeypatch: pytest.MonkeyPatch, course_run: CourseRun
) -> None:
    """
    Deleting a CourseRun with a course-scoped CompetencyRuleProfile succeeds and cascades the
    profile away even under MySQL's non-deferred constraint semantics, the same as the taxonomy
    case above: `course` is CompetencyRuleProfile's other newly-CASCADE foreign key, and shares the
    same pre-delete-nulling collector path and the same scope_code collision this fix removes.
    """
    monkeypatch.setattr(type(connection.features), "can_defer_constraint_checks", False, raising=False)
    profile = CompetencyRuleProfile.objects.create(
        course=course_run, rule_type=RuleType.GRADE, rule_payload=_GRADE_PAYLOAD
    )

    course_run.delete()

    assert not CompetencyRuleProfile.objects.filter(pk=profile.pk).exists()


def test_deleting_two_taxonomies_together_cascades_both_their_scoped_profiles_away(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """
    Deleting two CompetencyTaxonomy rows in one `.delete()` call, each with its own taxonomy-scoped
    profile, succeeds and cascades both profiles away -- neither profile's scope_code collides with
    the other's, even though both get their `competency_taxonomy_id` nulled in the same collector
    batch under MySQL's non-deferred constraint semantics.

    Same path as the single-taxonomy MySQL case above, but confirms it does not get worse when two
    scope owners are collected in the same collector pass: before scope_code became a plain column,
    nulling both profiles' `competency_taxonomy_id` in the same batch drove both scope_code values
    to the identical blank "org:,course:,taxonomy:" string and raised IntegrityError on whichever
    row the database processed second.
    """
    monkeypatch.setattr(type(connection.features), "can_defer_constraint_checks", False, raising=False)
    taxonomy1 = CompetencyTaxonomy.objects.create(name="Nursing Two Taxonomy Delete", export_id="nursing-two-del")
    taxonomy2 = CompetencyTaxonomy.objects.create(name="Welding Two Taxonomy Delete", export_id="welding-two-del")
    profile1 = CompetencyRuleProfile.objects.create(
        competency_taxonomy=taxonomy1, rule_type=RuleType.GRADE, rule_payload=_GRADE_PAYLOAD
    )
    profile2 = CompetencyRuleProfile.objects.create(
        competency_taxonomy=taxonomy2, rule_type=RuleType.GRADE, rule_payload=_GRADE_PAYLOAD
    )

    CompetencyTaxonomy.objects.filter(pk__in=[taxonomy1.pk, taxonomy2.pk]).delete()

    assert not CompetencyRuleProfile.objects.filter(pk__in=[profile1.pk, profile2.pk]).exists()
