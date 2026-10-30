import json
import re

from citecheck.report import (CLOSEST_HEADING, RunReport, Style, md_escape, render_json, render_markdown,
                              render_terminal, write_reports)
from citecheck.splitter import split_answer
from citecheck.textio import load_documents
from citecheck.verdict import LABELS, Corpus, check_claim
from tests.helpers import TempDocsTestCase

DOC = (
    "Warranty\n"
    "\n"
    "The warranty covers accidental damage for two years from purchase.\n"
    "The warranty does not cover water damage.\n"
    "Batteries are covered for 12 months.\n"
)
ANSWER = (
    "Here is a summary:\n"
    "- The warranty covers accidental damage for two years from purchase.\n"
    "- The warranty covers water damage.\n"
    "- Batteries are covered for 24 months.\n"
    "- The warranty is transferable to new owners.\n"
    "Anything else?\n"
)
ANSI = re.compile(r"\033\[")


class ReportTests(TempDocsTestCase):
    def build(self) -> RunReport:
        self.write_docs({"warranty.txt": DOC})
        docs, warnings = load_documents(self.docs_dir)
        corpus = Corpus(docs)
        split = split_answer(ANSWER)
        results = [check_claim(c, corpus) for c in split.claims]
        return RunReport("answer.txt", "docs", len(docs), "sentences", results,
                         split.skipped, split.blank_lines, warnings, 0.1, "2026-10-23")

    def test_terminal_shows_quote_source_rule_and_find_it(self):
        out = render_terminal(self.build(), Style(colour=False, unicode=True))
        self.assertIn("[SUPPORTED] ✔", out)
        self.assertIn("[CONTRADICTED] ✘", out)
        self.assertIn("[INSUFFICIENT EVIDENCE] ?", out)
        self.assertIn('Quote   : "The warranty covers accidental damage', out)
        self.assertIn("Source  : warranty.txt, line 3, characters", out)
        self.assertIn("Rule    : S1 ordered match", out)
        self.assertIn("Find it : open warranty.txt, go to line 3", out)
        self.assertIn("(answer.txt, line 2)", out)
        self.assertIn("SKIPPED (not checked as claims)", out)
        self.assertIn("lead-in line", out)
        self.assertNotRegex(out, ANSI)

    def test_quote_integrity_wording_does_not_claim_support(self):
        run = self.build()
        n = sum(len(r.evidence) for r in run.results)
        self.assertGreater(n, 0)
        out = render_terminal(run, Style(colour=False, unicode=True))
        self.assertIn(f"Quote integrity      : {n} of {n} quotes match their files exactly", out)
        self.assertIn("It checks the text, not whether the quote proves or disproves the claim.",
                      " ".join(out.split()))
        self.assertNotIn("Quotes verified", out)
        self.assertNotIn("back this claim", out)
        md = render_markdown(run)
        self.assertIn(f"| Quote integrity (exact text match) | {n} of {n} quotes match their files exactly |", md)
        self.assertIn("not whether the quote proves or disproves the claim", md)
        self.assertNotIn("back this claim", md)
        data = json.loads(render_json(run))
        self.assertIn("does not mean the quote supports", data["field_notes"]["verified_verbatim"])
        self.assertEqual((data["summary"]["quotes_shown"], data["summary"]["quotes_matching_file"]), (n, n))

    def test_ascii_fallback_and_colour(self):
        run = self.build()
        plain = render_terminal(run, Style(colour=False, unicode=False))
        self.assertIn("[SUPPORTED] +", plain)
        self.assertIn("[CONTRADICTED] x", plain)
        coloured = render_terminal(run, Style(colour=True, unicode=True))
        self.assertRegex(coloured, ANSI)

    def test_closest_passages_heading_and_no_score(self):
        run = self.build()
        out = render_terminal(run, Style())
        self.assertIn(CLOSEST_HEADING, out)
        self.assertEqual(CLOSEST_HEADING, "Closest passages (similar wording, not proof):")
        self.assertNotIn("score", out.lower())
        data = json.loads(render_json(run))
        for result in data["results"]:
            for passage in result["closest_passages"]:
                self.assertEqual(set(passage), {"rank", "document", "line", "char_start", "char_end", "text",
                                                "heading", "context"})

    def test_json_schema(self):
        data = json.loads(render_json(self.build()))
        self.assertEqual(data["schema_version"], "2.2")
        self.assertIn("not proof", data["disclaimer"])
        self.assertEqual(data["run"]["offset_convention"]["char_end"], "exclusive")
        for result in data["results"]:
            self.assertIn(result["label"], LABELS)
            if result["label"] in ("SUPPORTED", "CONTRADICTED"):
                self.assertIn(result["rule"], ("S1", "C1", "C2"))
                self.assertEqual(len(result["evidence"]), 1)
                self.assertTrue(result["evidence"][0]["verified_verbatim"])
            else:
                self.assertIsNone(result["rule"])
                self.assertIsNotNone(result["abstain_reason"])
            self.assertNotIn("score", json.dumps(result))
        self.assertEqual(data["skipped"][0]["reason"], "lead_in")

    def test_markdown_structure(self):
        md = render_markdown(self.build())
        self.assertTrue(md.startswith("# CiteCheck report"))
        self.assertIn("**Read this first.**", md)
        self.assertIn("## Claim 1: Supported", md)
        self.assertIn("## Claim 2: Contradicted", md)
        self.assertIn("## Rules used", md)
        self.assertIn("## Legend", md)
        self.assertNotRegex(md, ANSI)

    def test_reports_are_never_overwritten(self):
        run = self.build()
        out_dir = self.tmp / "out"
        first, renamed_first = write_reports(run, out_dir, "both")
        before = [p.read_text(encoding="utf-8") for p in first]
        second, renamed_second = write_reports(run, out_dir, "both")
        self.assertFalse(renamed_first)
        self.assertTrue(renamed_second)
        self.assertTrue(set(first).isdisjoint(second))
        self.assertEqual([p.read_text(encoding="utf-8") for p in first], before)

    def test_heading_is_shown_with_the_evidence(self):
        out = render_terminal(self.build(), Style(colour=False, unicode=True))
        self.assertIn('Heading : "Warranty"', out)


class UntrustedTextTests(TempDocsTestCase):
    """QA finding F11: text from files must not control the terminal."""

    def build(self) -> RunReport:
        doc = ("Pricing\n\n"
               "The plan costs \x1b[2J\x1b]0;title\x07 twenty pounds per month.\n"
               "The ‮plan‬ includes support for two users.\n")
        self.write_docs({"pricing.txt": doc})
        docs, warnings = load_documents(self.docs_dir)
        corpus = Corpus(docs)
        split = split_answer("The plan costs twenty pounds per month.\nThe plan includes support for two users.\n")
        results = [check_claim(c, corpus) for c in split.claims]
        return RunReport("answer\x1b[31m.txt", "docs", len(docs), "sentences", results,
                         split.skipped, split.blank_lines, warnings, 0.1, "2026-10-29")

    def test_terminal_and_markdown_neutralise_control_characters(self):
        run = self.build()
        for name, text in (("terminal", render_terminal(run, Style(colour=False, unicode=True))),
                           ("terminal, colour", render_terminal(run, Style(colour=True, unicode=True))),
                           ("markdown", render_markdown(run))):
            with self.subTest(output=name):
                self.assertNotIn("\x07", text)
                self.assertNotIn("‮", text)
                self.assertNotIn("\x1b[2J", text)
                self.assertNotIn("\x1b[31m.txt", text)
                self.assertIn("\\x1b", text)

    def test_colour_codes_from_citecheck_are_kept(self):
        out = render_terminal(self.build(), Style(colour=True, unicode=True))
        self.assertRegex(out, r"\033\[3[124]m\[")

    def test_json_keeps_exact_text_escaped(self):
        data = render_json(self.build())
        self.assertNotIn("\x1b", data)  # the JSON encoder writes \u001b
        self.assertEqual(json.loads(data)["run"]["answer_file"], "answer\x1b[31m.txt")


class MarkdownEscapingTests(TempDocsTestCase):
    """QA finding F17."""

    def test_special_characters_and_links_are_escaped(self):
        text = md_escape("Tom & Jerry (see https://example.com or www.example.org) ~ok~ !x @y")
        for fragment in ("\\&", "\\(", "\\)", "https\\://", "www\\.", "\\~", "\\!", "\\@"):
            self.assertIn(fragment, text)
        self.assertNotIn("https://", text)

    def test_list_markers_in_quotes_are_escaped(self):
        from citecheck.report import _md_blockquote
        self.assertEqual(_md_blockquote("- not a list\n1. not a list either"),
                         ["> \\- not a list", "> 1\\. not a list either"])

    def test_ordered_markers_escape_punctuation_not_digits(self):
        # A backslash before a digit isn't a Markdown escape and would show up
        # in the rendered text, so only the "." or ")" is escaped.
        from citecheck.report import _md_blockquote
        self.assertEqual(_md_blockquote("12) item\n  3. indented\n+ plus\n10.5 is a number"),
                         ["> 12\\) item", ">   3\\. indented", "> \\+ plus", "> 10.5 is a number"])
