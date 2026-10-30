"""Shared test helpers. All fixtures are synthetic."""

import tempfile
import unittest
from pathlib import Path
from typing import Dict

from citecheck.cues import cue_set
from citecheck.rules import find_hit
from citecheck.splitter import Claim
from citecheck.textio import load_documents
from citecheck.tokens import match_tokens, tokenize
from citecheck.verdict import Corpus, check_claim

APP_DIR = Path(__file__).resolve().parents[1]
EVAL_DIR = APP_DIR / "eval"


def hit(claim: str, sentence: str):
    c = match_tokens(tokenize(claim))
    t = match_tokens(tokenize(sentence))
    return find_hit(c, cue_set(c), t, cue_set(t))


class TempDocsTestCase(unittest.TestCase):
    """Gives each test a temporary folder for synthetic documents."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.docs_dir = self.tmp / "docs"
        self.docs_dir.mkdir()

    def tearDown(self):
        self._tmp.cleanup()

    def write_docs(self, docs: Dict[str, str]) -> None:
        for name, text in docs.items():
            (self.docs_dir / name).write_text(text, encoding="utf-8")

    def corpus(self, docs: Dict[str, str]) -> Corpus:
        self.write_docs(docs)
        loaded, _ = load_documents(self.docs_dir)
        return Corpus(loaded)

    def check(self, claim: str, docs: Dict[str, str]):
        return check_claim(Claim(1, claim, 1), self.corpus(docs))
