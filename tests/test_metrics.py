import unittest

from citecheck.evaluation.dataset import Case, GoldQuote, SplittingCase
from citecheck.evaluation.metrics import checks, compute, historical, pending, ratio, splitting_scores
from citecheck.splitter import Claim
from citecheck.thresholds import HISTORICAL_BENCHMARKS, PENDING_GATES, RELEASE_TARGETS
from citecheck.verdict import ClaimResult, Evidence, Passage

S, C, I = "SUPPORTED", "CONTRADICTED", "INSUFFICIENT_EVIDENCE"


def case(cid, gold, scope=None, case_type="verbatim", quote=(0, 10)):
    quotes = [GoldQuote("d.txt", "x", quote[0], quote[1])] if gold != I else []
    return Case(cid, "test", "claim", ["d.txt"], gold, scope, case_type, quotes,
                {"synthetic": True, "review_status": "unreviewed"})


def pred(label, span=None, verified=True, closest=None):
    evidence = []
    if span is not None:
        evidence = [Evidence("d.txt", 1, span[0], span[1], "x", "S1" if label == S else "C2", {}, verified)]
    passages = [Passage(i + 1, "d.txt", 1, a, b, "x") for i, (a, b) in enumerate(closest or [])]
    return ClaimResult(Claim(1, "claim", 1), label, evidence=evidence, closest=passages)


class MetricTests(unittest.TestCase):
    def setUp(self):
        self.cases = [
            case("s1", S, "in"), case("s2", S, "out", "paraphrase", (20, 30)),
            case("c1", C, "in"), case("c2", C, "out", "antonym"),
            case("i1", I, case_type="keyword_trap"), case("i2", I, case_type="absent"),
        ]
        self.preds = {
            "s1": pred(S, (0, 10)),
            "s2": pred(I, closest=[(20, 30)]),
            "c1": pred(C, (0, 10)),
            "c2": pred(I, closest=[(50, 60)]),
            "i1": pred(S, (40, 45)),
            "i2": pred(I),
        }
        self.m = compute(self.cases, self.preds, [])

    def test_confusion_and_precision(self):
        self.assertEqual(self.m["confusion"][S], {S: 1, C: 0, I: 1})
        self.assertEqual(self.m["precision_SUPPORTED"], 0.5)
        self.assertEqual(self.m["recall_SUPPORTED"], 0.5)
        self.assertEqual(self.m["precision_CONTRADICTED"], 1.0)
        self.assertAlmostEqual(self.m["precision_INSUFFICIENT_EVIDENCE"], 1 / 3)

    def test_in_scope_recall(self):
        self.assertEqual(self.m["in_scope_recall_SUPPORTED"], 1.0)
        self.assertEqual(self.m["in_scope_recall_CONTRADICTED"], 1.0)

    def test_false_support_rate_uses_non_supported_gold_cases(self):
        # Owner decision 3: 1 wrong SUPPORTED out of 4 cases whose gold label is not SUPPORTED.
        self.assertEqual(self.m["false_support_rate"], 0.25)
        self.assertAlmostEqual(self.m["false_support_share_of_all"], 1 / 6)
        self.assertEqual(self.m["keyword_trap_supported"], 1)

    def test_counts_are_kept_next_to_rates(self):
        f = self.m["fractions"]
        self.assertEqual(f["precision_SUPPORTED"], [1, 2])
        self.assertEqual(f["precision_CONTRADICTED"], [1, 1])
        self.assertEqual(f["false_support_rate"], [1, 4])
        self.assertEqual(f["keyword_trap_supported"], [1, 1])
        self.assertEqual(f["quote_verbatim_rate"], [3, 3])
        self.assertEqual(f["citation_verified_correctness"], [2, 3])
        result = {c["metric"]: c["numerator_denominator"] for c in checks(self.m)}
        self.assertEqual(result["precision_SUPPORTED"], [1, 2])
        self.assertEqual(result["false_support_rate"], [1, 4])

    def test_false_contradicted_rate_uses_non_contradicted_gold_cases(self):
        # No case outside gold CONTRADICTED was predicted CONTRADICTED here.
        self.assertEqual(self.m["false_contradicted_rate"], 0.0)
        self.assertEqual(self.m["fractions"]["false_contradicted_rate"], [0, 4])
        m = compute([case("s", S), case("i", I), case("c", C)],
                    {"s": pred(C, (0, 10)), "i": pred(I), "c": pred(C, (0, 10))}, [])
        self.assertEqual(m["false_contradicted_rate"], 0.5)
        self.assertEqual(m["fractions"]["false_contradicted_rate"], [1, 2])

    def test_false_contradicted_is_a_provisional_release_gate(self):
        # Owner decision 2026-10-29: <= 5%, symmetrical with false-support.
        self.assertEqual(PENDING_GATES, ())
        self.assertEqual(pending(self.m), [])
        result = {c["metric"]: c for c in checks(self.m)}["false_contradicted_rate"]
        self.assertEqual(result["target"], "<= 0.05")
        self.assertIn("provisional", result["name"])
        self.assertEqual(result["numerator_denominator"], [0, 4])
        self.assertTrue(result["pass"])

    def test_false_contradicted_gate_at_and_above_threshold(self):
        # 1 wrong CONTRADICTED out of 20 non-CONTRADICTED cases is 5%: passes.
        # 2 out of 20 is 10%: fails.
        for wrong, expected in ((1, True), (2, False)):
            with self.subTest(wrong=wrong):
                cases = [case(f"i{n}", I) for n in range(20)]
                preds = {f"i{n}": pred(C, (0, 10)) if n < wrong else pred(I) for n in range(20)}
                m = compute(cases, preds, [])
                self.assertEqual(m["fractions"]["false_contradicted_rate"], [wrong, 20])
                result = {c["metric"]: c["pass"] for c in checks(m)}
                self.assertEqual(result["false_contradicted_rate"], expected)

    def test_false_contradicted_zero_denominator_is_na_not_a_pass(self):
        # Only gold CONTRADICTED cases: nothing to measure.
        m = compute([case("c", C)], {"c": pred(C, (0, 10))}, [])
        self.assertIsNone(m["false_contradicted_rate"])
        self.assertEqual(m["fractions"]["false_contradicted_rate"], [0, 0])
        result = {c["metric"]: c["pass"] for c in checks(m)}
        self.assertFalse(result["false_contradicted_rate"])

    def test_abstention_and_coverage(self):
        self.assertAlmostEqual(self.m["abstention_correctness"], 1 / 3)
        self.assertEqual(self.m["coverage"], 0.5)

    def test_citation_metrics(self):
        self.assertEqual(self.m["quote_verbatim_rate"], 1.0)
        # Decided: s1 (correct, overlaps), c1 (correct, overlaps), i1 (wrong label).
        self.assertAlmostEqual(self.m["citation_verified_correctness"], 2 / 3)
        # Gold S/C cases: s1 hit (evidence), s2 hit (closest), c1 hit, c2 miss.
        self.assertEqual(self.m["hit_at_3"], 0.75)

    def test_v02_indicators(self):
        self.assertEqual(self.m["v02_abstained_share"], 0.5)
        self.assertEqual(self.m["v02_abstained_out_of_scope_share"], 1.0)
        self.assertEqual(self.m["v02_abstained_hit_at_3"], 0.5)

    def test_divide_by_zero_is_none_and_fails_checks(self):
        self.assertIsNone(ratio(0, 0))
        empty = compute([case("i", I)], {"i": pred(I)}, [])
        self.assertIsNone(empty["precision_SUPPORTED"])
        result = {c["metric"]: c["pass"] for c in checks(empty)}
        self.assertFalse(result["precision_SUPPORTED"])

    def test_release_targets_match_prd(self):
        targets = {key: (op, value) for key, op, value, _ in RELEASE_TARGETS}
        self.assertEqual(targets, {
            "citation_verified_correctness": (">=", 0.90),
            "quote_verbatim_rate": (">=", 1.00),
            "false_support_rate": ("<=", 0.05),
            "false_contradicted_rate": ("<=", 0.05),
            "keyword_trap_supported": ("<=", 0),
            "precision_SUPPORTED": (">=", 0.90),
            "precision_CONTRADICTED": (">=", 0.85),
            "recall_INSUFFICIENT_EVIDENCE": (">=", 0.85),
            "in_scope_recall_SUPPORTED": (">=", 0.90),
            "in_scope_recall_CONTRADICTED": (">=", 0.90),
            "hit_at_3": (">=", 0.80),
            "splitting_recall": (">=", 0.95),
        })

    def test_historical_benchmarks_are_kept(self):
        keys = {key: (op, value) for key, op, value, _ in HISTORICAL_BENCHMARKS}
        self.assertEqual(keys, {
            "recall_SUPPORTED": (">=", 0.60),
            "recall_CONTRADICTED": (">=", 0.60),
            "abstention_correctness": (">=", 0.85),
        })
        self.assertEqual(len(historical(self.m)), 3)

    def test_splitting_scores(self):
        scores = splitting_scores([SplittingCase("a.txt", "One two three. Four five six.\nSeven?\n",
                                                 [(1, "One two three."), (1, "Four five six."), (2, "x y z.")])])
        self.assertEqual(scores["splitting_gold_claims"], 3)
        self.assertEqual(scores["splitting_recovered"], 2)
        self.assertAlmostEqual(scores["splitting_recall"], 2 / 3)
        self.assertEqual(scores["splitting_precision"], 1.0)
        self.assertEqual(scores["splitting_extra_claims"], 0)

    def test_extra_claims_lower_precision_not_recall(self):
        # Every expected claim is found, plus one ordinary statement the splitter
        # keeps but the expected list leaves out. It must lower precision only.
        scores = splitting_scores([SplittingCase("b.txt", "The shop opens at nine. The car park closes at six.\n",
                                                 [(1, "The shop opens at nine.")])])
        self.assertEqual(scores["splitting_produced_claims"], 2)
        self.assertEqual(scores["splitting_recall"], 1.0)
        self.assertEqual(scores["splitting_precision"], 0.5)
        self.assertEqual(scores["splitting_extra_claims"], 1)
        self.assertEqual(scores["splitting_extra_list"],
                         [{"file": "b.txt", "line": 1, "text": "The car park closes at six."}])

    def test_skipped_chat_text_is_not_an_extra_claim(self):
        # Rule set 0.3.0 skips chat text such as "Let me know...", so it is never
        # produced as a claim and can't count against precision.
        scores = splitting_scores([SplittingCase("c.txt", "The shop opens at nine. Let me know if you need more!\n",
                                                 [(1, "The shop opens at nine.")])])
        self.assertEqual(scores["splitting_produced_claims"], 1)
        self.assertEqual(scores["splitting_recall"], 1.0)
        self.assertEqual(scores["splitting_precision"], 1.0)
        self.assertEqual(scores["splitting_extra_list"], [])


if __name__ == "__main__":
    unittest.main()
