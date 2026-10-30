"""Cue words: words that can change a sentence's truth or scope.

These lists are provisional heuristics approved for V0.1 (TECH_PLAN.md
section 4.2). They deliberately err towards abstaining: a false alarm only
turns a verdict into INSUFFICIENT_EVIDENCE. Any change to a list must bump
RULESET_VERSION and be recorded.
"""

from typing import FrozenSet, Iterable, List, NamedTuple, Optional

from .tokens import Token

RULESET_VERSION = "0.4.0"

NEGATION = frozenset({
    "not", "no", "never", "none", "nobody", "nothing", "neither", "nor", "without",
})

HEDGE = frozenset({
    "may", "might", "could", "would", "should", "possibly", "probably", "likely",
    "unlikely", "perhaps", "reportedly", "allegedly", "approximately", "about",
    "around", "roughly", "nearly", "almost", "estimated", "expected", "planned",
    "proposed",
    # Added in 0.4.0 (QA finding F19).
    "typically", "generally", "normally", "mostly", "mainly", "partly",
    "partially", "potentially", "tentatively", "temporarily",
})

CONDITION = frozenset({
    "if", "unless", "except", "only", "until", "when", "whenever", "provided",
    "excluding", "or", "but", "however", "although", "while",
    # Added in 0.4.0 (QA finding F19).
    "subject", "depending", "depends", "excludes", "excluded", "conditional",
    "eligible", "whereas", "otherwise",
})

QUANTITY = frozenset({
    "all", "every", "each", "some", "most", "many", "few", "several", "any",
    "always", "usually", "often", "sometimes", "rarely", "more", "less", "fewer",
    "least", "over", "under", "above", "below", "than", "maximum", "minimum",
})

# Framing, time and outdated or hypothetical context.
FRAMING = frozenset({
    "according", "said", "says", "claimed", "reported", "previously", "formerly",
    "former", "before", "after", "since", "suppose", "supposing", "imagine",
    "hypothetically", "hypothetical", "assume", "assuming", "myth", "rumour",
    "rumor", "outdated", "superseded", "obsolete", "deprecated",
})

PHRASES = {
    ("no", "longer"): "no longer",
    ("used", "to"): "used to",
    ("up", "to"): "up to",
    ("at", "least"): "at least",
    ("at", "most"): "at most",
}

NEGATION_PHRASES = frozenset({"no longer"})

SINGLE_CUES = NEGATION | HEDGE | CONDITION | QUANTITY | FRAMING

# Words in a section heading that mark the sentences under it as possibly old,
# draft or hypothetical. Rules never decide a label from such a sentence alone.
HEADING_CONTEXT = frozenset({
    "old", "older", "previous", "former", "superseded", "outdated", "obsolete",
    "archived", "archive", "draft", "proposed", "hypothetical", "example",
    "examples", "scenario", "scenarios", "deprecated", "historical", "history",
    "myth", "myths", "rumour", "rumours", "rumor", "rumors", "retired", "expired",
    # Added in 0.4.0: headings that limit or carve out scope (QA finding F10).
    "exclusion", "exclusions", "excluded", "exception", "exceptions",
    "limitation", "limitations", "restriction", "restrictions", "conditions",
    "eligibility", "not", "only", "unless", "except", "if", "following",
    "apply", "applies",
})


# Words that make a number a threshold, a limit, an estimate or part of a
# range. With these, two different numbers don't have to contradict each other
# ("within 3 days" implies "within 10 days"), so rule C2 never decides when
# they appear.
NUMBER_CONTEXT = frozenset({
    "within", "after", "before", "over", "under", "above", "below", "more",
    "less", "fewer", "than", "least", "most", "maximum", "minimum", "until",
    "since", "up to", "at least", "at most", "about", "around",
    "approximately", "roughly", "nearly", "almost", "estimated",
    # Added in 0.4.0 (QA finding F19): starting points and ranges.
    "from", "between", "starting", "typically", "generally",
})
# Signs that make a number a bound or an estimate (QA finding F7).
NUMBER_CONTEXT_SYMBOLS = frozenset({"<", ">", "≤", "≥", "≈", "~", "±", "+"})

# Words before a number that make it a label, not a quantity: "Section 3" and
# "Section 4" name different things, they don't disagree (QA finding F6).
IDENTIFIER_WORDS = frozenset({
    "section", "chapter", "clause", "article", "part", "page", "paragraph",
    "appendix", "annex", "schedule", "version", "v", "release", "build",
    "edition", "tier", "level", "grade", "class", "step", "phase", "stage",
    "round", "room", "unit", "floor", "gate", "platform", "route", "line",
    "model", "sku", "item", "no", "number", "table", "figure", "fig", "form",
    "code", "id", "ref", "reference", "ticket", "case", "order", "invoice",
    "account", "batch", "lot", "series", "type", "plan", "option", "zone",
    "band", "group", "team", "rule", "policy", "standard", "iso", "act",
})

# A claim whose subject is one of these can't be checked safely: "It has 45
# staff" could be about any company in the documents (QA finding F5).
REFERENCE_WORDS = frozenset({
    "it", "its", "they", "their", "theirs", "them", "he", "she", "his", "her",
    "him", "this", "that", "these", "those", "we", "our", "us", "i", "my",
    "you", "your", "one", "former", "latter",
})

# A neighbouring sentence in the same paragraph can narrow a sentence
# (QA finding F10). These are checked in the sentence before ...
PREVIOUS_SCOPE_WORDS = frozenset({
    "following", "below", "apply", "applies", "only", "unless", "except",
    "excluding", "if", "suppose", "supposing", "assume", "assuming", "imagine",
    "hypothetically",
})
# ... and these at the start of the sentence after.
NEXT_SCOPE_STARTS = frozenset({
    "however", "except", "but", "unless", "excluding", "exception",
    "exceptions", "although", "otherwise", "only",
})
# A following sentence that starts with a reference word counts too, if it
# also has a condition, a negation or "apply" ("This does not apply to ...").
NEXT_SCOPE_REFERENCES = frozenset({"this", "these", "that", "it", "they"})


class Cue(NamedTuple):
    start: int  # index of the first token
    end: int    # index after the last token
    text: str


def cue_spans(tokens: List[Token]) -> List[Cue]:
    """Find cue words and two-word cue phrases, in token order."""
    found: List[Cue] = []
    i = 0
    while i < len(tokens):
        tok = tokens[i]
        if tok.kind == "word":
            if i + 1 < len(tokens):
                phrase = PHRASES.get((tok.word, tokens[i + 1].word))
                if phrase:
                    found.append(Cue(i, i + 2, phrase))
                    i += 2
                    continue
            if tok.word in SINGLE_CUES:
                found.append(Cue(i, i + 1, tok.word))
        i += 1
    return found


def cue_set(tokens: List[Token]) -> FrozenSet[str]:
    return frozenset(cue.text for cue in cue_spans(tokens))


def is_negation(cue: str) -> bool:
    return cue in NEGATION or cue in NEGATION_PHRASES


def without_negation(cues: Iterable[str]) -> FrozenSet[str]:
    return frozenset(c for c in cues if not is_negation(c))


def number_context(tokens: List[Token]) -> FrozenSet[str]:
    """Threshold, approximation and range words or signs present in the tokens."""
    words = {t.word for t in tokens if t.kind == "word" and t.word in NUMBER_CONTEXT}
    words |= {cue.text for cue in cue_spans(tokens) if cue.text in NUMBER_CONTEXT}
    words |= {t.norm for t in tokens if t.kind == "symbol" and t.norm in NUMBER_CONTEXT_SYMBOLS}
    return frozenset(words)


def reference_subject(tokens: List[Token]) -> Optional[str]:
    """The claim's first word if it is a pronoun or pointing word, else None."""
    if tokens and tokens[0].kind == "word" and tokens[0].word in REFERENCE_WORDS:
        return tokens[0].word
    return None


def narrows_as_previous(tokens: List[Token], text: str) -> bool:
    """A sentence that may narrow the sentence after it."""
    if text.rstrip().endswith(":"):
        return True
    return any(t.kind == "word" and t.word in PREVIOUS_SCOPE_WORDS for t in tokens)


def narrows_as_next(tokens: List[Token]) -> bool:
    """A sentence that may carve an exception out of the sentence before it."""
    words = [t.word for t in tokens if t.kind == "word"]
    if not words:
        return False
    if words[0] in NEXT_SCOPE_STARTS:
        return True
    if words[0] in NEXT_SCOPE_REFERENCES:
        rest = set(words[1:])
        return bool(rest & (CONDITION | NEGATION | {"apply", "applies"}))
    return False


def heading_is_flagged(heading_tokens: List[Token]) -> bool:
    return any(t.kind == "word" and t.word in HEADING_CONTEXT for t in heading_tokens)
