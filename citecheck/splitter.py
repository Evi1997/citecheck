"""Splitting AI answers into claims, and documents into sentences.

All rules are documented in TECH_PLAN.md section 3 and covered by tests.
"""

import re
from typing import List, NamedTuple, Optional, Tuple

ABBREVIATIONS = frozenset({
    "mr", "mrs", "ms", "dr", "prof", "sr", "jr", "st", "vs", "etc", "e.g", "i.e",
    "approx", "est", "inc", "ltd", "co", "corp", "fig", "jan", "feb", "mar",
    "apr", "jun", "jul", "aug", "sep", "sept", "oct", "nov", "dec", "u.s",
    "u.k", "e.u",
})

_END_RE = re.compile(r"[.!?;]+[\"'”’)\]]*")
_CLOSERS = "\"'”’)]"
_OPENERS = "\"'“‘(["
_WORD_BEFORE_RE = re.compile(r"([A-Za-z][A-Za-z.]*)$")

FENCE_RE = re.compile(r"^\s*(```|~~~)")
HEADING_RE = re.compile(r"^\s*#{1,6}\s+")
QUOTE_MARK_RE = re.compile(r"^\s*>\s?")
LIST_MARKER_RE = re.compile(r"^\s*(?:[-*+•]|\d{1,3}[.)]|[a-z][.)])\s+")
LINK_RE = re.compile(r"\[([^\]]+)\]\([^)]*\)")
CITATION_RE = re.compile(r"\s*\[\d+(?:\s*[,–-]\s*\d+)*\]")
STRONG_RE = re.compile(r"(\*\*|__)(.+?)\1")
EMPHASIS_RE = re.compile(r"(?<![\w*])\*(?=\S)(.+?)(?<=\S)\*(?![\w*])|(?<![\w_])_(?=\S)(.+?)(?<=\S)_(?![\w_])")
CODE_RE = re.compile(r"`([^`]*)`")
WORD_RE = re.compile(r"\w+")

# Chat and navigation text that AI answers often add. These sentences talk to
# the reader or point elsewhere, so they are not claims about the documents.
META_RE = re.compile(
    r"^(?:let me know|feel free|please let me know|i hope|hope (?:this|that|it) helps"
    r"|happy to help|i(?:['’]d| would) be (?:happy|glad)|i can help"
    r"|if you (?:have|need) (?:any|anything|more|further|other)"
    r"|read more|learn more|find out more|see more|for more (?:information|details|info)"
    r"|click here|check out|visit|see)\b",
    re.IGNORECASE,
)
# A line that introduces a list of sources, such as "Sources: [1] ...".
SOURCES_RE = re.compile(r"^(?:sources?|references?|citations?|further reading|see also)\s*:", re.IGNORECASE)

MIN_CLAIM_WORDS = 3
MAX_HEADING_WORDS = 8


class Claim(NamedTuple):
    id: int
    text: str
    line: int


class Skipped(NamedTuple):
    line: int
    text: str
    reason: str  # "question", "lead_in", "too_short", "meta", "heading", "sources_list", "code_or_table"


class AnswerSplit(NamedTuple):
    claims: List[Claim]
    skipped: List[Skipped]
    blank_lines: int


class DocSentence(NamedTuple):
    start: int
    end: int
    heading: Optional[str]
    block: int = 0  # paragraph or list item number; neighbours share a block


LINE_BREAK_RE = re.compile(r"\r\n|\r|\n")


def line_spans(text: str) -> List[Tuple[int, int]]:
    """(start, end) of each line without its line break. Handles \\n, \\r\\n and \\r."""
    spans, pos = [], 0
    for match in LINE_BREAK_RE.finditer(text):
        spans.append((pos, match.start()))
        pos = match.end()
    spans.append((pos, len(text)))
    return spans


# Sentence boundaries ------------------------------------------------------

def _is_boundary(text: str, match, end: int, split_semicolons: bool = True) -> bool:
    j = match.end()
    if j >= end or not text[j].isspace():
        return False
    k = j
    while k < end and text[k].isspace():
        k += 1
    if k >= end:
        return False
    punct = match.group()
    if ";" in punct and not any(ch in punct for ch in ".!?"):
        # In documents, "Refunds are available; not for sale items" is one
        # statement, so a semicolon doesn't end it (QA finding F10).
        return split_semicolons
    if ";" in punct:
        return True
    nxt = text[k]
    if not (nxt.isupper() or nxt.isdigit() or nxt in _OPENERS):
        return False
    if punct.rstrip(_CLOSERS) == ".":
        found = _WORD_BEFORE_RE.search(text[max(0, match.start() - 20):match.start()])
        if found:
            word = found.group(1)
            if len(word) == 1 and word.isupper():
                return False  # an initial, as in "J. Smith"
            lowered = word.lower()
            if lowered in ABBREVIATIONS:
                return False
            if lowered == "no" and nxt.isdigit():
                return False  # "No. 5"
    return True


def _trim(text: str, start: int, end: int) -> Optional[Tuple[int, int]]:
    while start < end and text[start].isspace():
        start += 1
    while end > start and text[end - 1].isspace():
        end -= 1
    return (start, end) if start < end else None


def sentence_spans(text: str, start: int = 0, end: Optional[int] = None,
                   split_semicolons: bool = True) -> List[Tuple[int, int]]:
    """Split text[start:end] into sentence spans without surrounding whitespace.

    Answers split at semicolons (each part is checked as its own claim).
    Documents don't, so a restriction after a semicolon stays in the sentence.
    """
    if end is None:
        end = len(text)
    raw_spans = []
    seg_start = start
    for match in _END_RE.finditer(text, start, end):
        if _is_boundary(text, match, end, split_semicolons):
            raw_spans.append((seg_start, match.end()))
            seg_start = match.end()
    raw_spans.append((seg_start, end))
    spans = []
    for a, b in raw_spans:
        trimmed = _trim(text, a, b)
        if trimmed:
            spans.append(trimmed)
    return spans


# Answer files ---------------------------------------------------------------

def _strip_markers(line: str) -> Tuple[str, bool]:
    """Remove blockquote, heading and list markers. Report whether it was a heading."""
    while QUOTE_MARK_RE.match(line):
        line = QUOTE_MARK_RE.sub("", line, count=1)
    heading = HEADING_RE.match(line)
    if heading:
        return line[heading.end():], True
    marker = LIST_MARKER_RE.match(line)
    if marker:
        line = line[marker.end():]
    return line, False


def clean_line(line: str) -> Tuple[str, bool]:
    """Remove Markdown decoration from one answer line."""
    line, is_heading = _strip_markers(line)
    line = LINK_RE.sub(r"\1", line)
    line = CITATION_RE.sub("", line)
    line = STRONG_RE.sub(r"\2", line)
    line = EMPHASIS_RE.sub(lambda m: m.group(1) or m.group(2), line)
    line = CODE_RE.sub(r"\1", line)
    return line.strip(), is_heading


def skip_reason(text: str) -> Optional[str]:
    if not WORD_RE.search(text):
        return "blank"
    if META_RE.match(text):
        return "meta"
    if text.endswith("?"):
        return "question"
    if text.endswith(":"):
        return "lead_in"
    if len(WORD_RE.findall(text)) < MIN_CLAIM_WORDS:
        return "too_short"
    return None


def split_answer(text: str, mode: str = "sentences") -> AnswerSplit:
    """Split an AI answer into claims. mode is "sentences" or "lines"."""
    if mode not in ("sentences", "lines"):
        raise ValueError(f"unknown split mode: {mode}")
    claims: List[Claim] = []
    skipped: List[Skipped] = []
    blank = 0
    in_fence = False
    lines = LINE_BREAK_RE.split(text)
    if lines and lines[-1] == "":
        lines.pop()  # a final newline does not start another line
    for line_no, raw in enumerate(lines, start=1):
        line = raw.rstrip("\r")
        if FENCE_RE.match(line):
            in_fence = not in_fence
            skipped.append(Skipped(line_no, line.strip(), "code_or_table"))
            continue
        if in_fence or line.lstrip().startswith("|"):
            skipped.append(Skipped(line_no, line.strip(), "code_or_table"))
            continue
        if not line.strip():
            blank += 1
            continue
        cleaned, is_heading = clean_line(line)
        if is_heading:
            skipped.append(Skipped(line_no, cleaned, "heading"))
            continue
        if SOURCES_RE.match(cleaned):
            skipped.append(Skipped(line_no, cleaned, "sources_list"))
            continue
        if mode == "lines":
            pieces = [cleaned]
        else:
            pieces = [cleaned[a:b] for a, b in sentence_spans(cleaned)]
        for piece in pieces:
            piece = piece.strip()
            if piece.endswith(";"):
                piece = piece[:-1].rstrip()
            reason = skip_reason(piece)
            if reason == "blank":
                blank += 1
            elif reason:
                skipped.append(Skipped(line_no, piece, reason))
            else:
                claims.append(Claim(len(claims) + 1, piece, line_no))
    return AnswerSplit(claims, skipped, blank)


# Documents ------------------------------------------------------------------

class _Block(NamedTuple):
    start: int
    end: int
    lines: int
    kind: str  # "heading", "list" or "para"


def _blocks(text: str) -> List[_Block]:
    blocks: List[_Block] = []
    current: Optional[list] = None  # [start, end, lines, kind]
    in_fence = False
    for line_start, line_end in line_spans(text):
        line = text[line_start:line_end]
        if FENCE_RE.match(line):
            in_fence = not in_fence
            current = _close(blocks, current)
            continue
        if in_fence:
            continue
        if not line.strip():
            current = _close(blocks, current)
            continue
        heading = HEADING_RE.match(line)
        if heading:
            current = _close(blocks, current)
            blocks.append(_Block(line_start + heading.end(), line_end, 1, "heading"))
            continue
        marker = LIST_MARKER_RE.match(line)
        if marker:
            current = _close(blocks, current)
            current = [line_start + marker.end(), line_end, 1, "list"]
            continue
        if current is None:
            current = [line_start, line_end, 1, "para"]
        else:
            current[1] = line_end
            current[2] += 1
    _close(blocks, current)
    return blocks


def _close(blocks: List[_Block], current: Optional[list]) -> None:
    if current is not None:
        blocks.append(_Block(*current))
    return None


def _looks_like_plain_heading(block_text: str, block: _Block) -> bool:
    return (
        block.kind == "para"
        and block.lines == 1
        and len(WORD_RE.findall(block_text)) <= MAX_HEADING_WORDS
        and not block_text.endswith((".", "!", "?", ";", ","))
    )


def split_document(text: str) -> List[DocSentence]:
    """Split a document into sentences, each tagged with its current heading.

    Headings are Markdown "#" lines, or single-line paragraphs of up to 8 words
    with no closing punctuation. Headings are context, not sentences. A longer
    paragraph ending in ":" ("The following applies to members only:") is kept
    as sentences and also becomes the context for what follows. Semicolons
    don't end document sentences.
    """
    sentences: List[DocSentence] = []
    heading: Optional[str] = None
    for number, block in enumerate(_blocks(text)):
        block_text = text[block.start:block.end].strip()
        if block.kind == "heading" or _looks_like_plain_heading(block_text, block):
            heading = block_text
            continue
        for a, b in sentence_spans(text, block.start, block.end, split_semicolons=False):
            sentences.append(DocSentence(a, b, heading, number))
        if block.kind == "para" and block_text.endswith(":"):
            heading = block_text
    return sentences
