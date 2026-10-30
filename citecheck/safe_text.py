"""Neutralising control characters before text reaches a terminal or a Markdown file.

Documents, answers and filenames are untrusted. An escape sequence in any of
them could move the cursor, hide lines or change the terminal title, and a
bidirectional override could make text read differently from what it is
(QA finding F11). These characters are shown as visible escapes instead, such
as \\x1b or \\u202e. Line breaks and tabs are kept. The JSON report keeps the
exact text, escaped by the JSON encoder.
"""

import re

_UNSAFE_RE = re.compile(
    "[\x00-\x08\x0b-\x1f\x7f-\x9f"            # C0 and C1 controls, except \t and \n
    "؜‎‏‪-‮"          # bidirectional marks and overrides
    "⁦-⁩  ]"               # isolates, line and paragraph separators
)


def _escape(match) -> str:
    code = ord(match.group())
    return f"\\x{code:02x}" if code < 0x100 else f"\\u{code:04x}"


def neutralise(text: str) -> str:
    """Replace control and bidirectional characters with visible escapes."""
    return _UNSAFE_RE.sub(_escape, text)
