"""The three V0.1 verdict rules: S1, C1 and C2 (TECH_PLAN.md section 4.3).

Each rule compares one claim with one document sentence. Both arrive as match
tokens (articles removed). A rule returns a RuleHit or None. Rules never look
at more than one sentence, and keyword overlap alone never produces a hit.

Shared limits (rule set 0.4.0). Extra sentence words that the claim leaves out
can narrow what the sentence says, so they are allowed only where they can't:
- Before the claim's first word: never ("Cancelled accounts", "In Scotland, ...").
- After the claim's last word: never. "The fee is waived for UK students" must
  not support "The fee is waived". Since 0.4.0 this includes a closing date:
  "had 60 staff in 2015" says nothing about today (QA finding F9).
- Between the claim's words (S1 only): only earlier items of a list ending in
  "and", at most MAX_GAP words ("sells kettles, toasters and coffee machines").
  "waived for UK students" must not support "waived for students".
"""

from typing import FrozenSet, List, NamedTuple, Optional, Tuple

from .cues import IDENTIFIER_WORDS, Cue, cue_spans, is_negation, number_context, without_negation
from .thresholds import MAX_GAP
from .tokens import Token, is_number, number_value

MIN_CLAIM_TOKENS = 3
AUXILIARIES = frozenset({"do", "does", "did"})
DATE_PREPOSITIONS = frozenset({"in", "during", "since", "until", "by", "as", "of"})
ORDINAL_SUFFIXES = frozenset({"st", "nd", "rd", "th"})


class RuleHit(NamedTuple):
    rule: str    # "S1", "C1" or "C2"
    first: int   # index of the first matched sentence token
    last: int    # index of the last matched sentence token
    detail: dict


def is_year(tok: Token) -> bool:
    return tok.kind == "number" and len(tok.norm) == 4 and tok.norm.isdigit() and 1800 <= int(tok.norm) <= 2199


def is_date_phrase(tokens: List[Token]) -> bool:
    """Only a year, or a date preposition and a year: "2021", "in 2021", "as of 2021"."""
    words = [t for t in tokens if not (t.kind == "word" and t.word in DATE_PREPOSITIONS)]
    return len(words) == 1 and is_year(words[0]) and len(tokens) <= 3


def allowed_tail(tail: List[Token]) -> bool:
    """Words after the claim's last word. Since rule set 0.4.0: none at all."""
    return not tail


def is_list_insertion(region: List[Token]) -> bool:
    """Words between two claim words that are earlier items of a list: "kettles, toasters and"."""
    return (
        bool(region)
        and region[-1].kind == "word"
        and region[-1].word == "and"
        and not any(is_number(tok) or tok.kind == "symbol" for tok in region)
    )


def check_s1(c: List[Token], t: List[Token], c_cues: FrozenSet[str],
             t_cues: FrozenSet[str], max_gap: int = MAX_GAP) -> Optional[RuleHit]:
    """S1 ordered match: the sentence starts with the claim's words, in order."""
    if len(c) < MIN_CLAIM_TOKENS or c_cues != t_cues or len(t) < len(c):
        return None
    cn = [x.norm for x in c]
    tn = [x.norm for x in t]
    if tn[0] != cn[0]:
        return None
    positions = [0]
    j = 0
    for want in cn[1:]:
        j += 1
        while j < len(tn) and tn[j] != want:
            j += 1
        if j == len(tn):
            return None
        positions.append(j)
    for a, b in zip(positions, positions[1:]):
        region = t[a + 1:b]
        if region and not is_list_insertion(region):
            return None  # an adjective, place or condition inside the claim's words
    gap = positions[-1] + 1 - len(cn)
    if gap > max_gap:
        return None
    tail = t[positions[-1] + 1:]
    if not allowed_tail(tail):
        return None  # a trailing phrase may narrow the claim ("for UK students")
    return RuleHit("S1", 0, positions[-1], {"gap": gap, "tail": len(tail)})


def _reduce(tokens: List[Token]) -> Tuple[List[int], List[Cue]]:
    """Drop negations and do/does/did. Return kept indexes and the negations."""
    negations = [cue for cue in cue_spans(tokens) if is_negation(cue.text)]
    skip = set()
    for cue in negations:
        skip.update(range(cue.start, cue.end))
    kept = [
        i for i, tok in enumerate(tokens)
        if i not in skip and not (tok.kind == "word" and tok.word in AUXILIARIES)
    ]
    return kept, negations


def check_c1(c: List[Token], t: List[Token], c_cues: FrozenSet[str],
             t_cues: FrozenSet[str]) -> Optional[RuleHit]:
    """C1 negation flip: the same words, but exactly one side is negated once."""
    if without_negation(c_cues) != without_negation(t_cues):
        return None
    c_kept, c_neg = _reduce(c)
    t_kept, t_neg = _reduce(t)
    if sorted((len(c_neg), len(t_neg))) != [0, 1]:
        return None
    cn = [c[i].norm for i in c_kept]
    tn = [t[i].norm for i in t_kept]
    if len(cn) < MIN_CLAIM_TOKENS or len(tn) < len(cn) or tn[:len(cn)] != cn:
        return None
    first, last = t_kept[0], t_kept[len(cn) - 1]
    if not allowed_tail(t[last + 1:]):
        return None
    negation = (t_neg or c_neg)[0]
    if t_neg and not (first - 1 <= negation.start <= last):
        return None  # the document's negation belongs to another part of the sentence
    return RuleHit("C1", first, last, {"negation": negation.text})


def check_c2(c: List[Token], t: List[Token], c_cues: FrozenSet[str],
             t_cues: FrozenSet[str]) -> Optional[RuleHit]:
    """C2 value conflict: the same words, except one number differs.

    If either side has a threshold or approximation word ("within", "after",
    "over", "at least", "about"...) or a negation, a different number doesn't
    have to be a contradiction: "cannot cancel after 14 days" implies "cannot
    cancel after 90 days". The hit is then marked with context_words and the
    verdict treats it as unclear, never as CONTRADICTED.
    """
    if len(c) < MIN_CLAIM_TOKENS or c_cues != t_cues or len(t) < len(c):
        return None
    if not allowed_tail(t[len(c):]):
        return None  # "costs $25 in Canada" is not about the claim's general case
    cn = [x.norm for x in c]
    tn = [x.norm for x in t]
    diffs = [k for k in range(len(cn)) if tn[k] != cn[k]]
    if len(diffs) != 1:
        return None
    k = diffs[0]
    # Only plain numbers can conflict. "1,5" or "1.2.3" could mean several things.
    if c[k].kind != "number" or t[k].kind != "number":
        return None
    claim_value, doc_value = number_value(c[k]), number_value(t[k])
    if claim_value is None or doc_value is None or claim_value == doc_value:
        # "3.50" and "3.5", or "05" and "5": written differently, same value.
        return None
    if c[k].norm in {x.norm for x in t if is_number(x)}:
        # The claim's value also appears in the sentence, so it may be a
        # second case rather than a conflict ("14 days, or 30 days for ...").
        return None
    context = sorted(number_context(c) | number_context(t))
    negated = any(is_negation(cue) for cue in c_cues)
    identifier = None
    if k > 0 and c[k - 1].kind == "word" and c[k - 1].word in IDENTIFIER_WORDS:
        identifier = c[k - 1].word  # "Section 3" vs "Section 4": different items
    elif k + 1 < len(c) and c[k + 1].kind == "word" and c[k + 1].word in ORDINAL_SUFFIXES:
        identifier = "ordinal"      # "3rd floor" vs "4th floor"
    return RuleHit("C2", 0, len(cn) - 1, {
        "claim_index": k,
        "document_index": k,
        "context_words": context,
        "negated": negated,
        "identifier": identifier,
    })


def is_unclear(hit: RuleHit) -> bool:
    """A C2 hit whose numbers can't be compared safely."""
    return hit.rule == "C2" and bool(hit.detail.get("context_words") or hit.detail.get("negated")
                                     or hit.detail.get("identifier"))


def find_hit(c: List[Token], c_cues: FrozenSet[str], t: List[Token],
             t_cues: FrozenSet[str]) -> Optional[RuleHit]:
    """Return the first rule that fires for this claim and sentence."""
    return check_s1(c, t, c_cues, t_cues) or check_c1(c, t, c_cues, t_cues) or check_c2(c, t, c_cues, t_cues)
