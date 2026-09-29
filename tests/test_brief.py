"""Tests for understanding a brief: parsing the specification and building targets.

The failures these guard against were all real, and all of them were silent. A
specification parser that drops a number, or reads a pack weight as a protein
content, still returns a plausible-looking brief - it just sends every downstream
step after the wrong product.
"""
from __future__ import annotations

import unittest

from app.bootstrap import CASES, INFEASIBLE_CASE
from app.core import brief as brief_module, kb


def payload(case, **overrides):
    data = {
        "product_name": case["name"],
        "category": case["category"],
        "spec_text": case["spec_text"],
        "diet": case.get("diet", "vegetarian"),
        "claims": list(case.get("claims") or []),
        "unit_weight_g": case.get("unit_weight_g"),
    }
    data.update(overrides)
    return data


class SpecParsingTests(unittest.TestCase):
    def test_reads_numbers_written_the_way_indian_specs_write_them(self) -> None:
        # "per 100 g" is the standard basis and must not be mistaken for a pack size.
        numbers = brief_module.parse_spec_numbers(
            "Protein 15 g per 100 g. Total sugars not more than 40 g per 100 g. "
            "Moisture 3%. Water activity not more than 0.90. pH 3.6. Shelf life 6 months."
        )
        self.assertEqual(numbers["protein_g"]["value"], 15.0)
        self.assertEqual(numbers["sugar_g"]["value"], 40.0)
        self.assertEqual(numbers["sugar_g"]["comparator"], "<=")
        self.assertEqual(numbers["moisture_pct"]["value"], 3.0)
        self.assertEqual(numbers["water_activity"]["value"], 0.90)
        self.assertEqual(numbers["ph"]["value"], 3.6)
        self.assertEqual(numbers["shelf_life_days"]["value"], 180.0)

    def test_a_sentence_final_full_stop_is_not_part_of_the_number(self) -> None:
        # "0.90." used to be read as the float 0.90. which raised and dropped the KPI.
        numbers = brief_module.parse_spec_numbers(
            "Water activity not more than 0.90. pH 3.6. Moisture 3%."
        )
        self.assertIn("water_activity", numbers)
        self.assertEqual(numbers["water_activity"]["value"], 0.90)
        self.assertEqual(numbers["ph"]["value"], 3.6)

    def test_pack_weight_is_not_read_as_a_nutrient(self) -> None:
        numbers = brief_module.parse_spec_numbers(
            "High protein high fibre ragi cookie. 40 g pack (8 biscuits). "
            "Protein 12 g per 100 g and dietary fibre 7 g per 100 g."
        )
        self.assertEqual(numbers["protein_g"]["value"], 12.0)
        self.assertEqual(numbers["fibre_g"]["value"], 7.0)
        self.assertEqual(numbers["__pack_size"]["value"], 40.0)
        self.assertEqual(numbers["__pack_size"]["unit"], "g")

    def test_claim_words_do_not_leak_into_each_other(self) -> None:
        # "reduced sugar" is a reduced_sugar claim; it is not a sugar-free claim.
        claims = brief_module.claims_in_text("Reduced sugar mango fruit spread with no added sugar.")
        self.assertIn("reduced_sugar", claims)
        self.assertIn("low_sugar", claims)  # from "no added sugar"


class TargetTests(unittest.TestCase):
    def test_specification_numbers_win_over_category_defaults(self) -> None:
        brief = brief_module.build_brief(payload(CASES[2]))
        sugar = next(t for t in brief.targets if t.id == "sugar_g")
        # The spread spec says "not more than 40 g"; the reduced-sugar claim's
        # stricter default must not silently replace the customer's own number.
        self.assertEqual(sugar.target, 40.0)
        self.assertEqual(sugar.direction, "lower")
        self.assertTrue(sugar.hard)

    def test_every_case_parses_the_numbers_it_states(self) -> None:
        expectations = {
            "namkeen": {"protein_g": 15.0, "moisture_pct": 3.0, "sodium_mg": 480.0, "cost_inr_kg": 185.0},
            "cookie": {"protein_g": 12.0, "fibre_g": 7.0, "sugar_g": 12.0, "cost_inr_kg": 240.0},
            "spread": {"sugar_g": 40.0, "water_activity": 0.90, "ph": 3.6, "cost_inr_kg": 160.0},
            "conflict_demo": {"protein_g": 15.0, "fibre_g": 4.0, "moisture_pct": 5.0},
            # A per-bottle number is converted onto the per-100 g basis the models use.
            "beverage": {"protein_g": 10.0, "sugar_g": 6.0, "shelf_life_days": 180.0, "cost_inr_kg": 320.0},
        }
        for case in list(CASES) + [INFEASIBLE_CASE]:
            with self.subTest(case=case["key"]):
                brief = brief_module.build_brief(payload(case))
                by_id = {t.id: t for t in brief.targets}
                for kpi_id, expected in expectations[case["key"]].items():
                    self.assertIn(kpi_id, by_id)
                    self.assertAlmostEqual(by_id[kpi_id].target, expected, places=4)

    def test_a_category_the_spec_does_not_declare_keeps_its_defaults(self) -> None:
        brief = brief_module.build_brief(
            {
                "product_name": "Test bar",
                "category": "bar",
                "spec_text": "A protein bar with no numbers given at all.",
            }
        )
        self.assertTrue(brief.targets)
        self.assertGreater(brief.open_questions.__len__(), 0)

    def test_a_bare_number_is_a_band_and_a_comparator_is_a_limit(self) -> None:
        band = brief_module.build_brief(
            {"product_name": "x", "category": "cookie", "spec_text": "Protein 12 g per 100 g."}
        )
        limit = brief_module.build_brief(
            {"product_name": "x", "category": "cookie", "spec_text": "Sugar not more than 12 g per 100 g."}
        )
        self.assertEqual(next(t for t in band.targets if t.id == "protein_g").direction, "target")
        self.assertEqual(next(t for t in limit.targets if t.id == "sugar_g").direction, "lower")

    def test_unit_weight_falls_back_to_the_category(self) -> None:
        brief = brief_module.build_brief(
            {"product_name": "no pack size", "category": "cookie", "spec_text": "A ragi biscuit."}
        )
        self.assertGreater(brief.unit_weight_g, 0)


class PackUnitTests(unittest.TestCase):
    """A pack size is a volume for a drink and a weight for everything else.

    The form used to ask every category for a "unit weight (g)", so a beverage
    brief had to be converted from millilitres into grams by hand and the number
    then disagreed with the label printed on the bottle. The unit now travels with
    the number, from the specification text and the form all the way to the pack.
    """

    def test_a_pack_declared_in_volume_carries_the_volume_unit(self) -> None:
        numbers = brief_module.parse_spec_numbers(
            "High-protein ready-to-drink whey beverage. 200 ml bottle. "
            "Protein 20 g per bottle (10 g per 100 ml)."
        )
        self.assertEqual(numbers["__pack_size"]["value"], 200.0)
        self.assertEqual(numbers["__pack_size"]["unit"], "ml")
        # A volume declaration names the measure it is, not a weight.
        self.assertEqual(numbers["__pack_size"]["evidence"], "declared net volume")

    def test_a_litre_and_a_kilogram_are_converted_to_their_base_unit(self) -> None:
        litre = brief_module.parse_spec_numbers("A 1 litre bottle of lassi.")
        litre_l = brief_module.parse_spec_numbers("A 1 L bottle of lassi.")
        kilogram = brief_module.parse_spec_numbers("A 1 kg pouch of dry mix.")
        self.assertEqual((litre["__pack_size"]["value"], litre["__pack_size"]["unit"]), (1000.0, "ml"))
        self.assertEqual((litre_l["__pack_size"]["value"], litre_l["__pack_size"]["unit"]), (1000.0, "ml"))
        self.assertEqual((kilogram["__pack_size"]["value"], kilogram["__pack_size"]["unit"]), (1000.0, "g"))

    def test_a_net_volume_declaration_is_read(self) -> None:
        numbers = brief_module.parse_spec_numbers("Net volume 250 ml per can.")
        self.assertEqual(numbers["__pack_size"]["value"], 250.0)
        self.assertEqual(numbers["__pack_size"]["unit"], "ml")

    def test_the_pack_the_form_sent_wins_and_keeps_its_unit(self) -> None:
        brief = brief_module.build_brief(
            {
                "product_name": "Whey drink",
                "category": "beverage",
                "spec_text": "200 ml bottle.",
                "unit_weight_g": 330.0,
                "unit": "ml",
            }
        )
        self.assertEqual(brief.unit_weight_g, 330.0)
        self.assertEqual(brief.declared_unit, "ml")
        self.assertEqual(brief.declared_unit_size, 330.0)

    def test_a_unit_the_form_could_not_mean_falls_back_to_the_category(self) -> None:
        # The field is only ever labelled in grams or millilitres, so anything else
        # is a client bug: a beverage must not be relabelled by it.
        brief = brief_module.build_brief(
            {"product_name": "Drink", "category": "beverage", "unit_weight_g": 250.0, "unit": "kg"}
        )
        self.assertEqual(brief.declared_unit, "ml")

    def test_the_category_default_decides_the_unit_when_the_spec_is_silent(self) -> None:
        drink = brief_module.build_brief({"product_name": "Drink", "category": "beverage"})
        biscuit = brief_module.build_brief({"product_name": "Biscuit", "category": "cookie"})
        self.assertEqual(drink.declared_unit, "ml")
        self.assertEqual(drink.declared_unit_size, kb.category("beverage").typical_unit_weight_g)
        self.assertEqual(biscuit.declared_unit, "g")

    def test_the_brief_summary_carries_the_declared_unit_for_the_interface(self) -> None:
        brief = brief_module.build_brief(
            {"product_name": "Drink", "category": "beverage", "spec_text": "A 200 ml bottle."}
        )
        summary = brief_module.summary(brief)
        self.assertEqual(summary["declared_unit"], "ml")
        self.assertEqual(summary["declared_unit_size"], 200.0)

    def test_only_the_category_sold_by_volume_declares_millilitres(self) -> None:
        units = {category_id: card.pack_unit for category_id, card in kb.categories().items()}
        self.assertEqual(units["beverage"], "ml")
        self.assertEqual({u for c, u in units.items() if c != "beverage"}, {"g"})

    def test_an_unknown_pack_unit_is_read_as_grams(self) -> None:
        # A typo in the library must not label a cookie in millilitres.
        self.assertEqual(kb._pack_unit({"pack_unit": "teaspoons"}), "g")
        self.assertEqual(kb._pack_unit({}), "g")
        self.assertEqual(kb._pack_unit({"pack_unit": "ML"}), "ml")


if __name__ == "__main__":
    unittest.main()
