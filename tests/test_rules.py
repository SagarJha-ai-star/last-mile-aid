"""Unit tests for engine/rules.py — safety and compatibility rules."""

from engine.rules import (
    check_safety,
    rule_infant_formula_needs_clean_water,
    rule_refrigeration_required,
    rule_allergen_isolation,
    RuleResult,
)


class TestInfantFormulaRule:
    def test_blocked_without_clean_water(self):
        ok, reason = rule_infant_formula_needs_clean_water(
            ["infant_formula"], ["refrigeration"]
        )
        assert not ok
        assert "clean water" in reason.lower()

    def test_allowed_with_clean_water(self):
        ok, reason = rule_infant_formula_needs_clean_water(
            ["infant_formula"], ["clean_water", "refrigeration"]
        )
        assert ok
        assert reason == ""

    def test_irrelevant_tags_pass(self):
        ok, _ = rule_infant_formula_needs_clean_water(
            ["rice"], ["refrigeration"]
        )
        assert ok


class TestRefrigerationRule:
    def test_dairy_blocked_without_fridge(self):
        ok, reason = rule_refrigeration_required(
            ["dairy"], ["clean_water"]
        )
        assert not ok
        assert "refrigeration" in reason.lower()

    def test_dairy_allowed_with_fridge(self):
        ok, _ = rule_refrigeration_required(
            ["dairy"], ["refrigeration"]
        )
        assert ok

    def test_frozen_blocked(self):
        ok, _ = rule_refrigeration_required(
            ["frozen"], []
        )
        assert not ok

    def test_non_perishable_passes(self):
        ok, _ = rule_refrigeration_required(
            ["rice", "canned_beans"], []
        )
        assert ok


class TestAllergenRule:
    def test_nuts_blocked_at_nut_free_zone(self):
        ok, reason = rule_allergen_isolation(
            ["contains_nuts"], ["nut_free_zone"]
        )
        assert not ok
        assert "nut" in reason.lower()

    def test_nuts_allowed_at_normal_shelter(self):
        ok, _ = rule_allergen_isolation(
            ["contains_nuts"], ["clean_water"]
        )
        assert ok

    def test_no_nuts_at_nut_free_zone_passes(self):
        ok, _ = rule_allergen_isolation(
            ["rice"], ["nut_free_zone"]
        )
        assert ok


class TestCheckSafety:
    def test_all_rules_pass(self):
        result = check_safety(["rice"], ["clean_water", "refrigeration"])
        assert result.allowed
        assert result.reasons == []

    def test_single_block(self):
        result = check_safety(["infant_formula"], [])
        assert not result.allowed
        assert len(result.reasons) >= 1

    def test_multiple_blocks(self):
        # infant formula + contains_nuts → nut_free_zone shelter without clean water
        result = check_safety(
            ["infant_formula", "contains_nuts"],
            ["nut_free_zone"],
        )
        assert not result.allowed
        assert len(result.reasons) >= 2  # formula + allergen

    def test_empty_tags_always_pass(self):
        result = check_safety([], [])
        assert result.allowed
