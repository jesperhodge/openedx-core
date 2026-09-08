"""
Rule payload shapes for CBE evaluation rules, and the validator that checks a raw payload against
the shape its rule_type defines. See :ref:`openedx-learning-adr-0002` Decision 3 for the payload
contract. ``RuleType`` declares exactly the rule types with a shape defined here, so a rule type
can never be offered as a choice without also being saveable. These messages reach an API caller
or admin form, so they must not leak internal class or function names.
"""
from __future__ import annotations

from typing import Any, Callable

from django.core.exceptions import ValidationError
from django.db import models
from django.utils.translation import gettext_lazy as _

__all__ = [
    "RuleType",
    "validate_rule_payload",
]


class RuleType(models.TextChoices):
    """
    The evaluation rule types a CompetencyRuleProfile or CompetencyCriterion override can use.

    Declares exactly the rule types with a defined rule_payload shape below, i.e. exactly the keys
    of ``_RULE_PAYLOAD_SPECS``: see this module's own docstring for why the two are never allowed
    to drift apart.
    """

    GRADE = "Grade", _("Grade")


_GRADE_OPERATORS = {"gte", "lte", "eq"}


def _validate_grade_payload(payload: dict) -> None:
    """Validate a Grade payload's op, value, and scale. Keys are already checked."""
    if payload["op"] not in _GRADE_OPERATORS:
        raise ValidationError(_("The 'op' in a 'Grade' rule_payload must be one of: gte, lte, eq."))
    value = payload["value"]
    # isinstance(True, int) is True in Python, so a bool needs excluding explicitly.
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not 0.0 <= value <= 1.0:
        raise ValidationError(
            _(
                "The 'value' in a 'Grade' rule_payload must be a fraction between 0.0 and 1.0 inclusive "
                "(e.g. 0.8 for a passing grade of 80%%), not %(value)r."
            )
            % {"value": value}
        )
    if payload["scale"] != "percent":
        raise ValidationError(_("The 'scale' in a 'Grade' rule_payload must be 'percent'."))


# The required keys and validator for each rule type that has a defined payload shape. RuleType
# declares exactly these types, so a rule type can never be offered as a choice without being
# saveable. Adding one is an entry here, a validator, and the matching RuleType member.
_RULE_PAYLOAD_SPECS: dict[str, tuple[frozenset[str], Callable[[dict], None]]] = {
    RuleType.GRADE: (frozenset({"op", "value", "scale"}), _validate_grade_payload),
}


def validate_rule_payload(rule_type: str, payload: Any) -> None:
    """
    Raise ValidationError unless ``payload`` matches the shape ADR-0002 Decision 3 defines for
    ``rule_type``, including when ``rule_type`` has no defined shape at all.
    """
    spec = _RULE_PAYLOAD_SPECS.get(rule_type)
    if spec is None:
        raise ValidationError(
            _("Rule type '%(rule_type)s' is not supported yet; only 'Grade' has a defined rule_payload shape.")
            % {"rule_type": rule_type}
        )
    expected_keys, validate_values = spec
    if not isinstance(payload, dict):
        raise ValidationError(_("A '%(rule_type)s' rule_payload must be a JSON object.") % {"rule_type": rule_type})
    missing = sorted(expected_keys - payload.keys())
    unexpected = sorted(payload.keys() - expected_keys)
    if missing or unexpected:
        raise ValidationError(
            _("A '%(rule_type)s' rule_payload has the wrong keys: missing %(missing)s; unexpected %(unexpected)s.")
            % {
                "rule_type": rule_type,
                "missing": ", ".join(missing) or _("none"),
                "unexpected": ", ".join(unexpected) or _("none"),
            }
        )
    validate_values(payload)
