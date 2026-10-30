"""Terminal, Markdown and JSON output. The only module that writes files.

All user-facing copy follows DESIGN.md section 7. Text from documents, answers
and filenames is untrusted: the terminal and Markdown output neutralise control
characters (safe_text.py), and Markdown special characters are escaped.
"""

import json
import os
import re
import textwrap
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional, Tuple, Union

from . import __version__
from .cues import RULESET_VERSION
from .safe_text import neutralise
from .splitter import LINE_BREAK_RE, Skipped
from .thresholds import CLOSEST_K, LONG_CLAIM_CHARS, MAX_GAP
from .verdict import CONTRADICTED, INSUFFICIENT, SUPPORTED, ClaimResult

WIDTH = 80
QUOTE_LIMIT = 300
SCHEMA_VERSION = "2.2"

TAG = {
    SUPPORTED: "[SUPPORTED]",
    CONTRADICTED: "[CONTRADICTED]",
    INSUFFICIENT: "[INSUFFICIENT EVIDENCE]",
}
SYMBOL = {  # (unicode, ascii)
    SUPPORTED: ("✔", "+"),
    CONTRADICTED: ("✘", "x"),
    INSUFFICIENT: ("?", "?"),
}
COLOUR = {SUPPORTED: "\033[32m", CONTRADICTED: "\033[31m", INSUFFICIENT: "\033[34m"}
RESET = "\033[0m"
GLOSS = {
    SUPPORTED: "A sentence in your documents matches this claim under CiteCheck's rules.",
    CONTRADICTED: "A sentence in your documents conflicts with this claim under CiteCheck's rules.",
    INSUFFICIENT: "Your documents don't settle this. This is not an error.",
}
LABEL_WORDS = {
    SUPPORTED: "Supported",
    CONTRADICTED: "Contradicted",
    INSUFFICIENT: "Insufficient evidence",
}

HEADER = "experimental prototype. Runs on this computer. No network. No AI model."
DISCLAIMER = (
    "Verdicts are relative to the documents you provided only. They do not "
    "show whether a claim is true in the real world. Not for high-stakes decisions."
)
CLOSEST_HEADING = "Closest passages (similar wording, not proof):"
METHOD_LINE = (
    "CiteCheck uses strict word-matching rules, not AI. Always check important "
    "claims with the quote and location shown."
)
QUOTE_INTEGRITY_NOTE = (
    "Quote integrity means each quote was re-read from its file and matches it "
    "exactly. It checks the text, not whether the quote proves or disproves the claim."
)
ABSTAIN_EXPLAINER = (
    "Insufficient evidence means your documents don't settle the claim under "
    "CiteCheck's rules. It's a safe answer, not a failure."
)
SKIP_TEXT = {
    "question": "question",
    "lead_in": "lead-in line",
    "too_short": "too short to check",
    "meta": "chat or navigation text, not a claim",
    "sources_list": "list of sources",
    "heading": "heading",
    "code_or_table": "code or table",
}
# Short notes shown next to a closest passage whose context limits it.
CONTEXT_NOTES = {
    "flagged_context": "heading suggests old, draft or limited content",
    "dated_context": "heading names a year the claim doesn't",
    "nearby_context": "a nearby sentence may narrow it",
}
RULE_SUMMARY = (
    ("S1 ordered match", "one sentence starts with the claim's words in the same order and "
     "has nothing else, except earlier items of a list (\"kettles, toasters and\"). Numbers "
     "match. No negation or qualifier words differ."),
    ("C1 negation flip", "the same words, but exactly one side is negated."),
    ("C2 value conflict", "the same words, but one number, date or percentage differs. "
     "Not used when words such as \"within\", \"after\", \"from\" or \"about\", a sign such "
     "as \"<\", a negation, or a label such as \"Section\" or \"Version\" mean the numbers "
     "can't be compared safely."),
    ("Context checks", "a sentence can't decide a label on its own if its heading suggests "
     "old, draft or limited content, if its heading names a year the claim doesn't, or if a "
     "neighbouring sentence may narrow it. A claim that starts with a pronoun such as \"it\" "
     "or \"this\" is never decided."),
)

MAX_LISTED_WORDS = 8

# A terminal line is either plain text (neutralised before printing) or a
# (RAW, text) pair that CiteCheck built itself, such as a coloured label.
RAW = "raw"
Line = Union[str, Tuple[str, str]]


def rule_text(rule: Optional[str], detail: dict) -> str:
    if rule == "S1":
        return ("S1 ordered match - the sentence says the same words in the same order with "
                "no narrowing words, numbers match, no negation or qualifier words differ.")
    if rule == "C1":
        return f'C1 negation flip - the same sentence, but one side says "{detail.get("negation", "not")}".'
    if rule == "C2":
        values = f'claim: {detail.get("claim_value")}, document: {detail.get("document_value")}'
        if detail.get("context_words") or detail.get("negated") or detail.get("identifier"):
            return (f"Different number ({values}). Because of {_context_phrase(detail)}, "
                    "CiteCheck can't compare the numbers safely.")
        return f"C2 value conflict - the same sentence with a different number ({values})."
    return ""


def _quoted_list(words: List[str]) -> str:
    shown = ", ".join(f'"{w}"' for w in words[:MAX_LISTED_WORDS])
    return shown + (" and others" if len(words) > MAX_LISTED_WORDS else "")


def _context_phrase(detail: dict) -> str:
    parts = []
    if detail.get("context_words"):
        parts.append(_quoted_list(detail["context_words"]))
    if detail.get("negated"):
        parts.append("a negation")
    identifier = detail.get("identifier")
    if identifier == "ordinal":
        parts.append("an ordinal number (such as 3rd), which names an item rather than a quantity")
    elif identifier:
        parts.append(f'the label word "{identifier}", so the numbers may name different items')
    return " and ".join(parts)


def _where(detail: dict) -> str:
    if detail.get("document"):
        return f' ({detail["document"]}, line {detail.get("line")})'
    return ""


def reason_text(reason: Optional[str], detail: dict) -> str:
    # Absent evidence
    if reason == "no_content_words":
        return ('The claim has no distinctive words to look for, only common words such as '
                '"it" or "is".')
    if reason == "no_relevant_passage":
        return "No sentence in your documents shares enough words with this claim."
    if reason == "mostly_absent":
        return ("Your documents don't mention most of this claim. The closest sentence shares "
                "only a few of its words.")
    # Partial matches
    if reason == "partial_support":
        return "Part of the claim isn't in any single sentence."
    if reason == "qualifier_mismatch":
        in_doc = detail.get("cue_words_only_in_document") or []
        in_claim = detail.get("cue_words_only_in_claim") or []
        if in_doc and in_claim:
            return (f"The closest sentence has qualifier words the claim doesn't ({_quoted_list(in_doc)}), "
                    f"and the claim has some the sentence doesn't ({_quoted_list(in_claim)}).")
        if in_doc:
            return f"The closest sentence has qualifier words the claim doesn't: {_quoted_list(in_doc)}."
        return f"The claim has qualifier words the closest sentence doesn't: {_quoted_list(in_claim)}."
    if reason == "extra_words":
        return ("The closest sentence has words the claim leaves out, which may narrow its "
                f"meaning: {_quoted_list(detail.get('extra_terms') or [])}.")
    if reason == "outside_rules":
        return ("A sentence uses similar words, but not in a way CiteCheck's strict rules "
                "can judge. Read the closest passages yourself.")
    if reason == "needs_multiple_sources":
        return ("This needs facts from more than one place. CiteCheck V0.1 only judges "
                "one sentence at a time.")
    # Ambiguous context or values
    if reason == "unclear_number":
        if detail.get("also_supported"):
            return ("One sentence backs this claim, but another gives a different number. Because of "
                    f"{_context_phrase(detail)}, CiteCheck can't tell whether your documents agree.")
        return ("A sentence gives a different number, but because of "
                f"{_context_phrase(detail)} it may not contradict the claim.")
    if reason == "unclear_reference":
        return (f'The claim starts with "{detail.get("word", "it")}", and CiteCheck can\'t tell what '
                "that refers to. A matching sentence is shown, but the claim needs a named subject.")
    if reason == "conflicting_sources":
        return "Your documents disagree: one sentence backs this claim and another contradicts it."
    if reason == "flagged_context":
        return ("A sentence matches, but it sits under a heading that suggests old, draft, "
                f'hypothetical or limited content: "{detail.get("heading", "")}"{_where(detail)}.')
    if reason == "dated_context":
        if detail.get("heading"):
            return (f'A sentence matches, but it sits under a dated heading, "{detail["heading"]}"'
                    f"{_where(detail)}, and the claim doesn't mention that year. It may describe the past.")
        return (f'The closest sentence is tied to a date ("{detail.get("date", "")}"){_where(detail)} '
                "that the claim doesn't mention. It may describe the past.")
    if reason == "nearby_context":
        return ("A sentence matches, but the sentence next to it may narrow it "
                f'({detail.get("document")}, line {detail.get("neighbour_line")}): '
                f'"{detail.get("neighbour", "")}"')
    # Integrity
    if reason == "quote_not_verified":
        return ("The quote failed the integrity check. The text at that location changed or "
                "couldn't be read, so it was not used.")
    return ""


@dataclass
class Style:
    colour: bool = False
    unicode: bool = True

    @classmethod
    def detect(cls, stream, no_color_flag: bool) -> "Style":
        no_color = no_color_flag or "NO_COLOR" in os.environ
        dumb = os.environ.get("TERM") == "dumb"
        encoding = (getattr(stream, "encoding", "") or "").lower().replace("-", "")
        is_tty = bool(getattr(stream, "isatty", lambda: False)())
        return cls(colour=is_tty and not no_color and not dumb,
                   unicode=encoding.startswith("utf") and not dumb and not no_color)


@dataclass
class RunReport:
    answer_name: str
    docs_folder: str
    doc_count: int
    split_mode: str
    results: List[ClaimResult]
    skipped: List[Skipped]
    blank_lines: int
    warnings: List[str] = field(default_factory=list)
    elapsed: float = 0.0
    date: str = field(default_factory=lambda: time.strftime("%Y-%m-%d"))


def _counts(run: RunReport) -> dict:
    counts = {label: 0 for label in (SUPPORTED, CONTRADICTED, INSUFFICIENT)}
    for r in run.results:
        counts[r.label] += 1
    return counts


def _integrity(run: RunReport) -> str:
    quotes = [ev for r in run.results for ev in r.evidence]
    matching = sum(1 for ev in quotes if ev.verified_verbatim)
    return f"{matching} of {len(quotes)} quotes match their files exactly"


def _flat(text: str) -> str:
    return " ".join(text.split())


def _short_quote(text: str) -> str:
    flat = _flat(text)
    if len(flat) > QUOTE_LIMIT:
        return flat[:QUOTE_LIMIT] + " [...] (full quote in report)"
    return flat


def _wrap(lines: List[Line], prefix: str, text: str) -> None:
    wrapped = textwrap.wrap(text, width=WIDTH, initial_indent=prefix,
                            subsequent_indent=" " * len(prefix),
                            break_long_words=False, break_on_hyphens=False)
    lines.extend(wrapped or [prefix.rstrip()])


def _field(lines: List[Line], label: str, value: str, indent: str = "  ") -> None:
    _wrap(lines, f"{indent}{label:<8}: ", value)


def _find_hint(quote: str) -> str:
    return " ".join(quote.split()[:4])


def _evidence_lines(lines: List[Line], ev) -> None:
    _field(lines, "Quote", f'"{_short_quote(ev.quote)}"')
    _field(lines, "Source", f"{ev.document}, line {ev.line}, characters {ev.char_start}-{ev.char_end}")
    if ev.heading:
        _field(lines, "Heading", f'"{_flat(ev.heading)}"')
    if ev.rule:
        _field(lines, "Rule", rule_text(ev.rule, ev.detail))
    _field(lines, "Find it", f"open {ev.document}, go to line {ev.line}, search for: {_find_hint(ev.quote)}")


def _passage_place(p) -> str:
    place = f"{p.document}, line {p.line}, characters {p.char_start}-{p.char_end}"
    if p.heading:
        place += f', under "{_flat(p.heading)}"'
    if p.context:
        place += f" [{CONTEXT_NOTES[p.context]}]"
    return place


def _terminal_text(lines: List[Line]) -> str:
    return "\n".join(line[1] if isinstance(line, tuple) else neutralise(line) for line in lines) + "\n"


def render_terminal(run: RunReport, style: Style, quiet: bool = False) -> str:
    lines: List[Line] = []
    lines.append(f"CiteCheck {__version__} - {HEADER}")
    _wrap(lines, "", DISCLAIMER)
    lines.append("")
    claim_word = "claim" if len(run.results) == 1 else "claims"
    skipped_count = len(run.skipped) + run.blank_lines
    lines.append(f"Answer file : {run.answer_name}  ({len(run.results)} {claim_word}, "
                 f"{skipped_count} lines skipped)")
    lines.append(f"Documents   : {run.docs_folder}  ({run.doc_count} files)")
    lines.append("")

    if not quiet:
        lines.append("RESULTS")
        lines.append("")
        total = len(run.results)
        for r in run.results:
            _result_lines(lines, r, total, run.answer_name, style)
            lines.append("")
        if run.skipped:
            lines.append("SKIPPED (not checked as claims)")
            for s in run.skipped:
                _wrap(lines, "  ", f'{run.answer_name}, line {s.line}: "{_flat(s.text)}" - '
                                   f"{SKIP_TEXT.get(s.reason, s.reason)}")
            lines.append("")

    if run.warnings:
        lines.append("WARNINGS")
        for w in run.warnings:
            _wrap(lines, "  ", w)
        lines.append("")

    counts = _counts(run)
    total = len(run.results)
    decided = counts[SUPPORTED] + counts[CONTRADICTED]
    pct = round(100 * decided / total) if total else 0
    lines.append("SUMMARY")
    lines.append(f"  Claims checked       : {total}")
    lines.append(f"  Supported            : {counts[SUPPORTED]}")
    lines.append(f"  Contradicted         : {counts[CONTRADICTED]}")
    lines.append(f"  Insufficient evidence: {counts[INSUFFICIENT]}   "
                 "(your documents don't settle these under CiteCheck's rules)")
    lines.append(f"  Decided (coverage)   : {decided} of {total} ({pct}%)")
    lines.append(f"  Quote integrity      : {_integrity(run)}")
    lines.append(f"  Time                 : {run.elapsed:.1f} s")
    if total and decided == 0:
        lines.append("")
        _wrap(lines, "", "No claim could be decided under CiteCheck's strict rules. Check the "
                         "closest passages for each claim. This does not mean the claims are wrong.")
    lines.append("")
    _wrap(lines, "", QUOTE_INTEGRITY_NOTE)
    _wrap(lines, "", ABSTAIN_EXPLAINER)
    return _terminal_text(lines)


def _result_lines(lines: List[Line], r: ClaimResult, total: int, answer_name: str, style: Style) -> None:
    lines.append(f"Claim {r.claim.id} of {total} ({answer_name}, line {r.claim.line})")
    _wrap(lines, "  ", f'"{_flat(r.claim.text)}"')
    symbol = SYMBOL[r.label][0 if style.unicode else 1]
    tag = TAG[r.label]
    if style.colour:
        tag = f"{COLOUR[r.label]}{tag}{RESET}"
    # Built only from CiteCheck's own text, so it is printed as it is.
    lines.append((RAW, f"  {tag} {symbol}  {GLOSS[r.label]}"))
    if len(r.claim.text) > LONG_CLAIM_CHARS:
        _wrap(lines, "  ", "Note: strict rules rarely match long claims. Splitting it into "
                           "shorter statements usually gives better results.")

    if r.label in (SUPPORTED, CONTRADICTED):
        _evidence_lines(lines, r.evidence[0])
        if r.also_found:
            places = "place" if r.also_found == 1 else "places"
            lines.append(f"  Also found in {r.also_found} other {places}.")
    else:
        _field(lines, "Why", reason_text(r.abstain_reason, r.detail))
        missing = r.detail.get("missing_terms")
        if missing:
            _wrap(lines, "  Not found: ", _quoted_list(missing))
        for i, ev in enumerate(r.evidence, start=1):
            lines.append(f"  Evidence {i} of {len(r.evidence)}:")
            _evidence_lines(lines, ev)

    if r.closest:
        lines.append(f"  {CLOSEST_HEADING}")
        for p in r.closest:
            _wrap(lines, f"    {p.rank}. ", f'"{_short_quote(p.text)}"')
            _wrap(lines, "       ", _passage_place(p))


# Markdown -------------------------------------------------------------------

_MD_SPECIAL = re.compile(r"([\\`*_\[\]<>#|&!()~@])")
_MD_SCHEME_RE = re.compile(r"(?i)\b(https?|ftp|mailto|file)\\?:")
_MD_WWW_RE = re.compile(r"(?i)\bwww\.")
_MD_LINE_START_RE = re.compile(r"^(\s*)([-+=]|\d+[.)])(?=\s|$)")


def md_escape(text: str) -> str:
    """Escape Markdown syntax so untrusted text shows as plain text.

    Also breaks automatic links (web addresses, "www." and e-mail addresses), so a
    document can't put a clickable link into the report.
    """
    text = _MD_SPECIAL.sub(r"\\\1", text)
    text = _MD_SCHEME_RE.sub(r"\1\\:", text)
    return _MD_WWW_RE.sub(lambda m: m.group()[:-1] + "\\.", text)


def _escape_marker(match) -> str:
    # Markdown can only escape punctuation. "\1." would show the backslash, so
    # an ordered-list marker keeps its digits and escapes its "." or ")".
    marker = match.group(2)
    if marker[0].isdigit():
        return match.group(1) + marker[:-1] + "\\" + marker[-1]
    return match.group(1) + "\\" + marker


def _md_line(text: str) -> str:
    """md_escape, plus a list marker at the start of a line."""
    return _MD_LINE_START_RE.sub(_escape_marker, md_escape(text))


def _md_blockquote(text: str) -> List[str]:
    return ["> " + _md_line(line) for line in LINE_BREAK_RE.split(text)]


def render_markdown(run: RunReport) -> str:
    out: List[str] = ["# CiteCheck report", ""]
    disclaimer = (
        "**Read this first.** CiteCheck is an experimental prototype, not for high-stakes "
        "decisions. Each verdict is relative to the documents you provided. "
        "CiteCheck does not check whether a claim is true in the real world. It uses "
        "strict word-matching rules, not AI. \"Insufficient evidence\" means your "
        "documents don't settle the claim under those rules. It is not a mistake. "
        "Closest passages have similar wording. They are not proof."
    )
    out += ["> " + disclaimer, ""]
    skipped_count = len(run.skipped) + run.blank_lines
    out += [
        f"- Date: {run.date}",
        f"- Answer file: {md_escape(run.answer_name)} ({len(run.results)} claims, {skipped_count} lines skipped)",
        f"- Documents: {md_escape(run.docs_folder)} ({run.doc_count} files)",
        f"- Rule set: v{RULESET_VERSION}",
        "",
    ]
    counts = _counts(run)
    total = len(run.results)
    decided = counts[SUPPORTED] + counts[CONTRADICTED]
    pct = round(100 * decided / total) if total else 0
    out += [
        "## Summary", "",
        "| Result | Count |", "|---|---|",
        f"| Supported | {counts[SUPPORTED]} |",
        f"| Contradicted | {counts[CONTRADICTED]} |",
        f"| Insufficient evidence | {counts[INSUFFICIENT]} |",
        f"| **Decided** | {decided} of {total} ({pct}%) |",
        f"| Quote integrity (exact text match) | {_integrity(run)} |",
        "",
    ]
    for r in run.results:
        out.append(f"## Claim {r.claim.id}: {LABEL_WORDS[r.label]}")
        out.append("")
        out.append(f"**Claim** ({md_escape(run.answer_name)}, line {r.claim.line}): {md_escape(r.claim.text)}")
        out.append("")
        if r.label == SUPPORTED:
            out.append(f"**Result:** SUPPORTED. {GLOSS[SUPPORTED]}")
        elif r.label == CONTRADICTED:
            out.append(f"**Result:** CONTRADICTED. {GLOSS[CONTRADICTED]}")
        else:
            out.append("**Result:** INSUFFICIENT EVIDENCE. Your documents don't settle this claim.")
        out.append("")
        if r.label in (SUPPORTED, CONTRADICTED):
            ev = r.evidence[0]
            out.append(f"**Rule:** {md_escape(rule_text(ev.rule, ev.detail))}")
            out.append("")
            out += _md_evidence(ev)
            if r.also_found:
                out += [f"Also found in {r.also_found} other place(s).", ""]
        else:
            out.append(f"**Why:** {md_escape(reason_text(r.abstain_reason, r.detail))}")
            out.append("")
            missing = r.detail.get("missing_terms")
            if missing:
                out += [f"**Not found:** {md_escape(_quoted_list(missing))}", ""]
            for i, ev in enumerate(r.evidence, start=1):
                out += [f"**Evidence {i} of {len(r.evidence)}**", ""]
                out += _md_evidence(ev)
        if r.closest:
            out += [f"**{CLOSEST_HEADING}**", ""]
            for p in r.closest:
                out.append(f"{p.rank}. \"{md_escape(_flat(p.text))}\" ({md_escape(_passage_place(p))})")
            out.append("")
    if run.skipped:
        out += ["## Skipped lines", ""]
        for s in run.skipped:
            out.append(f"- Line {s.line}: \"{md_escape(s.text)}\" ({SKIP_TEXT.get(s.reason, s.reason)})")
        out.append("")
    if run.warnings:
        out += ["## Warnings", ""]
        out += [f"- {md_escape(w)}" for w in run.warnings]
        out.append("")
    out += ["## Rules used", ""]
    out += [f"- **{name}:** {md_escape(text)}" for name, text in RULE_SUMMARY]
    out += [
        "",
        "## Legend",
        "",
        "Line numbers start at 1. Character offsets start at 0, count Unicode code points "
        "from the start of the file, and the end is not included. If a file starts with a "
        "byte-order mark (BOM), offsets count from after it. A Windows line break (CRLF) "
        "counts as two characters. A line can end with LF, CRLF or CR.",
        "",
        "Control characters from your files are shown as escapes such as \\\\x1b. The JSON "
        "report keeps the exact text.",
        "",
        QUOTE_INTEGRITY_NOTE + " Check important claims yourself.",
        "",
        "> " + DISCLAIMER,
        "",
    ]
    return neutralise("\n".join(out))


def _md_evidence(ev) -> List[str]:
    lines = [f"**Quote** ({md_escape(ev.document)}, line {ev.line}, characters {ev.char_start}-{ev.char_end}):"]
    lines += _md_blockquote(ev.quote)
    lines.append("")
    if ev.heading:
        lines += [f"**Heading:** {md_escape(_flat(ev.heading))}", ""]
    lines += [f"**How to check it:** open {md_escape(ev.document)}, go to line {ev.line} and search for "
              f"\"{md_escape(_find_hint(ev.quote))}\".", ""]
    return lines


# JSON -----------------------------------------------------------------------

def result_to_dict(r: ClaimResult, answer_name: str) -> dict:
    return {
        "claim_id": r.claim.id,
        "claim_text": r.claim.text,
        "source": {"file": answer_name, "line": r.claim.line},
        "label": r.label,
        "rule": r.rule,
        "evidence": [
            {
                "document": ev.document,
                "line": ev.line,
                "char_start": ev.char_start,
                "char_end": ev.char_end,
                "quote": ev.quote,
                "heading": ev.heading,
                "rule": ev.rule,
                "detail": ev.detail,
                "verified_verbatim": ev.verified_verbatim,
            }
            for ev in r.evidence
        ],
        "also_found_elsewhere": r.also_found,
        "abstain_reason": r.abstain_reason,
        "detail": r.detail,
        "closest_passages": [
            {
                "rank": p.rank,
                "document": p.document,
                "line": p.line,
                "char_start": p.char_start,
                "char_end": p.char_end,
                "text": p.text,
                "heading": p.heading,
                "context": p.context,
            }
            for p in r.closest
        ],
    }


def render_json(run: RunReport) -> str:
    counts = _counts(run)
    total = len(run.results)
    decided = counts[SUPPORTED] + counts[CONTRADICTED]
    report = {
        "schema_version": SCHEMA_VERSION,
        "tool": {"name": "citecheck", "version": __version__, "rule_set": RULESET_VERSION,
                 "status": "experimental prototype"},
        "disclaimer": ("Verdicts are relative to the provided documents only. They do not "
                       "establish real-world truth. Closest passages are not proof. "
                       "Not for high-stakes decisions."),
        "field_notes": {
            "verified_verbatim": ("Quote integrity only: true when the quote matches its file exactly "
                                  "at the stated offsets. It does not mean the quote supports or "
                                  "contradicts the claim."),
            "closest_passages.context": ("Set when the passage's heading or a neighbouring sentence "
                                         "limits it: flagged_context, dated_context or nearby_context."),
        },
        "run": {
            "date": run.date,
            "answer_file": run.answer_name,
            "docs_folder": run.docs_folder,
            "settings": {"split": run.split_mode, "max_gap": MAX_GAP, "closest_k": CLOSEST_K},
            "offset_convention": {"line_base": 1, "char_base": 0, "char_end": "exclusive",
                                  "unit": "code_point", "bom": "offsets start after a leading BOM",
                                  "line_breaks": ["\n", "\r\n", "\r"]},
        },
        "summary": {
            "total": total,
            "supported": counts[SUPPORTED],
            "contradicted": counts[CONTRADICTED],
            "insufficient_evidence": counts[INSUFFICIENT],
            "skipped": len(run.skipped) + run.blank_lines,
            "coverage": round(decided / total, 4) if total else 0.0,
            "quotes_shown": sum(len(r.evidence) for r in run.results),
            "quotes_matching_file": sum(1 for r in run.results for ev in r.evidence if ev.verified_verbatim),
        },
        "results": [result_to_dict(r, run.answer_name) for r in run.results],
        "skipped": [
            {"file": run.answer_name, "line": s.line, "text": s.text, "reason": s.reason}
            for s in run.skipped
        ],
        "warnings": list(run.warnings),
    }
    return json.dumps(report, indent=2, ensure_ascii=False) + "\n"


# Files ----------------------------------------------------------------------

def write_new_file(directory: Path, stem: str, ext: str, content: str) -> Tuple[Path, bool]:
    """Write content to a file that doesn't exist yet. Never overwrites.

    Returns (path, renamed). renamed is True if the plain name was taken and a
    timestamp suffix was added instead.
    """
    directory.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    candidates = [directory / f"{stem}.{ext}", directory / f"{stem}-{stamp}.{ext}"]
    candidates += [directory / f"{stem}-{stamp}-{n}.{ext}" for n in range(2, 100)]
    for path in candidates:
        try:
            with open(path, "x", encoding="utf-8", newline="\n") as fh:
                fh.write(content)
            return path, path != candidates[0]
        except FileExistsError:
            continue
    raise FileExistsError(f"Could not find a free file name for {stem}.{ext} in {directory}")


def write_reports(run: RunReport, out_dir: Path, fmt: str) -> Tuple[List[Path], bool]:
    written: List[Path] = []
    renamed = False
    if fmt in ("md", "both"):
        path, was_renamed = write_new_file(out_dir, "report", "md", render_markdown(run))
        written.append(path)
        renamed = renamed or was_renamed
    if fmt in ("json", "both"):
        path, was_renamed = write_new_file(out_dir, "report", "json", render_json(run))
        written.append(path)
        renamed = renamed or was_renamed
    return written, renamed
