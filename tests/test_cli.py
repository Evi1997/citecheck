import contextlib
import io
import json

from citecheck.cli import main
from tests.helpers import APP_DIR, TempDocsTestCase

DOC = "Refund Policy\n\nThe refund window is 14 days.\nGift cards are not refundable.\n"
ANSWER = "The refund window is 14 days.\nGift cards are refundable.\nThe shop opens on Sundays.\n"


def run(argv):
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = main(argv)
    return code, out.getvalue(), err.getvalue()


class CheckCommandTests(TempDocsTestCase):
    def setUp(self):
        super().setUp()
        self.write_docs({"policy.txt": DOC})
        self.answer = self.tmp / "answer.txt"
        self.answer.write_text(ANSWER, encoding="utf-8")
        self.out = self.tmp / "out"

    def test_check_succeeds_and_writes_reports(self):
        code, out, err = run(["check", str(self.answer), str(self.docs_dir), "--out", str(self.out), "--no-color"])
        self.assertEqual(code, 0, err)
        self.assertIn("[SUPPORTED]", out)
        self.assertIn("[CONTRADICTED]", out)
        self.assertIn("[INSUFFICIENT EVIDENCE]", out)
        report = json.loads((self.out / "report.json").read_text(encoding="utf-8"))
        self.assertEqual([r["label"] for r in report["results"]],
                         ["SUPPORTED", "CONTRADICTED", "INSUFFICIENT_EVIDENCE"])
        self.assertTrue((self.out / "report.md").is_file())

    def test_quiet_and_format(self):
        code, out, _ = run(["check", str(self.answer), str(self.docs_dir), "--out", str(self.out),
                            "--format", "json", "--quiet"])
        self.assertEqual(code, 0)
        self.assertNotIn("RESULTS", out)
        self.assertIn("SUMMARY", out)
        self.assertTrue((self.out / "report.json").is_file())
        self.assertFalse((self.out / "report.md").exists())

    def test_missing_answer_file(self):
        code, _, err = run(["check", str(self.tmp / "nope.txt"), str(self.docs_dir)])
        self.assertEqual(code, 2)
        self.assertIn("Can't find the answer file", err)

    def test_docs_folder_is_a_file(self):
        code, _, err = run(["check", str(self.answer), str(self.answer)])
        self.assertEqual(code, 2)
        self.assertIn("is a file, not a folder", err)

    def test_no_documents(self):
        empty = self.tmp / "empty"
        empty.mkdir()
        code, _, err = run(["check", str(self.answer), str(empty)])
        self.assertEqual(code, 2)
        self.assertIn("No documents found", err)

    def test_no_claims(self):
        self.answer.write_text("\nAny questions?\nSummary:\n", encoding="utf-8")
        code, _, err = run(["check", str(self.answer), str(self.docs_dir)])
        self.assertEqual(code, 2)
        self.assertIn("Nothing to check", err)

    def test_unknown_option_suggests_a_fix(self):
        code, _, err = run(["check", str(self.answer), str(self.docs_dir), "--foramt", "md"])
        self.assertEqual(code, 2)
        self.assertIn('Unknown option "--foramt". Did you mean "--format"?', err)

    def test_version(self):
        code, out, _ = run(["--version"])
        self.assertEqual(code, 0)
        self.assertIn("CiteCheck 0.1.0", out)


class DemoTests(TempDocsTestCase):
    """The synthetic demo shipped in the runtime image (demo/)."""

    def test_demo_shows_each_kind_of_result(self):
        demo = APP_DIR / "demo"
        out = self.tmp / "out"
        code, stdout, err = run(["check", str(demo / "answer.txt"), str(demo / "docs"),
                                 "--out", str(out), "--format", "json", "--no-color"])
        self.assertEqual(code, 0, err)
        self.assertIn("experimental prototype", stdout)
        report = json.loads((out / "report.json").read_text(encoding="utf-8"))
        self.assertEqual(
            [(r["label"], r["rule"] or r["abstain_reason"]) for r in report["results"]],
            [("SUPPORTED", "S1"),
             ("CONTRADICTED", "C2"),
             ("CONTRADICTED", "C1"),
             ("INSUFFICIENT_EVIDENCE", "unclear_number"),
             ("INSUFFICIENT_EVIDENCE", "dated_context"),
             ("INSUFFICIENT_EVIDENCE", "no_relevant_passage")])
        self.assertEqual([s["reason"] for s in report["skipped"]], ["lead_in", "meta"])


class EvalCommandTests(TempDocsTestCase):
    def test_eval_dev_runs_twice_with_identical_results(self):
        fingerprints = []
        for name in ("one", "two"):
            out_dir = self.tmp / name
            code, out, err = run(["eval", "--subset", "dev", "--out", str(out_dir)])
            self.assertEqual(code, 0, err)
            self.assertIn("SYNTHETIC", out)
            self.assertIn("not validated", out)
            self.assertIn("HISTORICAL BENCHMARKS", out)
            self.assertIn("TUNING RESULTS ONLY", out)
            self.assertIn("not release evidence", out)
            self.assertIn("TARGET COMPARISON (tuning only; these are not release checks)", out)
            self.assertNotIn("RELEASE CHECKS", out)
            results = json.loads((out_dir / "eval-results.json").read_text(encoding="utf-8"))
            self.assertEqual(results["dataset"]["count"], 20)
            self.assertEqual(results["result_type"], "tuning_only")
            self.assertNotIn("release_checks", results)
            fingerprints.append(results["results_fingerprint"])
        self.assertEqual(fingerprints[0], fingerprints[1])

    # The test subset is never run here. The release guard is tested on a
    # temporary synthetic dataset in test_eval_guard.py.

    def test_eval_rejects_old_split_flag(self):
        code, _, err = run(["eval", "--split", "dev"])
        self.assertEqual(code, 2)
        self.assertIn("--split", err)
