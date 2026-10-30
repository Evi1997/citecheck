"""The release guard on eval --subset test, and the freeze record.

Every test here builds its own small SYNTHETIC dataset in a temporary folder.
None of them opens, parses or evaluates the bundled eval/data/test.jsonl.
"""

import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from citecheck import __version__
from citecheck.cli import main
from citecheck.cues import RULESET_VERSION
from citecheck.evaluation import freeze
from citecheck.evaluation import run as run_module
from citecheck.evaluation.dataset import load_cases, load_docs
from citecheck.evaluation.guard import FREEZE_FILE, build_record, data_digest

DOC = ("SYNTHETIC TEST DOCUMENT.\n\nRefund Policy\n\n"
       "The refund window is 14 days.\nGift cards are not refundable.\n")
REVIEWED = {"synthetic": True, "review_status": "reviewed", "reviewer": "unit-test fixture"}


def case(cid, subset, claim, label, scope, case_type, quote=None, provenance=REVIEWED):
    quotes = [{"doc": "policy.txt", "quote": quote}] if quote else []
    return {"id": cid, "subset": subset, "claim": claim, "docs": ["policy.txt"], "gold_label": label,
            "scope": scope, "case_type": case_type, "gold_quotes": quotes, "provenance": dict(provenance)}


def make_test_cases(provenance=REVIEWED):
    # The second case carries the provenance under test; the others are reviewed.
    return [
        case("t-1", "test", "The refund window is 14 days.", "SUPPORTED", "in", "verbatim",
             "The refund window is 14 days."),
        case("t-2", "test", "Gift cards are refundable.", "CONTRADICTED", "in", "negation",
             "Gift cards are not refundable.", provenance),
        case("t-3", "test", "The shop opens on Sundays.", "INSUFFICIENT_EVIDENCE", None, "absent"),
    ]


DEV_CASES = [case("d-1", "dev", "The refund window is 14 days.", "SUPPORTED", "in", "verbatim",
                  "The refund window is 14 days.")]


def cli(argv):
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = main(argv)
    return code, out.getvalue(), err.getvalue()


def run_freeze(eval_dir):
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = freeze.main([str(eval_dir)])
    return code, out.getvalue(), err.getvalue()


class SyntheticEvalSet(unittest.TestCase):
    """A three-case synthetic test subset and a one-case dev subset in a temp folder."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.root = self.tmp / "eval"
        (self.root / "docs").mkdir(parents=True)
        (self.root / "data").mkdir()
        (self.root / "docs" / "policy.txt").write_text(DOC, encoding="utf-8")
        self.write("dev", DEV_CASES)
        self.write("test", make_test_cases())
        self.out = self.tmp / "out"

    def tearDown(self):
        self._tmp.cleanup()

    def write(self, subset, rows):
        with open(self.root / "data" / f"{subset}.jsonl", "w", encoding="utf-8") as fh:
            for row in rows:
                fh.write(json.dumps(row) + "\n")

    def assert_refused(self, argv, reason):
        # If the guard let a run through, check_claim would be called and raise.
        with mock.patch.object(run_module, "check_claim",
                               side_effect=AssertionError("a test case was evaluated")) as checker:
            code, out, err = cli(argv)
        self.assertEqual(code, 2, err)
        self.assertIn("Refused:", err)
        self.assertIn(reason, err)
        self.assertIn("No test case was evaluated", err)
        checker.assert_not_called()
        self.assertFalse(self.out.exists(), "a refused run wrote a results file")
        self.assertNotIn("RELEASE CHECKS", out)


class ReleaseGuardTests(SyntheticEvalSet):
    def argv(self, *extra):
        return ["eval", "--subset", "test", "--dataset", str(self.root), "--out", str(self.out), *extra]

    def test_unfrozen_test_subset_is_refused(self):
        self.assert_refused(self.argv(), "hasn't been frozen")

    def test_ordinary_options_cannot_bypass_the_guard(self):
        d, o = str(self.root), str(self.out)
        variants = [
            ["eval", "--subset=test", "--dataset", d, "--out", o],
            ["eval", "--dataset", d, "--subset", "dev", "--subset", "test", "--out", o],
            ["eval", "--sub", "test", "--dataset", d, "--out", o],
            ["eval", "--subset", "test", "--dataset", d, "--out", o, "--debug"],
            ["eval", "--subset", "test", "--dataset", d, "--out", o, "--no-color"],
            ["eval", "--subset", "test", "--dataset=" + d, "--out=" + o],
        ]
        for argv in variants:
            with self.subTest(argv=argv):
                self.assert_refused(argv, "hasn't been frozen")

    def test_default_dataset_location_is_guarded_too(self):
        # Points the default at the synthetic set, so the bundled set is never touched.
        with mock.patch.object(run_module, "DEFAULT_EVAL_DIR", self.root):
            self.assert_refused(["eval", "--subset", "test", "--out", str(self.out)], "hasn't been frozen")

    def test_guard_runs_before_test_cases_are_parsed(self):
        # A broken test file would give a dataset error if it were parsed first.
        (self.root / "data" / "test.jsonl").write_text("not json\n", encoding="utf-8")
        self.assert_refused(self.argv(), "hasn't been frozen")

    def test_changed_data_after_freezing_is_refused(self):
        self.assertEqual(run_freeze(self.root)[0], 0)
        with open(self.root / "docs" / "policy.txt", "a", encoding="utf-8") as fh:
            fh.write("Refunds take 5 days.\n")
        self.assert_refused(self.argv(), "changed since it was frozen")

    def test_invalid_freeze_records_are_refused(self):
        for content in ("not json", "[]", "{}", '{"format": 1}', '{"format": 2, "fingerprint_sha256": "x"}'):
            with self.subTest(content=content):
                (self.root / FREEZE_FILE).write_text(content, encoding="utf-8")
                self.assert_refused(self.argv(), FREEZE_FILE)

    def test_frozen_but_unreviewed_cases_are_refused(self):
        # A hand-made record over unreviewed cases: the freeze command itself
        # would refuse, so the guard has to catch it too.
        unreviewed = {"synthetic": True, "review_status": "unreviewed"}
        self.write("test", make_test_cases(unreviewed))
        cases = load_cases(self.root, load_docs(self.root), subsets=("test",))
        record = build_record(self.root, cases, {}, "2026-10-29")
        (self.root / FREEZE_FILE).write_text(json.dumps(record), encoding="utf-8")
        self.assert_refused(self.argv(), "1 of 3 test cases haven't been reviewed by a named person (t-2)")

    def test_a_reviewed_case_needs_reviewed_status_and_a_named_reviewer(self):
        variants = [
            {"synthetic": True, "review_status": "disputed", "reviewer": "owner"},
            {"synthetic": True, "review_status": "unreviewed", "reviewer": "owner"},
            {"synthetic": True, "review_status": "reviewed"},
            {"synthetic": True, "review_status": "reviewed", "reviewer": "   "},
            {"synthetic": True, "review_status": "reviewed", "reviewer": None},
        ]
        for provenance in variants:
            with self.subTest(provenance=provenance):
                self.write("test", make_test_cases(provenance))
                cases = load_cases(self.root, load_docs(self.root), subsets=("test",))
                record = build_record(self.root, cases, {}, "2026-10-29")
                (self.root / FREEZE_FILE).write_text(json.dumps(record), encoding="utf-8")
                self.assert_refused(self.argv(), "haven't been reviewed")

    def test_reviewed_and_frozen_test_subset_runs(self):
        self.assertEqual(run_freeze(self.root)[0], 0)
        code, out, err = cli(self.argv())
        self.assertEqual(code, 0, err)
        self.assertIn("RELEASE EVALUATION", out)
        self.assertIn("RELEASE CHECKS", out)
        self.assertIn(f"under rule set {RULESET_VERSION}", out)
        self.assertIn("written independently of the rules", out)
        self.assertIn("False-contradicted rate (provisional)", out)
        self.assertNotIn("PENDING GATES", out)
        results = json.loads((self.out / "eval-results.json").read_text(encoding="utf-8"))
        self.assertEqual(results["result_type"], "release_evaluation")
        self.assertEqual(results["dataset"]["count"], 3)
        self.assertEqual(results["freeze"]["ruleset_version"], RULESET_VERSION)
        self.assertEqual(results["freeze"]["fingerprint_sha256"], data_digest(self.root)[1])
        self.assertNotIn("tuning_target_comparison", results)
        self.assertEqual(results["pending_gates"], [])
        gate = {c["metric"]: c for c in results["release_checks"]}["false_contradicted_rate"]
        self.assertEqual(gate["target"], "<= 0.05")
        # t-1 (SUPPORTED) and t-3 (INSUFFICIENT_EVIDENCE) are the non-CONTRADICTED cases.
        self.assertEqual(gate["numerator_denominator"][1], 2)


class FreezeTests(SyntheticEvalSet):
    def test_freeze_refuses_unreviewed_cases(self):
        self.write("test", make_test_cases({"synthetic": True, "review_status": "unreviewed"}))
        code, _, err = run_freeze(self.root)
        self.assertEqual(code, 2)
        self.assertIn("1 of 3 test cases haven't been reviewed", err)
        self.assertFalse((self.root / FREEZE_FILE).exists())

    def test_freeze_records_ruleset_version_and_fingerprint(self):
        code, out, err = run_freeze(self.root)
        self.assertEqual(code, 0, err)
        self.assertIn(f"rule set {RULESET_VERSION}", out)
        record = json.loads((self.root / FREEZE_FILE).read_text(encoding="utf-8"))
        files, digest = data_digest(self.root)
        self.assertEqual(record["format"], 1)
        self.assertEqual(record["subset"], "test")
        self.assertEqual(record["ruleset_version"], RULESET_VERSION)
        self.assertEqual(record["citecheck_version"], __version__)
        self.assertEqual(record["fingerprint_sha256"], digest)
        self.assertEqual(record["files"], files)
        self.assertTrue(any(line.endswith("  data/test.jsonl") for line in files))
        self.assertTrue(any(line.endswith("  docs/policy.txt") for line in files))
        self.assertFalse(any("dev.jsonl" in line for line in files))
        self.assertEqual((record["test_cases"], record["reviewed_cases"]), (3, 3))

    def test_freeze_never_replaces_an_existing_record(self):
        self.assertEqual(run_freeze(self.root)[0], 0)
        before = (self.root / FREEZE_FILE).read_text(encoding="utf-8")
        code, _, err = run_freeze(self.root)
        self.assertEqual(code, 1)
        self.assertIn("already exists", err)
        self.assertEqual((self.root / FREEZE_FILE).read_text(encoding="utf-8"), before)

    def test_fingerprint_covers_test_cases(self):
        _, before = data_digest(self.root)
        self.write("test", make_test_cases()[:2])
        self.assertNotEqual(data_digest(self.root)[1], before)


class DevSubsetTests(SyntheticEvalSet):
    def test_dev_eval_never_parses_the_test_file(self):
        (self.root / "data" / "test.jsonl").write_text("not json\n", encoding="utf-8")
        code, out, err = cli(["eval", "--subset", "dev", "--dataset", str(self.root), "--out", str(self.out)])
        self.assertEqual(code, 0, err)
        self.assertIn("TUNING RESULTS ONLY", out)
        results = json.loads((self.out / "eval-results.json").read_text(encoding="utf-8"))
        self.assertEqual(results["result_type"], "tuning_only")
        self.assertIsNone(results["freeze"])
        self.assertIn("tuning_target_comparison", results)
        self.assertNotIn("release_checks", results)

    def test_dev_eval_shows_counts_next_to_rates(self):
        code, out, err = cli(["eval", "--subset", "dev", "--dataset", str(self.root), "--out", str(self.out)])
        self.assertEqual(code, 0, err)
        self.assertIn("False-support rate", out)
        self.assertIn("(0/0)", out)  # no non-SUPPORTED dev cases in this fixture
        self.assertIn("False-contradicted rate", out)
        # A zero denominator is shown as n/a and never counts as met.
        self.assertIn("not met False-support rate: n/a (0/0), no cases to measure", out)


if __name__ == "__main__":
    unittest.main()
