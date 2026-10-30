import unittest

from citecheck.tokens import match_tokens, stem, tokenize


def norms(text):
    return [t.norm for t in tokenize(text)]


class TokenTests(unittest.TestCase):
    def test_contractions_expand_to_not(self):
        self.assertEqual(norms("doesn't"), ["does", "not"])
        self.assertEqual(norms("doesn’t"), ["does", "not"])
        self.assertEqual(norms("cannot"), ["can", "not"])
        self.assertEqual(norms("can't"), ["can", "not"])
        self.assertEqual(norms("won't"), ["will", "not"])

    def test_number_words_become_digits(self):
        self.assertEqual(norms("two years"), ["2", "year"])
        self.assertEqual(norms("twelve months"), ["12", "month"])

    def test_number_normalisation(self):
        self.assertEqual(norms("1,000"), ["1000"])
        # Rule set 0.4.0 keeps numbers as written, so "3.50" and "3.5" stay different.
        self.assertEqual(norms("3.50"), ["3.50"])
        self.assertEqual(norms("30"), ["30"])

    def test_percent_forms_are_equal(self):
        self.assertEqual(norms("15%"), ["15", "%"])
        self.assertEqual(norms("15 percent"), ["15", "%"])
        self.assertEqual(norms("15 per cent"), ["15", "%"])

    def test_light_stemming(self):
        self.assertEqual(stem("covers"), "cover")
        self.assertEqual(stem("policies"), "policy")
        self.assertEqual(stem("classes"), "class")
        self.assertEqual(stem("boxes"), "box")
        self.assertEqual(stem("is"), "is")
        self.assertEqual(stem("does"), "does")
        self.assertEqual(stem("status"), "status")
        self.assertEqual(stem("glass"), "glass")
        self.assertEqual(stem("cancelled"), "cancelled")

    def test_case_and_possessive(self):
        self.assertEqual(norms("Northwind's Kettles"), ["northwind", "kettle"])

    def test_offsets_point_at_original_text(self):
        text = "  The cat's £20 fee."
        for tok in tokenize(text):
            self.assertTrue(text[tok.start:tok.end])
        self.assertEqual([text[t.start:t.end] for t in tokenize(text)], ["The", "cat's", "£", "20", "fee"])
        shifted = tokenize("fee", offset=10)
        self.assertEqual((shifted[0].start, shifted[0].end), (10, 13))

    def test_meaning_changing_symbols_are_kept(self):
        # QA finding F7: "<5" and ">5" must not look the same.
        self.assertEqual(norms("<5 minutes"), ["<", "5", "minute"])
        self.assertEqual(norms(">5 minutes"), [">", "5", "minute"])
        self.assertEqual(norms("<=5 and >=10"), ["≤", "5", "and", "≥", "10"])
        self.assertEqual(norms("±2 and ~3 and 18+"), ["±", "2", "and", "~", "3", "and", "18", "+"])
        self.assertEqual(norms("It was -5 degrees"), ["it", "was", "-", "5", "degree"])
        self.assertEqual(norms("20°C"), ["20", "°", "c"])

    def test_hyphens_in_words_and_ranges_are_not_minus_signs(self):
        self.assertEqual(norms("9am-5pm"), ["9", "am", "5", "pm"])
        self.assertEqual(norms("2020-2021"), ["2020", "2021"])
        self.assertEqual(norms("interest-free"), ["interest", "free"])

    def test_numbers_are_kept_as_written(self):
        # QA finding F8: versions, codes and dates must not merge.
        self.assertEqual(norms("3.10"), ["3.10"])
        self.assertEqual(norms("05"), ["05"])
        self.assertEqual(norms("1,000.50"), ["1000.50"])

    def test_ambiguous_number_formats(self):
        for text in ("1,5", "1,50", "1.000", "1.2.3"):
            with self.subTest(text=text):
                (tok,) = tokenize(text)
                self.assertEqual(tok.kind, "ambiguous_number")
                self.assertEqual(tok.norm, text)
        self.assertEqual(tokenize("4.2")[0].kind, "number")
        self.assertEqual(tokenize("1,000")[0].kind, "number")

    def test_one_is_not_turned_into_a_number(self):
        # QA finding F19: "one" is often a pronoun.
        self.assertEqual(norms("one of the stores"), ["one", "of", "the", "store"])
        self.assertEqual(norms("two stores"), ["2", "store"])

    def test_match_tokens_drop_articles_only(self):
        words = [t.word for t in match_tokens(tokenize("The plan is a deal for an hour"))]
        self.assertEqual(words, ["plan", "is", "deal", "for", "hour"])


if __name__ == "__main__":
    unittest.main()
