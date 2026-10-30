"""Rule tests, including every worked example in TECH_PLAN.md section 4.5.

Owner decision 1: test negation, qualifiers, hypothetical statements,
outdated context and numeric differences, and abstain where unsure.
"""

import unittest

from citecheck.rules import is_unclear
from tests.helpers import hit


class S1Tests(unittest.TestCase):
    def test_verbatim(self):
        h = hit("Refunds are paid to the original payment method.",
                "Refunds are paid to the original payment method.")
        self.assertEqual(h.rule, "S1")
        self.assertEqual(h.detail["gap"], 0)

    def test_trailing_words_block_support(self):
        # Rule set 0.3.0: a trailing phrase may narrow the sentence, so S1 abstains.
        self.assertIsNone(hit("The warranty covers accidental damage for two years.",
                              "The warranty covers accidental damage for two years from purchase."))

    def test_gap_of_three_is_allowed(self):
        h = hit("Northwind sells coffee machines.", "Northwind sells kettles, toasters and coffee machines.")
        self.assertEqual(h.rule, "S1")
        self.assertEqual(h.detail["gap"], 3)

    def test_gap_of_four_is_not(self):
        self.assertIsNone(hit("Northwind sells coffee machines.",
                              "Northwind sells kettles, toasters, irons and coffee machines."))

    def test_number_in_the_gap_is_not_allowed(self):
        self.assertIsNone(hit("The kit includes spare cables.", "The kit includes 2 spare cables."))

    def test_number_words_match_digits(self):
        self.assertEqual(hit("Batteries are covered for twelve months.",
                             "Batteries are covered for 12 months.").rule, "S1")

    def test_percent_forms_match(self):
        self.assertEqual(hit("Billing gives a discount of 15 percent.",
                             "Billing gives a discount of 15%.").rule, "S1")

    def test_word_order_matters(self):
        self.assertIsNone(hit("Bob paid Alice.", "Alice paid Bob."))

    def test_claims_need_three_tokens(self):
        self.assertIsNone(hit("Prices rose.", "Prices rose."))


class QualifierTests(unittest.TestCase):
    def test_restriction_in_document_blocks_support(self):
        self.assertIsNone(hit("Customers can cancel their plan.",
                              "Customers can cancel their plan only if they give 30 days' notice."))

    def test_hedge_blocks_support(self):
        self.assertIsNone(hit("Customers are asked to provide proof of purchase.",
                              "Customers may be asked to provide proof of purchase."))

    def test_but_clause_blocks_support(self):
        self.assertIsNone(hit("The refund window is 30 days.",
                              "The refund window is 30 days for members but 14 days for others."))

    def test_quantity_word_in_claim_blocks_support(self):
        self.assertIsNone(hit("All orders ship the same day.", "Orders ship the same day."))

    def test_hypothetical_statements_abstain(self):
        self.assertIsNone(hit("Northwind offers a discounted replacement.",
                              "Northwind would offer a discounted replacement."))
        self.assertIsNone(hit("Northwind sends a new kettle.",
                              "If a kettle fails within 30 days, Northwind sends a new kettle."))
        self.assertIsNone(hit("The plan includes support.", "Suppose the plan includes support."))

    def test_outdated_statements_abstain(self):
        self.assertIsNone(hit("The refund window is 30 days.", "Previously, the refund window was 30 days."))
        self.assertIsNone(hit("The refund window is 30 days.", "Formerly the refund window is 30 days."))
        self.assertIsNone(hit("Shipping is free on all orders.", "Shipping used to be free on all orders."))

    def test_framing_words_abstain(self):
        self.assertIsNone(hit("Northwind products are made in Germany.",
                              "Myth: Northwind products are made in Germany."))
        self.assertIsNone(hit("The plan is free.", "According to the old site, the plan is free."))


class C1Tests(unittest.TestCase):
    def test_does_not(self):
        h = hit("The warranty covers water damage.", "The warranty does not cover water damage.")
        self.assertEqual(h.rule, "C1")
        self.assertEqual(h.detail["negation"], "not")

    def test_is_not(self):
        self.assertEqual(hit("Shipping is free on international orders.",
                             "Shipping is not free on international orders.").rule, "C1")

    def test_cannot(self):
        self.assertEqual(hit("Opened software can be returned.", "Opened software cannot be returned.").rule, "C1")

    def test_negated_claim(self):
        self.assertEqual(hit("The Premium plan does not include phone support.",
                             "The Premium plan includes phone support.").rule, "C1")

    def test_no_longer(self):
        h = hit("The warranty covers water damage.", "The warranty no longer covers water damage.")
        self.assertEqual(h.rule, "C1")
        self.assertEqual(h.detail["negation"], "no longer")

    def test_distant_negation_is_ignored(self):
        self.assertIsNone(hit("Shipping is free on international orders.",
                              "No refunds are given and shipping is free on international orders."))

    def test_both_sides_negated_is_not_a_flip(self):
        h = hit("Gift cards are not refundable.", "Gift cards are not refundable.")
        self.assertEqual(h.rule, "S1")

    def test_extra_words_block_c1(self):
        self.assertIsNone(hit("The plan includes support.", "The plan includes no phone support."))


class C2Tests(unittest.TestCase):
    def test_number_conflict(self):
        h = hit("The refund window is 30 days.", "The refund window is 14 days.")
        self.assertEqual(h.rule, "C2")

    def test_year_conflict(self):
        self.assertEqual(hit("Northwind Appliances was founded in 2009 in Leeds.",
                             "Northwind Appliances was founded in 2011 in Leeds.").rule, "C2")

    def test_percentage_conflict(self):
        self.assertEqual(hit("Sales rose by 20% in 2024.", "Sales rose by 12% in 2024.").rule, "C2")

    def test_claim_value_elsewhere_in_sentence(self):
        self.assertIsNone(hit("The refund window is 30 days.",
                              "The refund window is 14 days, 30 days for members."))

    def test_two_differences(self):
        self.assertIsNone(hit("The plan costs £20 per month.", "The plan costs £200 per year."))

    def test_different_subject(self):
        self.assertIsNone(hit("The Basic plan costs £35 per month.", "The Premium plan costs £35 per month."))

    def test_word_difference_is_not_a_value_conflict(self):
        self.assertIsNone(hit("Sales fell by 12% in 2024.", "Sales rose by 12% in 2024."))

    def test_threshold_entailment_is_unclear_not_contradicted(self):
        # "Cannot be cancelled after 14 days" implies "cannot be cancelled after 90 days".
        # Rule set 0.1.0 called this a contradiction; 0.2.0 marks it unclear.
        h = hit("Annual plans cannot be cancelled after 90 days.",
                "Annual plans cannot be cancelled after 14 days.")
        self.assertEqual(h.rule, "C2")
        self.assertTrue(is_unclear(h))
        self.assertIn("after", h.detail["context_words"])
        self.assertTrue(h.detail["negated"])


class NumberContextTests(unittest.TestCase):
    """C2 must not call a different number a contradiction when the numbers are
    thresholds, limits or estimates, or when the statement is negated."""

    def assert_unclear(self, claim, sentence):
        h = hit(claim, sentence)
        self.assertIsNotNone(h, (claim, sentence))
        self.assertEqual(h.rule, "C2")
        self.assertTrue(is_unclear(h), (claim, sentence))

    def test_within(self):
        self.assert_unclear("Refunds are processed within 10 days.", "Refunds are processed within 3 days.")

    def test_after_and_before(self):
        self.assert_unclear("Tickets can be exchanged after 30 days.", "Tickets can be exchanged after 7 days.")
        self.assert_unclear("Entries must arrive before 2025.", "Entries must arrive before 2024.")

    def test_over_and_under(self):
        self.assert_unclear("Orders over £100 ship free.", "Orders over £50 ship free.")
        self.assert_unclear("Children under 12 travel free.", "Children under 5 travel free.")

    def test_at_least_and_up_to(self):
        self.assert_unclear("The battery lasts at least 8 hours.", "The battery lasts at least 10 hours.")
        self.assert_unclear("The tank holds up to 40 litres.", "The tank holds up to 50 litres.")

    def test_more_than_and_since(self):
        self.assert_unclear("The firm has more than 200 staff.", "The firm has more than 300 staff.")
        self.assert_unclear("The shop has traded since 2015.", "The shop has traded since 2009.")

    def test_estimates(self):
        self.assert_unclear("The bridge is about 300 metres long.", "The bridge is about 310 metres long.")

    def test_both_sides_negated(self):
        # "is not 300" and "is not 250" can both be true.
        self.assert_unclear("The bridge is not 300 metres long.", "The bridge is not 250 metres long.")

    def test_plain_values_still_conflict(self):
        for claim, sentence in (
            ("The bridge is 300 metres long.", "The bridge is 250 metres long."),
            ("The office opened in 2019.", "The office opened in 2017."),
            ("Tickets cost £12 each.", "Tickets cost £15 each."),
            ("Sales rose by 20% last year.", "Sales rose by 12% last year."),
        ):
            h = hit(claim, sentence)
            self.assertEqual(h.rule, "C2", claim)
            self.assertFalse(is_unclear(h), claim)


class LeadingWordTests(unittest.TestCase):
    """Words before the claim's first word often narrow its subject. A sentence
    like that must not decide SUPPORTED or CONTRADICTED."""

    def test_leading_modifier_blocks_support(self):
        for claim, sentence in (
            ("Accounts are deleted after 90 days.", "Cancelled accounts are deleted after 90 days."),
            ("Members get free delivery.", "Premium members get free delivery."),
            ("Files are removed every night.", "Temporary files are removed every night."),
            ("Students can borrow laptops.", "Visiting students can borrow laptops."),
        ):
            self.assertIsNone(hit(claim, sentence), claim)

    def test_leading_place_or_framing_blocks_support(self):
        self.assertIsNone(hit("The fee is £5.", "In Scotland, the fee is £5."))
        self.assertIsNone(hit("The museum is free.", "Note that the museum is free."))

    def test_leading_modifier_blocks_contradiction(self):
        self.assertIsNone(hit("Files are kept for 30 days.", "Temporary files are kept for 7 days."))
        self.assertIsNone(hit("Students can borrow laptops.", "Visiting students cannot borrow laptops."))

    def test_matching_from_the_start_still_works(self):
        self.assertEqual(hit("Cancelled accounts are deleted after 90 days.",
                             "Cancelled accounts are deleted after 90 days.").rule, "S1")
        self.assertEqual(hit("The museum opens at 10am.", "The museum opens at 10am.").rule, "S1")


class TrailingWordTests(unittest.TestCase):
    """Rule set 0.3.0: words after the claim's last word can narrow who, where or
    when the sentence applies to. Only a closing date is allowed."""

    def assert_blocked(self, claim, sentence):
        self.assertIsNone(hit(claim, sentence), (claim, sentence))

    def test_restricted_population(self):
        self.assert_blocked("The fee is waived.", "The fee is waived for UK students.")
        self.assert_blocked("Parking is free.", "Parking is free for blue badge holders.")
        self.assert_blocked("Loans are interest-free.", "Loans are interest-free for staff members.")

    def test_location(self):
        self.assert_blocked("Repairs are free of charge.", "Repairs are free of charge in the United Kingdom.")
        self.assert_blocked("The service runs on Sundays.", "The service runs on Sundays in central London.")

    def test_time_of_day_or_period(self):
        self.assert_blocked("The museum opens at 10am.", "The museum opens at 10am in summer.")
        self.assert_blocked("Phone support is available.", "Phone support is available on weekdays.")

    def test_conditions_and_exceptions_without_cue_words(self):
        self.assert_blocked("Returns are accepted.", "Returns are accepted with a receipt.")
        self.assert_blocked("The discount applies to all items.", "The discount applies to all items in the sale section.")
        self.assert_blocked("Delivery is free.", "Delivery is free for orders placed online.")

    def test_contradiction_rules_also_ignore_narrowed_sentences(self):
        # A narrower sentence can't contradict the general claim either.
        self.assert_blocked("The ticket costs £20.", "The ticket costs £25 in Canada.")
        self.assert_blocked("Shipping is free.", "Shipping is not free for international orders.")

    def test_closing_date_blocks_a_verdict(self):
        # Rule set 0.4.0 (QA finding F9): a dated sentence may describe the past,
        # so it can't support or contradict an undated claim. 0.3.0 allowed this.
        self.assert_blocked("The company opened a store in Bristol.",
                            "The company opened a store in Bristol in 2021.")
        self.assert_blocked("Revenue reached £4 million.", "Revenue reached £4 million in 2024.")
        self.assert_blocked("The firm employs 240 people.", "The firm employs 60 people in 2015.")
        self.assert_blocked("The service is free.", "The service is not free as of 2019.")

    def test_dated_claims_still_match(self):
        # Positive control: when the claim carries the same date, the rules apply.
        self.assertEqual(hit("Revenue reached £4 million in 2024.",
                             "Revenue reached £4 million in 2024.").rule, "S1")
        self.assertEqual(hit("Revenue reached £4 million in 2024.",
                             "Revenue reached £5 million in 2024.").rule, "C2")

    def test_date_rule_is_narrow(self):
        # Not a year, or more than a bare date: still blocked.
        self.assert_blocked("Staff are paid.", "Staff are paid in 12.")
        self.assert_blocked("The new office opened.", "The new office opened in early 2019.")


class GapWordTests(unittest.TestCase):
    """Rule set 0.3.0: words between the claim's words are allowed only as earlier
    items of a list ending in "and"."""

    def test_list_insertion_allowed(self):
        h = hit("Northwind sells coffee machines.", "Northwind sells kettles, toasters and coffee machines.")
        self.assertEqual(h.rule, "S1")

    def test_list_item_not_ending_in_and_abstains(self):
        # Entailed, but "flood," doesn't end in "and", so the safe answer is to abstain.
        self.assertIsNone(hit("The plan covers fire and theft.", "The plan covers flood, fire and theft."))

    def test_restricting_words_inside_are_blocked(self):
        self.assertIsNone(hit("The fee is waived for students.", "The fee is waived for UK students."))
        self.assertIsNone(hit("Live chat is available to customers.", "Live chat is available to Premium customers."))
        self.assertIsNone(hit("Members can bring guests.", "Members can sometimes bring guests."))
        self.assertIsNone(hit("The plan includes support.", "The plan includes phone support."))

    def test_list_limit(self):
        self.assertIsNone(hit("Northwind sells coffee machines.",
                              "Northwind sells kettles, toasters, irons and coffee machines."))
        self.assertIsNone(hit("Northwind sells coffee machines.",
                              "Northwind sells kettles and coffee machines in Leeds."))


class SymbolTests(unittest.TestCase):
    """Rule set 0.4.0, QA finding F7: comparison and minus signs change meaning."""

    def test_opposite_comparison_signs_never_match(self):
        self.assertIsNone(hit("Response time is <5 minutes.", "Response time is >5 minutes."))
        self.assertIsNone(hit("The limit is <=10 items.", "The limit is >=10 items."))

    def test_minus_sign_is_not_dropped(self):
        self.assertIsNone(hit("The freezer runs at -18 degrees.", "The freezer runs at 18 degrees."))

    def test_bounded_numbers_are_unclear(self):
        h = hit("Response time is <5 minutes.", "Response time is <10 minutes.")
        self.assertEqual(h.rule, "C2")
        self.assertTrue(is_unclear(h))
        self.assertIn("<", h.detail["context_words"])

    def test_positive_control_same_symbol_matches(self):
        self.assertEqual(hit("Response time is <5 minutes.", "Response time is <5 minutes.").rule, "S1")


class IdentifierTests(unittest.TestCase):
    """Rule set 0.4.0, QA finding F6: numbers that name things are not quantities."""

    def assert_identifier(self, claim, sentence, word):
        h = hit(claim, sentence)
        self.assertIsNotNone(h, claim)
        self.assertEqual(h.rule, "C2")
        self.assertTrue(is_unclear(h), claim)
        self.assertEqual(h.detail["identifier"], word)

    def test_label_words(self):
        self.assert_identifier("Section 3 covers refunds.", "Section 4 covers refunds.", "section")
        self.assert_identifier("Version 2 supports exports.", "Version 3 supports exports.", "version")
        self.assert_identifier("Tier 1 includes priority support.", "Tier 2 includes priority support.", "tier")
        self.assert_identifier("Room 12 is on the ground floor.", "Room 14 is on the ground floor.", "room")

    def test_ordinals(self):
        self.assert_identifier("The meeting is on the 21st of May.", "The meeting is on the 31st of May.",
                               "ordinal")

    def test_positive_control_quantities_still_conflict(self):
        h = hit("The team has 12 members.", "The team has 14 members.")
        self.assertEqual(h.rule, "C2")
        self.assertFalse(is_unclear(h))


class NumberFormatTests(unittest.TestCase):
    """Rule set 0.4.0, QA finding F8: no unsafe number normalisation."""

    def test_same_value_written_differently_is_neither_support_nor_conflict(self):
        self.assertIsNone(hit("The fee is £3.50 per visit.", "The fee is £3.5 per visit."))
        self.assertIsNone(hit("The meeting is on 05 May.", "The meeting is on 5 May."))

    def test_versions_do_not_merge(self):
        self.assertIsNone(hit("The app requires release 3.10 or later.", "The app requires release 3.1 or later."))
        self.assertIsNone(hit("The current build is 1.2.3 today.", "The current build is 1.2.4 today."))

    def test_locale_ambiguous_numbers_never_conflict(self):
        self.assertIsNone(hit("The rate is 1,5 percent.", "The rate is 2 percent."))
        self.assertIsNone(hit("The tank holds 1.000 litres.", "The tank holds 2.000 litres."))

    def test_positive_controls(self):
        self.assertEqual(hit("The fee is £3.50 per visit.", "The fee is £4.50 per visit.").rule, "C2")
        self.assertEqual(hit("The hall seats 1,000 people.", "The hall seats 1000 people.").rule, "S1")


class NewCueTests(unittest.TestCase):
    """Rule set 0.4.0, QA finding F19: more qualifier and range words."""

    def test_new_hedges_block_support(self):
        for word in ("typically", "generally", "normally", "mostly", "temporarily"):
            with self.subTest(word=word):
                self.assertIsNone(hit("Delivery is free.", f"Delivery is {word} free."))

    def test_subject_to_blocks_support(self):
        self.assertIsNone(hit("Refunds are issued.", "Refunds are issued subject to approval."))

    def test_from_and_between_make_numbers_unclear(self):
        for claim, sentence in (
            ("Prices start from £20.", "Prices start from £25."),
            ("Classes run between 9 and 11.", "Classes run between 9 and 12."),
        ):
            h = hit(claim, sentence)
            self.assertEqual(h.rule, "C2", claim)
            self.assertTrue(is_unclear(h), claim)


if __name__ == "__main__":
    unittest.main()
