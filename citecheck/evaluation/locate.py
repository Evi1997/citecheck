"""Print the line and character offsets of a quote in a document.

Usage: python3 -m citecheck.evaluation.locate DOCUMENT "exact quote"
"""

import sys
from pathlib import Path

from ..textio import Document, TextReadError, read_text


def main(argv=None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if len(argv) != 2:
        sys.stderr.write('Usage: python3 -m citecheck.evaluation.locate DOCUMENT "exact quote"\n')
        return 2
    path, quote = Path(argv[0]), argv[1]
    try:
        text, sha = read_text(path)
    except (OSError, TextReadError) as exc:
        sys.stderr.write(f'Couldn\'t read "{path}": {exc}\n')
        return 2
    count = text.count(quote)
    if count != 1:
        sys.stderr.write(f"The quote must occur exactly once in {path.name}; found {count}.\n")
        return 1
    start = text.index(quote)
    doc = Document(path.name, path, text, sha)
    sys.stdout.write(f"{path.name}: line {doc.line_of(start)}, characters {start}-{start + len(quote)}\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
