"""Turning rule hits into a label, an abstain reason and closest passages.

See TECH_PLAN.md section 4.4 for the decision table. Rule set 0.4.0 adds
context checks: a sentence can't decide a label on its own if its heading
suggests old, draft or limited content, if its heading names a year the claim
doesn't, or if a neighbouring sentence may narrow it. A claim whose subject is
a pronoun ("It has 45 staff") never gets a confident label.
"""

from dataclasses import dataclass, field
from typing import FrozenSet, List, NamedTuple, Optional, Set

from .cues import cue_set, heading_is_flagged, narrows_as_next, narrows_as_previous, reference_subject
from .retrieval import BM25, STOPWORDS, ranking_terms
from .rules import RuleHit, find_hit, is_date_phrase, is_unclear, is_year
from .splitter import Claim, split_document
from .textio import Document, sort_key, verify_on_disk
from .thresholds import CLOSEST_K, MIN_PARTIAL_COVERAGE, MIN_SHARED_TERMS
from .tokens import Token, is_number, match_tokens, tokenize

SUPPORTED = "SUPPORTED"
CONTRADICTED = "CONTRADICTED"
INSUFFICIENT = "INSUFFICIENT_EVIDENCE"
LABELS = (SUPPORTED, CONTRADICTED, INSUFFICIENT)

ABSTAIN_REASONS = (
    # Absent evidence
    "no_content_words",
    "no_relevant_passage",
    "mostly_absent",
    # Partial matches
    "partial_support",
    "needs_multiple_sources",
    "qualifier_mismatch",
    "extra_words",
    "outside_rules",
    # Ambiguous context or values
    "unclear_number",
    "unclear_reference",
    "conflicting_sources",
    "flagged_context",
    "dated_context",
    "nearby_context",
    # Integrity
    "quote_not_verified",
)
CONTEXT_REASONS = ("flagged_context", "dated_context", "nearby_context")
NEIGHBOUR_PREVIEW = 200


class SentenceRecord(NamedTuple):
    index: int
    doc: Document
    doc_order: int
    start: int
    end: int
    heading: Optional[str]
    flagged: bool             # under a heading such as "Superseded policy" or "Exclusions"
    heading_years: FrozenSet[str]
    block: int
    tokens: List[Token]       # match tokens, offsets relative to doc.text
    cues: FrozenSet[str]
    norms: FrozenSet[str]
    terms: List[str]          # ranking terms
    nearby: Optional[int]     # index of a neighbouring sentence that may narrow this one


@dataclass
class Evidence:
    document: str
    line: int
    char_start: int
    char_end: int
    quote: str
    rule: Optional[str]
    detail: dict
    verified_verbatim: bool = False
    heading: Optional[str] = None


@dataclass
class Passage:
    rank: int
    document: str
    line: int
    char_start: int
    char_end: int
    text: str
    heading: Optional[str] = None
    context: Optional[str] = None  # one of CONTEXT_REASONS, or None


@dataclass
class ClaimResult:
    claim: Claim
    label: str
    rule: Optional[str] = None
    evidence: List[Evidence] = field(default_factory=list)
    abstain_reason: Optional[str] = None
    detail: dict = field(default_factory=dict)
    closest: List[Passage] = field(default_factory=list)
    also_found: int = 0


class Corpus:
    """All sentences from a set of documents, prepared once per run."""

    def __init__(self, docs: List[Document]):
        self.docs = sorted(docs, key=lambda d: sort_key(d.name))
        self.sentences: List[SentenceRecord] = []
        for order, doc in enumerate(self.docs):
            heading_cache = {}
            first = len(self.sentences)
            for sent in split_document(doc.text):
                tokens = match_tokens(tokenize(doc.text[sent.start:sent.end], sent.start))
                if sent.heading not in heading_cache:
                    heading_tokens = tokenize(sent.heading) if sent.heading else []
                    heading_cache[sent.heading] = (
                        bool(sent.heading) and heading_is_flagged(heading_tokens),
                        frozenset(t.norm for t in heading_tokens if is_year(t)),
                    )
                flagged, years = heading_cache[sent.heading]
                self.sentences.append(SentenceRecord(
                    index=len(self.sentences),
                    doc=doc,
                    doc_order=order,
                    start=sent.start,
                    end=sent.end,
                    heading=sent.heading,
                    flagged=flagged,
                    heading_years=years,
                    block=sent.block,
                    tokens=tokens,
                    cues=cue_set(tokens),
                    norms=frozenset(t.norm for t in tokens),
                    terms=ranking_terms(tokens),
                    nearby=None,
                ))
            self._mark_neighbours(first)
        self._bm25 = BM25([s.terms for s in self.sentences])

    def _mark_neighbours(self, first: int) -> None:
        """Record a neighbouring sentence in the same paragraph that may narrow each sentence."""
        recs = self.sentences
        for i in range(first, len(recs)):
            rec = recs[i]
            prev = recs[i - 1] if i - 1 >= first and recs[i - 1].block == rec.block else None
            nxt = recs[i + 1] if i + 1 < len(recs) and recs[i + 1].block == rec.block else None
            nearby = None
            if prev is not None and narrows_as_previous(prev.tokens, rec.doc.text[prev.start:prev.end]):
                nearby = prev.index
            elif nxt is not None and narrows_as_next(nxt.tokens):
                nearby = nxt.index
            if nearby is not None:
                recs[i] = rec._replace(nearby=nearby)

    def closest(self, terms: List[str], k: int, exclude: Set[int]) -> List[SentenceRecord]:
        """Up to k sentences ranked by BM25 that share enough content words with the claim."""
        wanted = set(terms)
        if not wanted:
            return []
        need = min(MIN_SHARED_TERMS, len(wanted))
        scores = self._bm25.scores(terms)
        ranked = sorted(
            (i for i, score in enumerate(scores) if score > 0 and i not in exclude),
            key=lambda i: (-scores[i], self.sentences[i].doc_order, self.sentences[i].start),
        )
        relevant = [i for i in ranked if len(wanted & set(self.sentences[i].terms)) >= need]
        return [self.sentences[i] for i in relevant[:k]]


def _context(rec: SentenceRecord, claim_years: Set[str]) -> Optional[str]:
    """Why this sentence can't decide a label on its own, or None."""
    if rec.flagged:
        return "flagged_context"
    if rec.heading_years and not (rec.heading_years & claim_years):
        return "dated_context"
    if rec.nearby is not None:
        return "nearby_context"
    return None


def _context_detail(kind: str, rec: SentenceRecord, corpus: Corpus) -> dict:
    detail = {"document": rec.doc.name, "line": rec.doc.line_of(rec.start)}
    if kind in ("flagged_context", "dated_context"):
        detail["heading"] = rec.heading
    if kind == "dated_context":
        detail["years"] = sorted(rec.heading_years)
    if kind == "nearby_context":
        other = corpus.sentences[rec.nearby]
        text = " ".join(other.doc.text[other.start:other.end].split())
        detail["neighbour"] = text if len(text) <= NEIGHBOUR_PREVIEW else text[:NEIGHBOUR_PREVIEW] + " [...]"
        detail["neighbour_line"] = other.doc.line_of(other.start)
    return detail


def _evidence(rec: SentenceRecord, hit: RuleHit, claim: Claim, c_tokens: List[Token]) -> Evidence:
    doc = rec.doc
    detail: dict = {}
    if hit.rule == "C1":
        detail = {"negation": hit.detail["negation"]}
    elif hit.rule == "C2":
        ct = c_tokens[hit.detail["claim_index"]]
        dt = rec.tokens[hit.detail["document_index"]]
        detail = {
            "claim_value": claim.text[ct.start:ct.end],
            "document_value": doc.text[dt.start:dt.end],
        }
        if is_unclear(hit):
            detail["context_words"] = list(hit.detail["context_words"])
            detail["negated"] = hit.detail["negated"]
            if hit.detail.get("identifier"):
                detail["identifier"] = hit.detail["identifier"]
    return Evidence(
        document=doc.name,
        line=doc.line_of(rec.start),
        char_start=rec.start,
        char_end=rec.end,
        quote=doc.text[rec.start:rec.end],
        rule=hit.rule,
        detail=detail,
        heading=rec.heading,
    )


def _passage(rank: int, rec: SentenceRecord, claim_years: Set[str]) -> Passage:
    return Passage(rank, rec.doc.name, rec.doc.line_of(rec.start), rec.start, rec.end,
                   rec.doc.text[rec.start:rec.end], rec.heading, _context(rec, claim_years))


def _unique(items: List[str]) -> List[str]:
    seen, out = set(), []
    for item in items:
        if item not in seen:
            seen.add(item)
            out.append(item)
    return out


def _abstain(claim: Claim, c_tokens: List[Token], c_cues: FrozenSet[str],
             closest: List[SentenceRecord]):
    """Pick the abstain reason when no rule fired (TECH_PLAN.md section 4.4)."""
    terms = _unique(ranking_terms(c_tokens))
    if not terms:
        return "no_content_words", {}
    if not closest:
        return "no_relevant_passage", {}
    best = closest[0]
    missing_tokens = [t for t in c_tokens if t.norm not in best.norms]
    if missing_tokens:
        # Common words such as "has" or "for" aren't worth listing as missing.
        meaningful = [t for t in missing_tokens if t.word not in STOPWORDS or is_number(t) or t.kind == "symbol"]
        missing = _unique([claim.text[t.start:t.end] for t in (meaningful or missing_tokens)])
        covered = set().union(*(set(s.terms) for s in closest))
        documents = {s.doc_order for s in closest}
        if len(documents) >= 2 and all(term in covered for term in terms):
            return "needs_multiple_sources", {"missing_terms": missing}
        share = sum(1 for term in terms if term in set(best.terms)) / len(terms)
        if share < MIN_PARTIAL_COVERAGE:
            return "mostly_absent", {"missing_terms": missing}
        return "partial_support", {"missing_terms": missing}
    if c_cues != best.cues:
        return "qualifier_mismatch", {
            "cue_words_only_in_document": sorted(best.cues - c_cues),
            "cue_words_only_in_claim": sorted(c_cues - best.cues),
        }
    claim_norms = {t.norm for t in c_tokens}
    extra_tokens = [t for t in best.tokens if t.norm not in claim_norms]
    if extra_tokens:
        if is_date_phrase(extra_tokens):
            start, end = extra_tokens[0].start, extra_tokens[-1].end
            return "dated_context", {"date": best.doc.text[start:end], "document": best.doc.name,
                                     "line": best.doc.line_of(best.start)}
        return "extra_words", {"extra_terms": _unique([best.doc.text[t.start:t.end] for t in extra_tokens])}
    return "outside_rules", {}


def check_claim(claim: Claim, corpus: Corpus, closest_k: int = CLOSEST_K) -> ClaimResult:
    """Check one claim against every sentence in the corpus."""
    c_tokens = match_tokens(tokenize(claim.text))
    c_cues = cue_set(c_tokens)
    claim_years = {t.norm for t in c_tokens if is_year(t)}
    supports, contradicts, unclear, blocked = [], [], [], []
    for rec in corpus.sentences:
        hit = find_hit(c_tokens, c_cues, rec.tokens, rec.cues)
        if hit is None:
            continue
        context = _context(rec, claim_years)
        if context:
            blocked.append((rec, context))
        elif hit.rule == "S1":
            supports.append((rec, hit))
        elif is_unclear(hit):
            unclear.append((rec, hit))
        else:
            contradicts.append((rec, hit))

    result = ClaimResult(claim=claim, label=INSUFFICIENT)
    used: List[SentenceRecord] = []
    if supports and contradicts:
        result.abstain_reason = "conflicting_sources"
        pairs = [supports[0], contradicts[0]]
        used = [rec for rec, _ in pairs]
        result.evidence = [_evidence(rec, hit, claim, c_tokens) for rec, hit in pairs]
    elif unclear and not contradicts:
        # A sentence gives a different number under "within", "after", a
        # negation, an identifier ("Section 3") and so on. That may or may not
        # contradict the claim, and a supporting sentence can't be trusted alone.
        result.abstain_reason = "unclear_number"
        pairs = ([supports[0]] if supports else []) + [unclear[0]]
        used = [rec for rec, _ in pairs]
        result.evidence = [_evidence(rec, hit, claim, c_tokens) for rec, hit in pairs]
        first = unclear[0][1].detail
        result.detail = {"also_supported": bool(supports),
                         "context_words": list(first["context_words"]),
                         "negated": first["negated"],
                         "identifier": first.get("identifier")}
    elif supports or contradicts:
        hits = supports or contradicts
        rec, hit = hits[0]
        result.label = SUPPORTED if supports else CONTRADICTED
        result.rule = hit.rule
        result.evidence = [_evidence(rec, hit, claim, c_tokens)]
        result.also_found = len(hits) - 1
        used = [rec]
    elif blocked:
        rec, context = blocked[0]
        result.abstain_reason = context
        result.detail = _context_detail(context, rec, corpus)

    for ev, rec in zip(result.evidence, used):
        ev.verified_verbatim = verify_on_disk(rec.doc, ev.char_start, ev.char_end, ev.quote)
    if result.evidence and not all(ev.verified_verbatim for ev in result.evidence):
        result.label = INSUFFICIENT
        result.rule = None
        result.evidence = []
        result.also_found = 0
        result.abstain_reason = "quote_not_verified"
        result.detail = {}

    reference = reference_subject(c_tokens)
    if reference and result.label in (SUPPORTED, CONTRADICTED):
        # "It has 45 staff" matches a sentence word for word, but "it" could be
        # any company in the documents. Show the sentence, don't decide.
        result.label = INSUFFICIENT
        result.rule = None
        result.also_found = 0
        result.abstain_reason = "unclear_reference"
        result.detail = {"word": claim.text[c_tokens[0].start:c_tokens[0].end]}

    exclude = {rec.index for rec in used}
    nearest = corpus.closest(ranking_terms(c_tokens), closest_k, exclude)
    result.closest = [_passage(i + 1, rec, claim_years) for i, rec in enumerate(nearest)]
    if result.label == INSUFFICIENT and result.abstain_reason is None:
        result.abstain_reason, result.detail = _abstain(claim, c_tokens, c_cues, nearest)
    return result
