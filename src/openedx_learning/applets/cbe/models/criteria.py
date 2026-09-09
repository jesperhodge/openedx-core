"""
The CompetencyAchievementCriteria tree: CompetencyCriteriaGroup, the internal AND/OR node.

See :ref:`openedx-learning-adr-0002` Decision 2 for the design and Decision 7 for why every
foreign key here cascades, and :ref:`openedx-learning-adr-0003` Decisions 1 and 2 for why this
model carries ``django-simple-history`` tracking and CompetencyTaxonomy does not.
"""
from __future__ import annotations

from django.db import models
from django.utils.translation import gettext_lazy as _
from simple_history.models import HistoricalRecords

from openedx_catalog.models import CourseRun
from openedx_django_lib.fields import case_insensitive_char_field, immutable_uuid_field
from openedx_tagging.models import Tag

__all__ = [
    "CompetencyCriteriaGroup",
    "LogicOperator",
]


class LogicOperator(models.TextChoices):
    """How a CompetencyCriteriaGroup combines its child nodes."""

    AND = "AND", _("And")
    OR = "OR", _("Or")


class CompetencyCriteriaGroup(models.Model):
    """
    An internal AND/OR node in a CompetencyAchievementCriteria expression tree.

    A single CompetencyAchievementCriteria is one root CompetencyCriteriaGroup plus all of its
    descendant groups and leaf :class:`CompetencyCriterion` rows. ``logic_operator`` says how
    this group's own children combine. ``ordering`` gives this group's own position among its
    siblings under their shared parent, which read-time evaluation and event-driven recomputation
    rely on for deterministic, short-circuiting evaluation order. A group's children can be a mix
    of child groups and leaf criteria, and only CompetencyCriteriaGroup carries an ``ordering``
    field, so that mix has no total order; #641 accepts this deliberately. See ADR-0002 Decision 2.

    .. no_pii:
    """

    uuid = immutable_uuid_field()
    parent = models.ForeignKey(
        "self",
        null=True,
        blank=True,
        on_delete=models.CASCADE,
        related_name="child_groups",
        help_text=_("The parent CompetencyCriteriaGroup. Null means this group is a tree root."),
    )
    tag = models.ForeignKey(
        Tag,
        db_column="oel_tagging_tag_id",
        on_delete=models.CASCADE,
        related_name="competency_criteria_groups",
        help_text=_("The competency (tag) that this criteria tree evaluates mastery of."),
    )
    course = models.ForeignKey(
        CourseRun,
        null=True,
        blank=True,
        on_delete=models.CASCADE,
        related_name="competency_criteria_groups",
        help_text=_("The course run that scopes this criteria tree for evaluation windowing, if any."),
    )
    name = case_insensitive_char_field(
        max_length=255, blank=True, default="", help_text=_("A human-readable label for this group, if any.")
    )
    ordering = models.PositiveIntegerField(
        default=0,
        help_text=_(
            "Deterministic sibling evaluation sequence. Used to short-circuit evaluation and to order "
            "child scans during event-driven recomputation."
        ),
    )
    logic_operator = models.CharField(
        max_length=3,
        choices=LogicOperator,
        null=True,
        blank=True,
        help_text=_(
            "How this group's children combine. Null only for a group with a single child, where combining "
            "logic is moot; the application layer treats null the same as OR."
        ),
    )

    history = HistoricalRecords()

    class Meta:
        indexes = [
            # ADR-0002 Decision 5, index 1: lookups by competency tag and course scope.
            models.Index(fields=["tag", "course"]),
            # ADR-0002 Decision 5 also lists an index on `parent` (index 2), but Django already
            # indexes every ForeignKey column by default, so a second explicit one here would only
            # cost write throughput without adding any read benefit.
        ]
        # No constraint tying `logic_operator` to child count, and no UniqueConstraint on (parent,
        # ordering): a child group cannot be saved until its parent's primary key exists, so
        # neither has a single-row state to check at save time. See ADR-0002 Decision 2.
