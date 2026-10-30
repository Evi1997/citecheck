import unittest

from citecheck.splitter import sentence_spans, split_answer, split_document


def sentences(text):
    return [text[a:b] for a, b in sentence_spans(text)]


class SentenceBoundaryTests(unittest.TestCase):
    def test_basic_split(self):
        self.assertEqual(sentences("One two three. Four five six! Seven eight?"),
                         ["One two three.", "Four five six!", "Seven eight?"])

    def test_abbreviations_are_not_boundaries(self):
        self.assertEqual(sentences("Dr. Smith arrived. He left."), ["Dr. Smith arrived.", "He left."])
        self.assertEqual(sentences("The U.S. office opened. It is small."),
                         ["The U.S. office opened.", "It is small."])

    def test_initials_and_decimals(self):
        self.assertEqual(sentences("M. Okafor joined in 2019. Sales rose."),
                         ["M. Okafor joined in 2019.", "Sales rose."])
        self.assertEqual(sentences("Revenue was 4.2 million. Costs fell."),
                         ["Revenue was 4.2 million.", "Costs fell."])

    def test_lowercase_after_full_stop_is_not_a_boundary(self):
        self.assertEqual(sentences("Prices include VAT, e.g. on kettles. Done here."),
                         ["Prices include VAT, e.g. on kettles.", "Done here."])

    def test_semicolon_is_a_boundary(self):
        self.assertEqual(sentences("A is true; b is false."), ["A is true;", "b is false."])

    def test_closing_quote_after_full_stop(self):
        self.assertEqual(sentences('She said "it will grow." Sales rose.'),
                         ['She said "it will grow."', "Sales rose."])


class AnswerSplitTests(unittest.TestCase):
    def test_skip_reasons(self):
        text = "Here is a summary:\n\nIs this right?\nOk then.\n## Big heading here\nThe plan costs £20.\n"
        split = split_answer(text)
        self.assertEqual([c.text for c in split.claims], ["The plan costs £20."])
        reasons = {(s.line, s.reason) for s in split.skipped}
        self.assertEqual(reasons, {(1, "lead_in"), (3, "question"), (4, "too_short"), (5, "heading")})
        self.assertEqual(split.blank_lines, 1)

    def test_code_and_tables_are_skipped(self):
        text = "| a | b |\n```\nprint('x is y')\n```\nThe plan costs £20.\n"
        split = split_answer(text)
        self.assertEqual([c.text for c in split.claims], ["The plan costs £20."])
        self.assertEqual([s.reason for s in split.skipped], ["code_or_table"] * 4)

    def test_markdown_is_cleaned(self):
        text = "1. **Northwind** sells kettles [1].\n- The [warranty page](https://example.com) lists all terms.\n"
        split = split_answer(text)
        self.assertEqual([c.text for c in split.claims],
                         ["Northwind sells kettles.", "The warranty page lists all terms."])

    def test_source_lines_and_ids(self):
        split = split_answer("First claim is here. Second claim is here.\n\nThird claim is here.\n")
        self.assertEqual([(c.id, c.line) for c in split.claims], [(1, 1), (2, 1), (3, 3)])

    def test_semicolon_is_removed_from_claim_text(self):
        split = split_answer("The plan has support; the other plan does not.")
        self.assertEqual([c.text for c in split.claims], ["The plan has support", "the other plan does not."])

    def test_lines_mode_keeps_one_claim_per_line(self):
        split = split_answer("One claim is here. Still the same line.\nAnother line here.\n", mode="lines")
        self.assertEqual([c.text for c in split.claims],
                         ["One claim is here. Still the same line.", "Another line here."])

    def test_chat_and_navigation_text_is_skipped(self):
        text = (
            "The plan costs £20 per month.\n"
            "Hope this helps!\n"
            "Feel free to ask a follow-up question.\n"
            "Let me know if you have any other questions.\n"
            "If you need anything else, just ask.\n"
            "I'd be happy to explain the refund rules.\n"
            "Read more in the official guide.\n"
            "For more information, visit the help centre.\n"
            "See the pricing page for details.\n"
            "Click here to compare plans.\n"
        )
        split = split_answer(text)
        self.assertEqual([c.text for c in split.claims], ["The plan costs £20 per month."])
        self.assertEqual({s.reason for s in split.skipped}, {"meta"})
        self.assertEqual(len(split.skipped), 9)

    def test_similar_looking_claims_are_kept(self):
        text = (
            "Seeds are sold in spring.\n"
            "Visitors must sign in at reception.\n"
            "Readers can borrow five books.\n"
            "You can cancel the plan at any time.\n"
            "If you cancel, your data is deleted after 30 days.\n"
            "Note: the fee is £5 per visit.\n"
            "Helpers receive a free lunch.\n"
        )
        split = split_answer(text)
        self.assertEqual(len(split.claims), 7)
        self.assertEqual(split.skipped, [])

    def test_source_lists_are_skipped(self):
        text = (
            "Revenue grew last year.\n"
            "Sources: [1] Company site. [2] Annual report.\n"
            "References: Smith (2020); Jones (2021).\n"
            "See also: the 2023 report.\n"
            "Source material was checked by the team.\n"
        )
        split = split_answer(text)
        self.assertEqual([c.text for c in split.claims],
                         ["Revenue grew last year.", "Source material was checked by the team."])
        self.assertEqual([s.reason for s in split.skipped], ["sources_list"] * 3)

    def test_lines_mode_also_skips_meta(self):
        split = split_answer("Gift cards are not refundable.\nHope this helps!\n", mode="lines")
        self.assertEqual([c.text for c in split.claims], ["Gift cards are not refundable."])
        self.assertEqual([s.reason for s in split.skipped], ["meta"])

    def test_split_is_deterministic(self):
        text = "A b c. D e f.\n- G h i.\n"
        self.assertEqual(split_answer(text), split_answer(text))


class DocumentSplitTests(unittest.TestCase):
    def test_headings_are_context_not_sentences(self):
        text = "Superseded policy (2022)\n\nThe refund window is 30 days.\n\n# Current\n\nIt is 14 days.\n"
        sents = split_document(text)
        self.assertEqual([text[s.start:s.end] for s in sents], ["The refund window is 30 days.", "It is 14 days."])
        self.assertEqual([s.heading for s in sents], ["Superseded policy (2022)", "Current"])

    def test_hard_wrapped_lines_join_and_list_items_split(self):
        text = "The plan costs\n£20 per month.\n- Item one is here.\n- Item two is here.\n"
        sents = split_document(text)
        self.assertEqual([text[s.start:s.end] for s in sents],
                         ["The plan costs\n£20 per month.", "Item one is here.", "Item two is here."])

    def test_spans_exclude_markers_and_whitespace(self):
        text = "Intro line one is long enough.\n\n  - Indented item text.\n"
        for s in split_document(text):
            span = text[s.start:s.end]
            self.assertEqual(span, span.strip())
            self.assertFalse(span.startswith("-"))

    def test_semicolons_do_not_split_document_sentences(self):
        # QA finding F10: the restriction after the semicolon stays with the sentence.
        text = "Rules\n\nRefunds are available; sale items are excluded. Shipping is free.\n"
        self.assertEqual([text[s.start:s.end] for s in split_document(text)],
                         ["Refunds are available; sale items are excluded.", "Shipping is free."])

    def test_sentences_record_their_paragraph(self):
        text = "Title\n\nOne is here. Two is here.\n\nThree is here.\n- Four is here.\n"
        self.assertEqual([s.block for s in split_document(text)][:3], [1, 1, 2])
        self.assertEqual(len({s.block for s in split_document(text)}), 3)

    def test_long_lead_in_becomes_context_for_the_list(self):
        text = "The rules below apply to students who live in the city only:\n- Laptops can be borrowed.\n"
        sents = split_document(text)
        self.assertEqual(sents[-1].heading, "The rules below apply to students who live in the city only:")

    def test_cr_only_documents(self):
        text = "Title here\r\rFirst sentence is here.\rSecond one is here.\r"
        self.assertEqual([text[s.start:s.end] for s in split_document(text)],
                         ["First sentence is here.", "Second one is here."])

    def test_cr_only_answers(self):
        split = split_answer("First claim is here.\rSecond claim is here.\r")
        self.assertEqual([(c.text, c.line) for c in split.claims],
                         [("First claim is here.", 1), ("Second claim is here.", 2)])

    def test_crlf_documents(self):
        text = "Title here\r\n\r\nFirst sentence is here.\r\nSecond one is here.\r\n"
        sents = split_document(text)
        self.assertEqual([text[s.start:s.end] for s in sents],
                         ["First sentence is here.", "Second one is here."])


if __name__ == "__main__":
    unittest.main()
