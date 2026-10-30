"""Reading files, tracking line numbers and re-checking quotes on disk."""

import hashlib
import os
import re
import stat
from bisect import bisect_right
from pathlib import Path
from typing import List, Tuple

from .thresholds import MAX_DOCUMENTS, MAX_TOTAL_DOC_BYTES

MAX_BYTES = 1_000_000
DOC_SUFFIXES = (".txt", ".md")
# The first line of every Markdown report CiteCheck writes.
REPORT_MARKER = "# CiteCheck report"
_LINE_BREAK_RE = re.compile(r"\r\n|\r|\n")


class TextReadError(Exception):
    """A file could not be used as text. kind is "too_large" or "encoding"."""

    def __init__(self, kind: str, path: Path):
        super().__init__(f"{kind}: {path}")
        self.kind = kind
        self.path = path


class InputLimitError(Exception):
    """The documents folder is too big to check in one run. The message says why."""


def read_text(path: Path) -> Tuple[str, str]:
    """Return (text, sha256 of the bytes). Keeps \\r\\n as it is; strips a BOM."""
    with open(path, "rb") as fh:
        data = fh.read(MAX_BYTES + 1)
    if len(data) > MAX_BYTES:
        raise TextReadError("too_large", path)
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        raise TextReadError("encoding", path) from None
    if text.startswith("\ufeff"):
        text = text[1:]
    return text, hashlib.sha256(data).hexdigest()


class Document:
    def __init__(self, name: str, path: Path, text: str, sha256: str):
        self.name = name
        self.path = path
        self.text = text
        self.sha256 = sha256
        # \n, \r\n and a lone \r all end a line.
        self._line_starts = [0] + [m.end() for m in _LINE_BREAK_RE.finditer(text)]

    def line_of(self, offset: int) -> int:
        """1-based line number of a character offset."""
        return bisect_right(self._line_starts, offset)


def sort_key(name: str) -> bytes:
    """Filenames are sorted by their bytes, so order never depends on locale."""
    return os.fsencode(name)


def _is_regular_file(path: Path) -> bool:
    """True for an ordinary file. Pipes, devices and sockets could hang a read."""
    try:
        return stat.S_ISREG(os.stat(path).st_mode)
    except OSError:
        return False


def load_documents(folder: Path) -> Tuple[List[Document], List[str]]:
    """Load top-level .txt and .md files. Return documents and warnings.

    Raises InputLimitError if the folder has more than MAX_DOCUMENTS candidate
    files or more than MAX_TOTAL_DOC_BYTES of text in total.
    """
    root = folder.resolve()
    docs: List[Document] = []
    warnings: List[str] = []
    total_bytes = 0
    candidates = 0
    for entry in sorted(folder.iterdir(), key=lambda p: sort_key(p.name)):
        if entry.name.startswith("."):
            continue
        if entry.is_dir():
            continue
        if entry.suffix.lower() not in DOC_SUFFIXES:
            warnings.append(f'"{entry.name}" was skipped: only .txt and .md files are supported.')
            continue
        candidates += 1
        if candidates > MAX_DOCUMENTS:
            raise InputLimitError(
                f"The folder has more than {MAX_DOCUMENTS} .txt or .md files. "
                "Check a smaller folder, or split the documents into several folders.")
        try:
            real = entry.resolve()
        except (OSError, RuntimeError):
            warnings.append(f'Skipped "{entry.name}": it is a broken or looping link.')
            continue
        if real.parent != root:
            warnings.append(f'Skipped "{entry.name}": it links to a file outside the folder.')
            continue
        if real.name.startswith("."):
            warnings.append(f'Skipped "{entry.name}": it links to a hidden file.')
            continue
        if not _is_regular_file(real):
            warnings.append(f'Skipped "{entry.name}": it is not a regular file.')
            continue
        try:
            text, sha = read_text(real)
        except TextReadError as exc:
            if exc.kind == "too_large":
                warnings.append(f'Skipped "{entry.name}": larger than 1 MB.')
            else:
                warnings.append(
                    f'Couldn\'t read "{entry.name}" as text (unsupported encoding). '
                    "Save it as UTF-8 and try again."
                )
            continue
        except OSError:
            warnings.append(f'Skipped "{entry.name}": it couldn\'t be opened.')
            continue
        if not text.strip():
            warnings.append(f'Skipped "{entry.name}": file is empty.')
            continue
        if text.startswith(REPORT_MARKER):
            warnings.append(f'Skipped "{entry.name}": it looks like a CiteCheck report, not a source document.')
            continue
        total_bytes += len(text.encode("utf-8"))
        if total_bytes > MAX_TOTAL_DOC_BYTES:
            raise InputLimitError(
                f"The documents add up to more than {MAX_TOTAL_DOC_BYTES // 1_000_000} MB of text. "
                "Check a smaller folder, or split the documents into several folders.")
        docs.append(Document(entry.name, real, text, sha))
    return docs, warnings


def verify_on_disk(doc: Document, start: int, end: int, quote: str) -> bool:
    """Re-read the file and confirm the quote is still exactly at start:end."""
    try:
        text, sha = read_text(doc.path)
    except (OSError, TextReadError):
        return False
    return sha == doc.sha256 and text[start:end] == quote
