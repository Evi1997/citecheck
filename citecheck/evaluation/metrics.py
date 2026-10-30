"""Metric definitions for PRD.md sections 7, 9 and 10 (TECH_PLAN.md section 8)."""

from typing import Dict, Iterable, List, Optional, Sequence, Tuple

from ..splitter import split_answer
from ..thresholds import HISTORICAL_BENCHMARKS, PENDING_GATES, RELEASE_TARGETS
from ..verdict import CONTRADICTED, INSUFFICIENT, LABELS, SUPPORTED, ClaimResult
from .dataset import Case, SplittingCase

DECIDED = (SUPPORTED, CONTRADICTED)


def ratio(numerator: float, denominator: float) -> Optional[float]:
    return None if denominator == 0 else numerator / denominator


def _f1(p: Optional[float], r: Optional[float]) -> Optional[float]:
    if p is None or r is None or p + r == 0:
        return None
    return 2 * p * r / (p + r)


def _overlaps(a: Tuple[int, int], b: Tuple[int, int]) -> bool:
    return a[0] < b[1] and b[0] < a[1]


def _gold_hit(case: Case, spans: Iterable[Tuple[str, int, int]]) -> bool:
    spans = list(spans)
    return any(
        doc == gq.doc and _overlaps((start, end), (gq.char_start, gq.char_end))
        for gq in case.gold_quotes
        for doc, start, end in spans
    )


def _shown_spans(result: ClaimResult) -> List[Tuple[str, int, int]]:
    spans = [(ev.document, ev.char_start, ev.char_end) for ev in result.evidence]
    spans += [(p.document, p.char_start, p.char_end) for p in result.closest]
    return spans


def splitting_scores(cases: Sequence[SplittingCase]) -> dict:
    """Claim-splitting recall and precision.

    Recall (the release metric) is gold claims recovered exactly, divided by gold
    claims. It ignores extra claims, so precision and the list of extra claims
    are always reported next to it.
    """
    gold = recovered = produced_total = 0
    extras: List[dict] = []
    for case in cases:
        split = split_answer(case.text, "sentences")
        produced = [(c.line, " ".join(c.text.split())) for c in split.claims]
        expected = [(line, " ".join(text.split())) for line, text in case.expected]
        gold += len(expected)
        produced_total += len(produced)
        recovered += sum(1 for item in expected if item in produced)
        extras += [{"file": case.name, "line": line, "text": text}
                   for line, text in produced if (line, text) not in expected]
    return {
        "splitting_gold_claims": gold,
        "splitting_produced_claims": produced_total,
        "splitting_recovered": recovered,
        "splitting_extra_claims": len(extras),
        "splitting_extra_list": extras,
        "splitting_recall": ratio(recovered, gold),
        "splitting_precision": ratio(recovered, produced_total),
    }


def _rate(m: dict, key: str, numerator: int, denominator: int) -> None:
    """Store a rate and the counts behind it, so small samples stay visible."""
    m[key] = ratio(numerator, denominator)
    m["fractions"][key] = [numerator, denominator]


def compute(cases: Sequence[Case], predictions: Dict[str, ClaimResult],
            splitting: Sequence[SplittingCase]) -> dict:
    confusion = {g: {p: 0 for p in LABELS} for g in LABELS}
    for case in cases:
        confusion[case.gold_label][predictions[case.id].label] += 1

    m: dict = {"cases": len(cases), "confusion": confusion, "fractions": {}}
    for label in LABELS:
        tp = confusion[label][label]
        predicted = sum(confusion[g][label] for g in LABELS)
        gold = sum(confusion[label].values())
        _rate(m, f"precision_{label}", tp, predicted)
        _rate(m, f"recall_{label}", tp, gold)
        m[f"f1_{label}"] = _f1(m[f"precision_{label}"], m[f"recall_{label}"])

    for label in DECIDED:
        in_scope = [c for c in cases if c.gold_label == label and c.scope == "in"]
        hits = sum(1 for c in in_scope if predictions[c.id].label == label)
        _rate(m, f"in_scope_recall_{label}", hits, len(in_scope))

    not_supported = [c for c in cases if c.gold_label != SUPPORTED]
    false_support = sum(1 for c in not_supported if predictions[c.id].label == SUPPORTED)
    # Owner decision 3: incorrect SUPPORTED / all cases whose gold label is not SUPPORTED.
    _rate(m, "false_support_rate", false_support, len(not_supported))
    m["false_support_share_of_all"] = ratio(false_support, len(cases))
    # Owner decision (2026-10-29): incorrect CONTRADICTED / all cases whose
    # correct label is not CONTRADICTED. Provisional release gate, <= 5%. On the
    # test subset the guard ensures every correct label is human-reviewed; dev
    # labels are unreviewed, so dev values are tuning results only.
    not_contradicted = [c for c in cases if c.gold_label != CONTRADICTED]
    _rate(m, "false_contradicted_rate",
          sum(1 for c in not_contradicted if predictions[c.id].label == CONTRADICTED), len(not_contradicted))
    traps = [c for c in cases if c.case_type == "keyword_trap"]
    m["keyword_trap_supported"] = sum(1 for c in traps if predictions[c.id].label == SUPPORTED)
    m["fractions"]["keyword_trap_supported"] = [m["keyword_trap_supported"], len(traps)]

    predicted_ie = [c for c in cases if predictions[c.id].label == INSUFFICIENT]
    _rate(m, "abstention_correctness",
          sum(1 for c in predicted_ie if c.gold_label == INSUFFICIENT), len(predicted_ie))
    decided = [c for c in cases if predictions[c.id].label in DECIDED]
    _rate(m, "coverage", len(decided), len(cases))

    # Quote integrity: each quote matches its file exactly. This says nothing
    # about whether the quote supports or contradicts the claim.
    quotes = [ev for c in cases for ev in predictions[c.id].evidence]
    m["quotes_shown"] = len(quotes)
    _rate(m, "quote_verbatim_rate", sum(1 for ev in quotes if ev.verified_verbatim), len(quotes))

    # An automatic proxy for PRD section 7: correct label, intact quote, and the
    # quote overlaps a gold quote. Whether the quote genuinely justifies the
    # verdict needs a person to judge it against independently labelled cases.
    correct_cited = 0
    for c in decided:
        result = predictions[c.id]
        if (result.label == c.gold_label
                and all(ev.verified_verbatim for ev in result.evidence)
                and _gold_hit(c, [(ev.document, ev.char_start, ev.char_end) for ev in result.evidence])):
            correct_cited += 1
    _rate(m, "citation_verified_correctness", correct_cited, len(decided))

    gold_decided = [c for c in cases if c.gold_label in DECIDED and c.gold_quotes]
    _rate(m, "hit_at_3", sum(1 for c in gold_decided if _gold_hit(c, _shown_spans(predictions[c.id]))),
          len(gold_decided))

    m.update(splitting_scores(splitting))
    m["fractions"]["splitting_recall"] = [m["splitting_recovered"], m["splitting_gold_claims"]]
    m["fractions"]["splitting_precision"] = [m["splitting_recovered"], m["splitting_produced_claims"]]

    # V0.2 indicators (PRD.md section 10, criteria 2 and 3).
    abstained = [c for c in cases if c.gold_label in DECIDED and predictions[c.id].label == INSUFFICIENT]
    all_decided_gold = [c for c in cases if c.gold_label in DECIDED]
    _rate(m, "v02_abstained_share", len(abstained), len(all_decided_gold))
    _rate(m, "v02_abstained_out_of_scope_share", sum(1 for c in abstained if c.scope == "out"), len(abstained))
    _rate(m, "v02_abstained_hit_at_3",
          sum(1 for c in abstained if c.gold_quotes and _gold_hit(c, _shown_spans(predictions[c.id]))),
          sum(1 for c in abstained if c.gold_quotes))
    return m


def _passes(value: Optional[float], op: str, target: float) -> bool:
    if value is None:
        return False
    return value >= target if op == ">=" else value <= target


def checks(metrics: dict, targets=RELEASE_TARGETS) -> List[dict]:
    fractions = metrics.get("fractions", {})
    return [
        {"name": name, "metric": key, "target": f"{op} {target}", "actual": metrics.get(key),
         "numerator_denominator": fractions.get(key),
         "pass": _passes(metrics.get(key), op, target)}
        for key, op, target, name in targets
    ]


def historical(metrics: dict) -> List[dict]:
    return checks(metrics, HISTORICAL_BENCHMARKS)


def pending(metrics: dict) -> List[dict]:
    """Proposed gates with no approved threshold. Reported, never passed or failed."""
    fractions = metrics.get("fractions", {})
    return [
        {"name": name, "metric": key, "target": None, "status": "awaiting owner approval",
         "actual": metrics.get(key), "numerator_denominator": fractions.get(key)}
        for key, name in PENDING_GATES
    ]
