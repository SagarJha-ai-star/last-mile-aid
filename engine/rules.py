"""
Safety and compatibility rules.

The rule engine hard-blocks assignments that violate safety constraints
and returns a human-readable reason for every block.

Each rule is a callable:
    (batch_tags, shelter_capabilities) → (allowed: bool, reason: str)

To add a new rule, define a function and append it to SAFETY_RULES.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

# Type alias for a rule function
RuleFn = Callable[[list[str], list[str]], tuple[bool, str]]


@dataclass
class RuleResult:
    """Result of running all safety rules on a proposed match."""
    allowed: bool
    reasons: list[str]   # non-empty only when blocked


# ---------------------------------------------------------------------------
# Individual rules
# ---------------------------------------------------------------------------

def rule_infant_formula_needs_clean_water(
    batch_tags: list[str],
    shelter_capabilities: list[str],
) -> tuple[bool, str]:
    """Infant formula must go to a site with clean water."""
    if "infant_formula" in batch_tags and "clean_water" not in shelter_capabilities:
        return False, "Infant formula requires a shelter with clean water supply"
    return True, ""


def rule_refrigeration_required(
    batch_tags: list[str],
    shelter_capabilities: list[str],
) -> tuple[bool, str]:
    """Perishable dairy/meat needs refrigeration at the shelter."""
    needs_fridge = {"dairy", "raw_meat", "frozen"}
    if needs_fridge & set(batch_tags) and "refrigeration" not in shelter_capabilities:
        items = needs_fridge & set(batch_tags)
        return False, (
            f"Items tagged {items} require a shelter with refrigeration"
        )
    return True, ""


def rule_allergen_isolation(
    batch_tags: list[str],
    shelter_capabilities: list[str],
) -> tuple[bool, str]:
    """Nut-containing items should not go to a nut-free shelter."""
    if "contains_nuts" in batch_tags and "nut_free_zone" in shelter_capabilities:
        return False, (
            "Items containing nuts cannot be sent to a nut-free shelter"
        )
    return True, ""


# ---------------------------------------------------------------------------
# Rule registry
# ---------------------------------------------------------------------------

SAFETY_RULES: list[RuleFn] = [
    rule_infant_formula_needs_clean_water,
    rule_refrigeration_required,
    rule_allergen_isolation,
]


# ---------------------------------------------------------------------------
# Engine
# ---------------------------------------------------------------------------

def check_safety(
    batch_tags: list[str],
    shelter_capabilities: list[str],
    rules: list[RuleFn] | None = None,
) -> RuleResult:
    """
    Run every registered safety rule against a proposed match.

    Returns a RuleResult with `allowed=False` and a list of human-readable
    reasons if **any** rule blocks the match.
    """
    rules = rules if rules is not None else SAFETY_RULES
    blocked_reasons: list[str] = []

    for rule in rules:
        allowed, reason = rule(batch_tags, shelter_capabilities)
        if not allowed:
            blocked_reasons.append(reason)

    return RuleResult(
        allowed=len(blocked_reasons) == 0,
        reasons=blocked_reasons,
    )
