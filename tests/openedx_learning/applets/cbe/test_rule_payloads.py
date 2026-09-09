"""
Tests for the CBE rule payload contract: RuleType and validate_rule_payload.

These need no database. validate_rule_payload is a plain function over a dict, and the models
that call it from clean() arrive in later changes, so every behavior here is exercised directly
rather than through a model save.

ADR-0002 Decision 3 defines one payload shape per rule_type. The single supported type is
"Grade", whose payload is {"op": ..., "value": ..., "scale": ...} where op is one of gte, lte or
eq, value is a fraction from 0.0 to 1.0 rather than a number out of 100, and scale is "percent".
"""
import pytest
from django.core.exceptions import ValidationError

# Private: the payload-spec registry, compared against RuleType's declared choices below.
from openedx_learning.applets.cbe.rule_payloads import _RULE_PAYLOAD_SPECS, RuleType, validate_rule_payload

_GRADE_PAYLOAD = {"op": "gte", "value": 0.8, "scale": "percent"}


def test_a_well_formed_grade_payload_is_accepted() -> None:
    """A Grade payload with a valid op, a fraction value, and the percent scale raises nothing."""
    validate_rule_payload(RuleType.GRADE, _GRADE_PAYLOAD)


@pytest.mark.parametrize(
    "op",
    [pytest.param("gte", id="gte"), pytest.param("lte", id="lte"), pytest.param("eq", id="eq")],
)
def test_every_documented_comparison_operator_is_accepted(op: str) -> None:
    """All three operators ADR-0002 Decision 3 lists are accepted, not just the seeded gte."""
    validate_rule_payload(RuleType.GRADE, {**_GRADE_PAYLOAD, "op": op})


@pytest.mark.parametrize(
    "value",
    [pytest.param(0.0, id="lower_bound"), pytest.param(1.0, id="upper_bound"), pytest.param(1, id="int_one")],
)
def test_the_ends_of_the_zero_to_one_range_are_accepted(value: float) -> None:
    """0.0 and 1.0 are both inside the range, and an int is a number as far as this rule cares."""
    validate_rule_payload(RuleType.GRADE, {**_GRADE_PAYLOAD, "value": value})


# One (rule_type, payload) pair per way ADR-0002 Decision 3 says a rule_payload can be invalid.
_INVALID_PAYLOADS = [
    pytest.param(RuleType.GRADE, {"op": "startswith", "value": 0.8, "scale": "percent"}, id="bad_op"),
    pytest.param(RuleType.GRADE, {"op": "gte", "value": 80, "scale": "percent"}, id="value_80_not_0_8"),
    pytest.param(RuleType.GRADE, {"op": "gte", "value": 1.5, "scale": "percent"}, id="value_above_range"),
    pytest.param(RuleType.GRADE, {"op": "gte", "value": -0.1, "scale": "percent"}, id="value_below_range"),
    pytest.param(RuleType.GRADE, {"op": "gte", "scale": "percent"}, id="missing_key"),
    pytest.param(RuleType.GRADE, {**_GRADE_PAYLOAD, "extra": 1}, id="extra_key"),
    pytest.param(RuleType.GRADE, ["not", "a", "dict"], id="non_dict"),
    pytest.param(RuleType.GRADE, {"op": "gte", "value": 0.8, "scale": "raw"}, id="wrong_scale"),
    pytest.param(RuleType.GRADE, {"op": "gte", "value": True, "scale": "percent"}, id="boolean_value"),
    # "View" is a plain string, not RuleType.VIEW: RuleType declares only rule types that have a
    # payload spec, so an unsupported rule type is by construction not a RuleType member at all.
    pytest.param("View", _GRADE_PAYLOAD, id="unsupported_rule_type"),
]


@pytest.mark.parametrize("rule_type, payload", _INVALID_PAYLOADS)
def test_every_documented_way_a_payload_can_be_wrong_raises_validation_error(
    rule_type: str, payload: object
) -> None:
    """
    Each invalid shape raises ValidationError rather than passing or raising something the caller
    would not expect: a bad op, a value given out of 100 instead of as a fraction, a value outside
    the range at either end, a missing or extra key, a non-dict payload, a wrong scale, a boolean
    masquerading as a number, and a rule_type with no defined payload shape.
    """
    with pytest.raises(ValidationError):
        validate_rule_payload(rule_type, payload)


def test_a_boolean_value_is_rejected_even_though_python_calls_it_an_int() -> None:
    """
    True is rejected. isinstance(True, int) is True in Python, so a bool would slip through a
    plain numeric check, and True would then read as the fraction 1.0, silently meaning "100%".
    """
    with pytest.raises(ValidationError):
        validate_rule_payload(RuleType.GRADE, {**_GRADE_PAYLOAD, "value": True})


def test_an_out_of_range_value_message_names_the_fraction_convention() -> None:
    """
    The message for a value given out of 100 (for example 80) names the 0.0 to 1.0 fraction
    convention, so an author who wrote 80 meaning 80% is told what to write instead. This is the
    single most likely authoring mistake for this payload.
    """
    with pytest.raises(ValidationError) as exc_info:
        validate_rule_payload(RuleType.GRADE, {"op": "gte", "value": 80, "scale": "percent"})

    assert "fraction between 0.0 and 1.0" in " ".join(exc_info.value.messages)


def test_a_wrong_keys_message_names_the_offending_keys() -> None:
    """
    The message for a wrong key set names both what is missing and what is unexpected, so an
    author can see which key to fix rather than being told only that the payload is invalid.
    """
    with pytest.raises(ValidationError) as exc_info:
        validate_rule_payload(RuleType.GRADE, {"op": "gte", "extra": 1})

    message = " ".join(exc_info.value.messages)
    assert "extra" in message
    assert "value" in message and "scale" in message


def test_an_unsupported_rule_type_says_only_grade_is_defined() -> None:
    """
    A rule_type with no payload shape is rejected with a message saying so, rather than being
    silently accepted. ADR-0002 Decision 3 lists View and MasteryLevel as future types; neither
    has a defined shape yet.
    """
    with pytest.raises(ValidationError) as exc_info:
        validate_rule_payload("MasteryLevel", {"level": 3})

    assert "not supported yet" in " ".join(exc_info.value.messages)


def test_rule_type_declares_exactly_the_types_that_have_a_payload_spec() -> None:
    """
    RuleType's choices, which is what a serializer or admin form offers an author, contain exactly
    the rule types that can actually be saved.

    A rule_type with no payload spec is always rejected regardless of payload content, so
    declaring a RuleType member without its spec would offer an author a dead-end choice. This
    pins the invariant so adding one without the other fails a test instead of shipping.
    """
    assert {value for value, _label in RuleType.choices} == set(_RULE_PAYLOAD_SPECS)
