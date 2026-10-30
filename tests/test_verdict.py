import unittest

from citecheck.splitter import Claim
from citecheck.verdict import CONTRADICTED, INSUFFICIENT, SUPPORTED, check_claim
from tests.helpers import TempDocsTestCase

POLICY = (
    "Refund Policy\n"
    "\n"
    "The refund window is 14 days.\n"
    "Gift cards are not refundable.\n"
    "Customers can cancel their plan only if they give 30 days' notice.\n"
)
OLD_POLICY = (
    "Superseded policy (2022)\n"
    "\n"
    "The refund window is 30 days.\n"
    "Customers receive a free mug with every order.\n"
)


class VerdictTests(TempDocsTestCase):
    def test_supported_with_verified_quote_and_location(self):
        r = self.check("The refund window is 14 days.", {"policy.txt": POLICY})
        self.assertEqual(r.label, SUPPORTED)
        self.assertEqual(r.rule, "S1")
        ev = r.evidence[0]
        self.assertEqual(ev.document, "policy.txt")
        self.assertEqual(ev.line, 3)
        self.assertEqual(POLICY[ev.char_start:ev.char_end], ev.quote)
        self.assertEqual(ev.quote, "The refund window is 14 days.")
        self.assertTrue(ev.verified_verbatim)

    def test_contradicted_value_conflict_detail(self):
        r = self.check("The refund window is 21 days.", {"policy.txt": POLICY})
        self.assertEqual(r.label, CONTRADICTED)
        self.assertEqual(r.rule, "C2")
        self.assertEqual(r.evidence[0].detail, {"claim_value": "21", "document_value": "14"})

    def test_contradicted_negation(self):
        r = self.check("Gift cards are refundable.", {"policy.txt": POLICY})
        self.assertEqual((r.label, r.rule), (CONTRADICTED, "C1"))

    def test_outdated_heading_only(self):
        r = self.check("Customers receive a free mug with every order.", {"old.txt": OLD_POLICY})
        self.assertEqual(r.label, INSUFFICIENT)
        self.assertEqual(r.abstain_reason, "flagged_context")
        self.assertEqual(r.detail["heading"], "Superseded policy (2022)")

    def test_current_policy_wins_over_superseded(self):
        docs = {"old.txt": OLD_POLICY, "policy.txt": POLICY}
        self.assertEqual(self.check("The refund window is 14 days.", docs).label, SUPPORTED)
        self.assertEqual(self.check("The refund window is 30 days.", docs).label, CONTRADICTED)

    def test_conflicting_sources(self):
        # Rule set 0.4.0 treats "from 9am to 5pm" as a range (unclear), so this
        # test uses a single time.
        docs = {"a.txt": "Support hours\n\nThe help desk opens at 8am on weekdays.\n",
                "b.txt": "FAQ\n\nThe help desk opens at 9am on weekdays.\n"}
        r = self.check("The help desk opens at 8am on weekdays.", docs)
        self.assertEqual(r.label, INSUFFICIENT)
        self.assertEqual(r.abstain_reason, "conflicting_sources")
        self.assertEqual([ev.document for ev in r.evidence], ["a.txt", "b.txt"])
        self.assertEqual([ev.rule for ev in r.evidence], ["S1", "C2"])

    def test_no_relevant_passage(self):
        r = self.check("Zebras migrate across grasslands.", {"policy.txt": POLICY})
        self.assertEqual((r.label, r.abstain_reason), (INSUFFICIENT, "no_relevant_passage"))
        self.assertEqual(r.closest, [])
        self.assertEqual(r.evidence, [])

    def test_partial_support_lists_missing_words(self):
        r = self.check("The refund window is 14 days for premium members.", {"policy.txt": POLICY})
        self.assertEqual((r.label, r.abstain_reason), (INSUFFICIENT, "partial_support"))
        # Common words such as "for" are no longer listed as missing (QA finding F12).
        self.assertEqual(r.detail["missing_terms"], ["premium", "members"])

    def test_qualifier_mismatch_lists_cue_words(self):
        r = self.check("Customers can cancel their plan.", {"policy.txt": POLICY})
        self.assertEqual((r.label, r.abstain_reason), (INSUFFICIENT, "qualifier_mismatch"))
        self.assertEqual(r.detail["cue_words_only_in_document"], ["if", "only"])

    def test_needs_multiple_sources(self):
        docs = {"a.txt": "Shipping\n\nExpress delivery costs £9.\n",
                "b.txt": "Pricing\n\nThe Basic plan costs £20 per month.\n"}
        r = self.check("Express delivery costs £9 and the Basic plan costs £20 per month.", docs)
        self.assertEqual((r.label, r.abstain_reason), (INSUFFICIENT, "needs_multiple_sources"))
        self.assertEqual({p.document for p in r.closest}, {"a.txt", "b.txt"})

    def test_outside_rules_for_reordered_roles(self):
        r = self.check("Bob paid Alice yesterday.", {"t.txt": "Notes\n\nAlice paid Bob yesterday.\n"})
        self.assertEqual((r.label, r.abstain_reason), (INSUFFICIENT, "outside_rules"))

    def test_leading_modifier_abstains_with_extra_words(self):
        doc = {"files.txt": "Retention\n\nTemporary files are deleted after 30 days.\n"}
        r = self.check("Files are deleted after 30 days.", doc)
        self.assertEqual((r.label, r.abstain_reason), (INSUFFICIENT, "extra_words"))
        self.assertEqual(r.detail["extra_terms"], ["Temporary"])

    def test_threshold_number_abstains(self):
        doc = {"tickets.txt": "Tickets\n\nTickets cannot be exchanged after 7 days.\n"}
        r = self.check("Tickets cannot be exchanged after 60 days.", doc)
        self.assertEqual((r.label, r.abstain_reason), (INSUFFICIENT, "unclear_number"))
        self.assertFalse(r.detail["also_supported"])
        self.assertEqual(len(r.evidence), 1)
        self.assertTrue(r.evidence[0].verified_verbatim)

    def test_threshold_number_blocks_support_from_another_document(self):
        docs = {"a.txt": "Refunds\n\nRefunds are processed within 10 days.\n",
                "b.txt": "FAQ\n\nRefunds are processed within 3 days.\n"}
        r = self.check("Refunds are processed within 10 days.", docs)
        self.assertEqual((r.label, r.abstain_reason), (INSUFFICIENT, "unclear_number"))
        self.assertTrue(r.detail["also_supported"])
        self.assertEqual([ev.rule for ev in r.evidence], ["S1", "C2"])

    def test_plain_contradiction_still_wins_over_unclear(self):
        # a.txt flips the negation (clear C1); b.txt gives another negated number (unclear C2).
        docs = {"a.txt": "Size\n\nThe bridge is 300 metres long.\n",
                "b.txt": "Notes\n\nThe bridge is not 250 metres long.\n"}
        r = self.check("The bridge is not 300 metres long.", docs)
        self.assertEqual((r.label, r.rule), (CONTRADICTED, "C1"))
        self.assertEqual(r.evidence[0].document, "a.txt")

    def test_trailing_restriction_abstains_with_extra_words(self):
        doc = {"fees.txt": "Fees\n\nThe fee is waived for UK students.\n"}
        r = self.check("The fee is waived.", doc)
        self.assertEqual((r.label, r.abstain_reason), (INSUFFICIENT, "extra_words"))
        self.assertEqual(r.detail["extra_terms"], ["for", "UK", "students"])

    def test_restricted_population_inside_claim_abstains(self):
        doc = {"fees.txt": "Fees\n\nThe fee is waived for UK students.\n"}
        r = self.check("The fee is waived for students.", doc)
        self.assertEqual((r.label, r.abstain_reason), (INSUFFICIENT, "extra_words"))
        self.assertEqual(r.detail["extra_terms"], ["UK"])

    def test_both_negated_numbers_abstain(self):
        doc = {"bridge.txt": "Size\n\nThe bridge is not 250 metres long.\n"}
        r = self.check("The bridge is not 300 metres long.", doc)
        self.assertEqual((r.label, r.abstain_reason), (INSUFFICIENT, "unclear_number"))
        self.assertTrue(r.detail["negated"])

    def test_quote_not_verified_when_file_changes(self):
        corpus = self.corpus({"policy.txt": POLICY})
        (self.docs_dir / "policy.txt").write_text(POLICY.replace("14 days", "15 days"), encoding="utf-8")
        r = check_claim(Claim(1, "The refund window is 14 days.", 1), corpus)
        self.assertEqual((r.label, r.abstain_reason), (INSUFFICIENT, "quote_not_verified"))
        self.assertEqual(r.evidence, [])

    def test_closest_passages_rules(self):
        r = self.check("The refund window is 14 days.", {"policy.txt": POLICY})
        self.assertLessEqual(len(r.closest), 3)
        self.assertEqual([p.rank for p in r.closest], list(range(1, len(r.closest) + 1)))
        self.assertNotIn(r.evidence[0].char_start, [p.char_start for p in r.closest])

    def test_evidence_and_passages_carry_their_heading(self):
        r = self.check("The refund window is 14 days.", {"policy.txt": POLICY})
        self.assertEqual(r.evidence[0].heading, "Refund Policy")


class ReferenceTests(TempDocsTestCase):
    """Rule set 0.4.0, QA finding F5: unresolved pronouns never get a confident label."""

    def test_pronoun_subject_abstains_but_shows_the_sentence(self):
        docs = {"facts.txt": "Company facts\n\nIt has 45 employees.\n"}
        for claim in ("It has 45 employees.", "It has 60 employees."):
            with self.subTest(claim=claim):
                r = self.check(claim, docs)
                self.assertEqual((r.label, r.abstain_reason), (INSUFFICIENT, "unclear_reference"))
                self.assertEqual(r.detail["word"], "It")
                self.assertEqual(len(r.evidence), 1)
                self.assertIsNone(r.rule)

    def test_pointing_words_abstain(self):
        r = self.check("This plan includes phone support.",
                       {"p.txt": "Plans\n\nThis plan includes phone support.\n"})
        self.assertEqual(r.abstain_reason, "unclear_reference")

    def test_positive_control_named_subject(self):
        r = self.check("The bakery has 45 employees.", {"facts.txt": "Facts\n\nThe bakery has 45 employees.\n"})
        self.assertEqual(r.label, SUPPORTED)


class ContextTests(TempDocsTestCase):
    """Rule set 0.4.0, QA findings F9 and F10: context outside the matched words."""

    def test_semicolon_restriction_stays_in_the_sentence(self):
        r = self.check("Refunds are available.", {"r.txt": "Refunds\n\nRefunds are available; sale items are excluded.\n"})
        self.assertEqual(r.label, INSUFFICIENT)

    def test_exception_in_the_next_sentence(self):
        doc = "Refunds\n\nRefunds are available.\nHowever, sale items cannot be refunded.\n"
        r = self.check("Refunds are available.", {"r.txt": doc})
        self.assertEqual((r.label, r.abstain_reason), (INSUFFICIENT, "nearby_context"))
        self.assertEqual(r.detail["neighbour_line"], 4)
        self.assertIn("However", r.detail["neighbour"])

    def test_this_does_not_apply_in_the_next_sentence(self):
        doc = "Parking\n\nParking is free on weekends.\nThis does not apply to coaches.\n"
        r = self.check("Parking is free on weekends.", {"p.txt": doc})
        self.assertEqual(r.abstain_reason, "nearby_context")

    def test_restriction_in_the_previous_sentence(self):
        doc = "Membership\n\nThe following applies only to members. Parking is free.\n"
        r = self.check("Parking is free.", {"m.txt": doc})
        self.assertEqual((r.label, r.abstain_reason), (INSUFFICIENT, "nearby_context"))

    def test_lead_in_paragraph_becomes_context(self):
        doc = "Rules\n\nThe rules below apply to visiting students only:\n\n- Laptops can be borrowed overnight.\n"
        r = self.check("Laptops can be borrowed overnight.", {"s.txt": doc})
        self.assertEqual((r.label, r.abstain_reason), (INSUFFICIENT, "flagged_context"))

    def test_scope_heading_is_flagged(self):
        r = self.check("Water damage is covered.", {"w.txt": "Exclusions\n\nWater damage is covered.\n"})
        self.assertEqual((r.label, r.abstain_reason), (INSUFFICIENT, "flagged_context"))
        self.assertEqual((r.detail["document"], r.detail["line"]), ("w.txt", 3))

    def test_dated_heading_needs_a_matching_year(self):
        doc = "Annual report 2019\n\nThe company has 60 staff.\nRevenue grew by 5% in 2019.\n"
        r = self.check("The company has 60 staff.", {"a.txt": doc})
        self.assertEqual((r.label, r.abstain_reason), (INSUFFICIENT, "dated_context"))
        self.assertEqual(r.detail["years"], ["2019"])
        # Positive control: the claim names the same year.
        self.assertEqual(self.check("Revenue grew by 5% in 2019.", {"a.txt": doc}).label, SUPPORTED)

    def test_dated_sentence_reason(self):
        r = self.check("The company has 60 staff.", {"s.txt": "Staff\n\nThe company has 60 staff in 2015.\n"})
        self.assertEqual((r.label, r.abstain_reason), (INSUFFICIENT, "dated_context"))
        self.assertEqual(r.detail["date"], "in 2015")

    def test_positive_control_unrelated_neighbours(self):
        doc = "Opening hours\n\nThe library opens at 9am.\nThe cafe opens at 10am.\n"
        self.assertEqual(self.check("The library opens at 9am.", {"o.txt": doc}).label, SUPPORTED)
        self.assertEqual(self.check("The library opens at 8am.", {"o.txt": doc}).label, CONTRADICTED)

    def test_identifier_numbers_are_unclear(self):
        r = self.check("Section 3 covers refunds.", {"p.txt": "Policy\n\nSection 4 covers refunds.\n"})
        self.assertEqual((r.label, r.abstain_reason), (INSUFFICIENT, "unclear_number"))
        self.assertEqual(r.detail["identifier"], "section")


class AbstainReasonTests(TempDocsTestCase):
    """QA finding F12: absent evidence, partial matches and context are told apart."""

    def test_no_content_words(self):
        r = self.check("It is what it is.", {"p.txt": POLICY})
        self.assertEqual(r.abstain_reason, "no_content_words")

    def test_one_shared_word_is_not_a_relevant_passage(self):
        doc = {"o.txt": "Office\n\nThe office has a large meeting room.\n"}
        r = self.check("The office has a rooftop swimming pool.", doc)
        self.assertEqual(r.abstain_reason, "no_relevant_passage")
        self.assertEqual(r.closest, [])

    def test_mostly_absent(self):
        doc = {"o.txt": "Office\n\nThe office has a gym and a large meeting room.\n"}
        r = self.check("The office has a rooftop swimming pool and a gym.", doc)
        self.assertEqual(r.abstain_reason, "mostly_absent")
        self.assertEqual(len(r.closest), 1)

    def test_closest_passages_are_marked_with_context(self):
        r = self.check("The refund window is 45 days for members.", {"old.txt": OLD_POLICY})
        self.assertTrue(r.closest)
        self.assertEqual(r.closest[0].heading, "Superseded policy (2022)")
        self.assertEqual(r.closest[0].context, "flagged_context")

    def test_deterministic(self):
        docs = {"policy.txt": POLICY, "old.txt": OLD_POLICY}
        corpus = self.corpus(docs)
        first = check_claim(Claim(1, "The refund window is 30 days.", 1), corpus)
        second = check_claim(Claim(1, "The refund window is 30 days.", 1), corpus)
        self.assertEqual(first, second)


if __name__ == "__main__":
    unittest.main()
