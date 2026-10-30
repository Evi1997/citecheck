"""Input safety and limits (QA findings F14, F15 and F18). All fixtures are synthetic."""

import contextlib
import io
import os
from pathlib import Path
from unittest import mock

from citecheck import cli, textio
from citecheck.cli import main
from citecheck.splitter import Claim
from citecheck.textio import Document, load_documents
from citecheck.verdict import SUPPORTED, Corpus, check_claim
from tests.helpers import TempDocsTestCase

DOC = "Hours\n\nThe library opens at 9am.\n"


def run(argv):
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = main(argv)
    return code, out.getvalue(), err.getvalue()


class FileSafetyTests(TempDocsTestCase):
    def test_special_files_are_skipped_without_reading(self):
        if not hasattr(os, "mkfifo"):
            self.skipTest("named pipes not supported here")
        self.write_docs({"a.txt": DOC})
        os.mkfifo(self.docs_dir / "pipe.txt")  # opening this for reading would block
        docs, warnings = load_documents(self.docs_dir)
        self.assertEqual([d.name for d in docs], ["a.txt"])
        self.assertTrue(any("not a regular file" in w for w in warnings))

    def test_link_to_a_hidden_file_is_skipped(self):
        self.write_docs({".secret.txt": DOC})
        try:
            (self.docs_dir / "visible.txt").symlink_to(self.docs_dir / ".secret.txt")
        except (OSError, NotImplementedError):
            self.skipTest("symlinks not supported here")
        docs, warnings = load_documents(self.docs_dir)
        self.assertEqual(docs, [])
        self.assertTrue(any("hidden file" in w for w in warnings))

    def test_broken_link_is_skipped(self):
        try:
            (self.docs_dir / "gone.txt").symlink_to(self.docs_dir / "missing.txt")
        except (OSError, NotImplementedError):
            self.skipTest("symlinks not supported here")
        self.write_docs({"a.txt": DOC})
        docs, _ = load_documents(self.docs_dir)
        self.assertEqual([d.name for d in docs], ["a.txt"])

    def test_citecheck_reports_are_not_read_as_documents(self):
        self.write_docs({"a.txt": DOC, "report.md": "# CiteCheck report\n\nThe library opens at 9am.\n"})
        docs, warnings = load_documents(self.docs_dir)
        self.assertEqual([d.name for d in docs], ["a.txt"])
        self.assertTrue(any("looks like a CiteCheck report" in w for w in warnings))

    def test_out_folder_cannot_be_the_documents_folder(self):
        self.write_docs({"a.txt": DOC})
        answer = self.tmp / "answer.txt"
        answer.write_text("The library opens at 9am.\n", encoding="utf-8")
        code, _, err = run(["check", str(answer), str(self.docs_dir), "--out", str(self.docs_dir)])
        self.assertEqual(code, 2)
        self.assertIn("is the documents folder", err)
        self.assertEqual(sorted(p.name for p in self.docs_dir.iterdir()), ["a.txt"])

    def test_error_messages_neutralise_control_characters(self):
        code, _, err = run(["check", str(self.tmp / "no\x1b]0;x\x07such.txt"), str(self.docs_dir)])
        self.assertEqual(code, 2)
        self.assertNotIn("\x1b", err)
        self.assertNotIn("\x07", err)


class LimitTests(TempDocsTestCase):
    def setUp(self):
        super().setUp()
        self.answer = self.tmp / "answer.txt"
        self.answer.write_text("The library opens at 9am.\nThe cafe opens at 10am.\n", encoding="utf-8")
        self.out = self.tmp / "out"

    def test_too_many_documents(self):
        self.write_docs({f"d{n}.txt": DOC for n in range(3)})
        with mock.patch.object(textio, "MAX_DOCUMENTS", 2):
            code, _, err = run(["check", str(self.answer), str(self.docs_dir), "--out", str(self.out)])
        self.assertEqual(code, 2)
        self.assertIn("more than 2 .txt or .md files", err)
        self.assertFalse(self.out.exists())

    def test_too_much_text(self):
        self.write_docs({"a.txt": DOC, "b.txt": DOC})
        with mock.patch.object(textio, "MAX_TOTAL_DOC_BYTES", len(DOC) + 1):
            code, _, err = run(["check", str(self.answer), str(self.docs_dir), "--out", str(self.out)])
        self.assertEqual(code, 2)
        self.assertIn("Check a smaller folder", err)

    def test_too_many_claims(self):
        self.write_docs({"a.txt": DOC})
        with mock.patch.object(cli, "MAX_CLAIMS", 1):
            code, _, err = run(["check", str(self.answer), str(self.docs_dir), "--out", str(self.out)])
        self.assertEqual(code, 2)
        self.assertIn("has 2 claims", err)

    def test_within_limits_runs(self):
        self.write_docs({"a.txt": DOC})
        code, _, err = run(["check", str(self.answer), str(self.docs_dir), "--out", str(self.out)])
        self.assertEqual(code, 0, err)


class OffsetTests(TempDocsTestCase):
    """QA finding F18: line numbers and offsets with BOM, CRLF and CR-only files."""

    def test_line_numbers_for_every_line_break(self):
        doc = Document("d.txt", Path("d.txt"), "a\rb\r\nc\nd", "")
        self.assertEqual([doc.line_of(i) for i in (0, 2, 5, 7)], [1, 2, 3, 4])

    def test_offsets_with_bom_crlf_and_cr(self):
        for name, raw in (
            ("bom.txt", "﻿Hours\r\n\r\nThe library opens at 9am.\r\n"),
            ("cr.txt", "Hours\r\rThe library opens at 9am.\r"),
        ):
            with self.subTest(name=name):
                (self.docs_dir / name).write_bytes(raw.encode("utf-8"))
                docs, _ = load_documents(self.docs_dir)
                doc = next(d for d in docs if d.name == name)
                r = check_claim(Claim(1, "The library opens at 9am.", 1), Corpus([doc]))
                self.assertEqual(r.label, SUPPORTED)
                ev = r.evidence[0]
                self.assertEqual(doc.text[ev.char_start:ev.char_end], "The library opens at 9am.")
                self.assertEqual(ev.line, 3)
                self.assertTrue(ev.verified_verbatim)
                (self.docs_dir / name).unlink()
