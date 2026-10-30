import builtins
import contextlib
import io
import json
import os
import shutil
import tempfile
import unittest
from collections import Counter
from pathlib import Path
from unittest import mock

from citecheck.cli import main
from citecheck.evaluation.dataset import (DatasetError, fingerprint, load_cases, load_docs, load_manifest,
                                          load_splitting)
from tests.helpers import EVAL_DIR


BUNDLED_TEST_FILE = (EVAL_DIR / "data" / "test.jsonl").resolve()


@contextlib.contextmanager
def bundled_test_file_blocked():
    """Fail the test if anything opens the bundled test.jsonl."""
    real_open = builtins.open

    def guarded_open(file, *args, **kwargs):
        if isinstance(file, (str, os.PathLike)) and Path(file).resolve() == BUNDLED_TEST_FILE:
            raise AssertionError("a unit test opened the bundled test subset")
        return real_open(file, *args, **kwargs)

    with mock.patch.object(builtins, "open", guarded_open):
        yield


class BundledDatasetTests(unittest.TestCase):
    """Checks the bundled synthetic dev subset, documents and splitting set.

    It does not validate the labels. The bundled test subset is never opened
    by unit tests (owner decision, 2026-10-29): it is compromised for
    independent release evaluation. Schema and balance checks on a test
    subset use temporary synthetic fixtures (ValidatorTests).
    """

    @classmethod
    def setUpClass(cls):
        with bundled_test_file_blocked():
            cls.docs = load_docs(EVAL_DIR)
            cls.cases = load_cases(EVAL_DIR, cls.docs, subsets=("dev",))

    def test_dev_size_and_balance(self):
        self.assertEqual(len(self.cases), 20)
        self.assertEqual(Counter(c.gold_label for c in self.cases),
                         {"SUPPORTED": 7, "CONTRADICTED": 7, "INSUFFICIENT_EVIDENCE": 6})
        self.assertEqual(Counter(c.subset for c in self.cases), {"dev": 20})

    def test_dev_eval_never_opens_the_bundled_test_file(self):
        out = Path(tempfile.mkdtemp())
        try:
            with bundled_test_file_blocked(), contextlib.redirect_stdout(io.StringIO()):
                code = main(["eval", "--subset", "dev", "--out", str(out), "--debug"])
            self.assertEqual(code, 0)
        finally:
            shutil.rmtree(out)

    def test_every_case_is_marked_synthetic_and_unreviewed(self):
        for case in self.cases:
            self.assertTrue(case.provenance["synthetic"], case.id)
            self.assertEqual(case.provenance["review_status"], "unreviewed", case.id)

    def test_manifest_does_not_claim_validation(self):
        manifest = load_manifest(EVAL_DIR)
        self.assertTrue(manifest["synthetic"])
        self.assertFalse(manifest["validated"])
        self.assertIn("Optional", manifest["second_reviewer"])

    def test_gold_quotes_resolve_to_offsets(self):
        for case in self.cases:
            for gq in case.gold_quotes:
                self.assertEqual(self.docs[gq.doc].text[gq.char_start:gq.char_end], gq.quote, case.id)

    def test_every_document_is_marked_synthetic(self):
        for doc in self.docs.values():
            self.assertTrue(doc.text.startswith("SYNTHETIC TEST DOCUMENT."), doc.name)

    def test_splitting_set_loads(self):
        splitting = load_splitting(EVAL_DIR)
        self.assertEqual(len(splitting), 5)
        self.assertEqual(sum(len(s.expected) for s in splitting), 18)


class ValidatorTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name) / "eval"
        shutil.copytree(EVAL_DIR / "docs", self.root / "docs")
        (self.root / "data").mkdir()
        self.good = {
            "id": "x-1", "subset": "dev", "claim": "Gift cards are refundable.", "docs": ["*"],
            "gold_label": "CONTRADICTED", "scope": "in", "case_type": "negation",
            "gold_quotes": [{"doc": "d03_refunds.txt", "quote": "Gift cards are not refundable."}],
            "provenance": {"synthetic": True, "review_status": "unreviewed"},
        }

    def tearDown(self):
        self._tmp.cleanup()

    def write(self, dev_cases, test_cases=()):
        for name, rows in (("dev", dev_cases), ("test", test_cases)):
            with open(self.root / "data" / f"{name}.jsonl", "w", encoding="utf-8") as fh:
                for row in rows:
                    fh.write(json.dumps(row) + "\n")

    def load(self):
        return load_cases(self.root, load_docs(self.root))

    def test_good_case_loads(self):
        self.write([self.good])
        self.assertEqual(len(self.load()), 1)

    def test_schema_and_balance_on_a_synthetic_test_subset(self):
        supported = dict(self.good, id="s", claim="Gift cards are not refundable.", gold_label="SUPPORTED",
                         case_type="verbatim")
        absent = dict(self.good, id="i", claim="The shop opens on Sundays.", gold_label="INSUFFICIENT_EVIDENCE",
                      scope=None, case_type="absent", gold_quotes=[])
        dev = [dict(self.good, id="d-c"), dict(supported, id="d-s"), dict(absent, id="d-i")]
        test = [dict(case, id=f"t-{n}", subset="test")
                for n, case in enumerate([self.good, self.good, supported, supported, absent, absent])]
        self.write(dev, test)
        cases = self.load()
        self.assertEqual(Counter(c.subset for c in cases), {"dev": 3, "test": 6})
        test_cases = [c for c in cases if c.subset == "test"]
        self.assertEqual(Counter(c.gold_label for c in test_cases),
                         {"SUPPORTED": 2, "CONTRADICTED": 2, "INSUFFICIENT_EVIDENCE": 2})
        for c in test_cases:
            for gq in c.gold_quotes:
                self.assertEqual(load_docs(self.root)[gq.doc].text[gq.char_start:gq.char_end], gq.quote)
        self.assertEqual(len(load_cases(self.root, load_docs(self.root), subsets=("test",))), 6)

    def test_quote_must_exist_once(self):
        bad = dict(self.good, gold_quotes=[{"doc": "d03_refunds.txt", "quote": "Not in the file."}])
        self.write([bad])
        with self.assertRaises(DatasetError):
            self.load()

    def test_duplicate_ids_rejected(self):
        self.write([self.good], [dict(self.good, subset="test")])
        with self.assertRaises(DatasetError):
            self.load()

    def test_invalid_label_rejected(self):
        self.write([dict(self.good, gold_label="TRUE")])
        with self.assertRaises(DatasetError):
            self.load()

    def test_scope_rules(self):
        self.write([dict(self.good, scope=None)])
        with self.assertRaises(DatasetError):
            self.load()

    def test_subset_must_match_file(self):
        self.write([dict(self.good, subset="test")])
        with self.assertRaises(DatasetError):
            self.load()

    def test_one_subset_loads_without_opening_the_other(self):
        self.write([self.good])
        (self.root / "data" / "test.jsonl").write_text("not json\n", encoding="utf-8")
        cases = load_cases(self.root, load_docs(self.root), subsets=("dev",))
        self.assertEqual([c.id for c in cases], ["x-1"])

    def test_fingerprint_is_per_subset(self):
        self.write([self.good])
        self.assertIn("  data/dev.jsonl", fingerprint(self.root, "dev"))
        self.assertNotIn("dev.jsonl", fingerprint(self.root, "test"))
        self.assertIn("  data/test.jsonl", fingerprint(self.root))

    def test_provenance_required(self):
        self.write([dict(self.good, provenance={"review_status": "unreviewed"})])
        with self.assertRaises(DatasetError):
            self.load()


if __name__ == "__main__":
    unittest.main()
