"""Tokenising and normalising text for the matching rules.

Tokens are found in the original text, so every token keeps its original
character span. Normalisation is applied to each token on its own, which means
offsets never shift. See TECH_PLAN.md section 4.1.
"""

import re
import unicodedata
from typing import List, NamedTuple, Optional, Tuple


class Token(NamedTuple):
    norm: str   # normalised and stemmed; used for matching
    word: str   # normalised but not stemmed; used for cue words and stopwords
    kind: str   # "word", "number", "ambiguous_number" or "symbol"
    start: int  # character offset in the original text
    end: int


_TOKEN_RE = re.compile(
    r"\d+(?:[.,]\d+)*"                      # numbers: 12, 1,000, 4.2, 1.2.3
    r"|[^\W\d_]+(?:['’][^\W\d_]+)*"    # words, including contractions
    r"|(?<![\w.])[-−](?=\d)"                # a minus sign: "-5", not "9am-5pm"
    r"|<=|>=|[$€£%<>≤≥≈~±°+]"               # currency, %, comparison and other signs
)

SYMBOLS = frozenset({"$", "€", "£", "%", "<", ">", "≤", "≥", "≈", "~", "±", "°", "+", "-"})
SYMBOL_FORMS = {"<=": "≤", ">=": "≥", "−": "-"}
ARTICLES = frozenset({"a", "an", "the"})
NUMBER_KINDS = frozenset({"number", "ambiguous_number"})

# Thousands separators are accepted only in the usual English grouping.
_THOUSANDS_RE = re.compile(r"\d{1,3}(?:,\d{3})+(?:\.\d+)?")
_PLAIN_NUMBER_RE = re.compile(r"\d+(?:\.\d+)?")
# "1.000" may be a European thousand or a decimal with three places.
_DOT_THOUSAND_RE = re.compile(r"\d{1,3}\.\d{3}")

# "one" is left out on purpose: it is often a pronoun ("one of the stores").
NUMBER_WORDS = {
    "zero": "0", "two": "2", "three": "3", "four": "4",
    "five": "5", "six": "6", "seven": "7", "eight": "8", "nine": "9",
    "ten": "10", "eleven": "11", "twelve": "12", "thirteen": "13",
    "fourteen": "14", "fifteen": "15", "sixteen": "16", "seventeen": "17",
    "eighteen": "18", "nineteen": "19", "twenty": "20", "thirty": "30",
    "forty": "40", "fifty": "50", "sixty": "60", "seventy": "70",
    "eighty": "80", "ninety": "90",
}

CONTRACTIONS = {
    "can't": ("can", "not"),
    "cannot": ("can", "not"),
    "won't": ("will", "not"),
    "shan't": ("shall", "not"),
}

SUFFIX_EXPANSIONS = (
    ("n't", "not"),
    ("'re", "are"),
    ("'ve", "have"),
    ("'ll", "will"),
    ("'d", "would"),
    ("'m", "am"),
)

# Short or irregular words the light stemmer must leave alone.
STEM_EXCEPTIONS = frozenset({
    "does", "has", "was", "is", "this", "its", "his", "as", "us", "yes",
    "news", "less", "always", "perhaps", "sometimes", "besides", "towards",
    "afterwards", "whereas", "unless", "across", "plus", "thus", "series",
})


def stem(word: str) -> str:
    """Light, deterministic suffix stripping. No -ed or -ing stripping."""
    if word in STEM_EXCEPTIONS:
        return word
    if len(word) > 4 and word.endswith("ies"):
        return word[:-3] + "y"
    if len(word) > 3 and word.endswith("es") and word[:-2].endswith(("s", "x", "z", "ch", "sh")):
        return word[:-2]
    if len(word) > 3 and word.endswith("s") and not word.endswith(("ss", "us", "is")):
        return word[:-1]
    return word


def _normalise_word(raw: str) -> str:
    return unicodedata.normalize("NFKC", raw).casefold().replace("’", "'")


def is_number(tok: Token) -> bool:
    return tok.kind in NUMBER_KINDS


def _normalise_number(raw: str) -> Tuple[str, str]:
    """Return (norm, kind). Rule set 0.4.0 keeps numbers as written.

    Trailing zeros and leading zeros are kept ("3.10" is not "3.1", "05" is not
    "5"), so versions and codes never merge. Formats that could mean two
    different values ("1,5", "1.000", "1.2.3") are "ambiguous_number": they can
    match the same text exactly but never count as a value conflict.
    """
    if _THOUSANDS_RE.fullmatch(raw):
        return raw.replace(",", ""), "number"
    if _PLAIN_NUMBER_RE.fullmatch(raw) and not _DOT_THOUSAND_RE.fullmatch(raw):
        return raw, "number"
    return raw, "ambiguous_number"


def number_value(tok: Token) -> Optional[float]:
    """The numeric value of a plain number token, or None."""
    if tok.kind != "number":
        return None
    try:
        return float(tok.norm)
    except ValueError:
        return None


def _expand(word: str) -> List[str]:
    if word in CONTRACTIONS:
        return list(CONTRACTIONS[word])
    for suffix, full in SUFFIX_EXPANSIONS:
        if word.endswith(suffix) and len(word) > len(suffix):
            return [word[: -len(suffix)], full]
    if word.endswith("'s"):
        return [word[:-2]]
    return [word.replace("'", "")]


def tokenize(text: str, offset: int = 0) -> List[Token]:
    """Return every token in text. Offsets are shifted by offset."""
    tokens: List[Token] = []
    for match in _TOKEN_RE.finditer(text):
        raw = match.group()
        start, end = match.start() + offset, match.end() + offset
        if raw[0].isdigit():
            norm, kind = _normalise_number(raw)
            tokens.append(Token(norm, raw, kind, start, end))
            continue
        symbol = SYMBOL_FORMS.get(raw, raw)
        if symbol in SYMBOLS:
            tokens.append(Token(symbol, symbol, "symbol", start, end))
            continue
        for part in _expand(_normalise_word(raw)):
            if not part:
                continue
            if part in NUMBER_WORDS:
                tokens.append(Token(NUMBER_WORDS[part], part, "number", start, end))
            elif part == "percent":
                tokens.append(Token("%", "%", "symbol", start, end))
            else:
                tokens.append(Token(stem(part), part, "word", start, end))
    return _merge_per_cent(tokens)


def _merge_per_cent(tokens: List[Token]) -> List[Token]:
    merged: List[Token] = []
    i = 0
    while i < len(tokens):
        if (i + 1 < len(tokens) and tokens[i].word == "per" and tokens[i + 1].word == "cent"):
            merged.append(Token("%", "%", "symbol", tokens[i].start, tokens[i + 1].end))
            i += 2
            continue
        merged.append(tokens[i])
        i += 1
    return merged


def match_tokens(tokens: List[Token]) -> List[Token]:
    """Tokens used by the rules: everything except a, an and the."""
    return [t for t in tokens if not (t.kind == "word" and t.word in ARTICLES)]
