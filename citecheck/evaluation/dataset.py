"""Loading and validating the evaluation dataset.

Layout of an evaluation folder:
  docs/*.txt                 documents the cases may use
  data/dev.jsonl, test.jsonl one verdict case per line
  splitting/aN.txt           AI-style answers
  splitting/aN.expected.json the claims a person expects from each answer
  MANIFEST.json              dataset version, provenance and review status

Gold quotes are stored as text only. The loader finds each quote in its
document and rejects the case unless the quote occurs exactly once, so
offsets are never typed by hand.
"""

import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from ..textio import Document, TextReadError, read_text, sort_key

LABELS = ("SUPPORTED", "CONTRADICTED", "INSUFFICIENT_EVIDENCE")
SUBSETS = ("dev", "test")
SCOPES = ("in", "out")
REVIEW_STATUSES = ("unreviewed", "reviewed", "disputed")
CASE_TYPES = frozenset({
    "verbatim", "near_verbatim", "paraphrase", "multi_doc", "format", "number_date",
    "negation", "antonym", "paraphrased_contradiction", "absent", "keyword_trap",
    "qualifier_trap", "partial", "conflict", "outdated_context",
})
REQUIRED_KEYS = ("id", "subset", "claim", "docs", "gold_label", "scope", "case_type",
                 "gold_quotes", "provenance")


class DatasetError(Exception):
    pass


@dataclass
class GoldQuote:
    doc: str
    quote: str
    char_start: int
    char_end: int


@dataclass
class Case:
    id: str
    subset: str
    claim: str
    docs: List[str]
    gold_label: str
    scope: Optional[str]
    case_type: str
    gold_quotes: List[GoldQuote]
    provenance: dict
    notes: str = ""


@dataclass
class SplittingCase:
    name: str
    text: str
    expected: List[Tuple[int, str]] = field(default_factory=list)  # (line, claim text)


def load_docs(eval_dir: Path) -> Dict[str, Document]:
    folder = eval_dir / "docs"
    if not folder.is_dir():
        raise DatasetError(f'No "docs" folder in "{eval_dir}".')
    docs: Dict[str, Document] = {}
    for path in sorted(folder.glob("*.txt"), key=lambda p: sort_key(p.name)):
        try:
            text, sha = read_text(path)
        except (OSError, TextReadError) as exc:
            raise DatasetError(f'Couldn\'t read evaluation document "{path.name}": {exc}') from None
        docs[path.name] = Document(path.name, path.resolve(), text, sha)
    if not docs:
        raise DatasetError(f'The "docs" folder in "{eval_dir}" has no .txt files.')
    return docs


def _fail(path: Path, line_no: int, message: str) -> DatasetError:
    return DatasetError(f"{path.name}, line {line_no}: {message}")


def _parse_case(raw: dict, path: Path, line_no: int, docs: Dict[str, Document]) -> Case:
    missing = [k for k in REQUIRED_KEYS if k not in raw]
    if missing:
        raise _fail(path, line_no, f"missing keys: {', '.join(missing)}")
    label = raw["gold_label"]
    if label not in LABELS:
        raise _fail(path, line_no, f'invalid gold_label "{label}"')
    if raw["subset"] not in SUBSETS:
        raise _fail(path, line_no, f'invalid subset "{raw["subset"]}"')
    if path.stem != raw["subset"]:
        raise _fail(path, line_no, f'case is marked "{raw["subset"]}" but is in {path.name}')
    scope = raw["scope"]
    if label == "INSUFFICIENT_EVIDENCE":
        if scope is not None:
            raise _fail(path, line_no, "scope must be null for INSUFFICIENT_EVIDENCE")
    elif scope not in SCOPES:
        raise _fail(path, line_no, 'scope must be "in" or "out" for SUPPORTED and CONTRADICTED')
    if raw["case_type"] not in CASE_TYPES:
        raise _fail(path, line_no, f'invalid case_type "{raw["case_type"]}"')
    if not isinstance(raw["claim"], str) or not raw["claim"].strip():
        raise _fail(path, line_no, "claim must be a non-empty string")

    names = list(raw["docs"])
    if names == ["*"]:
        names = list(docs)
    for name in names:
        if name not in docs:
            raise _fail(path, line_no, f'unknown document "{name}"')

    quotes: List[GoldQuote] = []
    for gq in raw["gold_quotes"]:
        doc_name, quote = gq.get("doc"), gq.get("quote")
        if doc_name not in names:
            raise _fail(path, line_no, f'gold quote document "{doc_name}" is not in the case\'s docs')
        text = docs[doc_name].text
        count = text.count(quote) if quote else 0
        if count != 1:
            raise _fail(path, line_no, f'gold quote must occur exactly once in {doc_name} (found {count}): "{quote}"')
        start = text.index(quote)
        quotes.append(GoldQuote(doc_name, quote, start, start + len(quote)))
    if label != "INSUFFICIENT_EVIDENCE" and not quotes:
        raise _fail(path, line_no, "SUPPORTED and CONTRADICTED cases need at least one gold quote")
    if label == "INSUFFICIENT_EVIDENCE" and quotes and raw["case_type"] != "conflict":
        raise _fail(path, line_no, "only conflict cases may record gold quotes for INSUFFICIENT_EVIDENCE")

    provenance = raw["provenance"]
    if not isinstance(provenance, dict) or "synthetic" not in provenance:
        raise _fail(path, line_no, 'provenance must record "synthetic"')
    if provenance.get("review_status") not in REVIEW_STATUSES:
        raise _fail(path, line_no, f"review_status must be one of {', '.join(REVIEW_STATUSES)}")

    return Case(
        id=raw["id"], subset=raw["subset"], claim=raw["claim"], docs=names,
        gold_label=label, scope=scope, case_type=raw["case_type"], gold_quotes=quotes,
        provenance=provenance, notes=raw.get("notes", ""),
    )


def load_cases(eval_dir: Path, docs: Dict[str, Document],
               subsets: Tuple[str, ...] = SUBSETS) -> List[Case]:
    """Load and validate the cases in data/<subset>.jsonl for each subset given.

    The default loads dev and test, which also checks ids are unique across
    both. eval --subset dev passes ("dev",), so it never opens test.jsonl.
    """
    cases: List[Case] = []
    seen = set()
    for subset in subsets:
        path = eval_dir / "data" / f"{subset}.jsonl"
        if not path.is_file():
            raise DatasetError(f'Missing "{path}".')
        with open(path, encoding="utf-8") as fh:
            for line_no, line in enumerate(fh, start=1):
                if not line.strip():
                    continue
                try:
                    raw = json.loads(line)
                except json.JSONDecodeError as exc:
                    raise _fail(path, line_no, f"not valid JSON ({exc.msg})") from None
                case = _parse_case(raw, path, line_no, docs)
                if case.id in seen:
                    raise _fail(path, line_no, f'duplicate id "{case.id}"')
                seen.add(case.id)
                cases.append(case)
    return cases


def load_splitting(eval_dir: Path) -> List[SplittingCase]:
    folder = eval_dir / "splitting"
    if not folder.is_dir():
        return []
    cases = []
    for answer in sorted(folder.glob("*.txt"), key=lambda p: sort_key(p.name)):
        expected_path = answer.with_name(answer.stem + ".expected.json")
        if not expected_path.is_file():
            raise DatasetError(f'Missing "{expected_path.name}" for "{answer.name}".')
        text, _ = read_text(answer)
        with open(expected_path, encoding="utf-8") as fh:
            expected = json.load(fh)
        claims = [(int(c["line"]), str(c["text"])) for c in expected.get("claims", [])]
        cases.append(SplittingCase(answer.name, text, claims))
    return cases


def load_manifest(eval_dir: Path) -> dict:
    path = eval_dir / "MANIFEST.json"
    if not path.is_file():
        return {}
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def fingerprint(eval_dir: Path, subset: str = "test") -> str:
    """SHA-256 lines for one subset's data: <subset>.jsonl, docs and splitting files.

    Only file bytes are hashed. Nothing is parsed, so fingerprinting the test
    subset doesn't read its labels.
    """
    files = [eval_dir / "data" / f"{subset}.jsonl"]
    for sub in ("docs", "splitting"):
        folder = eval_dir / sub
        if folder.is_dir():
            files += sorted((p for p in folder.iterdir() if p.is_file() and not p.name.startswith(".")),
                            key=lambda p: sort_key(p.name))
    lines = []
    for path in files:
        with open(path, "rb") as fh:
            digest = hashlib.sha256(fh.read()).hexdigest()
        lines.append(f"{digest}  {path.relative_to(eval_dir).as_posix()}")
    return "\n".join(lines) + "\n"
